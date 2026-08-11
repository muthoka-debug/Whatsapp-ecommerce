import {
  api, rows, count, $, currency, badge, escapeHtml, showError, busy,
} from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';
import { onBusinessReady } from './shell.js';

const PAGE_SIZE = 25;
let page = 1;

/* Transitions offered per status, mirroring Delivery's model methods.
 * Terminal statuses offer nothing. */
const TRANSITIONS = {
  PENDING: [['assign', 'Assign'], ['cancel', 'Cancel']],
  ASSIGNED: [['mark_picked_up', 'Mark picked up'], ['mark_failed', 'Mark failed'], ['cancel', 'Cancel']],
  PICKED_UP: [['mark_in_transit', 'Mark in transit'], ['mark_failed', 'Mark failed']],
  IN_TRANSIT: [['mark_delivered', 'Mark delivered'], ['mark_failed', 'Mark failed']],
  DELIVERED: [],
  FAILED: [],
  CANCELLED: [],
};

bindModals();
onBusinessReady(() => {
  loadSummary();
  loadDeliveries();
  $('#filter-status').addEventListener('change', () => { page = 1; loadDeliveries(); });
  $('#filter-active').addEventListener('change', () => { page = 1; loadDeliveries(); });
});

async function loadSummary() {
  const row = $('#delivery-kpis');
  busy(row, true);
  try {
    const data = await api.get('/api/v1/deliveries/summary/');
    $('#kpi-active').textContent = data.active ?? 0;
    $('#kpi-pending').textContent = data.by_status?.PENDING ?? 0;
    $('#kpi-transit').textContent = data.by_status?.IN_TRANSIT ?? 0;
    $('#kpi-fees').textContent = currency(data.fees_collected);
  } catch (error) {
    showError($('#page-error'), error.message);
  } finally {
    busy(row, false);
  }
}

async function loadDeliveries() {
  const body = $('#delivery-rows');
  busy(body, true);

  const params = new URLSearchParams({ page: String(page) });
  const status = $('#filter-status').value;
  if (status) params.set('status', status);
  else if ($('#filter-active').checked) params.set('active', 'true');

  try {
    const payload = await api.get(`/api/v1/deliveries/?${params}`);
    const items = rows(payload);

    body.innerHTML = items.length
      ? items.map(d => `
          <tr class="clickable" data-id="${d.id}">
            <td class="nowrap">${escapeHtml(d.order_number || '—')}</td>
            <td>${escapeHtml(d.recipient_name || d.customer_name || '—')}</td>
            <td class="muted">${escapeHtml(truncate(d.address, 34))}</td>
            <td class="muted">${escapeHtml(d.method_display || d.method)}</td>
            <td>${badge(d.status, d.status_display)}</td>
            <td class="num">${escapeHtml(currency(d.fee))}</td>
            <td class="right"><button class="btn btn-sm" type="button">Manage</button></td>
          </tr>`).join('')
      : '<tr><td colspan="7" class="empty">No deliveries match.</td></tr>';

    body.querySelectorAll('tr.clickable').forEach(tr => {
      tr.addEventListener('click', () => showDelivery(tr.dataset.id));
    });

    renderPager(count(payload, items));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    busy(body, false);
  }
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#delivery-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">${total} deliver${total === 1 ? 'y' : 'ies'} · page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;
  $('#prev')?.addEventListener('click', () => { page -= 1; loadDeliveries(); });
  $('#next')?.addEventListener('click', () => { page += 1; loadDeliveries(); });
}

async function showDelivery(id) {
  showError($('#delivery-error'), '');
  $('#delivery-detail').innerHTML = '<div class="empty">Loading…</div>';
  openModal('delivery-modal');

  try {
    const delivery = await api.get(`/api/v1/deliveries/${id}/`);
    $('#delivery-title').textContent = `Delivery · ${delivery.order_number || ''}`;
    $('#delivery-detail').innerHTML = renderDetail(delivery);
    bindActions(delivery);
  } catch (error) {
    $('#delivery-detail').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderDetail(delivery) {
  const moves = TRANSITIONS[delivery.status] || [];

  const buttons = moves.length
    ? moves.map(([action, label]) => `
        <button class="btn btn-sm ${action === 'cancel' || action === 'mark_failed' ? 'btn-danger' : 'btn-primary'}"
                data-action="${action}" type="button">${label}</button>`).join(' ')
    : `<p class="note">
         This delivery is ${escapeHtml((delivery.status_display || '').toLowerCase())} — nothing further to do.
       </p>`;

  return `
    <div class="kv-list">
      <div class="kv"><span class="kv-key">Status</span><span class="kv-value">${badge(delivery.status, delivery.status_display)}</span></div>
      <div class="kv"><span class="kv-key">Recipient</span><span class="kv-value">${escapeHtml(delivery.recipient_name || '—')}</span></div>
      <div class="kv"><span class="kv-key">Phone</span><span class="kv-value">${escapeHtml(delivery.phone || '—')}</span></div>
      <div class="kv"><span class="kv-key">Method</span><span class="kv-value">${escapeHtml(delivery.method_display || delivery.method)}</span></div>
      <div class="kv"><span class="kv-key">Fee</span><span class="kv-value">${escapeHtml(currency(delivery.fee))}</span></div>
      ${delivery.assigned_to_name ? `<div class="kv"><span class="kv-key">Assigned to</span><span class="kv-value">${escapeHtml(delivery.assigned_to_name)}</span></div>` : ''}
      ${delivery.courier_name ? `<div class="kv"><span class="kv-key">Courier</span><span class="kv-value">${escapeHtml(delivery.courier_name)}</span></div>` : ''}
      ${delivery.tracking_number ? `<div class="kv"><span class="kv-key">Tracking</span><span class="kv-value">${escapeHtml(delivery.tracking_number)}</span></div>` : ''}
      ${delivery.failure_reason ? `<div class="kv"><span class="kv-key">Reason</span><span class="kv-value">${escapeHtml(delivery.failure_reason)}</span></div>` : ''}
    </div>

    <div class="detail-block" style="margin-top:14px;">
      <div class="field-label">Address</div>
      <div class="field-value">${escapeHtml(delivery.address || '—')}</div>
    </div>

    <div id="action-extra" style="margin-top:14px;"></div>
    <div class="modal-actions" style="justify-content:flex-start;flex-wrap:wrap;">${buttons}</div>`;
}

function bindActions(delivery) {
  document.querySelectorAll('#delivery-detail [data-action]').forEach(button => {
    button.addEventListener('click', () => runAction(delivery, button));
  });
}

async function runAction(delivery, button) {
  const action = button.dataset.action;
  let body = {};

  // assign and mark_failed need input; collect it inline rather than in a
  // second modal stacked on this one.
  if (action === 'assign') {
    const courier = prompt('Courier name (leave blank for an in-house rider):', delivery.courier_name || '');
    if (courier === null) return;
    const tracking = prompt('Tracking number (optional):', delivery.tracking_number || '');
    if (tracking === null) return;
    if (!courier.trim() && !tracking.trim()) {
      showError($('#delivery-error'), 'Provide a courier name or a tracking number.');
      return;
    }
    body = { courier_name: courier.trim(), tracking_number: tracking.trim() };
  }

  if (action === 'mark_failed') {
    const reason = prompt('Why did the delivery fail?');
    if (reason === null) return;
    if (!reason.trim()) {
      showError($('#delivery-error'), 'A reason is required to mark a delivery failed.');
      return;
    }
    body = { reason: reason.trim() };
  }

  if (action === 'cancel') {
    if (!confirm('Cancel this delivery?')) return;
    body = { reason: 'Cancelled from the dashboard' };
  }

  button.disabled = true;
  showError($('#delivery-error'), '');
  try {
    await api.post(`/api/v1/deliveries/${delivery.id}/${action}/`, body);
    closeModal('delivery-modal');
    loadSummary();
    loadDeliveries();
  } catch (error) {
    showError($('#delivery-error'), error.message);
    button.disabled = false;
  }
}

function truncate(value, max) {
  const text = String(value || '');
  return text.length > max ? `${text.slice(0, max - 1)}…` : text || '—';
}
