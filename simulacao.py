import argparse
import copy

from comum.seguranca import criar_envelope, validar_envelope


def publicar(fila, produtor, evento, dados):
    # A fila local imita a passagem de envelopes pelo RabbitMQ.
    fila.append(criar_envelope(produtor, evento, copy.deepcopy(dados)))


def executar(cenario):
    fila = []
    estoque = {"1": 1}
    reservas = {}
    pedidos = {}
    pedido = {
        "pedido_id": "pedido-teste",
        "produtos": [{"produto_id": "1", "quantidade": 1}],
        "status": "criado",
    }
    pedidos[pedido["pedido_id"]] = pedido
    publicar(fila, "principal", "pedido.criado", pedido)

    while fila:
        envelope = fila.pop(0)
        # A simulacao tambem verifica as assinaturas dos eventos.
        if not validar_envelope(envelope):
            print("Evento invalido descartado")
            continue

        evento = envelope["Event"]
        dados = envelope["Data"]
        pedido_id = dados["pedido_id"]

        if evento == "pedido.criado":
            disponivel = estoque.get("1", 0) >= dados["produtos"][0]["quantidade"]
            if cenario == "estoque":
                disponivel = False
            if disponivel:
                estoque["1"] -= 1
                reservas[pedido_id] = dados["produtos"]
                publicar(fila, "estoque", "pedido.estoque_ok", dados)
            else:
                publicar(fila, "estoque", "estoque.indisponivel", dados)
        elif evento == "estoque.indisponivel":
            pedidos[pedido_id]["status"] = "cancelado_estoque"
            publicar(fila, "principal", "pedido.excluido", dados)
        elif evento == "pedido.estoque_ok":
            if cenario == "pagamento":
                publicar(fila, "pagamento", "pagamento.recusado", dados)
            else:
                publicar(fila, "pagamento", "pagamento.aprovado", dados)
        elif evento == "pagamento.recusado":
            pedidos[pedido_id]["status"] = "cancelado_pagamento"
            publicar(fila, "principal", "pedido.excluido", dados)
        elif evento == "pagamento.aprovado":
            dados_envio = dict(dados)
            dados_envio["nota_fiscal"] = "NF-PEDIDO-TESTE"
            publicar(fila, "entrega", "pedido.enviado", dados_envio)
        elif evento == "pedido.enviado":
            pedidos[pedido_id]["status"] = "enviado"
        elif evento == "pedido.excluido":
            for item in reservas.pop(pedido_id, []):
                estoque[item["produto_id"]] += item["quantidade"]

    print(cenario, "->", pedidos["pedido-teste"]["status"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cenario",
        choices=["sucesso", "estoque", "pagamento", "todos"],
        default="todos",
    )
    args = parser.parse_args()
    cenarios = ["sucesso", "estoque", "pagamento"] if args.cenario == "todos" else [args.cenario]
    for cenario in cenarios:
        executar(cenario)


if __name__ == "__main__":
    main()
