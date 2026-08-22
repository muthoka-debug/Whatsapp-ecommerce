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

  $('#category-parent').innerHTML = ['<option value="">None (top level)</option>']
    .concat(categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`))
    .join('');
}

/* --- repeatable image URLs ------------------------------------------------- */

/* Rows are built as DOM nodes rather than an HTML string: a URL goes straight
 * into .value, so nothing needs escaping and a stray quote cannot break out. */
function addImageRow(value = '') {
  const container = $('#product-images');
  container.querySelector('.repeat-empty')?.remove();

  const row = document.createElement('div');
  row.className = 'repeat-row';

  const input = document.createElement('input');
  input.type = 'url';
  input.placeholder = 'https://…';
  input.value = value;

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'btn btn-sm btn-danger';
  remove.textContent = 'Remove';
  remove.setAttribute('aria-label', 'Remove this image');
  remove.addEventListener('click', () => {
    row.remove();
    if (!container.querySelector('.repeat-row')) renderNoImages();
  });

  row.append(input, remove);
  container.append(row);
  return input;
}

function renderNoImages() {
  const container = $('#product-images');
  container.innerHTML = '';
  const note = document.createElement('p');
  note.className = 'repeat-empty';
  note.textContent = 'No images yet.';
  container.append(note);
}

function setImages(urls) {
  $('#product-images').innerHTML = '';
  const list = (urls || []).filter(url => typeof url === 'string' && url.trim());
  if (!list.length) {
    renderNoImages();
    return;
  }
  list.forEach(url => addImageRow(url));
}

function readImages() {
  return Array.from($('#product-images').querySelectorAll('input'))
    .map(input => input.value.trim())
    .filter(Boolean);
}

/* --- optional numeric / JSON fields --------------------------------------- */

/* Empty means "no value", which for a nullable decimal is null — not ''. DRF
 * rejects '' on a DecimalField, and omitting the key would leave a previous
 * value in place, so clearing the input in the edit form has to send null. */
function optionalNumber(selector) {
  const raw = $(selector).value.trim();
  return raw === '' ? null : raw;
}

function readDimensions() {
  const dimensions = {};
  [['length', '#product-length'], ['width', '#product-width'], ['height', '#product-height']]
    .forEach(([key, selector]) => {
      const raw = $(selector).value.trim();
      if (raw !== '') dimensions[key] = Number(raw);
    });
  return dimensions;
}

function setDimensions(dimensions) {
  const values = dimensions || {};
  $('#product-length').value = values.length ?? '';
  $('#product-width').value = values.width ?? '';
  $('#product-height').value = values.height ?? '';
}

/* --- create / edit -------------------------------------------------------- */

function bindForms() {
  $('#new-product').addEventListener('click', () => {
    $('#product-form').reset();
    $('#product-id').value = '';
    $('#product-active').checked = true;
    // form.reset() cannot clear rows that were added to the DOM.
    setImages([]);
    $('#product-modal-title').textContent = 'New product';
    showError($('#product-error'), '');
    openModal('product-modal');
  });

  $('#add-image').addEventListener('click', () => addImageRow().focus());

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
    $('#product-cost').value = product.cost_price ?? '';
    $('#product-stock').value = product.stock ?? 0;
    $('#product-threshold').value = product.low_stock_threshold ?? 5;
    $('#product-category').value = product.category ?? '';
    $('#product-sku').value = product.sku || '';
    $('#product-barcode').value = product.barcode || '';
    $('#product-weight').value = product.weight ?? '';
    $('#product-tags').value = (product.tags || []).join(', ');
    setImages(product.images);
    setDimensions(product.dimensions);
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
    compare_price: optionalNumber('#product-compare'),
    cost_price: optionalNumber('#product-cost'),
    stock: Number($('#product-stock').value || 0),
    low_stock_threshold: Number($('#product-threshold').value || 0),
    category: $('#product-category').value || null,
    barcode: $('#product-barcode').value.trim(),
    weight: optionalNumber('#product-weight'),
    tags: $('#product-tags').value.split(',').map(tag => tag.trim()).filter(Boolean),
    images: readImages(),
    dimensions: readDimensions(),
    is_active: $('#product-active').checked,
    is_featured: $('#product-featured').checked,
  };

  // Blank means "generate one" on create, and "keep the existing one" on edit —
  // sending '' would ask the server to store an empty SKU instead.
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
      parent: $('#category-parent').value || null,
      icon: $('#category-icon').value.trim(),
      display_order: Number($('#category-order').value || 0),
    });
    closeModal('category-modal');
    await loadCategories();
    loadProducts();
  } catch (error) {
    showError($('#category-error'), error.message);
  }
}
