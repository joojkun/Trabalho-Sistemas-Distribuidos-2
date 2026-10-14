from comum.config import EXCHANGE_PROMOCOES
from comum.mensagens import consumir_eventos


def mostrar_promocao(envelope):
    print("C2 recebeu:", envelope["Event"], envelope["Data"])


if __name__ == "__main__":
    # O asterisco permite receber qualquer categoria.
    consumir_eventos(
        "fila.C2",
        ["promocao.categoria.*"],
        mostrar_promocao,
        EXCHANGE_PROMOCOES,
    )
