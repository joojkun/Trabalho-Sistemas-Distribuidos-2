import hmac
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from flask import Flask, jsonify, request

from comum.config import PASTA_DADOS
from comum.http import ServicoHTTPError, requisitar_json
from comum.mensagens import (
    ErroPublicacaoEvento,
    consumir_eventos,
    publicar_evento,
)


logger = logging.getLogger(__name__)
DB_PATH = Path(
    os.getenv(
        "PAGAMENTO_DATABASE_PATH",
        str(PASTA_DADOS / "pagamento.sqlite3"),
    )
)
MOCK_PAGAMENTO_URL = os.getenv(
    "MOCK_PAGAMENTO_URL",
    "http://127.0.0.1:5004",
).rstrip("/")
WEBHOOK_URL = os.getenv(
    "PAGAMENTO_WEBHOOK_URL",
    "http://127.0.0.1:5003/webhook",
)
WEBHOOK_TOKEN = os.getenv("PAYMENT_WEBHOOK_TOKEN", "")
HOST = os.getenv("PAGAMENTO_HOST", "127.0.0.1")
PORTA = int(os.getenv("PAGAMENTO_PORT", "5003"))

app = Flask(__name__)


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
                CREATE TABLE IF NOT EXISTS pagamentos (
                    pedido_id TEXT PRIMARY KEY,
                    cobranca_id TEXT NOT NULL,
                    checkout_url TEXT NOT NULL,
                    pedido_json TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
    finally:
        conexao.close()


def _publicar_checkout(pedido):
    pedido_id = pedido.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id:
        raise ValueError("O evento nao contem um pedido_id valido.")
    valor_total = pedido.get("valor_total")
    if (
        isinstance(valor_total, bool)
        or not isinstance(valor_total, (int, float))
        or valor_total <= 0
    ):
        raise ValueError("O pedido nao contem um valor_total valido.")

    conexao = _conectar()
    try:
        existente = conexao.execute(
            "SELECT checkout_url, cobranca_id, status FROM pagamentos WHERE pedido_id = ?",
            (pedido_id,),
        ).fetchone()
    finally:
        conexao.close()

    if existente is not None:
        if existente["status"] != "pendente":
            logger.info("Pagamento do pedido %s ja foi finalizado.", pedido_id)
            return
        checkout_url = existente["checkout_url"]
        cobranca_id = existente["cobranca_id"]
    else:
        cobranca = requisitar_json(
            f"{MOCK_PAGAMENTO_URL}/api/charges",
            metodo="POST",
            dados={
                "pedido_id": pedido_id,
                "valor_total": valor_total,
                "webhook_url": WEBHOOK_URL,
                "webhook_token": WEBHOOK_TOKEN,
            },
        )
        checkout_url = cobranca.get("checkout_url")
        cobranca_id = cobranca.get("charge_id")
        if not isinstance(checkout_url, str) or not isinstance(cobranca_id, str):
            raise ServicoHTTPError(
                "O Mock de Pagamento nao retornou charge_id e checkout_url."
            )

        conexao = _conectar()
        try:
            with conexao:
                conexao.execute(
                    """
                    INSERT OR IGNORE INTO pagamentos
                        (pedido_id, cobranca_id, checkout_url, pedido_json, status)
                    VALUES (?, ?, ?, ?, 'pendente')
                    """,
                    (
                        pedido_id,
                        cobranca_id,
                        checkout_url,
                        json.dumps(pedido, ensure_ascii=False),
                    ),
                )
                existente = conexao.execute(
                    "SELECT checkout_url, cobranca_id, status FROM pagamentos WHERE pedido_id = ?",
                    (pedido_id,),
                ).fetchone()
        finally:
            conexao.close()

        if existente["status"] != "pendente":
            return
        checkout_url = existente["checkout_url"]
        cobranca_id = existente["cobranca_id"]

    dados_checkout = dict(pedido)
    dados_checkout.update(
        {
            "checkout_url": checkout_url,
            "cobranca_id": cobranca_id,
        }
    )
    publicar_evento("pagamento", "pagamento.checkout_criado", dados_checkout)
    print("Checkout criado para", pedido_id, checkout_url)


def processar_pagamento(envelope):
    _publicar_checkout(envelope["Data"])


@app.get("/health")
def health():
    return jsonify({"servico": "pagamento", "status": "ok"})


@app.post("/webhook")
def receber_webhook():
    if WEBHOOK_TOKEN and not hmac.compare_digest(
        request.headers.get("X-Payment-Token", ""),
        WEBHOOK_TOKEN,
    ):
        return jsonify({"erro": "Token de webhook invalido."}), 401

    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "O corpo deve ser um objeto JSON."}), 400

    pedido_id = dados.get("pedido_id")
    resultado = dados.get("status")
    cobranca_id = dados.get("charge_id")
    status_evento = {
        "APROVADO": "pagamento.aprovado",
        "RECUSADO": "pagamento.recusado",
    }.get(resultado.upper() if isinstance(resultado, str) else "")
    if not isinstance(pedido_id, str) or not pedido_id or status_evento is None:
        return jsonify({"erro": "pedido_id ou status de pagamento invalido."}), 400

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute("BEGIN IMMEDIATE")
            pagamento = conexao.execute(
                """
                SELECT cobranca_id, pedido_json, status
                FROM pagamentos
                WHERE pedido_id = ?
                """,
                (pedido_id,),
            ).fetchone()
            if pagamento is None:
                return jsonify({"erro": "Cobranca nao encontrada."}), 404
            if cobranca_id and cobranca_id != pagamento["cobranca_id"]:
                return jsonify({"erro": "Cobranca nao corresponde ao pedido."}), 409

            status_final = status_evento.removeprefix("pagamento.")
            if pagamento["status"] == status_final:
                return jsonify({"recebido": True, "idempotente": True})
            if pagamento["status"] != "pendente":
                return jsonify({"erro": "O pagamento ja esta sendo processado."}), 409

            conexao.execute(
                "UPDATE pagamentos SET status = ? WHERE pedido_id = ?",
                (f"processando_{status_final}", pedido_id),
            )
            pedido = json.loads(pagamento["pedido_json"])
    finally:
        conexao.close()

    try:
        publicar_evento("pagamento", status_evento, pedido)
    except ErroPublicacaoEvento:
        conexao = _conectar()
        try:
            with conexao:
                conexao.execute(
                    "UPDATE pagamentos SET status = 'pendente' WHERE pedido_id = ?",
                    (pedido_id,),
                )
        finally:
            conexao.close()
        raise

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute(
                "UPDATE pagamentos SET status = ? WHERE pedido_id = ?",
                (status_final, pedido_id),
            )
    finally:
        conexao.close()

    return jsonify({"recebido": True, "status": status_final})


def iniciar():
    inicializar_banco()
    consumidor = threading.Thread(
        target=consumir_eventos,
        args=("fila.pagamento", ["pedido.estoque_ok"], processar_pagamento),
        daemon=True,
    )
    consumidor.start()
    app.run(host=HOST, port=PORTA, threaded=True, use_reloader=False)


if __name__ == "__main__":
    iniciar()
