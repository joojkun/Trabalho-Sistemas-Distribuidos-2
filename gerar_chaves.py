from comum.config import SERVICOS
from comum.seguranca import garantir_chaves


def gerar_todas():
    # Cada microsservico assina seus proprios eventos.
    for servico in SERVICOS:
        garantir_chaves(servico)
        print("Chaves prontas:", servico)


if __name__ == "__main__":
    gerar_todas()
