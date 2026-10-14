import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, stream_with_context

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
        "API_GATEWAY_DATABASE_PATH",
        str(PASTA_DADOS / "gateway.sqlite3"),
    )
)
ESTOQUE_API_URL = os.getenv(
    "ESTOQUE_API_URL",
    "http://127.0.0.1:5001",
).rstrip("/")
HOST = os.getenv("API_GATEWAY_HOST", "127.0.0.1")
PORTA = int(os.getenv("API_GATEWAY_PORT", "5000"))
ORIGENS_CORS = {
    origem.strip()
    for origem in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if origem.strip()
}

MAPA_STATUS = {
    "pedido.estoque_ok": ("estoque_confirmado", "Estoque confirmado"),
    "estoque.indisponivel": ("cancelado_estoque", "Estoque indisponivel"),
    "pagamento.checkout_criado": ("aguardando_pagamento", "Checkout de pagamento disponivel"),
    "pagamento.aprovado": ("pagamento_aprovado", "Pagamento aprovado"),
    "pagamento.recusado": ("cancelado_pagamento", "Pagamento recusado"),
    "pedido.enviado": ("enviado", "Pedido enviado"),
}
PADRAO_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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
            conexao.executescript(
                """
                CREATE TABLE IF NOT EXISTS pedidos (
                    pedido_id TEXT PRIMARY KEY,
                    cliente_id TEXT NOT NULL,
                    pedido_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checkout_url TEXT,
                    criado_em TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eventos_pedido (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pedido_id TEXT NOT NULL,
                    cliente_id TEXT NOT NULL,
                    evento TEXT NOT NULL,
                    dados_json TEXT NOT NULL,
                    criado_em TEXT NOT NULL,
                    UNIQUE (pedido_id, evento)
                );
                CREATE INDEX IF NOT EXISTS idx_eventos_cliente_id
                    ON eventos_pedido (cliente_id, id);
                """
            )
    finally:
        conexao.close()


def _validar_cliente_id(valor):
    if not isinstance(valor, str):
        return None
    try:
        return str(uuid.UUID(valor))
    except ValueError:
        return None


def _obter_cliente_id(valor):
    cliente_id = _validar_cliente_id(valor)
    if cliente_id is None:
        return None, (jsonify({"erro": "cliente_id deve ser um UUID valido."}), 400)
    return cliente_id, None


def _obter_produtos_disponiveis():
    resposta = requisitar_json(f"{ESTOQUE_API_URL}/api/products")
    produtos = resposta.get("products")
    if not isinstance(produtos, list):
        raise ServicoHTTPError("O Estoque nao retornou uma lista de produtos.")
    return [
        produto
        for produto in produtos
        if isinstance(produto, dict)
        and isinstance(produto.get("estoque"), int)
        and produto["estoque"] > 0
    ]


def _inserir_pedido(pedido):
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conexao = _conectar()
    try:
        with conexao:
            conexao.execute(
                """
                INSERT INTO pedidos
                    (pedido_id, cliente_id, pedido_json, status, criado_em)
                VALUES (?, ?, ?, 'criado', ?)
                """,
                (
                    pedido["pedido_id"],
                    pedido["cliente_id"],
                    json.dumps(pedido, ensure_ascii=False),
                    agora,
                ),
            )
            conexao.execute(
                """
                INSERT INTO eventos_pedido
                    (pedido_id, cliente_id, evento, dados_json, criado_em)
                VALUES (?, ?, 'pedido.criado', ?, ?)
                """,
                (
                    pedido["pedido_id"],
                    pedido["cliente_id"],
                    json.dumps(
                        {
                            "pedido_id": pedido["pedido_id"],
                            "status": "criado",
                            "evento": "pedido.criado",
                            "mensagem": "Pedido criado",
                        },
                        ensure_ascii=False,
                    ),
                    agora,
                ),
            )
    finally:
        conexao.close()


def _pedido_publico(linha):
    pedido = json.loads(linha["pedido_json"])
    pedido["status"] = linha["status"]
    if linha["checkout_url"]:
        pedido["checkout_url"] = linha["checkout_url"]
    return pedido


def _atualizar_pedido(envelope):
    evento = envelope["Event"]
    dados = envelope["Data"]
    if evento not in MAPA_STATUS:
        logger.warning("Evento de pedido desconhecido: %s", evento)
        return

    pedido_id = dados.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id:
        raise ValueError("O evento nao contem um pedido_id valido.")

    status, mensagem = MAPA_STATUS[evento]
    conexao = _conectar()
    try:
        with conexao:
            conexao.execute("BEGIN IMMEDIATE")
            linha = conexao.execute(
                """
                SELECT cliente_id, pedido_json, checkout_url
                FROM pedidos
                WHERE pedido_id = ?
                """,
                (pedido_id,),
            ).fetchone()
            if linha is None:
                logger.warning("Evento recebido para pedido desconhecido: %s", pedido_id)
                return

            pedido = json.loads(linha["pedido_json"])
            pedido["status"] = status
            checkout_url = linha["checkout_url"]
            if evento == "pagamento.checkout_criado":
                checkout_url = dados.get("checkout_url")
                partes = urlsplit(checkout_url) if isinstance(checkout_url, str) else None
                if (
                    partes is None
                    or partes.scheme not in ("http", "https")
                    or not partes.netloc
                ):
                    raise ValueError("O evento contem uma checkout_url invalida.")
                pedido["checkout_url"] = checkout_url
                pedido["cobranca_id"] = dados.get("cobranca_id")
            if evento == "pedido.enviado":
                pedido["nota_fiscal"] = dados.get("nota_fiscal")
                pedido["data_envio"] = dados.get("data_envio")

            conexao.execute(
                """
                UPDATE pedidos
                SET pedido_json = ?, status = ?, checkout_url = ?
                WHERE pedido_id = ?
                """,
                (
                    json.dumps(pedido, ensure_ascii=False),
                    status,
                    checkout_url,
                    pedido_id,
                ),
            )

            dados_sse = {
                "pedido_id": pedido_id,
                "status": status,
                "evento": evento,
                "mensagem": mensagem,
                "checkout_url": checkout_url,
                "nota_fiscal": pedido.get("nota_fiscal"),
                "data_envio": pedido.get("data_envio"),
            }
            agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conexao.execute(
                """
                INSERT OR IGNORE INTO eventos_pedido
                    (pedido_id, cliente_id, evento, dados_json, criado_em)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pedido_id,
                    linha["cliente_id"],
                    evento,
                    json.dumps(dados_sse, ensure_ascii=False),
                    agora,
                ),
            )
    finally:
        conexao.close()

    if evento in ("estoque.indisponivel", "pagamento.recusado"):
        publicar_evento("principal", "pedido.excluido", pedido)

    logger.info("Pedido %s atualizado para %s.", pedido_id, status)


def _ultimo_evento_cliente(cliente_id):
    conexao = _conectar()
    try:
        linha = conexao.execute(
            "SELECT COALESCE(MAX(id), 0) AS ultimo_id FROM eventos_pedido WHERE cliente_id = ?",
            (cliente_id,),
        ).fetchone()
        return linha["ultimo_id"]
    finally:
        conexao.close()


def _buscar_eventos(cliente_id, depois_do_id):
    conexao = _conectar()
    try:
        return conexao.execute(
            """
            SELECT id, dados_json
            FROM eventos_pedido
            WHERE cliente_id = ? AND id > ?
            ORDER BY id
            """,
            (cliente_id, depois_do_id),
        ).fetchall()
    finally:
        conexao.close()


def _validar_email(email):
    return isinstance(email, str) and PADRAO_EMAIL.fullmatch(email.strip()) is not None


@app.after_request
def adicionar_cors(resposta):
    origem = request.headers.get("Origin")
    if origem in ORIGENS_CORS:
        resposta.headers["Access-Control-Allow-Origin"] = origem
        resposta.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resposta.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Last-Event-ID"
        )
        resposta.headers["Access-Control-Max-Age"] = "600"
        resposta.headers["Vary"] = "Origin"
    return resposta


@app.get("/")
def indice():
    return jsonify(
        {
            "servico": "API Gateway",
            "documentacao": "Use os endpoints /api/products, /api/orders e /api/interests.",
        }
    )


@app.get("/health")
def health():
    return jsonify({"servico": "api-gateway", "status": "ok"})


@app.get("/api/products")
def listar_produtos():
    try:
        produtos = _obter_produtos_disponiveis()
    except ServicoHTTPError as erro:
        logger.error("Falha ao consultar o Estoque: %s", erro)
        return jsonify({"erro": "Nao foi possivel consultar o catalogo."}), 502
    return jsonify({"products": produtos})


@app.post("/api/orders")
def criar_pedido():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "O corpo deve ser um objeto JSON."}), 400

    cliente_id, erro_cliente = _obter_cliente_id(dados.get("cliente_id"))
    if erro_cliente:
        return erro_cliente

    itens = dados.get("produtos")
    if not isinstance(itens, list) or not itens:
        return jsonify({"erro": "Informe ao menos um produto."}), 400
    if len(itens) > 50:
        return jsonify({"erro": "Um pedido pode conter ate 50 itens diferentes."}), 400

    try:
        produtos = _obter_produtos_disponiveis()
    except ServicoHTTPError as erro:
        logger.error("Falha ao consultar o Estoque: %s", erro)
        return jsonify({"erro": "Nao foi possivel validar o catalogo."}), 502

    catalogo = {
        produto["produto_id"]: produto
        for produto in produtos
        if isinstance(produto.get("produto_id"), str)
    }
    quantidades = {}
    for item in itens:
        if not isinstance(item, dict):
            return jsonify({"erro": "Cada item deve ser um objeto."}), 400
        produto_id = item.get("produto_id")
        quantidade = item.get("quantidade")
        if not isinstance(produto_id, str) or produto_id not in catalogo:
            return jsonify({"erro": "Produto inexistente ou sem estoque."}), 400
        if (
            isinstance(quantidade, bool)
            or not isinstance(quantidade, int)
            or quantidade <= 0
            or quantidade > 10000
        ):
            return jsonify(
                {"erro": "A quantidade deve ser um inteiro entre 1 e 10000."}
            ), 400
        quantidades[produto_id] = quantidades.get(produto_id, 0) + quantidade

    produtos_pedido = []
    valor_total = 0.0
    for produto_id, quantidade in quantidades.items():
        produto = catalogo[produto_id]
        if quantidade > produto["estoque"]:
            return jsonify(
                {
                    "erro": f"Estoque insuficiente para {produto['nome']}.",
                    "disponivel": produto["estoque"],
                }
            ), 409
        preco = produto.get("preco")
        if (
            isinstance(preco, bool)
            or not isinstance(preco, (int, float))
            or preco < 0
        ):
            return jsonify({"erro": "O catalogo retornou um preco invalido."}), 502
        produtos_pedido.append(
            {"produto_id": produto_id, "quantidade": quantidade}
        )
        valor_total += preco * quantidade

    pedido = {
        "pedido_id": uuid.uuid4().hex[:12],
        "cliente_id": cliente_id,
        "produtos": produtos_pedido,
        "valor_total": round(valor_total, 2),
        "status": "criado",
    }
    try:
        _inserir_pedido(pedido)
        publicar_evento("principal", "pedido.criado", pedido)
    except ErroPublicacaoEvento as erro:
        logger.error("Falha ao publicar pedido %s: %s", pedido["pedido_id"], erro)
        return jsonify({"erro": "Nao foi possivel enviar o pedido ao processamento."}), 503
    except sqlite3.Error:
        logger.exception("Falha ao persistir o pedido.")
        return jsonify({"erro": "Nao foi possivel salvar o pedido."}), 503

    return jsonify(pedido), 202


@app.get("/api/orders")
def consultar_pedidos():
    cliente_id, erro_cliente = _obter_cliente_id(request.args.get("cliente_id"))
    if erro_cliente:
        return erro_cliente

    conexao = _conectar()
    try:
        linhas = conexao.execute(
            """
            SELECT pedido_id, pedido_json, status, checkout_url, criado_em
            FROM pedidos
            WHERE cliente_id = ?
            ORDER BY criado_em DESC
            """,
            (cliente_id,),
        ).fetchall()
    finally:
        conexao.close()

    pedidos = []
    for linha in linhas:
        pedido = _pedido_publico(linha)
        pedido["criado_em"] = linha["criado_em"]
        pedidos.append(pedido)
    return jsonify({"orders": pedidos})


@app.get("/api/orders/<pedido_id>")
def consultar_pedido(pedido_id):
    cliente_id, erro_cliente = _obter_cliente_id(request.args.get("cliente_id"))
    if erro_cliente:
        return erro_cliente

    conexao = _conectar()
    try:
        linha = conexao.execute(
            """
            SELECT pedido_id, cliente_id, pedido_json, status, checkout_url, criado_em
            FROM pedidos
            WHERE pedido_id = ? AND cliente_id = ?
            """,
            (pedido_id, cliente_id),
        ).fetchone()
    finally:
        conexao.close()

    if linha is None:
        return jsonify({"erro": "Pedido nao encontrado."}), 404
    pedido = _pedido_publico(linha)
    pedido["criado_em"] = linha["criado_em"]
    return jsonify(pedido)


@app.get("/api/events")
def acompanhar_eventos():
    cliente_id, erro_cliente = _obter_cliente_id(request.args.get("cliente_id"))
    if erro_cliente:
        return erro_cliente

    cursor = request.headers.get("Last-Event-ID")
    if cursor is None:
        cursor = request.args.get("after")
    if cursor is None:
        depois_do_id = _ultimo_evento_cliente(cliente_id)
    else:
        try:
            depois_do_id = int(cursor)
        except ValueError:
            return jsonify({"erro": "Last-Event-ID deve ser um inteiro."}), 400
        if depois_do_id < 0:
            return jsonify({"erro": "Last-Event-ID nao pode ser negativo."}), 400

    def fluxo():
        nonlocal depois_do_id
        ultimo_heartbeat = time.monotonic()
        yield "retry: 3000\n\n"
        while True:
            eventos = _buscar_eventos(cliente_id, depois_do_id)
            if eventos:
                for evento in eventos:
                    depois_do_id = evento["id"]
                    dados_evento = json.loads(evento["dados_json"])
                    yield (
                        f"id: {evento['id']}\n"
                        "event: order.status\n"
                        f"data: {json.dumps(dados_evento, ensure_ascii=False)}\n\n"
                    )
                continue

            agora = time.monotonic()
            if agora - ultimo_heartbeat >= 15:
                yield ": keep-alive\n\n"
                ultimo_heartbeat = agora
            time.sleep(1)

    return Response(
        stream_with_context(fluxo()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/interests")
def registrar_interesse():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return jsonify({"erro": "O corpo deve ser um objeto JSON."}), 400

    email = dados.get("email")
    categorias = dados.get("categorias")
    if not _validar_email(email):
        return jsonify({"erro": "Informe um e-mail valido."}), 400
    if (
        not isinstance(categorias, list)
        or not categorias
        or any(categoria not in ("A", "B", "C") for categoria in categorias)
    ):
        return jsonify(
            {"erro": "Selecione ao menos uma categoria valida: A, B ou C."}
        ), 400

    try:
        publicar_evento(
            "principal",
            "interesse.promocao",
            {
                "email": email.strip().lower(),
                "categorias": sorted(set(categorias)),
            },
        )
    except ErroPublicacaoEvento as erro:
        logger.error("Falha ao registrar interesse: %s", erro)
        return jsonify({"erro": "Nao foi possivel registrar o interesse."}), 503

    return jsonify({"registrado": True}), 202


@app.delete("/api/interests")
def cancelar_interesse():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict) or not _validar_email(dados.get("email")):
        return jsonify({"erro": "Informe um e-mail valido para cancelar."}), 400

    try:
        publicar_evento(
            "principal",
            "interesse.cancelado",
            {"email": dados["email"].strip().lower()},
        )
    except ErroPublicacaoEvento as erro:
        logger.error("Falha ao cancelar interesse: %s", erro)
        return jsonify({"erro": "Nao foi possivel cancelar o interesse."}), 503

    return jsonify({"cancelado": True}), 202


@app.errorhandler(ServicoHTTPError)
def erro_servico_http(erro):
    logger.error("Falha na chamada REST entre microsservicos: %s", erro)
    return jsonify({"erro": "Falha ao consultar um microsservico."}), 502


def iniciar():
    inicializar_banco()
    consumidor = threading.Thread(
        target=consumir_eventos,
        args=(
            "fila.principal",
            [
                "pedido.estoque_ok",
                "estoque.indisponivel",
                "pagamento.checkout_criado",
                "pagamento.aprovado",
                "pagamento.recusado",
                "pedido.enviado",
            ],
            _atualizar_pedido,
        ),
        daemon=True,
    )
    consumidor.start()
    app.run(host=HOST, port=PORTA, threaded=True, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    iniciar()
