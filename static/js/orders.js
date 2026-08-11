import {
  api, rows, count, $, currency, dateTime, badge, escapeHtml, showError, busy,
} from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';
import { onBusinessReady } from './shell.js';

const PAGE_SIZE = 25;
let page = 1;

/* Which statuses a user can move an order to next. Cancelled and delivered are
 * terminal; delivery-driven states are set by the deliveries board, not here. */
const NEXT_STATUS = {
  PENDING: ['CONFIRMED', 'CANCELLED'],
  CONFIRMED: ['PAID', 'PROCESSING', 'CANCELLED'],
  PAID: ['PROCESSING', 'CANCELLED'],
  PROCESSING: ['READY', 'CANCELLED'],
  READY: ['OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED'],
  OUT_FOR_DELIVERY: ['DELIVERED'],
  DELIVERED: [],
  CANCELLED: [],
};

const STATUS_LABELS = {
  PENDING: 'Pending', CONFIRMED: 'Confirmed', PAID: 'Paid', PROCESSING: 'Processing',
  READY: 'Ready', OUT_FOR_DELIVERY: 'Out for delivery', DELIVERED: 'Delivered',
  CANCELLED: 'Cancelled',
};

bindModals();
onBusinessReady(() => {
  bindFilters();
  loadOrders().then(openFromQueryString);
});

function query() {
  const params = new URLSearchParams({ page: String(page) });
  const status = $('#filter-status').value;
  const start = $('#filter-start').value;
  const end = $('#filter-end').value;
  if (status) params.set('status', status);
  if (start) params.set('start_date', start);
  if (end) params.set('end_date', end);
  return params.toString();
}

async function loadOrders() {
  const body = $('#order-rows');
  busy(body, true);
  try {
    const payload = await api.get(`/api/v1/orders/?${query()}`);
    const items = rows(payload);

    body.innerHTML = items.length
      ? items.map(order => `
          <tr class="clickable" data-id="${order.id}">
            <td class="nowrap">${escapeHtml(order.order_number)}</td>
            <td>${escapeHtml(order.customer_details?.name || '—')}</td>
            <td class="muted">${escapeHtml(order.source_display || order.source)}</td>
            <td>${badge(order.status, order.status_display)}</td>
            <td class="nowrap muted">${escapeHtml(dateTime(order.placed_at))}</td>
            <td class="num">${escapeHtml(currency(order.total_amount))}</td>
          </tr>`).join('')
      : '<tr><td colspan="6" class="empty">No orders match these filters.</td></tr>';

    body.querySelectorAll('tr.clickable').forEach(tr => {
      tr.addEventListener('click', () => showOrder(tr.dataset.id));
    });

    renderPager(count(payload, items));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    busy(body, false);
  }
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#order-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">${total} order${total === 1 ? '' : 's'} · page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;
  $('#prev')?.addEventListener('click', () => { page -= 1; loadOrders(); });
  $('#next')?.addEventListener('click', () => { page += 1; loadOrders(); });
}

function bindFilters() {
  ['#filter-status', '#filter-start', '#filter-end'].forEach(sel => {
    $(sel).addEventListener('change', () => { page = 1; loadOrders(); });
  });
  $('#clear-filters').addEventListener('click', () => {
    $('#filter-status').value = '';
    $('#filter-start').value = '';
    $('#filter-end').value = '';
    page = 1;
    loadOrders();
  });
}

/* --- detail --------------------------------------------------------------- */

async function showOrder(id) {
  showError($('#order-error'), '');
  $('#order-detail').innerHTML = '<div class="empty">Loading…</div>';
  openModal('order-modal');

  try {
    const order = await api.get(`/api/v1/orders/${id}/`);
    $('#order-title').textContent = `Order ${order.order_number}`;
    $('#order-detail').innerHTML = renderDetail(order);
    bindDetailActions(order);
  } catch (error) {
    $('#order-detail').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderDetail(order) {
  const items = (order.items || []).map(item => `
    <tr>
      <td>${escapeHtml(item.product_details?.name || `#${item.product}`)}</td>
      <td class="num">${escapeHtml(String(item.quantity))}</td>
      <td class="num">${escapeHtml(currency(item.price))}</td>
      <td class="num">${escapeHtml(currency(item.total))}</td>
    </tr>`).join('');

  const delivery = order.delivery
    ? `<div class="detail-block">
         <div class="field-label">Delivery</div>
         <div class="field-value">${badge(order.delivery.status, order.delivery.status_display)}</div>
         <div class="field-note">
           ${escapeHtml(order.delivery.address || '—')}<br>
           ${escapeHtml(order.delivery.phone || '')}
           ${order.delivery.tracking_number ? `<br>Tracking: ${escapeHtml(order.delivery.tracking_number)}` : ''}
         </div>
       </div>`
    : '<p class="note">No delivery record for this order.</p>';

  const options = (NEXT_STATUS[order.status] || [])
    .map(s => `<option value="${s}">${STATUS_LABELS[s]}</option>`).join('');

  const actions = options
    ? `<div class="toolbar" style="margin:16px 0 0;">
         <div class="field">
           <label for="next-status">Move to</label>
           <select id="next-status">${options}</select>
         </div>
         <button class="btn btn-primary btn-sm" id="apply-status" type="button">Update status</button>
       </div>`
    : `<p class="note">
         This order is ${escapeHtml((order.status_display || order.status).toLowerCase())} — no further changes.
       </p>`;

  return `
    <div class="detail-grid">
      <div class="detail-block">
        <div class="field-label">Customer</div>
        <div class="field-value">${escapeHtml(order.customer_details?.name || '—')}</div>
        <div class="field-note">${escapeHtml(order.customer_details?.phone || '')}</div>
      </div>
      <div class="detail-block">
        <div class="field-label">Status</div>
        <div class="field-value">${badge(order.status, order.status_display)}</div>
        <div class="field-note">Placed ${escapeHtml(dateTime(order.placed_at))}</div>
      </div>
    </div>

    <div class="section-label">Items</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Total</th></tr></thead>
        <tbody>${items || '<tr><td colspan="4" class="empty">No line items.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="kv-list" style="margin-top:12px;">
      <div class="kv"><span class="kv-key">Subtotal</span><span class="kv-value">${escapeHtml(currency(order.subtotal))}</span></div>
      <div class="kv"><span class="kv-key">Tax</span><span class="kv-value">${escapeHtml(currency(order.tax))}</span></div>
      <div class="kv"><span class="kv-key">Delivery</span><span class="kv-value">${escapeHtml(currency(order.shipping_fee))}</span></div>
      <div class="kv"><span class="kv-key">Discount</span><span class="kv-value">−${escapeHtml(currency(order.discount))}</span></div>
      <div class="kv total"><span class="kv-key">Total</span><span class="kv-value">${escapeHtml(currency(order.total_amount))}</span></div>
    </div>

    <div class="section-label">Fulfilment</div>
    ${delivery}
    ${actions}`;
}

function bindDetailActions(order) {
  $('#apply-status')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    const next = $('#next-status').value;
    button.disabled = true;
    showError($('#order-error'), '');
    try {
      await api.post(`/api/v1/orders/${order.id}/update_status/`, { status: next });
      closeModal('order-modal');
      loadOrders();
    } catch (error) {
      showError($('#order-error'), error.message);
      button.disabled = false;
    }
  });
}

/* Deep link from the dashboard's recent-orders table: ?order=<id> */
function openFromQueryString() {
  const id = new URLSearchParams(location.search).get('order');
  if (id) showOrder(id);
}
