import {
  api, rows, count, $, currency, badge, escapeHtml, showError, busy,
} from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';
import { onBusinessReady } from './shell.js';

const PAGE_SIZE = 25;
let page = 1;
let categories = [];

bindModals();
onBusinessReady(() => {
  seedSearchFromUrl();
  loadCategories().then(loadProducts);
  bindFilters();
  bindForms();
});

/* The top bar hands off here with ?q=<term> from any page that has no search
 * box of its own. */
function seedSearchFromUrl() {
  const q = new URLSearchParams(location.search).get('q');
  if (q) $('#filter-search').value = q;
}

/* --- list ----------------------------------------------------------------- */

function query() {
  const params = new URLSearchParams({ page: String(page) });
  const search = $('#filter-search').value.trim();
  const category = $('#filter-category').value;
  const active = $('#filter-status').value;

  if (search) params.set('search', search);
  if (category) params.set('category', category);
  if (active) params.set('is_active', active);
  return params.toString();
}

async function loadProducts() {
  const body = $('#product-rows');
  busy(body, true);
  try {
    const payload = await api.get(`/api/v1/products/?${query()}`);
    const items = rows(payload);

    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">No products match. Create one to get started.</td></tr>';
    } else {
      body.innerHTML = items.map(renderRow).join('');
      bindRowActions();
    }
    renderPager(count(payload, items));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    busy(body, false);
  }
}

function renderRow(product) {
  const stockBadge = product.stock <= 0
    ? badge('CANCELLED', 'out of stock')
    : product.is_low_stock
      ? badge('PENDING', 'low')
      : '';

  return `
    <tr>
      <td>
        <div>${escapeHtml(product.name)}</div>
        ${product.is_featured ? '<span class="tag accent">Featured</span>' : ''}
      </td>
      <td class="muted">${escapeHtml(product.category_details?.name || '—')}</td>
      <td class="muted nowrap">${escapeHtml(product.sku || '—')}</td>
      <td class="num">${escapeHtml(currency(product.price))}</td>
      <td class="num">${escapeHtml(String(product.stock))} ${stockBadge}</td>
      <td>${product.is_active ? badge('PAID', 'active') : badge('CONFIRMED', 'inactive')}</td>
      <td class="right nowrap">
        <button class="btn btn-sm" data-edit="${product.id}" type="button">Edit</button>
        <button class="btn btn-sm" data-stock="${product.id}" data-name="${escapeHtml(product.name)}" data-current="${product.stock}" type="button">Stock</button>
      </td>
    </tr>`;
}

function bindRowActions() {
  document.querySelectorAll('[data-edit]').forEach(button => {
    button.addEventListener('click', () => editProduct(button.dataset.edit));
  });
  document.querySelectorAll('[data-stock]').forEach(button => {
    button.addEventListener('click', () => {
      $('#stock-product-id').value = button.dataset.stock;
      $('#stock-context').textContent =
        `${button.dataset.name} — currently ${button.dataset.current} in stock.`;
      $('#stock-quantity').value = '1';
      showError($('#stock-error'), '');
      openModal('stock-modal');
    });
  });
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#product-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">${total} product${total === 1 ? '' : 's'} · page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;

  $('#prev')?.addEventListener('click', () => { page -= 1; loadProducts(); });
  $('#next')?.addEventListener('click', () => { page += 1; loadProducts(); });
}

function bindFilters() {
  let timer = null;
  $('#filter-search').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { page = 1; loadProducts(); }, 280);
  });
  ['#filter-category', '#filter-status'].forEach(sel => {
    $(sel).addEventListener('change', () => { page = 1; loadProducts(); });
  });
}

/* --- categories ----------------------------------------------------------- */

async function loadCategories() {
  try {
    categories = rows(await api.get('/api/v1/products/categories/'));
  } catch {
    categories = [];
  }
  const options = ['<option value="">All categories</option>']
    .concat(categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`));
  $('#filter-category').innerHTML = options.join('');

  $('#product-category').innerHTML = ['<option value="">None</option>']
    .concat(categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`))
    .join('');
}

/* --- create / edit -------------------------------------------------------- */

function bindForms() {
  $('#new-product').addEventListener('click', () => {
    $('#product-form').reset();
    $('#product-id').value = '';
    $('#product-active').checked = true;
    $('#product-modal-title').textContent = 'New product';
    showError($('#product-error'), '');
    openModal('product-modal');
  });

  $('#new-category').addEventListener('click', () => {
    $('#category-form').reset();
    showError($('#category-error'), '');
    openModal('category-modal');
  });

  $('#product-form').addEventListener('submit', saveProduct);
  $('#stock-form').addEventListener('submit', saveStock);
  $('#category-form').addEventListener('submit', saveCategory);
}

async function editProduct(id) {
  showError($('#product-error'), '');
  try {
    const product = await api.get(`/api/v1/products/${id}/`);
    $('#product-id').value = product.id;
    $('#product-name').value = product.name || '';
    $('#product-description').value = product.description || '';
    $('#product-price').value = product.price ?? '';
    $('#product-compare').value = product.compare_price ?? '';
    $('#product-stock').value = product.stock ?? 0;
    $('#product-threshold').value = product.low_stock_threshold ?? 5;
    $('#product-category').value = product.category ?? '';
    $('#product-sku').value = product.sku || '';
    $('#product-active').checked = !!product.is_active;
    $('#product-featured').checked = !!product.is_featured;
    $('#product-modal-title').textContent = 'Edit product';
    openModal('product-modal');
  } catch (error) {
    showError($('#page-error'), error.message);
  }
}

async function saveProduct(event) {
  event.preventDefault();
  const button = $('#product-save');
  const id = $('#product-id').value;

  const payload = {
    name: $('#product-name').value.trim(),
    description: $('#product-description').value.trim(),
    price: $('#product-price').value,
    stock: Number($('#product-stock').value || 0),
    low_stock_threshold: Number($('#product-threshold').value || 0),
    category: $('#product-category').value || null,
    is_active: $('#product-active').checked,
    is_featured: $('#product-featured').checked,
  };

  const compare = $('#product-compare').value;
  if (compare) payload.compare_price = compare;
  const sku = $('#product-sku').value.trim();
  if (sku) payload.sku = sku;

  button.disabled = true;
  try {
    if (id) await api.patch(`/api/v1/products/${id}/`, payload);
    else await api.post('/api/v1/products/', payload);
    closeModal('product-modal');
    loadProducts();
  } catch (error) {
    showError($('#product-error'), error.message);
  } finally {
    button.disabled = false;
  }
}

async function saveStock(event) {
  event.preventDefault();
  const id = $('#stock-product-id').value;
  try {
    await api.post(`/api/v1/products/${id}/update_stock/`, {
      action: $('#stock-action').value,
      quantity: Number($('#stock-quantity').value || 0),
    });
    closeModal('stock-modal');
    loadProducts();
  } catch (error) {
    showError($('#stock-error'), error.message);
  }
}

async function saveCategory(event) {
  event.preventDefault();
  try {
    await api.post('/api/v1/products/categories/', {
      name: $('#category-name').value.trim(),
      description: $('#category-description').value.trim(),
    });
    closeModal('category-modal');
    await loadCategories();
    loadProducts();
  } catch (error) {
    showError($('#category-error'), error.message);
  }
}
