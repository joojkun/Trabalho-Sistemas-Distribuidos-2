import os
from pathlib import Path


PASTA_PROJETO = Path(__file__).resolve().parent.parent
PASTA_CHAVES = PASTA_PROJETO / "chaves"
# Pode ser trocada por uma URL de outro servidor RabbitMQ.
URL_RABBITMQ = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")

EXCHANGE_ECOMMERCE = "eCommerce"
EXCHANGE_PROMOCOES = "Promoções"

SERVICOS = ("principal", "estoque", "pagamento", "entrega", "promocoes")

PASTA_DADOS = PASTA_PROJETO / "data"
