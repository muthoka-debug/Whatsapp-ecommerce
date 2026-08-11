/* Public storefront: browse -> cart -> checkout.
 *
 * No login. The only tenant context is the X-Business-ID header, resolved from
 * the slug in the URL via the public info endpoint. The cart itself lives in the
 * Django session server-side, so it survives a reload without any local state.
 */

import { api, store, $, currency, escapeHtml, showError } from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';

const slug = document.currentScript?.dataset.slug
  || document.querySelector('script[data-slug]')?.dataset.slug
  || location.pathname.split('/').filter(Boolean)[1];

const PAGE_SIZE = 20;
let page = 1;
let currentCategory = '';
let shopCurrency = 'KSh';

bindModals();
start();

async function start() {
  try {
    const info = await api.get(`/api/v1/storefront/public/${slug}/`, { auth: false, business: false });

    // Every later cart call is business-scoped by this header.
    store.businessId = info.business.id;
    shopCurrency = info.business.currency || 'KSh';

    $('#shop-name').textContent = info.business.name;
    document.title = info.business.name;

    const settings = info.settings || {};
    if (settings.banner_text || settings.banner_subtext) {
      $('#banner-title').textContent = settings.banner_text || info.business.name;
      $('#banner-text').textContent = settings.banner_subtext || '';
      $('#shop-banner').hidden = false;
    }
  } catch (error) {
    showError($('#shop-error'), error.status === 404
      ? 'This shop is not available.'
      : error.message);
    $('#product-grid').innerHTML = '';
    return;
  }

  await loadCategories();
  await loadProducts();
  await refreshCart();
  bindUi();
}

/* --- catalogue ------------------------------------------------------------ */

async function loadCategories() {
  let categories = [];
  try {
    const payload = await api.get(
      `/api/v1/storefront/public/${slug}/categories/`, { auth: false, business: false },
    );
    categories = Array.isArray(payload) ? payload : (payload.results || []);
  } catch {
    categories = [];
  }

  const chips = [{ id: '', name: 'All' }].concat(categories);
  $('#category-chips').innerHTML = chips.map(c => `
    <button class="chip ${String(c.id) === String(currentCategory) ? 'current' : ''}"
            data-category="${c.id}" type="button">${escapeHtml(c.name)}</button>`).join('');

  document.querySelectorAll('[data-category]').forEach(chip => {
    chip.addEventListener('click', () => {
      currentCategory = chip.dataset.category;
      page = 1;
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('current'));
      chip.classList.add('current');
      loadProducts();
    });
  });
}

async function loadProducts() {
  const grid = $('#product-grid');
  grid.classList.add('loading');

  const params = new URLSearchParams({ page: String(page) });
  const search = $('#search').value.trim();
  if (search) params.set('search', search);
  if (currentCategory) params.set('category', currentCategory);

  try {
    const payload = await api.get(
      `/api/v1/storefront/public/${slug}/products/?${params}`, { auth: false, business: false },
    );
    const items = payload.results || [];

    grid.innerHTML = items.length
      ? items.map(renderProduct).join('')
      : '<div class="empty">No products found.</div>';

    grid.querySelectorAll('[data-add]').forEach(button => {
      button.addEventListener('click', () => addToCart(button.dataset.add, button));
    });

    renderPager(payload.count || items.length);
  } catch (error) {
    grid.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  } finally {
    grid.classList.remove('loading');
  }
}

function renderProduct(product) {
  const image = (product.images || [])[0];
  const soldOut = product.stock <= 0;

  return `
    <article class="product-card">
      <div class="product-thumb">
        ${image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(product.name)}" loading="lazy">` : 'No image'}
      </div>
      <div class="product-name">${escapeHtml(product.name)}</div>
      <div class="product-price">
        ${escapeHtml(currency(product.price, shopCurrency))}
        ${product.compare_price && Number(product.compare_price) > Number(product.price)
          ? `<span class="product-compare">${escapeHtml(currency(product.compare_price, shopCurrency))}</span>` : ''}
      </div>
      ${soldOut ? '<div class="tag">Out of stock</div>' : ''}
      <button class="btn btn-primary btn-sm" data-add="${product.id}" ${soldOut ? 'disabled' : ''} type="button">
        ${soldOut ? 'Unavailable' : 'Add to cart'}
      </button>
    </article>`;
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#shop-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">Page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;
  $('#prev')?.addEventListener('click', () => { page -= 1; loadProducts(); window.scrollTo(0, 0); });
  $('#next')?.addEventListener('click', () => { page += 1; loadProducts(); window.scrollTo(0, 0); });
}

/* --- cart ----------------------------------------------------------------- */

async function addToCart(productId, button) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = 'Adding…';
  try {
    const cart = await api.post(
      '/api/v1/storefront/cart/add_item/',
      { product_id: Number(productId), quantity: 1 },
      { auth: false },
    );
    renderCart(cart);
    button.textContent = 'Added';
    setTimeout(() => { button.textContent = original; button.disabled = false; }, 900);
  } catch (error) {
    showError($('#shop-error'), error.message);
    button.textContent = original;
    button.disabled = false;
  }
}

async function refreshCart() {
  try {
    renderCart(await api.get('/api/v1/storefront/cart/', { auth: false }));
  } catch {
    // An empty or missing cart is not an error worth showing.
  }
}

function renderCart(cart) {
  const items = cart?.items || [];
  $('#cart-count').textContent = String(cart?.total_items ?? 0);
  $('#cart-subtotal').textContent = currency(cart?.subtotal, shopCurrency);
  $('#go-checkout').disabled = items.length === 0;

  $('#cart-body').innerHTML = items.length
    ? items.map(item => `
        <div class="cart-line">
          <div class="cart-line-main">
            <div class="cart-line-name">${escapeHtml(item.product_details?.name || 'Item')}</div>
            <div class="muted" style="font-size:0.8rem;">${escapeHtml(currency(item.price, shopCurrency))} each</div>
          </div>
          <div class="qty">
            <button class="btn btn-sm" data-dec="${item.id}" data-qty="${item.quantity}" type="button" aria-label="Decrease quantity">−</button>
            <span>${escapeHtml(String(item.quantity))}</span>
            <button class="btn btn-sm" data-inc="${item.id}" data-qty="${item.quantity}" type="button" aria-label="Increase quantity">+</button>
          </div>
          <div style="width:78px;text-align:right;font-weight:600;">${escapeHtml(currency(item.total, shopCurrency))}</div>
        </div>`).join('')
    : '<div class="empty">Your cart is empty.</div>';

  $('#cart-body').querySelectorAll('[data-inc]').forEach(b =>
    b.addEventListener('click', () => setQuantity(b.dataset.inc, Number(b.dataset.qty) + 1)));
  $('#cart-body').querySelectorAll('[data-dec]').forEach(b =>
    b.addEventListener('click', () => setQuantity(b.dataset.dec, Number(b.dataset.qty) - 1)));
}

async function setQuantity(itemId, quantity) {
  try {
    // The API removes the line when quantity hits 0.
    renderCart(await api.post(
      '/api/v1/storefront/cart/update_item/',
      { item_id: itemId, quantity: Math.max(0, quantity) },
      { auth: false },
    ));
  } catch (error) {
    showError($('#shop-error'), error.message);
  }
}

/* --- checkout ------------------------------------------------------------- */

function bindUi() {
  $('#open-cart').addEventListener('click', () => { $('#cart-drawer').hidden = false; });
  $('#close-cart').addEventListener('click', () => { $('#cart-drawer').hidden = true; });

  let timer = null;
  $('#search').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { page = 1; loadProducts(); }, 280);
  });

  $('#go-checkout').addEventListener('click', () => {
    showError($('#checkout-error'), '');
    openModal('checkout-modal');
  });

  $('#payment_method').addEventListener('change', event => {
    $('#mpesa-field').hidden = event.target.value !== 'MPESA';
  });

  $('#checkout-form').addEventListener('submit', placeOrder);
  $('#done-close').addEventListener('click', () => {
    closeModal('done-modal');
    loadProducts();
  });
}

async function placeOrder(event) {
  event.preventDefault();
  const button = $('#place-order');
  const method = $('#payment_method').value;

  const payload = {
    customer_name: $('#customer_name').value.trim(),
    customer_phone: $('#customer_phone').value.trim(),
    delivery_address: $('#delivery_address').value.trim(),
    delivery_phone: $('#delivery_phone').value.trim(),
    delivery_notes: $('#delivery_notes').value.trim(),
    payment_method: method,
  };

  const email = $('#customer_email').value.trim();
  if (email) payload.customer_email = email;
  if (method === 'MPESA') payload.mpesa_phone = $('#mpesa_phone').value.trim();

  button.disabled = true;
  button.textContent = 'Placing order…';
  showError($('#checkout-error'), '');

  try {
    const result = await api.post(
      '/api/v1/storefront/cart/checkout/', payload, { auth: false },
    );

    closeModal('checkout-modal');
    $('#cart-drawer').hidden = true;
    $('#done-text').textContent =
      `Order ${result.order.order_number} is confirmed. ` +
      `We will contact you on ${payload.customer_phone}.`;
    openModal('done-modal');

    $('#checkout-form').reset();
    $('#mpesa-field').hidden = true;
    await refreshCart();
  } catch (error) {
    showError($('#checkout-error'), error.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Place order';
  }
}
