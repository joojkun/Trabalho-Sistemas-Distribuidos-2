const API_BASE_URL = (window.API_BASE_URL || "http://localhost:5000").replace(/\/+$/, "");
const CLIENT_ID_KEY = "loja-distribuida-cliente-id";
const connectionStatus = document.querySelector("#connection-status");
const catalogMessage = document.querySelector("#catalog-message");
const orderMessage = document.querySelector("#orders-message");
const interestMessage = document.querySelector("#interest-message");
const productsList = document.querySelector("#products-list");
const ordersList = document.querySelector("#orders-list");
const orderForm = document.querySelector("#order-form");
const createOrderButton = document.querySelector("#create-order");
const orders = new Map();
const checkoutWindows = new Map();
const checkoutUrls = new Map();
const orderNotices = new Map();

function getClientId() {
  let clientId = localStorage.getItem(CLIENT_ID_KEY);
  if (!clientId) {
    clientId = crypto.randomUUID();
    localStorage.setItem(CLIENT_ID_KEY, clientId);
  }
  return clientId;
}

const clientId = getClientId();

async function requestJSON(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    throw new Error(`Não foi possível conectar à API: ${error.message}`);
  }

  let result;
  try {
    result = await response.json();
  } catch (error) {
    throw new Error(`A API respondeu com conteúdo inválido (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(result.erro || `A solicitação falhou (${response.status}).`);
  }
  return result;
}

function setMessage(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function renderProducts(products) {
  productsList.replaceChildren();
  if (products.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Não há produtos disponíveis no estoque.";
    productsList.append(empty);
    return;
  }

  for (const product of products) {
    const card = document.createElement("article");
    card.className = "product-card";

    const name = document.createElement("h3");
    name.textContent = product.nome;
    const details = document.createElement("p");
    details.textContent = `R$ ${Number(product.preco).toFixed(2)} · Categoria ${product.categoria} · ${product.estoque} em estoque`;
    const label = document.createElement("label");
    label.className = "quantity-field";
    label.textContent = "Quantidade";
    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.min = "0";
    quantity.max = String(product.estoque);
    quantity.step = "1";
    quantity.value = "0";
    quantity.dataset.productId = product.produto_id;
    quantity.setAttribute("aria-label", `Quantidade de ${product.nome}`);
    label.append(quantity);
    card.append(name, details, label);
    productsList.append(card);
  }
}

async function loadProducts() {
  setMessage(catalogMessage, "Consultando estoque...");
  try {
    const result = await requestJSON("/api/products");
    if (!Array.isArray(result.products)) {
      throw new Error("O catálogo retornado pela API é inválido.");
    }
    renderProducts(result.products);
    setMessage(catalogMessage, `${result.products.length} produto(s) carregado(s).`);
  } catch (error) {
    setMessage(catalogMessage, error.message, true);
  }
}

function statusClass(status) {
  if (["enviado", "pagamento_aprovado"].includes(status)) return "success";
  if (["cancelado_estoque", "cancelado_pagamento"].includes(status)) return "failure";
  return "";
}

function renderOrders() {
  ordersList.replaceChildren();
  const orderValues = [...orders.values()];
  if (orderValues.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Ainda não há pedidos nesta sessão.";
    ordersList.append(empty);
    return;
  }

  orderValues.sort((first, second) => second.pedido_id.localeCompare(first.pedido_id));
  for (const order of orderValues) {
    const card = document.createElement("article");
    card.className = "order-card";
    const heading = document.createElement("div");
    heading.className = "order-heading";
    const title = document.createElement("h3");
    title.textContent = `Pedido ${order.pedido_id}`;
    const badge = document.createElement("span");
    badge.className = `status-badge ${statusClass(order.status)}`;
    badge.textContent = (order.status || "criado").replaceAll("_", " ");
    heading.append(title, badge);

    const items = document.createElement("p");
    const productSummary = (order.produtos || [])
      .map((item) => `${item.quantidade} × produto ${item.produto_id}`)
      .join(", ");
    items.textContent = productSummary || "Itens do pedido";
    const total = document.createElement("p");
    total.textContent = `Total: R$ ${Number(order.valor_total || 0).toFixed(2)}`;
    card.append(heading, items, total);

    const notice = orderNotices.get(order.pedido_id);
    if (notice) {
      const latest = document.createElement("p");
      latest.textContent = notice;
      card.append(latest);
    }
    if (order.nota_fiscal) {
      const invoice = document.createElement("p");
      invoice.textContent = `Nota fiscal: ${order.nota_fiscal}`;
      card.append(invoice);
    }
    if (order.checkout_url) {
      const checkout = document.createElement("a");
      checkout.className = "checkout-link";
      checkout.href = order.checkout_url;
      checkout.target = "_blank";
      checkout.rel = "noopener noreferrer";
      checkout.textContent = "Abrir checkout de pagamento";
      card.append(checkout);
    }
    ordersList.append(card);
  }
}

async function loadOrders() {
  try {
    const result = await requestJSON(
      `/api/orders?cliente_id=${encodeURIComponent(clientId)}`,
    );
    for (const order of result.orders) {
      orders.set(order.pedido_id, order);
    }
    renderOrders();
  } catch (error) {
    setMessage(orderMessage, error.message, true);
  }
}

function handleOrderEvent(event) {
  const update = JSON.parse(event.data);
  const order = orders.get(update.pedido_id) || { pedido_id: update.pedido_id };
  Object.assign(order, update);
  orders.set(update.pedido_id, order);
  orderNotices.set(update.pedido_id, update.mensagem);

  if (update.checkout_url) {
    checkoutUrls.set(update.pedido_id, update.checkout_url);
    const checkoutWindow = checkoutWindows.get(update.pedido_id);
    if (checkoutWindow && !checkoutWindow.closed) {
      checkoutWindow.location.replace(update.checkout_url);
      checkoutWindows.delete(update.pedido_id);
    }
  }

  if (["cancelado_estoque", "cancelado_pagamento"].includes(update.status)) {
    const checkoutWindow = checkoutWindows.get(update.pedido_id);
    if (checkoutWindow && !checkoutWindow.closed) checkoutWindow.close();
    checkoutWindows.delete(update.pedido_id);
  }

  renderOrders();
  setMessage(orderMessage, update.mensagem);
}

function connectToEvents() {
  const eventSource = new EventSource(
    `${API_BASE_URL}/api/events?cliente_id=${encodeURIComponent(clientId)}`,
  );
  eventSource.onopen = () => {
    connectionStatus.textContent = "Atualizações em tempo real conectadas";
    connectionStatus.classList.remove("disconnected");
  };
  eventSource.onerror = () => {
    connectionStatus.textContent = "Reconectando às atualizações...";
    connectionStatus.classList.add("disconnected");
  };
  eventSource.addEventListener("order.status", handleOrderEvent);
}

orderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const produtos = [...productsList.querySelectorAll("input[data-product-id]")]
    .map((input) => ({
      produto_id: input.dataset.productId,
      quantidade: Number(input.value),
    }))
    .filter((item) => item.quantidade > 0);
  if (produtos.length === 0) {
    setMessage(orderMessage, "Selecione ao menos um produto.", true);
    return;
  }
  if (produtos.some((item) => !Number.isSafeInteger(item.quantidade))) {
    setMessage(orderMessage, "As quantidades devem ser números inteiros.", true);
    return;
  }

  const checkoutWindow = window.open("about:blank", "_blank");
  if (checkoutWindow) {
    checkoutWindow.document.title = "Aguardando checkout";
    checkoutWindow.document.body.textContent = "Aguardando confirmação do estoque...";
  }
  createOrderButton.disabled = true;
  setMessage(orderMessage, "Enviando pedido...");
  try {
    const pedido = await requestJSON("/api/orders", {
      method: "POST",
      body: JSON.stringify({ cliente_id: clientId, produtos }),
    });
    orders.set(pedido.pedido_id, pedido);
    if (checkoutWindow) checkoutWindows.set(pedido.pedido_id, checkoutWindow);
    const checkoutUrl = checkoutUrls.get(pedido.pedido_id) || pedido.checkout_url;
    if (checkoutUrl && checkoutWindow && !checkoutWindow.closed) {
      checkoutWindow.location.replace(checkoutUrl);
      checkoutWindows.delete(pedido.pedido_id);
    }
    renderOrders();
    setMessage(orderMessage, `Pedido ${pedido.pedido_id} criado; aguardando estoque.`);
    orderForm.reset();
    await loadProducts();
  } catch (error) {
    if (checkoutWindow && !checkoutWindow.closed) checkoutWindow.close();
    setMessage(orderMessage, error.message, true);
  } finally {
    createOrderButton.disabled = false;
  }
});

document.querySelector("#refresh-products").addEventListener("click", loadProducts);

document.querySelector("#interest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const email = form.elements.email.value.trim();
  const categorias = [...form.querySelectorAll('input[name="category"]:checked')]
    .map((input) => input.value);
  if (categorias.length === 0) {
    setMessage(interestMessage, "Selecione ao menos uma categoria.", true);
    return;
  }

  try {
    await requestJSON("/api/interests", {
      method: "POST",
      body: JSON.stringify({ email, categorias }),
    });
    setMessage(interestMessage, "Interesse registrado. Você receberá ofertas das categorias escolhidas.");
  } catch (error) {
    setMessage(interestMessage, error.message, true);
  }
});

document.querySelector("#cancel-interest").addEventListener("click", async () => {
  const email = document.querySelector("#interest-email").value.trim();
  if (!email) {
    setMessage(interestMessage, "Informe o e-mail cuja inscrição deseja cancelar.", true);
    return;
  }

  try {
    await requestJSON("/api/interests", {
      method: "DELETE",
      body: JSON.stringify({ email }),
    });
    setMessage(interestMessage, "Inscrição cancelada.");
  } catch (error) {
    setMessage(interestMessage, error.message, true);
  }
});

connectToEvents();
loadProducts();
loadOrders();
