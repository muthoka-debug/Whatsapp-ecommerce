import {
  api, rows, store, $, currency, compact, dateTime, badge, escapeHtml, showError, busy,
} from './api.js';
import { trendChart, donutChart, sparkline, tableView, bindTableToggle } from './charts.js';
import { onBusinessReady } from './shell.js';

const SOURCE_LABELS = {
  WHATSAPP: 'WhatsApp', STOREFRONT: 'Storefront', MANUAL: 'Manual',
  PHONE: 'Phone', INSTORE: 'In-store',
};

const PERIOD_LABELS = { 7: 'Last 7 days', 30: 'Last 30 days', 365: 'Last 12 months' };

/* Latest revenue series, so the single resize handler always redraws what is
 * currently on screen rather than whatever period it was created with. */
let revenueSeries = [];

onBusinessReady(() => {
  loadSummary();
  loadRevenue();
  loadSources();
  loadTopProducts();
  loadLowStock();
  bindRangeSwitch();
});

/* Week / Month / Year. Each refetches at a different period rather than
 * re-slicing what is already loaded, so the totals in the subtitle stay true
 * to the range on screen. */
function bindRangeSwitch() {
  const group = $('#revenue-range');
  if (!group) return;
  group.querySelectorAll('button').forEach(button => {
    button.addEventListener('click', () => {
      if (button.classList.contains('current')) return;
      group.querySelectorAll('button').forEach(b => {
        b.classList.remove('current');
        b.removeAttribute('aria-pressed');
      });
      button.classList.add('current');
      button.setAttribute('aria-pressed', 'true');
      loadRevenue(Number(button.dataset.period));
    });
  });
}

/* --- KPI row -------------------------------------------------------------- */

async function loadSummary() {
  const row = $('#kpi-row');
  busy(row, true);
  try {
    const data = await api.get('/api/v1/analytics/dashboard/summary/');

    $('#kpi-revenue').textContent = currency(data.today.revenue);
    $('#kpi-orders').textContent = compact(data.today.orders);
    $('#kpi-customers').textContent = compact(data.customers.total);
    $('#kpi-pending').textContent = compact(data.pending.payments);

    renderDelta($('#kpi-revenue-delta'), data.today.revenue_growth, 'vs yesterday');
    renderDelta($('#kpi-orders-delta'), data.today.order_growth, 'vs yesterday');

    $('#kpi-customers-sub').textContent =
      `${compact(data.inventory.total_products)} active products`;
    $('#kpi-pending-sub').textContent =
      `${compact(data.pending.orders)} orders awaiting action`;

    renderRecentOrders(data.recent_orders || []);

    if (data.inventory.low_stock > 0) {
      $('#kpi-customers-sub').textContent +=
        ` · ${data.inventory.low_stock} low stock`;
    }
  } catch (error) {
    showError($('#page-error'), error.message);
    $('#recent-orders').innerHTML =
      '<tr><td colspan="5" class="empty">Could not load orders.</td></tr>';
  } finally {
    busy(row, false);
  }
}

/* Signed delta against a named period. Colour is direction x whether up is
 * good, and the sign is always spelled out so colour is not the only cue. */
function renderDelta(el, growth, period) {
  if (!el) return;
  const value = Number(growth || 0);
  if (!value) {
    el.innerHTML = `<span class="muted">No change ${escapeHtml(period)}</span>`;
    return;
  }
  const cls = value > 0 ? 'up' : 'down';
  const arrow = value > 0 ? '↑' : '↓';
  el.innerHTML =
    `<span class="${cls}">${arrow} ${Math.abs(value).toFixed(1)}%</span> ` +
    `<span class="muted">${escapeHtml(period)}</span>`;
}

function renderRecentOrders(orders) {
  const body = $('#recent-orders');
  if (!orders.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">No orders yet.</td></tr>';
    return;
  }
  body.innerHTML = orders.slice(0, 8).map(order => {
    const name = order.customer_details?.name || '—';
    return `
    <tr class="clickable" data-id="${order.id}">
      <td class="nowrap">${escapeHtml(order.order_number)}</td>
      <td>
        <span class="cell-user">
          <span class="avatar">${escapeHtml(name.trim().charAt(0) || '?')}</span>
          <span>${escapeHtml(name)}</span>
        </span>
      </td>
      <td>${badge(order.status, order.status_display)}</td>
      <td class="nowrap muted">${escapeHtml(dateTime(order.placed_at))}</td>
      <td class="num">${escapeHtml(currency(order.total_amount))}</td>
    </tr>`;
  }).join('');

  body.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', () => {
      location.href = `/dashboard/orders/?order=${tr.dataset.id}`;
    });
  });
}

/* --- Revenue trend -------------------------------------------------------- */

async function loadRevenue(period = 30) {
  const chartEl = $('#revenue-chart');
  const tableEl = $('#revenue-table');
  busy(chartEl, true);

  try {
    const data = await api.get(`/api/v1/analytics/dashboard/revenue_chart/?period=${period}`);
    const series = (data.data || []).map(point => ({
      label: shortDate(point.date),
      tipLabel: longDate(point.date),
      value: Number(point.revenue || 0),
      orders: point.orders,
    }));

    $('#revenue-sub').textContent =
      `${PERIOD_LABELS[period] || `Last ${period} days`} · ` +
      `${currency(data.total_revenue)} from ${compact(data.total_orders)} orders`;

    trendChart(chartEl, series, { formatValue: v => currency(v) });

    // Tile waves plot the same real series - no decorative squiggles.
    sparkline($('#kpi-revenue-spark'), series.map(p => p.value));
    sparkline($('#kpi-orders-spark'), series.map(p => Number(p.orders || 0)));

    // Table-view twin: every plotted value readable without hovering.
    tableView(
      tableEl,
      ['Date', 'Orders', 'Revenue'],
      series.map(p => [p.tipLabel, String(p.orders), currency(p.value)]),
    );
    bindTableToggle($('#revenue-toggle'), chartEl, tableEl);

    // Re-render on resize so the SVG matches its container width. Registered
    // once and reading the latest series, so switching period does not stack
    // handlers that would redraw stale data.
    revenueSeries = series;
    onResize('revenue', () => trendChart(chartEl, revenueSeries, { formatValue: v => currency(v) }));
  } catch (error) {
    chartEl.innerHTML = `<div class="empty">Could not load revenue: ${escapeHtml(error.message)}</div>`;
  } finally {
    busy(chartEl, false);
  }
}

/* --- Sales by channel ----------------------------------------------------- */

async function loadSources() {
  const chartEl = $('#source-chart');
  const tableEl = $('#source-table');
  busy(chartEl, true);

  try {
    const data = await api.get('/api/v1/analytics/dashboard/sales_by_source/');
    const items = rows(data)
      .map(row => ({
        label: SOURCE_LABELS[row.source] || row.source,
        value: Number(row.total_revenue || 0),
        orders: row.total_orders,
      }))
      .sort((a, b) => b.value - a.value);

    donutChart(chartEl, items, { formatValue: v => currency(v) });
    tableView(
      tableEl,
      ['Channel', 'Orders', 'Revenue'],
      items.map(i => [i.label, String(i.orders), currency(i.value)]),
    );
    bindTableToggle($('#source-toggle'), chartEl, tableEl);
  } catch (error) {
    chartEl.innerHTML = `<div class="empty">Could not load channels: ${escapeHtml(error.message)}</div>`;
  } finally {
    busy(chartEl, false);
  }
}

/* --- Top products ---------------------------------------------------------- */

async function loadTopProducts() {
  const list = $('#top-products');
  if (!list) return;
  try {
    const items = rows(await api.get('/api/v1/analytics/dashboard/top_products/?limit=6'));
    list.innerHTML = items.length
      ? items.map(item => {
          const name = item.product__name || `#${item.product}`;
          return `
            <li class="rank-item">
              <span class="avatar">${escapeHtml(name.trim().charAt(0) || '?')}</span>
              <span class="rank-name">${escapeHtml(name)}</span>
              <span class="rank-count">${escapeHtml(compact(item.total_sold || 0))}</span>
            </li>`;
        }).join('')
      : '<li class="empty">No sales recorded yet.</li>';
  } catch {
    list.innerHTML = '<li class="empty">Could not load top products.</li>';
  }
}

/* --- Low stock ------------------------------------------------------------ */

async function loadLowStock() {
  const body = $('#low-stock');
  try {
    const products = rows(await api.get('/api/v1/products/?ordering=stock'));
    const low = products.filter(p => p.is_low_stock).slice(0, 8);

    body.innerHTML = low.length
      ? low.map(p => `
          <tr>
            <td>${escapeHtml(p.name)}</td>
            <td class="num">${escapeHtml(String(p.stock))}</td>
            <td class="num muted">${escapeHtml(String(p.low_stock_threshold))}</td>
          </tr>`).join('')
      : '<tr><td colspan="3" class="empty">Every product is above its threshold.</td></tr>';
  } catch {
    body.innerHTML = '<tr><td colspan="3" class="empty">Could not load stock levels.</td></tr>';
  }
}

/* --- helpers -------------------------------------------------------------- */

function shortDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function longDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });
}

/* Keyed so a chart that reloads (period switch) replaces its handler instead of
 * adding another one. */
const resizeHandlers = new Map();
let resizeBound = false;
function onResize(key, handler) {
  resizeHandlers.set(key, handler);
  if (resizeBound) return;
  resizeBound = true;
  let timer = null;
  window.addEventListener('resize', () => {
    clearTimeout(timer);
    timer = setTimeout(() => resizeHandlers.forEach(fn => fn()), 160);
  });
}
