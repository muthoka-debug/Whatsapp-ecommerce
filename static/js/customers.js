import {
  api, rows, count, $, currency, dateOnly, escapeHtml, showError, busy,
} from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';
import { onBusinessReady } from './shell.js';

const PAGE_SIZE = 25;
let page = 1;

bindModals();
onBusinessReady(() => {
  // The top bar can hand off here with ?q=<term>.
  const q = new URLSearchParams(location.search).get('q');
  if (q) $('#filter-search').value = q;

  loadCustomers();

  let timer = null;
  $('#filter-search').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { page = 1; loadCustomers(); }, 280);
  });

  $('#new-customer').addEventListener('click', () => {
    $('#customer-form').reset();
    showError($('#customer-error'), '');
    openModal('customer-modal');
  });

  $('#customer-form').addEventListener('submit', saveCustomer);
});

async function loadCustomers() {
  const body = $('#customer-rows');
  busy(body, true);

  const params = new URLSearchParams({ page: String(page) });
  const search = $('#filter-search').value.trim();
  if (search) params.set('search', search);

  try {
    const payload = await api.get(`/api/v1/customers/?${params}`);
    const items = rows(payload);

    body.innerHTML = items.length
      ? items.map(customer => `
          <tr>
            <td>${escapeHtml(customer.name)}</td>
            <td class="nowrap">${escapeHtml(customer.phone || '—')}</td>
            <td class="muted">${escapeHtml(customer.email || '—')}</td>
            <td class="num">${escapeHtml(String(customer.total_orders ?? 0))}</td>
            <td class="num">${escapeHtml(currency(customer.total_spent))}</td>
            <td class="muted nowrap">${escapeHtml(dateOnly(customer.created_at))}</td>
          </tr>`).join('')
      : '<tr><td colspan="6" class="empty">No customers yet.</td></tr>';

    renderPager(count(payload, items));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    busy(body, false);
  }
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#customer-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">${total} customer${total === 1 ? '' : 's'} · page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;
  $('#prev')?.addEventListener('click', () => { page -= 1; loadCustomers(); });
  $('#next')?.addEventListener('click', () => { page += 1; loadCustomers(); });
}

async function saveCustomer(event) {
  event.preventDefault();
  try {
    await api.post('/api/v1/customers/', {
      name: $('#customer-name').value.trim(),
      phone: $('#customer-phone').value.trim(),
      email: $('#customer-email').value.trim() || null,
      address: $('#customer-address').value.trim(),
    });
    closeModal('customer-modal');
    page = 1;
    loadCustomers();
  } catch (error) {
    showError($('#customer-error'), error.message);
  }
}
