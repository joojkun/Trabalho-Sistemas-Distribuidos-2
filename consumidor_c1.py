from comum.mensagens import consumir_eventos
from comum.config import EXCHANGE_PROMOCOES


def mostrar_promocao(envelope):
    print("C1 recebeu:", envelope["Event"], envelope["Data"])


if __name__ == "__main__":
    # C1 acompanha somente as categorias A e B.
    consumir_eventos(
        "fila.C1",
        ["promocao.categoria.A", "promocao.categoria.B"],
        mostrar_promocao,
        EXCHANGE_PROMOCOES,
    )
