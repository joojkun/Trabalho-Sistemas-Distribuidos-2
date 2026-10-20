# JavaScript do frontend

Este guia analisa somente os arquivos JavaScript: `server.js`, executado em Node.js, e `app.js`, executado no navegador. O HTML fornece os elementos e IDs que `app.js` procura; estilos e estrutura visual nao sao explicados aqui.

## `server.js` - servidor HTTP da interface

### Imports e configuracao

O codigo usa apenas modulos nativos do Node.js: `node:http` cria o servidor, `node:fs` le os arquivos e `node:path` monta seus caminhos. `ROOT` e a pasta `frontend/`; `PORT` vem de `FRONTEND_PORT` (padrao 8080); `API_BASE_URL` vem do ambiente (padrao `http://localhost:5000`).

O mapa `FILES` e uma lista permitida de rotas para os arquivos servidos: `/` e `/index.html`, `/app.js` e `/styles.css`. Cada entrada associa nome de arquivo e MIME type. Como nao se usa diretamente o caminho requisitado para procurar arquivos, rotas arbitrarias nao expoem arquivos fora da lista.

### Tratamento da requisicao

O servidor recusa metodos diferentes de `GET` e `HEAD` com status 405. Em seguida normaliza o caminho da URL:

- `/config.js` e gerado dinamicamente. O valor de `API_BASE_URL` e serializado como literal JavaScript e atribuido a `window.API_BASE_URL`. `Cache-Control: no-store` evita que uma configuracao antiga seja reutilizada. No HTML, esse script e carregado antes de `app.js`.
- Outros caminhos sao procurados no mapa `FILES`. Um caminho desconhecido recebe 404.
- Para um caminho conhecido, `readFile()` carrega o arquivo, responde com o MIME type indicado e bloqueia inferencia de tipo com `X-Content-Type-Options: nosniff`. Erro de leitura recebe 500. Em `HEAD`, envia cabecalhos sem o corpo.

O listener final vincula o servidor a `127.0.0.1` e imprime a URL local. Esse processo serve a interface; as operacoes de negocio continuam na API Python.

## `app.js` - aplicacao no navegador

### Configuracao, elementos e estado em memoria

`API_BASE_URL` le o valor fornecido por `/config.js`, remove barras finais e usa o mesmo padrao local caso o script de configuracao nao tenha sido carregado. As constantes seguintes guardam seletores para mensagens, listas e formularios do HTML.

Os mapas mantem estado durante a pagina aberta:

- `orders`: pedidos indexados por `pedido_id`;
- `checkoutWindows`: pop-ups abertos enquanto o pedido aguarda a URL do checkout;
- `checkoutUrls`: URL que chegou por SSE, util se o evento chegar antes da resposta REST;
- `orderNotices`: ultima mensagem de estado de cada pedido.

### Identidade do cliente

`getClientId()` busca um UUID em `localStorage`; se nao houver, cria um com `crypto.randomUUID()` e salva. O ID e criado uma vez no carregamento e enviado ao Gateway em consultas e na conexao SSE. Ele permite recuperar os pedidos associados a esta sessao do navegador.

### Chamadas e feedback

`requestJSON(path, options)` combina a URL base com o caminho e chama `fetch()`. Acrescenta `Accept: application/json`, inclui `Content-Type` quando ha corpo e preserva cabecalhos adicionais. Trata separadamente falha de rede, JSON de resposta invalido e resposta HTTP nao bem-sucedida; neste ultimo caso prefere a mensagem `erro` retornada pela API. Se estiver tudo correto, retorna o JSON.

`setMessage(element, message, isError)` atualiza o texto com `textContent` e alterna a classe CSS `error`. `textContent` insere o texto como conteudo, nao como HTML.

### Catalogo: `loadProducts()` e `renderProducts()`

`loadProducts()` apresenta estado de carregamento, busca `/api/products`, verifica se `products` e uma lista e chama a funcao de renderizacao. Erros da API ficam visiveis junto ao catalogo.

`renderProducts()` limpa o conteudo anterior e trata a lista vazia. Para cada produto cria elementos DOM, mostra preco/categoria/quantidade disponivel e gera um campo numerico. O campo guarda `produto_id` em `dataset`, limita o maximo ao estoque e recebe um rotulo acessivel. O uso de `textContent` evita interpretar nomes ou dados retornados como markup.

### Lista de pedidos: `statusClass()`, `renderOrders()` e `loadOrders()`

`statusClass(status)` associa estados finais de sucesso e cancelamento a classes CSS; outros estados ficam sem classe especial.

`renderOrders()` recria a lista a partir dos valores do mapa `orders`, ordena os pedidos por identificador e monta cada bloco com status, itens e total. Acrescenta mensagem recente, nota fiscal e link de checkout quando existirem. O link abre em outra aba e usa `rel="noopener noreferrer"` para isolar a janela aberta.

`loadOrders()` consulta `/api/orders` com o `cliente_id` codificado para URL, atualiza o mapa e redesenha a lista. Se falhar, exibe o erro na area de pedidos.

### Eventos SSE: `connectToEvents()` e `handleOrderEvent()`

`connectToEvents()` cria um `EventSource` para `/api/events?cliente_id=...`. `onopen` atualiza o indicador de conexao; `onerror` informa que a conexao esta reconectando. EventSource tenta reconectar automaticamente. O listener do evento `order.status` chama `handleOrderEvent()`.

O handler interpreta `event.data`, encontra ou cria o pedido e mescla a atualizacao. Guarda tambem a mensagem mais recente. Se o evento incluir `checkout_url`, salva-a e redireciona a janela provisoria aberta para aquele pedido, caso ainda esteja aberta. Se o estado indicar cancelamento, fecha a janela pendente. Por fim, redesenha os pedidos e mostra a mensagem recebida.

### Criacao do pedido

O listener de `submit` no formulario impede o envio HTML padrao e coleta os campos com `data-product-id`. Converte as quantidades para numero, remove zeros, exige ao menos um item e rejeita valores que nao sejam inteiros seguros.

Antes de iniciar uma chamada assincrona, tenta abrir `about:blank`; abrir a janela durante a acao direta do usuario reduz o bloqueio de pop-ups. Depois desabilita o botao e envia `POST /api/orders` com o UUID e os produtos. Na resposta:

1. Guarda o pedido no mapa e associa a janela provisoria ao pedido.
2. Escolhe uma URL de checkout que pode ter chegado antes por SSE (`checkoutUrls`) ou na resposta REST.
3. Redireciona a janela se a URL ja estiver disponivel.
4. Redesenha os pedidos, informa que aguarda o estoque, limpa o formulario e recarrega o catalogo.

Se a chamada falhar, fecha a janela aberta e mostra o erro. O bloco `finally` sempre reabilita o botao. O botao de atualizar catalogo simplesmente registra `loadProducts()` como callback.

### Cadastro e cancelamento de interesses

O `submit` do formulario de interesse le o e-mail sem espacos externos e recolhe as categorias marcadas. Sem categoria, nao envia a chamada. Caso contrario faz `POST /api/interests` e mostra confirmacao ou erro.

O botao de cancelamento le o e-mail do campo, exige que nao esteja vazio e faz `DELETE /api/interests` com esse endereco. A resposta tambem e apresentada na area de feedback.

### Inicializacao e fluxo de ponta a ponta

No final, `connectToEvents()`, `loadProducts()` e `loadOrders()` iniciam em paralelo: abre-se o canal de atualizacoes, carrega-se o catalogo e recuperam-se os pedidos existentes. Depois, os novos estados chegam por SSE. O browser fala REST/SSE com `principal.py`; nao acessa RabbitMQ ou o webhook diretamente. Os scripts Python e seus eventos estao descritos em [`../README_SCRIPTS.md`](../README_SCRIPTS.md).