import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from catalogo import PRODUTOS
from comum.config import EXCHANGE_PROMOCOES, PASTA_DADOS
from comum.http import ServicoHTTPError, requisitar_json
from comum.mensagens import (
    ErroPublicacaoEvento,
    consumir_eventos,
    publicar_evento,
)


logger = logging.getLogger(__name__)
DB_PATH = Path(
    os.getenv(
        "PROMOCOES_DATABASE_PATH",
        str(PASTA_DADOS / "promocoes.sqlite3"),
    )
)
INTERVALO_SEGUNDOS = int(os.getenv("PROMOCOES_INTERVALO", "30"))
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_API_URL = os.getenv("RESEND_API_URL", "https://api.resend.com/emails")
RESEND_FROM_EMAIL = os.getenv(
    "RESEND_FROM_EMAIL",
    "onboarding@resend.dev",
)
CATEGORIAS = {produto["categoria"] for produto in PRODUTOS.values()}
PADRAO_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
                CREATE TABLE IF NOT EXISTS interesses (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    categorias TEXT NOT NULL
                )
                """
            )
    finally:
        conexao.close()


def _validar_email(email):
    if not isinstance(email, str) or not PADRAO_EMAIL.fullmatch(email):
        raise ValueError("O evento contem um e-mail invalido.")
    return email.strip().lower()


def _validar_categorias(categorias):
    if not isinstance(categorias, list) or not categorias:
        raise ValueError("Informe ao menos uma categoria de interesse.")
    if any(not isinstance(categoria, str) for categoria in categorias):
        raise ValueError("As categorias devem ser textos.")
    normalizadas = sorted(set(categorias))
    if any(categoria not in CATEGORIAS for categoria in normalizadas):
        raise ValueError("O evento contem uma categoria inexistente.")
    return normalizadas


def atualizar_interesse(envelope):
    evento = envelope["Event"]
    dados = envelope["Data"]
    email = _validar_email(dados.get("email"))

    conexao = _conectar()
    try:
        with conexao:
            if evento == "interesse.promocao":
                categorias = _validar_categorias(dados.get("categorias"))
                conexao.execute(
                    """
                    INSERT INTO interesses (email, categorias)
                    VALUES (?, ?)
                    ON CONFLICT(email) DO UPDATE SET categorias = excluded.categorias
                    """,
                    (email, json.dumps(categorias, ensure_ascii=False)),
                )
                logger.info("Interesse promocional atualizado para uma conta de cliente.")
            elif evento == "interesse.cancelado":
                conexao.execute("DELETE FROM interesses WHERE email = ?", (email,))
                logger.info("Interesse promocional cancelado para uma conta de cliente.")
    finally:
        conexao.close()


def _destinatarios(categoria):
    conexao = _conectar()
    try:
        linhas = conexao.execute(
            "SELECT email, categorias FROM interesses ORDER BY email"
        ).fetchall()
    finally:
        conexao.close()

    return [
        linha["email"]
        for linha in linhas
        if categoria in json.loads(linha["categorias"])
    ]


def _enviar_email(email, promocao):
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY nao configurada; promocao %s nao enviada por e-mail.",
            promocao["promotion_id"],
        )
        return

    preco_com_desconto = promocao["preco_original"] * (
        1 - promocao["desconto"] / 100
    )
    requisitar_json(
        RESEND_API_URL,
        metodo="POST",
        dados={
            "from": RESEND_FROM_EMAIL,
            "to": [email],
            "subject": f"Oferta de {promocao['desconto']}% em {promocao['nome']}",
            "text": (
                f"{promocao['nome']} esta com {promocao['desconto']}% de desconto. "
                f"Preco promocional: R$ {preco_com_desconto:.2f}."
            ),
        },
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        timeout=10,
    )
    logger.info("Notificacao de promocao enviada via API externa.")


def gerar_promocao():
    codigo = random.choice(list(PRODUTOS))
    produto = PRODUTOS[codigo]
    desconto = random.choice([10, 15, 20, 30])
    dados = {
        "promotion_id": uuid.uuid4().hex,
        "produto_id": codigo,
        "nome": produto["nome"],
        "categoria": produto["categoria"],
        "desconto": desconto,
        "preco_original": produto["preco"],
    }
    evento = "promocao.categoria." + produto["categoria"]
    publicar_evento("promocoes", evento, dados, EXCHANGE_PROMOCOES)

    for email in _destinatarios(produto["categoria"]):
        try:
            _enviar_email(email, dados)
        except ServicoHTTPError:
            logger.exception("Falha ao enviar uma notificacao pelo provedor de e-mail.")

    print("Promocao publicada:", evento, dados)
    return dados


def iniciar():
    if INTERVALO_SEGUNDOS <= 0:
        raise ValueError("PROMOCOES_INTERVALO deve ser maior que zero.")
    inicializar_banco()
    consumidor = threading.Thread(
        target=consumir_eventos,
        args=(
            "fila.promocoes",
            ["interesse.promocao", "interesse.cancelado"],
            atualizar_interesse,
        ),
        daemon=True,
    )
    consumidor.start()

    while True:
        try:
            gerar_promocao()
        except ErroPublicacaoEvento:
            logger.exception("Falha ao publicar promocao no RabbitMQ.")
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    iniciar()
