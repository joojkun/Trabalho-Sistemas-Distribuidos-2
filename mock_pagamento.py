import logging
import math
import os
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template_string, request

from comum.config import PASTA_DADOS
from comum.http import ServicoHTTPError, requisitar_json


logger = logging.getLogger(__name__)
DB_PATH = Path(
    os.getenv(
        "MOCK_PAGAMENTO_DATABASE_PATH",
        str(PASTA_DADOS / "mock_pagamento.sqlite3"),
    )
)
PUBLIC_URL = os.getenv("MOCK_PAGAMENTO_PUBLIC_URL", "http://localhost:5004").rstrip(
    "/"
)
WEBHOOK_URL_PERMITIDA = os.getenv(
    "PAGAMENTO_WEBHOOK_URL",
    "http://127.0.0.1:5003/webhook",
)
HOST = os.getenv("MOCK_PAGAMENTO_HOST", "127.0.0.1")
PORTA = int(os.getenv("MOCK_PAGAMENTO_PORT", "5004"))

app = Flask(__name__)

PAGINA_CHECKOUT = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Checkout de pagamento</title>
  <style>
    body { font: 16px system-ui, sans-serif; margin: 3rem auto; max-width: 34rem; padding: 0 1rem; }
    main { border: 1px solid #d7dce5; border-radius: 1rem; padding: 2rem; }
    button { border: 0; border-radius: .5rem; cursor: pointer; font: inherit; margin: .5rem .4rem .5rem 0; padding: .8rem 1rem; }
    button:disabled { cursor: wait; opacity: .6; }
    .approve { background: #16794b; color: white; }
    .decline { background: #b42318; color: white; }
    #message { min-height: 1.5rem; }
  </style>
</head>
<body>
  <main>
    <h1>Checkout de pagamento (simulado)</h1>
    <p>Pedido: <strong>{{ charge.pedido_id }}</strong></p>
    <p>Valor: <strong>R$ {{ "%.2f"|format(charge.valor_total) }}</strong></p>
    {% if charge.status == "pendente" %}
      <p>Escolha o resultado para enviar o webhook ao microsservico de pagamento.</p>
      <button class="approve" data-status="APROVADO">Pagamento aprovado</button>
      <button class="decline" data-status="RECUSADO">Pagamento recusado</button>
    {% else %}
      <p>Resultado: <strong>{{ charge.status }}</strong></p>
    {% endif %}
    <p id="message" role="status" aria-live="polite"></p>
  </main>
  {% if charge.status == "pendente" %}
  <script>
    const chargeId = {{ charge.charge_id|tojson }};
    const message = document.querySelector("#message");
    document.querySelectorAll("button[data-status]").forEach((button) => {
      button.addEventListener("click", async () => {
        document.querySelectorAll("button[data-status]").forEach((item) => {
          item.disabled = true;
        });
        message.textContent = "Enviando resultado...";
        try {
          const response = await fetch(`/checkout/${chargeId}/result`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: button.dataset.status }),
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.erro || "Falha no webhook.");
          message.textContent = "Resultado enviado: " + result.status;
          document.querySelectorAll("button[data-status]").forEach((item) => {
            item.remove();
          });
        } catch (error) {
          message.textContent = error.message;
          document.querySelectorAll("button[data-status]").forEach((item) => {
            item.disabled = false;
          });
        }
      });
    });
  </script>
  {% endif %}
</body>
</html>
"""


def _conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(DB_PATH, timeout=10)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA busy_timeout = 5000")
    return conexao


def inicializar_banco():
    conexao = _conectar()
    try:
        with conexao:
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS cobrancas (
                    charge_id TEXT PRIMARY KEY,
                    pedido_id TEXT NOT NULL UNIQUE,
                    valor_total REAL NOT NULL CHECK (valor_total > 0),
                    webhook_url TEXT NOT NULL,
                    webhook_token TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN (
                            'pendente', 'processando', 'APROVADO', 'RECUSADO'
                        ))
                )
                """
            )
    finally:
        conexao.close()


def _url_webhook_valida(url):
    partes = urlsplit(url)
    permitido = urlsplit(WEBHOOK_URL_PERMITIDA)
    return (
        partes.scheme in ("http", "https")
        and partes.netloc
        and partes.scheme == permitido.scheme
        and partes.netloc == permitido.netloc
        and partes.path == permitido.path
    )


def _serializar_cobranca(cobranca):
    return {
        "charge_id": cobranca["charge_id"],
        "pedido_id": cobranca["pedido_id"],
        "valor_total": cobranca["valor_total"],
        "status": cobranca["status"],
        "checkout_url": f"{PUBLIC_URL}/checkout/{cobranca['charge_id']}",
    }


@app.get("/health")
def health():
    return jsonify({"servico": "mock-pagamento", "status": "ok"})


@app.post("/api/charges")
def criar_cobranca():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "O corpo deve ser um objeto JSON."}), 400

    pedido_id = dados.get("pedido_id")
    valor_total = dados.get("valor_total")
    webhook_url = dados.get("webhook_url")
    webhook_token = dados.get("webhook_token", "")
    if (
        not isinstance(pedido_id, str)
        or not pedido_id
        or isinstance(valor_total, bool)
        or not isinstance(valor_total, (int, float))
        or abs(valor_total) > 1_000_000_000_000
        or not math.isfinite(valor_total)
        or valor_total <= 0
        or not isinstance(webhook_url, str)
        or not _url_webhook_valida(webhook_url)
        or not isinstance(webhook_token, str)
    ):
        return jsonify({"erro": "Dados da cobranca ou webhook invalidos."}), 400

    conexao = _conectar()
    try:
        with conexao:
            cobranca = conexao.execute(
                "SELECT * FROM cobrancas WHERE pedido_id = ?",
                (pedido_id,),
            ).fetchone()
            if cobranca is None:
                cobranca_id = uuid.uuid4().hex
                conexao.execute(
                    """
                    INSERT INTO cobrancas
                        (charge_id, pedido_id, valor_total, webhook_url,
                         webhook_token, status)
                    VALUES (?, ?, ?, ?, ?, 'pendente')
                    """,
                    (
                        cobranca_id,
                        pedido_id,
                        float(valor_total),
                        webhook_url,
                        webhook_token,
                    ),
                )
                cobranca = conexao.execute(
                    "SELECT * FROM cobrancas WHERE charge_id = ?",
                    (cobranca_id,),
                ).fetchone()
            elif (
                cobranca["webhook_url"] != webhook_url
                or cobranca["valor_total"] != float(valor_total)
            ):
                return jsonify({"erro": "Ja existe uma cobranca diferente para o pedido."}), 409
    finally:
        conexao.close()

    return jsonify(_serializar_cobranca(cobranca)), 201


@app.get("/checkout/<charge_id>")
def checkout(charge_id):
    conexao = _conectar()
    try:
        cobranca = conexao.execute(
            "SELECT * FROM cobrancas WHERE charge_id = ?",
            (charge_id,),
        ).fetchone()
    finally:
        conexao.close()

    if cobranca is None:
        return jsonify({"erro": "Cobranca nao encontrada."}), 404
    return render_template_string(PAGINA_CHECKOUT, charge=cobranca)


@app.post("/checkout/<charge_id>/result")
def processar_resultado(charge_id):
    dados = request.get_json(silent=True)
    resultado = dados.get("status") if isinstance(dados, dict) else None
    if resultado not in ("APROVADO", "RECUSADO"):
        return jsonify({"erro": "status deve ser APROVADO ou RECUSADO."}), 400

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute("BEGIN IMMEDIATE")
            cobranca = conexao.execute(
                "SELECT * FROM cobrancas WHERE charge_id = ?",
                (charge_id,),
            ).fetchone()
            if cobranca is None:
                return jsonify({"erro": "Cobranca nao encontrada."}), 404
            if cobranca["status"] in ("APROVADO", "RECUSADO"):
                if cobranca["status"] == resultado:
                    return jsonify(
                        {"recebido": True, "status": cobranca["status"]}
                    )
                return jsonify({"erro": "A cobranca ja foi finalizada."}), 409
            if cobranca["status"] == "processando":
                return jsonify({"erro": "A cobranca ja esta sendo processada."}), 409

            conexao.execute(
                "UPDATE cobrancas SET status = 'processando' WHERE charge_id = ?",
                (charge_id,),
            )
    finally:
        conexao.close()

    headers = {}
    if cobranca["webhook_token"]:
        headers["X-Payment-Token"] = cobranca["webhook_token"]
    try:
        requisitar_json(
            cobranca["webhook_url"],
            metodo="POST",
            dados={
                "pedido_id": cobranca["pedido_id"],
                "charge_id": charge_id,
                "status": resultado,
                "valor_total": cobranca["valor_total"],
            },
            headers=headers,
        )
    except ServicoHTTPError as erro:
        conexao = _conectar()
        try:
            with conexao:
                conexao.execute(
                    "UPDATE cobrancas SET status = 'pendente' WHERE charge_id = ?",
                    (charge_id,),
                )
        finally:
            conexao.close()
        logger.exception("Falha ao entregar webhook da cobranca %s", charge_id)
        return jsonify({"erro": str(erro)}), 502

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute(
                "UPDATE cobrancas SET status = ? WHERE charge_id = ?",
                (resultado, charge_id),
            )
    finally:
        conexao.close()

    return jsonify({"recebido": True, "status": resultado})


if __name__ == "__main__":
    inicializar_banco()
    app.run(host=HOST, port=PORTA, threaded=True, use_reloader=False)
