import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from flask import Flask, jsonify

from catalogo import PRODUTOS
from comum.config import PASTA_DADOS
from comum.mensagens import consumir_eventos, publicar_evento


logger = logging.getLogger(__name__)
DB_PATH = Path(
    os.getenv(
        "ESTOQUE_DATABASE_PATH",
        str(PASTA_DADOS / "estoque.sqlite3"),
    )
)
ESTOQUE_INICIAL = {"1": 10, "2": 8, "3": 3, "4": 5}
HOST = os.getenv("ESTOQUE_HOST", "127.0.0.1")
PORTA = int(os.getenv("ESTOQUE_PORT", "5001"))

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
                CREATE TABLE IF NOT EXISTS produtos (
                    produto_id TEXT PRIMARY KEY,
                    nome TEXT NOT NULL,
                    preco REAL NOT NULL CHECK (preco >= 0),
                    categoria TEXT NOT NULL,
                    quantidade INTEGER NOT NULL CHECK (quantidade >= 0)
                );
                CREATE TABLE IF NOT EXISTS reservas (
                    pedido_id TEXT PRIMARY KEY,
                    itens TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('reservado', 'indisponivel', 'liberado'))
                );
                """
            )
            for produto_id, produto in PRODUTOS.items():
                conexao.execute(
                    """
                    INSERT OR IGNORE INTO produtos
                        (produto_id, nome, preco, categoria, quantidade)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        produto_id,
                        produto["nome"],
                        produto["preco"],
                        produto["categoria"],
                        ESTOQUE_INICIAL[produto_id],
                    ),
                )
    finally:
        conexao.close()


def _normalizar_itens(itens):
    if not isinstance(itens, list) or not itens:
        raise ValueError("O pedido deve conter ao menos um produto.")

    quantidades = {}
    for item in itens:
        if not isinstance(item, dict):
            raise ValueError("Cada item do pedido deve ser um objeto.")
        produto_id = item.get("produto_id")
        quantidade = item.get("quantidade")
        if not isinstance(produto_id, str) or produto_id not in PRODUTOS:
            raise ValueError("O pedido contem um produto inexistente.")
        if (
            isinstance(quantidade, bool)
            or not isinstance(quantidade, int)
            or quantidade <= 0
        ):
            raise ValueError("A quantidade deve ser um inteiro positivo.")
        quantidades[produto_id] = quantidades.get(produto_id, 0) + quantidade

    return [
        {"produto_id": produto_id, "quantidade": quantidade}
        for produto_id, quantidade in quantidades.items()
    ]


def _processar_pedido_criado(pedido):
    pedido_id = pedido.get("pedido_id")
    if not isinstance(pedido_id, str) or not pedido_id:
        raise ValueError("O evento nao contem um pedido_id valido.")
    itens = _normalizar_itens(pedido.get("produtos"))

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute("BEGIN IMMEDIATE")
            reserva = conexao.execute(
                "SELECT status FROM reservas WHERE pedido_id = ?",
                (pedido_id,),
            ).fetchone()
            if reserva is not None:
                status = reserva["status"]
                processar = status in ("reservado", "indisponivel")
            else:
                disponivel = True
                for item in itens:
                    linha = conexao.execute(
                        "SELECT quantidade FROM produtos WHERE produto_id = ?",
                        (item["produto_id"],),
                    ).fetchone()
                    if linha is None or linha["quantidade"] < item["quantidade"]:
                        disponivel = False
                        break

                status = "reservado" if disponivel else "indisponivel"
                if disponivel:
                    for item in itens:
                        conexao.execute(
                            """
                            UPDATE produtos
                            SET quantidade = quantidade - ?
                            WHERE produto_id = ?
                            """,
                            (item["quantidade"], item["produto_id"]),
                        )
                conexao.execute(
                    """
                    INSERT INTO reservas (pedido_id, itens, status)
                    VALUES (?, ?, ?)
                    """,
                    (
                        pedido_id,
                        json.dumps(itens, ensure_ascii=False),
                        status,
                    ),
                )
                processar = True
    finally:
        conexao.close()

    if not processar:
        logger.info("Pedido %s ja foi liberado; evento duplicado ignorado.", pedido_id)
        return

    if status == "reservado":
        publicar_evento("estoque", "pedido.estoque_ok", pedido)
        print("Estoque reservado para", pedido_id)
    else:
        publicar_evento("estoque", "estoque.indisponivel", pedido)
        print("Estoque insuficiente para", pedido_id)


def _liberar_reserva(pedido_id):
    if not isinstance(pedido_id, str) or not pedido_id:
        raise ValueError("O evento nao contem um pedido_id valido.")

    conexao = _conectar()
    try:
        with conexao:
            conexao.execute("BEGIN IMMEDIATE")
            reserva = conexao.execute(
                "SELECT itens, status FROM reservas WHERE pedido_id = ?",
                (pedido_id,),
            ).fetchone()
            if reserva is None or reserva["status"] != "reservado":
                return False

            itens = json.loads(reserva["itens"])
            for item in itens:
                conexao.execute(
                    """
                    UPDATE produtos
                    SET quantidade = quantidade + ?
                    WHERE produto_id = ?
                    """,
                    (item["quantidade"], item["produto_id"]),
                )
            conexao.execute(
                "UPDATE reservas SET status = 'liberado' WHERE pedido_id = ?",
                (pedido_id,),
            )
            return True
    finally:
        conexao.close()


def atualizar_estoque(envelope):
    evento = envelope["Event"]
    pedido = envelope["Data"]

    if evento == "pedido.criado":
        _processar_pedido_criado(pedido)
    elif evento == "pedido.excluido":
        pedido_id = pedido.get("pedido_id")
        if _liberar_reserva(pedido_id):
            print("Estoque devolvido para", pedido_id)


@app.get("/health")
def health():
    return jsonify({"servico": "estoque", "status": "ok"})


@app.get("/api/products")
def listar_produtos_disponiveis():
    conexao = _conectar()
    try:
        produtos = conexao.execute(
            """
            SELECT produto_id, nome, preco, categoria, quantidade
            FROM produtos
            ORDER BY produto_id
            """
        ).fetchall()
    finally:
        conexao.close()

    return jsonify(
        {
            "products": [
                {
                    "produto_id": produto["produto_id"],
                    "nome": produto["nome"],
                    "preco": produto["preco"],
                    "categoria": produto["categoria"],
                    "estoque": produto["quantidade"],
                }
                for produto in produtos
            ]
        }
    )


def iniciar():
    inicializar_banco()
    consumidor = threading.Thread(
        target=consumir_eventos,
        args=("fila.estoque", ["pedido.criado", "pedido.excluido"], atualizar_estoque),
        daemon=True,
    )
    consumidor.start()
    app.run(host=HOST, port=PORTA, threaded=True, use_reloader=False)


if __name__ == "__main__":
    iniciar()
