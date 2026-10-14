# Checklist da especificacao

Baseada nas quatro paginas da descricao do trabalho em `images/`.

## Frontend web

- [x] Interface grafica em JavaScript, diferente do backend Python.
- [x] Consultar catalogo e estoque por REST e criar pedidos pelo API Gateway.
- [x] Abrir o checkout do Mock de Pagamento em nova aba; oferecer link alternativo.
- [x] Manter SSE por cliente e apresentar as mudancas de estado do pedido.
- [x] Cadastrar e cancelar inscricao de e-mail por categorias de interesse.

## MS Principal / API Gateway

- [x] Expor endpoints REST consumidos pelo frontend.
- [x] Consultar produtos diretamente do MS Estoque via REST.
- [x] Publicar `pedido.criado`, `pedido.excluido`, `interesse.promocao` e
  `interesse.cancelado` no RabbitMQ.
- [x] Consumir `pedido.estoque_ok`, `estoque.indisponivel`,
  `pagamento.aprovado`, `pagamento.recusado` e `pedido.enviado`.
- [x] Persistir pedidos e eventos e enviar atualizacoes SSE filtradas por
  cliente, incluindo estoque confirmado, resultado do pagamento e envio.

## RabbitMQ e seguranca

- [x] Usar RabbitMQ para comunicacao assincrona entre os microsservicos.
- [x] Manter assinatura digital RSA/SHA-256 e validacao dos envelopes.

## MS Estoque

- [x] Consumir `pedido.criado` e `pedido.excluido`.
- [x] Validar disponibilidade, reservar/devolver unidades e publicar
  `pedido.estoque_ok` ou `estoque.indisponivel`.
- [x] Persistir catalogo e quantidades em SQLite.
- [x] Expor produtos disponiveis por REST para o Gateway.

## MS Pagamento e Mock externo

- [x] Consumir `pedido.estoque_ok` e solicitar cobranca REST ao Mock, incluindo
  a URL do webhook.
- [x] Expor webhook HTTP que valida e publica pagamento aprovado/recusado.
- [x] Disponibilizar checkout web com botoes de aprovacao e recusa que enviam
  POST ao webhook.
- [x] Retornar a URL do checkout ao frontend por evento e SSE.

## MS Entrega

- [x] Consumir pagamento aprovado, simular nota fiscal e despacho, e publicar
  `pedido.enviado`.

## MS Promocoes

- [x] Consumir cadastro e cancelamento de interesses e persistir e-mail e
  categorias.
- [x] Gerar ofertas por categoria e enviar notificacoes pela API REST do
  Resend quando configurada.

## Verificacao e itens humanos

- [x] Testes automatizados dos contratos REST/SSE, persistencia, reserva,
  webhook, interesses e chamada da API de e-mail.
- [x] Simulacao original dos cenarios de sucesso, estoque indisponivel e
  pagamento recusado.
- [ ] Executar o fluxo completo com RabbitMQ local; Docker nao esta disponivel
  neste ambiente de desenvolvimento.
- [ ] Validar o envio real de e-mail com uma chave Resend e remetente verificado.
- [ ] Realizar a defesa oral obrigatoria.
- [ ] Confirmar a formacao da dupla, conforme a observacao da especificacao.
