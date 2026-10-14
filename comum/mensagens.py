import binascii
import json
import logging
import sqlite3

import pika

from .config import (
    EXCHANGE_ECOMMERCE,
    EXCHANGE_PROMOCOES,
    URL_RABBITMQ,
)
from .http import ServicoHTTPError
from .seguranca import criar_envelope, validar_envelope


logger = logging.getLogger(__name__)


class ErroPublicacaoEvento(RuntimeError):
    pass


def abrir_conexao():
    return pika.BlockingConnection(pika.URLParameters(URL_RABBITMQ))


def preparar_exchanges(canal):
    # Os exchanges sao declarados por todos os processos para evitar ordem fixa.
    canal.exchange_declare(
        exchange=EXCHANGE_ECOMMERCE,
        exchange_type="direct",
        durable=True,
    )
    canal.exchange_declare(
        exchange=EXCHANGE_PROMOCOES,
        exchange_type="topic",
        durable=True,
    )


def publicar_evento(produtor, evento, dados, exchange=EXCHANGE_ECOMMERCE):
    conexao = None
    try:
        conexao = abrir_conexao()
        canal = conexao.channel()
        preparar_exchanges(canal)

        envelope = criar_envelope(produtor, evento, dados)
        canal.basic_publish(
            exchange=exchange,
            routing_key=evento,
            body=json.dumps(envelope, ensure_ascii=False).encode(),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
            ),
        )
    except (pika.exceptions.AMQPError, OSError) as erro:
        raise ErroPublicacaoEvento(
            f"Nao foi possivel publicar {evento} no exchange {exchange}."
        ) from erro
    finally:
        if conexao is not None and conexao.is_open:
            conexao.close()


def consumir_eventos(fila, bindings, funcao, exchange=EXCHANGE_ECOMMERCE):
    conexao = abrir_conexao()
    canal = conexao.channel()
    preparar_exchanges(canal)
    canal.queue_declare(queue=fila, durable=True)

    for binding in bindings:
        # Cada fila recebe apenas os eventos dos seus bindings.
        canal.queue_bind(
            exchange=exchange,
            queue=fila,
            routing_key=binding,
        )

    def ler_mensagem(canal_receber, metodo, propriedades, corpo):
        try:
            envelope = json.loads(corpo.decode())
            valido = validar_envelope(envelope)
        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            FileNotFoundError,
            binascii.Error,
        ):
            valido = False

        if not valido:
            print("[seguranca] evento descartado na fila", fila)
            canal_receber.basic_ack(delivery_tag=metodo.delivery_tag)
            return

        try:
            funcao(envelope)
        except (
            ErroPublicacaoEvento,
            ServicoHTTPError,
            sqlite3.Error,
            pika.exceptions.AMQPError,
            OSError,
            TimeoutError,
        ):
            logger.exception("Falha transitoria ao processar evento na fila %s", fila)
            canal_receber.basic_nack(
                delivery_tag=metodo.delivery_tag,
                requeue=True,
            )
            return
        except (KeyError, TypeError, ValueError):
            logger.exception("Evento invalido para a fila %s", fila)
            canal_receber.basic_ack(delivery_tag=metodo.delivery_tag)
            return

        canal_receber.basic_ack(delivery_tag=metodo.delivery_tag)

    canal.basic_qos(prefetch_count=1)
    canal.basic_consume(queue=fila, on_message_callback=ler_mensagem)
    print("Consumidor ativo:", fila)
    canal.start_consuming()
