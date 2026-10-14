# E-commerce orientado a eventos

Evolucao do Trabalho 1 de Sistemas Distribuidos para uma aplicacao web baseada
em microsservicos. O backend usa Python/Flask; a interface e servida por um
servidor Node.js e executa JavaScript no navegador. O sistema combina REST,
RabbitMQ, SSE, SQLite, assinatura RSA, um checkout de pagamento simulado e
integracao opcional com a API de e-mail Resend.

Consulte a [checklist da especificacao](CHECKLIST.md) para conferir cada item
do trabalho.

## Arquitetura

```text
Navegador (JavaScript) --REST/SSE--> Principal / API Gateway (Python, :5000)
                                             | REST
                                             v
                                       Estoque (Python, :5001)

Principal --eventos assinados--> RabbitMQ <--eventos assinados-- Estoque
                                      |              |
                                      v              v
                                 Pagamento       Promocoes
                               (Python, :5003)    |     \
                                      |            |      \ REST
                               REST / webhook     |       v
                                      v            |   API de e-mail
                              Mock de Pagamento   |
                               (Python, :5004)    |
                                      |            |
                                      +-----> Entrega
                                             (Python)
```

O API Gateway consulta o catalogo diretamente no endpoint REST do Estoque.
Pedidos, resultados de estoque, pagamentos, entrega e inscricoes de promocao
trafegam pelo RabbitMQ. As mensagens RabbitMQ mantem o envelope assinado por
RSA/SHA-256 existente no Trabalho 1.

| Processo | Responsabilidade |
| --- | --- |
| `principal.py` | API REST, estado dos pedidos, tradutor de eventos e SSE |
| `estoque.py` | REST de produtos e reservas persistidas em SQLite |
| `pagamento.py` | Cria cobrancas no mock e recebe o webhook |
| `mock_pagamento.py` | Checkout web com botoes de aprovacao/recusa |
| `entrega.py` | Emite nota fiscal simulada apos pagamento aprovado |
| `promocoes.py` | Mantem interesses e envia ofertas por categoria |
| `frontend/` | Interface JavaScript servida por Node.js |

O exchange `eCommerce` (direct) carrega eventos de pedidos, estoque,
pagamentos e interesses. O exchange `Promoções` (topic) distribui ofertas por
categoria. As filas de cada servico sao independentes.

## Requisitos

- Python 3.11 ou superior;
- Node.js 18 ou superior;
- Docker Desktop com Docker Compose para iniciar o RabbitMQ;
- uma chave da API Resend e um remetente verificado somente para enviar
  mensagens reais.

## Instalar e executar no Windows

Na pasta do projeto:

```powershell
python -m pip install -r requirements.txt
docker compose up -d
python gerar_chaves.py
```

Abra um terminal PowerShell para cada processo abaixo e execute-os a partir
desta pasta, nesta ordem recomendada:

```powershell
python estoque.py
```

```powershell
python mock_pagamento.py
```

```powershell
python pagamento.py
```

```powershell
python entrega.py
```

```powershell
python promocoes.py
```

```powershell
python principal.py
```

```powershell
node frontend\server.js
```

Abra `http://localhost:8080`. O API Gateway fica em `http://localhost:5000`,
o checkout simulado em `http://localhost:5004` e o RabbitMQ Management em
`http://localhost:15672` (usuario e senha locais `guest`).

Se o Gateway usar outra porta ou endereco, informe a URL da API ao iniciar o
frontend:

```powershell
$env:API_BASE_URL = "http://localhost:5000"
node frontend\server.js
```

Os servicos Python usam por padrao RabbitMQ em `localhost:5672`, estoque em
`127.0.0.1:5001`, pagamento em `127.0.0.1:5003` e mock em
`127.0.0.1:5004`. Para trocar o endereco do broker:

```powershell
$env:RABBITMQ_URL = "amqp://guest:guest@localhost:5672/%2F"
```

O valor de `PAGAMENTO_WEBHOOK_URL` precisa ser o mesmo nos processos de
Pagamento e Mock de Pagamento. Para executar em outro endereco, configure nos
dois terminais:

```powershell
$env:PAGAMENTO_WEBHOOK_URL = "http://127.0.0.1:5003/webhook"
```

O token compartilhado do webhook e opcional no modo local. Se definido, use o
mesmo valor nos dois terminais:

```powershell
$env:PAYMENT_WEBHOOK_TOKEN = "token-local-de-desenvolvimento"
```

Para enviar e-mails reais, configure a chave e o remetente do Resend no
terminal que executa `promocoes.py`:

```powershell
$env:RESEND_API_KEY = "re_sua_chave"
$env:RESEND_FROM_EMAIL = "Loja <ofertas@seu-dominio-verificado.com>"
```

Sem `RESEND_API_KEY`, as ofertas continuam sendo publicadas no RabbitMQ, mas
os envios por e-mail sao explicitamente registrados como nao configurados.
Nao coloque chaves reais no codigo nem no controle de versao.

## Endpoints

Todos os endpoints de negocio abaixo pertencem ao API Gateway:

| Metodo e caminho | Funcao |
| --- | --- |
| `GET /api/products` | Consulta o Estoque via REST e lista produtos disponiveis |
| `POST /api/orders` | Valida o carrinho e publica `pedido.criado` |
| `GET /api/orders?cliente_id=<UUID>` | Lista pedidos do cliente |
| `GET /api/orders/<id>?cliente_id=<UUID>` | Consulta um pedido do cliente |
| `GET /api/events?cliente_id=<UUID>` | Abre o stream SSE de atualizacoes do cliente |
| `POST /api/interests` | Publica `interesse.promocao` com e-mail e categorias |
| `DELETE /api/interests` | Publica `interesse.cancelado` para o e-mail |
| `GET /health` | Verifica se o Gateway esta ativo |

Exemplo de pedido:

```json
{
  "cliente_id": "UUID gerado pelo navegador",
  "produtos": [
    {"produto_id": "1", "quantidade": 2}
  ]
}
```

Exemplo de inscricao:

```json
{
  "email": "cliente@exemplo.com",
  "categorias": ["A", "C"]
}
```

O SSE transmite cada transicao do pedido: estoque confirmado, checkout
disponivel, pagamento aprovado/recusado e pedido enviado. O navegador mantem
uma conexao `EventSource` por cliente e o servidor filtra os eventos pelo UUID
da sessao.

## Fluxo de pedido e pagamento

1. O navegador consulta produtos e envia o pedido por REST ao API Gateway.
2. O Gateway publica `pedido.criado`; o Estoque reserva unidades em SQLite e
   publica `pedido.estoque_ok` ou `estoque.indisponivel`.
3. Com estoque confirmado, Pagamento solicita uma cobranca via REST ao Mock.
   O Gateway recebe `pagamento.checkout_criado` e envia a URL ao navegador
   por SSE. A interface abre o checkout em uma nova aba e tambem mostra um
   link alternativo.
4. No checkout, escolha **Pagamento aprovado** ou **Pagamento recusado**. O
   Mock envia um HTTP POST para o webhook do Pagamento.
5. Pagamento publica `pagamento.aprovado` ou `pagamento.recusado`. Em caso de
   recusa, o Gateway publica `pedido.excluido` para devolver a reserva.
6. Com pagamento aprovado, Entrega emite a nota fiscal simulada e publica
   `pedido.enviado`. O Gateway registra todas as mudancas e as transmite por
   SSE.

As URLs do checkout e do webhook sao configuraveis por variaveis de ambiente.
O Mock aceita somente a URL de webhook permitida configurada para evitar que
uma solicitacao de cobranca o transforme em um cliente HTTP arbitrario.

## Interesses e promocoes

O Gateway publica cadastro e cancelamento no RabbitMQ. Promocoes persiste os
interesses por e-mail/categoria em SQLite e gera ofertas periodicamente. Uma
oferta e publicada em `promocao.categoria.A`, `.B` ou `.C` e enviada aos
clientes interessados por meio da API REST do Resend.

Variaveis opcionais:

| Variavel | Padrao | Uso |
| --- | --- | --- |
| `RESEND_API_KEY` | vazio | Autoriza o envio real de e-mails |
| `RESEND_FROM_EMAIL` | `onboarding@resend.dev` | Remetente usado pela API |
| `RESEND_API_URL` | `https://api.resend.com/emails` | URL da API de e-mail |
| `PROMOCOES_INTERVALO` | `30` | Intervalo entre ofertas, em segundos |

## Persistencia e seguranca

- O catalogo e as reservas do Estoque sao persistidos em `data/estoque.sqlite3`.
- Pedidos e eventos SSE ficam em `data/gateway.sqlite3`.
- Cobrancas e interesses de promocao tambem usam SQLite em `data/`.
- A entrega, pagamento e demais eventos RabbitMQ usam as chaves RSA dos
  produtores para assinatura e validacao.
- Uma mensagem com assinatura invalida e descartada antes da logica de
  negocio. Falhas temporarias de HTTP, banco ou RabbitMQ sao registradas e a
  mensagem pode ser reprocessada.
- O webhook pode ser protegido com `PAYMENT_WEBHOOK_TOKEN`; configure o mesmo
  token no Pagamento e no Mock.

## Testes

Os testes de servicos nao precisam de RabbitMQ nem de uma chave Resend:

```powershell
python -m unittest discover -s tests -v
```

A simulacao original do Trabalho 1 continua disponivel:

```powershell
python simulacao.py --cenario todos
```

Thunder Client ou Postman tambem podem testar os endpoints REST. A interface
Swagger UI nao e requisito da especificacao; os endpoints estao listados nesta
pagina.

### Estoque, pagamento e checkout

| Processo | Metodo e caminho | Funcao |
| --- | --- | --- |
| Estoque | `GET /api/products` | Retorna catalogo, preco, categoria e quantidade |
| Mock de Pagamento | `POST /api/charges` | Cria ou recupera a cobranca idempotente |
| Mock de Pagamento | `GET /checkout/<charge_id>` | Exibe os botoes de aprovacao/recusa |
| Mock de Pagamento | `POST /checkout/<charge_id>/result` | Envia o resultado ao webhook configurado |
| Pagamento | `POST /webhook` | Valida o resultado e publica o evento RabbitMQ |

O Gateway recebe os eventos `pedido.estoque_ok`,
`estoque.indisponivel`, `pagamento.checkout_criado`,
`pagamento.aprovado`, `pagamento.recusado` e `pedido.enviado`. Em caso de
estoque indisponivel ou pagamento recusado, publica `pedido.excluido` para
liberar uma reserva existente.
