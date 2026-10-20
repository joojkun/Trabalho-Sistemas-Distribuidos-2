# Scripts Python

Este guia analisa os scripts Python da raiz por responsabilidade e pelas principais etapas do codigo. O sistema combina REST, RabbitMQ, SQLite, assinatura digital e um checkout de pagamento simulado. A visao geral da arquitetura e dos endpoints esta no [README principal](README.md).

## Catalogo e consumidores de demonstracao

### `catalogo.py`

O dicionario `PRODUTOS` e a fonte de dados compartilhada do catalogo. Cada chave e o identificador do produto; seu valor contem nome, preco e categoria. O Estoque usa esses dados para inicializar os registros no banco, enquanto Promocoes os usa para sortear ofertas. `listar_produtos()` percorre o dicionario e imprime os campos de cada produto; nao inicia servidor nem acessa o banco.

### `consumidor_c1.py` e `consumidor_c2.py`

Os dois scripts demonstram roteamento por topico no exchange `Promoções` e reutilizam `consumir_eventos()` de `comum.mensagens`.

- C1 associa sua fila aos bindings `promocao.categoria.A` e `promocao.categoria.B`; por isso so recebe ofertas dessas categorias.
- C2 usa `promocao.categoria.*`, em que `*` corresponde a um unico segmento do routing key. Assim, recebe as ofertas A, B ou C publicadas nesse formato.
- `mostrar_promocao()` apenas imprime o nome do evento e os dados do envelope.

As filas sao independentes. E necessario iniciar o RabbitMQ e preparar as chaves antes de executar os consumidores. Eles sao exemplos de assinantes e nao alteram pedidos, estoque ou cadastro de clientes.

## Processamento de pedidos

### `principal.py` - API Gateway e estado do pedido

Este script concentra a API que o frontend chama e traduz eventos internos em estados visiveis ao cliente.

- **Configuracao e `MAPA_STATUS`:** define banco SQLite, URL do Estoque, host, porta, origens CORS permitidas e a traducao de eventos para status e mensagens apresentados pela API.
- **`_conectar()` e `inicializar_banco()`:** criam a conexao e as tabelas `pedidos` e `eventos_pedido`. A segunda tabela registra as transicoes que o SSE precisa entregar; a restricao unica por pedido e evento evita gravar a mesma transicao duas vezes.
- **Validacao de cliente:** `_validar_cliente_id()` normaliza UUIDs e rejeita valores invalidos; `_obter_cliente_id()` adapta esse resultado para uma resposta HTTP 400.
- **Catalogo remoto:** `_obter_produtos_disponiveis()` consulta `estoque.py` por REST e filtra produtos sem estoque. Falhas sao representadas por `ServicoHTTPError`.
- **Persistencia:** `_inserir_pedido()` grava o pedido inicial e o primeiro evento. `_pedido_publico()` reconstroi a resposta incluindo status e URL de checkout armazenados separadamente.
- **`_atualizar_pedido(envelope)`:** recebe eventos assinados do RabbitMQ, ignora tipos desconhecidos e pedidos que nao existem, traduz o evento usando `MAPA_STATUS` e atualiza o pedido em uma transacao. No checkout valida a URL antes de salvar; no envio copia dados de nota fiscal e data. Depois registra a transicao para SSE. Estoque indisponivel ou pagamento recusado tambem produzem `pedido.excluido`, para que o Estoque libere a reserva.
- **Leitura de eventos para SSE:** `_ultimo_evento_cliente()` e `_buscar_eventos()` consultam o banco por cliente e cursor. A rota `/api/events` usa `Last-Event-ID` (ou `after`) para continuar uma conexao interrompida, envia eventos `order.status` e periodicamente um heartbeat.
- **Rotas REST:** `/api/products` encaminha a consulta do catalogo; `POST /api/orders` valida cliente, itens e quantidades, calcula o total, persiste e publica `pedido.criado`; os endpoints `GET /api/orders` e `GET /api/orders/<id>` retornam apenas pedidos daquele UUID. As rotas de interesse validam e publicam cadastro ou cancelamento no RabbitMQ.
- **CORS e erros:** `adicionar_cors()` inclui cabecalhos somente para origens permitidas. O handler de `ServicoHTTPError` converte falhas entre servicos em HTTP 502.
- **`iniciar()`:** prepara o banco, inicia em thread um consumidor de eventos de estoque, pagamento e entrega e executa o servidor Flask.

O Gateway confirma o pedido ao frontend antes de todo o fluxo distribuido terminar. O status inicial e atualizado depois, de forma assincrona, por RabbitMQ e SSE.

### `estoque.py` - catalogo, reservas e devolucoes

O Estoque oferece uma API REST de leitura e processa comandos de reserva recebidos do broker.

- **Configuracao e banco:** `DB_PATH`, `ESTOQUE_INICIAL`, `HOST` e `PORTA` definem os padroes, substituiveis por variaveis de ambiente. `_conectar()` cria a pasta de dados e configura espera para locks SQLite. `inicializar_banco()` cria as tabelas de produtos e reservas e insere os produtos do catalogo sem sobrescrever dados existentes.
- **`_normalizar_itens(itens)`:** exige uma lista nao vazia, valida cada identificador e quantidade inteira positiva e soma linhas repetidas do mesmo produto. Isso simplifica a reserva em um item por produto.
- **`_processar_pedido_criado(pedido)`:** verifica o identificador, normaliza os itens e abre uma transacao de escrita (`BEGIN IMMEDIATE`). Confere todas as quantidades antes de descontar qualquer uma; se todas estiverem disponiveis, reduz o estoque e grava a reserva. Caso contrario, grava o resultado indisponivel sem descontar. Depois da transacao publica `pedido.estoque_ok` ou `estoque.indisponivel`. A tabela de reservas associa o resultado ao pedido e protege contra desconto duplicado.
- **`_liberar_reserva(pedido_id)`:** so devolve produtos se houver uma reserva com status `reservado`. Atualiza as quantidades e marca a reserva como `liberado` na mesma transacao; uma segunda solicitacao nao devolve novamente.
- **`atualizar_estoque(envelope)`:** direciona `pedido.criado` para a reserva e `pedido.excluido` para a devolucao.
- **`GET /api/products`:** le os produtos persistidos e os entrega como `products`, com a quantidade chamada `estoque` para o Gateway.
- **`iniciar()`:** inicializa o banco, executa o consumidor das filas em thread separada e inicia o Flask.

### `pagamento.py` - cobranca e webhook

Este servico integra eventos RabbitMQ com o Mock por HTTP. Mantem em SQLite a cobranca, o pedido original, a URL de checkout e o estado do pagamento.

- **`_publicar_checkout(pedido)`:** valida o pedido e o total. Se ja existir uma cobranca pendente, reutiliza seus dados; se nao, envia um `POST /api/charges` ao Mock com valor, pedido e URL/token do webhook. Persiste a resposta, acrescenta `checkout_url` e `cobranca_id` aos dados do pedido e publica `pagamento.checkout_criado`.
- **`processar_pagamento(envelope)`:** adapta o envelope recebido para chamar `_publicar_checkout()` com seus dados.
- **`POST /webhook` (`receber_webhook()`):** verifica o token `X-Payment-Token` quando configurado, valida o JSON e traduz `APROVADO`/`RECUSADO` para eventos internos. Procura a cobranca pelo pedido, confere o `charge_id` se informado e impede processar pagamentos ja finalizados ou em processamento. Marca o estado temporario antes de publicar o evento; se a publicacao falhar, retorna o estado para pendente. Se publicar com sucesso, grava o estado final e confirma a recepcao.
- **`iniciar()`:** cria as tabelas, inicia um consumidor de `pedido.estoque_ok` em thread e sobe o endpoint HTTP do webhook.

O webhook e a fronteira HTTP de retorno do Mock. A aprovacao/recusa depois vira um evento RabbitMQ para os demais servicos.

### `mock_pagamento.py` - provedor simulado

O Mock substitui um provedor externo de pagamentos; nao processa dinheiro real.

- **Configuracao e banco:** configura URL publica de checkout, URL de webhook permitida, host/porta e tabela de cobrancas. Cada cobranca tem identificador, pedido, valor, URL/token de retorno e estado.
- **`_url_webhook_valida(url)`:** compara esquema, host e caminho com o destino permitido. Essa restricao impede que uma requisicao de cobranca direcione o Mock para um endpoint arbitrario.
- **`_serializar_cobranca(cobranca)`:** prepara a resposta JSON e monta o link `/checkout/<charge_id>`.
- **`POST /api/charges`:** valida o corpo, o valor e o callback; cria uma cobranca pendente ou reaproveita a do mesmo pedido. Se uma cobranca existente tiver valor ou URL diferentes, responde conflito.
- **`GET /checkout/<charge_id>`:** consulta a cobranca e renderiza a pagina de aprovacao/recusa. O JavaScript embutido envia a escolha para `/checkout/<id>/result`.
- **`POST /checkout/<charge_id>/result`:** aceita somente `APROVADO` ou `RECUSADO`, bloqueia duplicidade/conflito e marca a cobranca como em processamento. Em seguida faz o POST de webhook com os dados da cobranca e token opcional. Se a chamada falhar, retorna para pendente e responde 502; se for aceita, grava o resultado final.
- **Inicializacao:** cria a tabela e inicia o Flask. A pagina de checkout e definida como template HTML dentro do proprio arquivo.

### `entrega.py` - conclusao apos pagamento

- `preparar_envio(envelope)` recebe os dados do pedido aprovado, gera uma nota fiscal simulada baseada no identificador, adiciona a data UTC em ISO 8601 e publica `pedido.enviado`.
- `iniciar()` consome apenas `pagamento.aprovado` na fila de entrega.

O servico nao recebe pagamentos nem altera estoque; sua funcao e representar a etapa posterior a aprovacao e produzir o evento final para o Gateway.

## Promocoes e preparacao

### `promocoes.py`

Mantem as inscricoes de e-mail e produz ofertas periodicamente.

- **Configuracao:** usa o catalogo compartilhado para descobrir categorias. O intervalo, URL/chave do Resend e remetente podem ser configurados por ambiente.
- **Banco e validacao:** `_conectar()` e `inicializar_banco()` mantem a tabela `interesses`; `_validar_email()` normaliza e verifica o formato; `_validar_categorias()` rejeita lista vazia, valores nao textuais ou categorias fora do catalogo.
- **`atualizar_interesse(envelope)`:** `interesse.promocao` cria ou atualiza as categorias por e-mail, e `interesse.cancelado` remove o registro. A chave textual usa comparacao sem diferenciar maiusculas/minusculas.
- **`_destinatarios(categoria)`:** le as inscricoes e seleciona apenas quem marcou a categoria da oferta.
- **`_enviar_email(email, promocao)`:** sem `RESEND_API_KEY`, registra que o envio foi ignorado. Com a chave, calcula o preco promocional e envia uma requisicao autenticada ao Resend usando `requisitar_json()`.
- **`gerar_promocao()`:** sorteia produto e percentual de desconto, cria `promotion_id`, publica `promocao.categoria.<categoria>` no exchange de topico e tenta enviar e-mail aos destinatarios. Falha em um e-mail e registrada sem impedir os outros.
- **`iniciar()`:** valida intervalo positivo, inicializa o banco, inicia o consumidor dos eventos de interesse e executa o ciclo de ofertas com pausa entre iteracoes.

### `gerar_chaves.py`

`gerar_todas()` percorre os nomes de servico de `comum.config.SERVICOS` e chama `garantir_chaves()` para cada um. A criacao e idempotente: pares existentes sao preservados. O script nao inicia os microsservicos; prepara os arquivos de chave que eles usarao para assinar e verificar eventos.

### `simulacao.py`

Este script permite entender e exercitar a logica do fluxo sem RabbitMQ ou servidores Flask.

- `publicar()` cria um envelope assinado e adiciona uma copia dos dados a uma lista local, que representa a fila.
- `executar(cenario)` cria pedido, estoque e reservas em memoria; remove mensagens da fila em ordem, valida cada assinatura e executa a transicao correspondente. Os cenarios forcam falta de estoque ou recusa do pagamento; no sucesso, o pagamento e aprovado e a entrega publica o pedido enviado.
- `main()` le `--cenario` (`sucesso`, `estoque`, `pagamento` ou `todos`) e executa a simulacao escolhida.

A simulacao nao persiste estado, nao envia webhooks e nao abre conexoes reais; ela e uma demonstracao deterministica da sequencia de eventos assinados.

## Ordem conceitual dos eventos

`pedido.criado` -> resultado da reserva -> criacao do checkout -> webhook de pagamento -> aprovacao/recusa -> envio ou liberacao da reserva. O Gateway traduz esses eventos em estados de pedido e os transmite ao navegador por SSE. As funcoes de configuracao, HTTP, mensagens e assinatura estao descritas em [`comum/README.md`](comum/README.md); o cliente web esta em [`frontend/README.md`](frontend/README.md).