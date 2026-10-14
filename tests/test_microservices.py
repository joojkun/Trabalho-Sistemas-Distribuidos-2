import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import entrega
import estoque
import mock_pagamento
import pagamento
import principal
import promocoes


def _produtos_estoque():
    return {
        "products": [
            {
                "produto_id": "1",
                "nome": "Teclado",
                "preco": 120.0,
                "categoria": "A",
                "estoque": 10,
            },
            {
                "produto_id": "2",
                "nome": "Mouse",
                "preco": 80.0,
                "categoria": "A",
                "estoque": 8,
            },
            {
                "produto_id": "3",
                "nome": "Monitor",
                "preco": 900.0,
                "categoria": "B",
                "estoque": 3,
            },
            {
                "produto_id": "4",
                "nome": "Fone",
                "preco": 150.0,
                "categoria": "C",
                "estoque": 5,
            },
        ]
    }


class TemporaryDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()


class StockServiceTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        self.database_patch = patch.object(
            estoque,
            "DB_PATH",
            self.database_directory / "stock.sqlite3",
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        estoque.inicializar_banco()

    def _quantidade(self, produto_id):
        conexao = estoque._conectar()
        try:
            linha = conexao.execute(
                "SELECT quantidade FROM produtos WHERE produto_id = ?",
                (produto_id,),
            ).fetchone()
            return linha["quantidade"]
        finally:
            conexao.close()

    def test_reservation_and_release_are_persistent_and_idempotent(self):
        pedido = {
            "pedido_id": "pedido-estoque-1",
            "produtos": [{"produto_id": "1", "quantidade": 2}],
        }
        with patch.object(estoque, "publicar_evento") as publicar:
            estoque.atualizar_estoque({"Event": "pedido.criado", "Data": pedido})
            self.assertEqual(8, self._quantidade("1"))
            publicar.assert_called_once_with("estoque", "pedido.estoque_ok", pedido)

            estoque.inicializar_banco()
            self.assertEqual(8, self._quantidade("1"))

            estoque.atualizar_estoque({"Event": "pedido.criado", "Data": pedido})
            self.assertEqual(8, self._quantidade("1"))

            evento_exclusao = {"Event": "pedido.excluido", "Data": pedido}
            estoque.atualizar_estoque(evento_exclusao)
            estoque.atualizar_estoque(evento_exclusao)
            self.assertEqual(10, self._quantidade("1"))

    def test_unavailable_order_does_not_change_stock(self):
        pedido = {
            "pedido_id": "pedido-estoque-2",
            "produtos": [{"produto_id": "3", "quantidade": 4}],
        }
        with patch.object(estoque, "publicar_evento") as publicar:
            estoque.atualizar_estoque({"Event": "pedido.criado", "Data": pedido})
            self.assertEqual(3, self._quantidade("3"))
            publicar.assert_called_once_with(
                "estoque",
                "estoque.indisponivel",
                pedido,
            )


class GatewayTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        self.database_patch = patch.object(
            principal,
            "DB_PATH",
            self.database_directory / "gateway.sqlite3",
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.stock_patch = patch.object(
            principal,
            "requisitar_json",
            return_value=_produtos_estoque(),
        )
        self.stock_patch.start()
        self.addCleanup(self.stock_patch.stop)
        principal.inicializar_banco()
        self.client = principal.app.test_client()

    def test_rest_order_and_sse_status_updates(self):
        cliente_id = str(uuid.uuid4())
        with patch.object(principal, "publicar_evento") as publicar:
            resposta = self.client.post(
                "/api/orders",
                json={
                    "cliente_id": cliente_id,
                    "produtos": [
                        {"produto_id": "1", "quantidade": 1},
                        {"produto_id": "2", "quantidade": 2},
                    ],
                },
            )
            publicar.assert_called_once()
        self.assertEqual(202, resposta.status_code)
        pedido = resposta.get_json()
        self.assertEqual(280.0, pedido["valor_total"])

        with patch.object(principal, "publicar_evento") as publicar:
            principal._atualizar_pedido(
                {"Event": "pedido.estoque_ok", "Data": pedido}
            )
            self.assertEqual("estoque_confirmado", self.client.get(
                f"/api/orders?cliente_id={cliente_id}"
            ).get_json()["orders"][0]["status"])

            principal._atualizar_pedido(
                {
                    "Event": "pagamento.checkout_criado",
                    "Data": {
                        **pedido,
                        "checkout_url": "http://localhost:5004/checkout/teste",
                        "cobranca_id": "cobranca-teste",
                    },
                }
            )
            principal._atualizar_pedido(
                {"Event": "pagamento.aprovado", "Data": pedido}
            )
            principal._atualizar_pedido(
                {
                    "Event": "pedido.enviado",
                    "Data": {
                        **pedido,
                        "nota_fiscal": "NF-TESTE",
                        "data_envio": "2026-10-13T00:00:00+00:00",
                    },
                }
            )
            publicar.assert_not_called()

        resposta_pedido = self.client.get(
            f"/api/orders/{pedido['pedido_id']}?cliente_id={cliente_id}"
        )
        self.assertEqual(200, resposta_pedido.status_code)
        self.assertEqual("enviado", resposta_pedido.get_json()["status"])
        self.assertEqual("NF-TESTE", resposta_pedido.get_json()["nota_fiscal"])
        self.assertEqual(
            "http://localhost:5004/checkout/teste",
            resposta_pedido.get_json()["checkout_url"],
        )

        stream = self.client.get(
            f"/api/events?cliente_id={cliente_id}",
            headers={"Last-Event-ID": "0"},
            buffered=False,
        )
        partes = [next(stream.response) for _ in range(6)]
        stream.close()
        conteudo = b"".join(partes).decode("utf-8")
        self.assertIn('"status": "estoque_confirmado"', conteudo)
        self.assertIn('"status": "pagamento_aprovado"', conteudo)
        self.assertIn('"status": "enviado"', conteudo)

    def test_products_are_fetched_from_stock_and_interest_events_are_published(self):
        resposta = self.client.get(
            "/api/products",
            headers={"Origin": "http://localhost:8080"},
        )
        self.assertEqual(200, resposta.status_code)
        self.assertEqual(4, len(resposta.get_json()["products"]))
        self.assertEqual(
            "http://localhost:8080",
            resposta.headers["Access-Control-Allow-Origin"],
        )

        with patch.object(principal, "publicar_evento") as publicar:
            resposta = self.client.post(
                "/api/interests",
                json={"email": "Pessoa@exemplo.com", "categorias": ["B", "A", "A"]},
            )
            self.assertEqual(202, resposta.status_code)
            publicar.assert_called_once_with(
                "principal",
                "interesse.promocao",
                {"email": "pessoa@exemplo.com", "categorias": ["A", "B"]},
            )

            resposta = self.client.delete(
                "/api/interests",
                json={"email": "Pessoa@exemplo.com"},
            )
            self.assertEqual(202, resposta.status_code)
            self.assertEqual(
                "interesse.cancelado",
                publicar.call_args.args[1],
            )

    def test_order_validation_and_sse_are_scoped_to_client(self):
        resposta = self.client.post(
            "/api/orders",
            json={"cliente_id": "nao-e-uuid", "produtos": [{"produto_id": "1", "quantidade": 1}]},
        )
        self.assertEqual(400, resposta.status_code)

        cliente_id = str(uuid.uuid4())
        outra_conta = str(uuid.uuid4())
        pedido = {
            "pedido_id": "pedido-cliente-1",
            "cliente_id": cliente_id,
            "produtos": [{"produto_id": "1", "quantidade": 1}],
            "valor_total": 120.0,
            "status": "criado",
        }
        principal._inserir_pedido(pedido)
        pedido_outra_conta = {**pedido, "pedido_id": "pedido-cliente-2", "cliente_id": outra_conta}
        principal._inserir_pedido(pedido_outra_conta)

        self.assertEqual(
            1,
            len(
                self.client.get(
                    f"/api/orders?cliente_id={cliente_id}"
                ).get_json()["orders"]
            ),
        )

        stream = self.client.get(
            f"/api/events?cliente_id={cliente_id}",
            headers={"Last-Event-ID": "0"},
            buffered=False,
        )
        partes = [next(stream.response) for _ in range(2)]
        stream.close()
        conteudo = b"".join(partes).decode("utf-8")
        self.assertIn("pedido-cliente-1", conteudo)
        self.assertNotIn("pedido-cliente-2", conteudo)

    def test_failure_statuses_cancel_order_and_publish_inventory_release(self):
        cliente_id = str(uuid.uuid4())
        pedido = {
            "pedido_id": "pedido-recusado-1",
            "cliente_id": cliente_id,
            "produtos": [{"produto_id": "1", "quantidade": 1}],
            "valor_total": 120.0,
            "status": "criado",
        }
        principal._inserir_pedido(pedido)

        with patch.object(principal, "publicar_evento") as publicar:
            principal._atualizar_pedido(
                {"Event": "pagamento.recusado", "Data": pedido}
            )
            publicar.assert_called_once_with(
                "principal",
                "pedido.excluido",
                {
                    **pedido,
                    "status": "cancelado_pagamento",
                },
            )

        status = self.client.get(
            f"/api/orders/{pedido['pedido_id']}?cliente_id={cliente_id}"
        ).get_json()["status"]
        self.assertEqual("cancelado_pagamento", status)

    def test_stock_unavailable_event_sets_order_cancelled(self):
        cliente_id = str(uuid.uuid4())
        pedido = {
            "pedido_id": "pedido-sem-estoque-1",
            "cliente_id": cliente_id,
            "produtos": [{"produto_id": "3", "quantidade": 4}],
            "valor_total": 3600.0,
            "status": "criado",
        }
        principal._inserir_pedido(pedido)

        with patch.object(principal, "publicar_evento") as publicar:
            principal._atualizar_pedido(
                {"Event": "estoque.indisponivel", "Data": pedido}
            )
            publicar.assert_called_once()
            self.assertEqual("pedido.excluido", publicar.call_args.args[1])

        status = self.client.get(
            f"/api/orders/{pedido['pedido_id']}?cliente_id={cliente_id}"
        ).get_json()["status"]
        self.assertEqual("cancelado_estoque", status)


class PaymentServiceTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        self.database_patch = patch.object(
            pagamento,
            "DB_PATH",
            self.database_directory / "payment.sqlite3",
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        pagamento.inicializar_banco()

    def test_checkout_is_created_and_webhook_publishes_signed_event(self):
        pedido = {
            "pedido_id": "pedido-pagamento-1",
            "cliente_id": str(uuid.uuid4()),
            "produtos": [{"produto_id": "1", "quantidade": 1}],
            "valor_total": 120.0,
        }
        with (
            patch.object(
                pagamento,
                "requisitar_json",
                return_value={
                    "charge_id": "cobranca-1",
                    "checkout_url": "http://localhost:5004/checkout/cobranca-1",
                },
            ) as requisitar,
            patch.object(pagamento, "publicar_evento") as publicar,
        ):
            pagamento.processar_pagamento({"Event": "pedido.estoque_ok", "Data": pedido})
            self.assertTrue(requisitar.call_args.args[0].endswith("/api/charges"))
            publicar.assert_called_once()
            self.assertEqual("pagamento.checkout_criado", publicar.call_args.args[1])

        with (
            patch.object(pagamento, "WEBHOOK_TOKEN", ""),
            patch.object(pagamento, "publicar_evento") as publicar,
        ):
            resposta = pagamento.app.test_client().post(
                "/webhook",
                json={
                    "pedido_id": pedido["pedido_id"],
                    "charge_id": "cobranca-1",
                    "status": "APROVADO",
                },
            )
            self.assertEqual(200, resposta.status_code)
            publicar.assert_called_once()
            self.assertEqual("pagamento.aprovado", publicar.call_args.args[1])


class PaymentMockTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        self.database_patch = patch.object(
            mock_pagamento,
            "DB_PATH",
            self.database_directory / "payment-mock.sqlite3",
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        mock_pagamento.inicializar_banco()

    def test_checkout_returns_url_and_posts_payment_result_to_webhook(self):
        webhook_url = mock_pagamento.WEBHOOK_URL_PERMITIDA
        client = mock_pagamento.app.test_client()
        response = client.post(
            "/api/charges",
            json={
                "pedido_id": "pedido-mock-1",
                "valor_total": 45.5,
                "webhook_url": webhook_url,
                "webhook_token": "local-test-token",
            },
        )
        self.assertEqual(201, response.status_code)
        cobranca = response.get_json()
        self.assertIn("/checkout/", cobranca["checkout_url"])
        checkout = client.get(f"/checkout/{cobranca['charge_id']}")
        self.assertEqual(200, checkout.status_code)
        self.assertIn(b"Pagamento aprovado", checkout.data)
        self.assertIn(b"Pagamento recusado", checkout.data)

        with patch.object(mock_pagamento, "requisitar_json") as requisitar:
            response = client.post(
                f"/checkout/{cobranca['charge_id']}/result",
                json={"status": "APROVADO"},
            )
            self.assertEqual(200, response.status_code)
            self.assertEqual("APROVADO", response.get_json()["status"])
            self.assertEqual(webhook_url, requisitar.call_args.args[0])
            self.assertEqual(
                "local-test-token",
                requisitar.call_args.kwargs["headers"]["X-Payment-Token"],
            )

        checkout = client.get(f"/checkout/{cobranca['charge_id']}")
        self.assertEqual(200, checkout.status_code)
        self.assertIn(b"APROVADO", checkout.data)

    def test_mock_rejects_unapproved_webhook_destination(self):
        response = mock_pagamento.app.test_client().post(
            "/api/charges",
            json={
                "pedido_id": "pedido-ssrf",
                "valor_total": 10,
                "webhook_url": "http://example.com/collect",
            },
        )
        self.assertEqual(400, response.status_code)


class PromotionServiceTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        self.database_patch = patch.object(
            promocoes,
            "DB_PATH",
            self.database_directory / "promotions.sqlite3",
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        promocoes.inicializar_banco()

    def test_interest_is_persisted_updated_and_cancelled(self):
        promocoes.atualizar_interesse(
            {
                "Event": "interesse.promocao",
                "Data": {"email": "cliente@exemplo.com", "categorias": ["A"]},
            }
        )
        self.assertEqual(["cliente@exemplo.com"], promocoes._destinatarios("A"))
        self.assertEqual([], promocoes._destinatarios("B"))

        promocoes.atualizar_interesse(
            {
                "Event": "interesse.promocao",
                "Data": {"email": "cliente@exemplo.com", "categorias": ["B"]},
            }
        )
        self.assertEqual([], promocoes._destinatarios("A"))
        self.assertEqual(["cliente@exemplo.com"], promocoes._destinatarios("B"))

        promocoes.atualizar_interesse(
            {
                "Event": "interesse.cancelado",
                "Data": {"email": "cliente@exemplo.com"},
            }
        )
        self.assertEqual([], promocoes._destinatarios("B"))

    def test_email_delivery_calls_resend_api(self):
        with (
            patch.object(promocoes, "RESEND_API_KEY", "test-api-key"),
            patch.object(promocoes, "requisitar_json") as requisitar,
        ):
            promocoes._enviar_email(
                "cliente@exemplo.com",
                {
                    "promotion_id": "promotion-1",
                    "nome": "Teclado",
                    "desconto": 20,
                    "preco_original": 120.0,
                },
            )
        self.assertEqual("POST", requisitar.call_args.kwargs["metodo"])
        self.assertEqual(
            "Bearer test-api-key",
            requisitar.call_args.kwargs["headers"]["Authorization"],
        )


class DeliveryServiceTests(unittest.TestCase):
    def test_approved_payment_creates_invoice_and_shipping_event(self):
        pedido = {
            "pedido_id": "pedido-entrega-1",
            "produtos": [{"produto_id": "1", "quantidade": 1}],
        }
        with patch.object(entrega, "publicar_evento") as publicar:
            entrega.preparar_envio(
                {"Event": "pagamento.aprovado", "Data": pedido}
            )

        publicar.assert_called_once()
        produtor, evento, dados = publicar.call_args.args
        self.assertEqual("entrega", produtor)
        self.assertEqual("pedido.enviado", evento)
        self.assertEqual("NF-PEDIDO-ENTREGA-1", dados["nota_fiscal"])
        self.assertTrue(dados["data_envio"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
