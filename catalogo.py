# Catalogo usado pelo Principal e pelo servico de promocoes.
PRODUTOS = {
    "1": {"nome": "Teclado", "preco": 120.0, "categoria": "A"},
    "2": {"nome": "Mouse", "preco": 80.0, "categoria": "A"},
    "3": {"nome": "Monitor", "preco": 900.0, "categoria": "B"},
    "4": {"nome": "Fone", "preco": 150.0, "categoria": "C"},
}


def listar_produtos():
    print("\nProdutos:")
    for codigo, produto in PRODUTOS.items():
        print(
            f"{codigo} - {produto['nome']} - "
            f"R$ {produto['preco']:.2f} - categoria {produto['categoria']}"
        )
