# Modulos compartilhados

`comum/` reune as configuracoes e operacoes transversais usadas pelos scripts Python. Os arquivos nao iniciam processos autonomos: sao importados pelos servicos. O fluxo geral e: o produtor prepara e assina um envelope (`seguranca.py`), `mensagens.py` o publica no RabbitMQ, e o consumidor verifica a assinatura antes de executar a logica de negocio.

## `__init__.py`

Marca `comum` como pacote Python. Isso habilita imports como `from comum.http import requisitar_json`. O arquivo nao contem logica de negocio.

## `config.py` - caminhos e parametros compartilhados

- `PASTA_PROJETO` calcula a raiz do repositorio a partir da localizacao deste modulo.
- `PASTA_CHAVES` e `PASTA_DADOS` apontam para as pastas comuns de chaves RSA e bancos locais.
- `URL_RABBITMQ` le `RABBITMQ_URL`, usando o broker local como padrao.
- `EXCHANGE_ECOMMERCE` nomeia o exchange direct de pedidos, estoque, pagamento e entrega. Como e `direct`, a chave de roteamento precisa corresponder ao binding da fila.
- `EXCHANGE_PROMOCOES` nomeia o exchange topic de ofertas. Seus bindings permitem padroes como `promocao.categoria.*`.
- `SERVICOS` e a lista de produtores para os quais `gerar_chaves.py` prepara chaves.

Os servicos leem suas proprias variaveis de configuracao; este modulo centraliza valores que precisam ser consistentes entre eles.

## `http.py` - requisicoes JSON

### `ServicoHTTPError`

Excecao comum para falhas de chamadas HTTP. Os servicos podem capturar esse tipo sem precisar tratar separadamente todas as excecoes de `urllib` e de decodificacao.

### `requisitar_json(url, metodo, dados, headers, timeout)`

1. Define `Accept: application/json` e combina os cabecalhos fornecidos pelo chamador.
2. Se `dados` existir, serializa-o como JSON UTF-8 e inclui `Content-Type: application/json`.
3. Cria um `Request` e executa `urlopen` com timeout (cinco segundos por padrao). O metodo padrao e `GET`; chamadas com corpo, como criacao de cobranca e webhook, usam `POST`.
4. Converte respostas HTTP de erro em `ServicoHTTPError`, incluindo ate 4096 bytes do detalhe retornado. Falhas de conexao e timeout tambem viram essa excecao; a causa original e mantida com `raise ... from erro`.
5. Retorna `{}` para corpo vazio. Caso contrario, decodifica o JSON e exige um objeto/dicionario; JSON invalido ou outro tipo de dado gera `ServicoHTTPError`.

O helper nao repete chamadas nem decide a recuperacao. Por exemplo, o Mock e quem devolve uma cobranca a pendente se a entrega do webhook falhar.

## `seguranca.py` - envelope e assinatura dos eventos

- `_arquivo_chave(servico, nome)` localiza a chave do produtor na pasta `chaves/<servico>/`.
- `garantir_chaves(servico)` cria um par RSA de 2048 bits se ambos os arquivos ainda nao existirem. A chave privada nao usa senha no ambiente demonstrativo; a chave publica e distribuida para verificacao pelos consumidores.
- `_conteudo_assinado(evento, produtor, dados)` monta apenas os campos de negocio `Event`, `Producer` e `Data`, serializando com chaves ordenadas e separadores fixos. Essa forma canonica garante que o produtor e consumidor calculem os mesmos bytes.
- `_gerar_hash(conteudo)` calcula SHA-256 em fluxo.
- `criar_envelope(produtor, evento, dados)` garante as chaves, assina o hash com a chave privada RSA e retorna o envelope incluindo `Signature` em Base64.
- `validar_envelope(envelope)` extrai produtor, evento, dados e assinatura, recalcula o hash e verifica a assinatura com a chave publica do produtor. Retorna `False` se a assinatura criptografica nao corresponder.

A assinatura permite verificar origem e integridade: alteracoes em `Event`, `Producer` ou `Data` invalidam a verificacao. Ela nao cifra o conteudo; consumidores ainda conseguem ler os dados do evento.

## `mensagens.py` - envio e consumo RabbitMQ

### Conexao e exchanges

`abrir_conexao()` usa `pika.URLParameters(URL_RABBITMQ)`. `preparar_exchanges(canal)` declara os dois exchanges como duraveis; todos os processos fazem a declaracao, evitando depender de uma ordem de inicializacao fixa.

### `publicar_evento(produtor, evento, dados, exchange)`

Abre conexao e canal, garante os exchanges, cria o envelope assinado e publica o JSON. A routing key e o proprio nome do evento. `delivery_mode=2` marca a mensagem como persistente. Erros AMQP ou de sistema operacional viram `ErroPublicacaoEvento`; a conexao e fechada no bloco `finally`.

### `consumir_eventos(fila, bindings, funcao, exchange)`

Declara a fila duravel, associa cada binding ao exchange e registra um callback de consumo. `basic_qos(prefetch_count=1)` limita cada consumidor a uma mensagem nao confirmada por vez.

Para cada mensagem, `ler_mensagem()`:

1. Decodifica o corpo JSON e valida o envelope assinado. JSON ilegivel, campos ausentes, chave indisponivel ou assinatura invalida causam descarte e `basic_ack`, evitando reentrega indefinida de uma mensagem sem confianca.
2. Chama a funcao de negocio passada pelo servico.
3. Em falhas classificadas como transitorias (por exemplo, banco, HTTP ou RabbitMQ), registra o erro e usa `basic_nack(requeue=True)` para solicitar reentrega.
4. Em erros de dados/formato (`KeyError`, `TypeError` ou `ValueError`), registra e confirma a mensagem para nao repetir o mesmo evento invalido.
5. Se o processamento termina sem erro, confirma com `basic_ack`.

Essa politica separa erro recuperavel de mensagem inutilizavel. Ela nao substitui uma estrategia de dead-letter ou limite de tentativas; a configuracao atual reenfileira falhas transitorias.

## Como os modulos colaboram

Os servicos obtêm caminhos e nomes em `config.py`; `mensagens.py` usa `seguranca.py` para publicar/verificar envelopes; chamadas REST usam `http.py`. `gerar_chaves.py` prepara os arquivos usados por `seguranca.py`. As responsabilidades dos scripts estao em [`../README_SCRIPTS.md`](../README_SCRIPTS.md).