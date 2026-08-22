/* Automation page.
 *
 * Answers one question: did the system do what it was supposed to, and if not,
 * what is stuck. The tiles are read top-left first, so the stranded count —
 * the only figure that implies a dead worker or broker — sits there.
 */

import {
  api, rows, count, $, badge, escapeHtml, showError, busy, dateTime,
} from './api.js';
import { bindModals, openModal, closeModal } from './modal.js';
import { onBusinessReady } from './shell.js';

const PAGE_SIZE = 25;
let page = 1;

/* Retrying re-runs every handler, so it is offered only where the work
 * plausibly never happened. A SUCCESS run is refused by the API too. */
const RETRYABLE = new Set(['FAILED', 'PENDING']);

bindModals();
onBusinessReady(() => {
  loadEventFilter();
  refresh();

  $('#filter-status').addEventListener('change', () => { page = 1; loadRuns(); });
  $('#filter-event').addEventListener('change', () => { page = 1; loadRuns(); });
  $('#filter-stuck').addEventListener('change', () => { page = 1; loadRuns(); });
  $('#filter-window').addEventListener('change', () => { page = 1; refresh(); });
});

function refresh() {
  loadSummary();
  loadRuns();
}

function windowHours() {
  return $('#filter-window').value || '24';
}

async function loadEventFilter() {
  try {
    const events = await api.get('/api/v1/automation/runs/events/');
    const select = $('#filter-event');
    select.insertAdjacentHTML('beforeend', events.map(e => `
      <option value="${escapeHtml(e.value)}">${escapeHtml(e.label)}</option>
    `).join(''));
  } catch {
    // A missing filter is a degraded page, not a broken one — the table below
    // still loads, so this failure stays quiet.
  }
}

async function loadSummary() {
  const tiles = $('#automation-kpis');
  busy(tiles, true);
  try {
    const data = await api.get(`/api/v1/automation/runs/summary/?hours=${windowHours()}`);

    $('#kpi-stuck').textContent = data.stuck ?? 0;
    $('#kpi-failed').textContent = data.failed ?? 0;
    $('#kpi-success').textContent = data.by_status?.SUCCESS ?? 0;
    $('#kpi-total').textContent = data.total ?? 0;
    $('#kpi-window').textContent = windowLabel(data.window_hours);

    renderEvents(data.by_event || []);
  } catch (error) {
    showError($('#page-error'), error.message);
  } finally {
    busy(tiles, false);
  }
}

function windowLabel(hours) {
  const h = Number(hours || 24);
  if (h <= 24) return 'Last 24 hours';
  return `Last ${Math.round(h / 24)} days`;
}

function renderEvents(events) {
  const body = $('#event-rows');
  body.innerHTML = events.length
    ? events.map(e => `
        <tr>
          <td>${escapeHtml(readable(e.event))}</td>
          <td class="num">${e.n}</td>
          <td class="num">${e.failed ? `<span class="badge critical">${e.failed}</span>` : '—'}</td>
        </tr>`).join('')
    : '<tr><td colspan="3" class="empty">Nothing has fired in this window.</td></tr>';
}

async function loadRuns() {
  const body = $('#run-rows');
  busy(body, true);

  const params = new URLSearchParams({ page: String(page), hours: windowHours() });
  const status = $('#filter-status').value;
  const event = $('#filter-event').value;
  if (status) params.set('status', status);
  if (event) params.set('event', event);
  if ($('#filter-stuck').checked) params.set('stuck', 'true');

  try {
    const payload = await api.get(`/api/v1/automation/runs/?${params}`);
    const items = rows(payload);

    body.innerHTML = items.length
      ? items.map(run => `
          <tr class="clickable" data-id="${run.id}">
            <td>${escapeHtml(readable(run.event_display || run.event))}</td>
            <td class="muted">${escapeHtml(subject(run))}</td>
            <td>${badge(run.status, run.status_display)}</td>
            <td class="num muted">${escapeHtml(took(run))}</td>
            <td class="nowrap muted">${escapeHtml(dateTime(run.created_at))}</td>
            <td class="right"><button class="btn btn-sm" type="button">Inspect</button></td>
          </tr>`).join('')
      : '<tr><td colspan="6" class="empty">No runs match.</td></tr>';

    body.querySelectorAll('tr.clickable').forEach(tr => {
      tr.addEventListener('click', () => showRun(tr.dataset.id));
    });

    renderPager(count(payload, items));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    busy(body, false);
  }
}

function subject(run) {
  if (!run.subject_type) return '—';
  // The id is a UUID for most models; the tail is enough to match against a
  // row on another page, and the full value is in the detail panel.
  const id = String(run.subject_id || '');
  return `${run.subject_type} ${id.length > 8 ? `…${id.slice(-8)}` : id}`;
}

function took(run) {
  if (run.duration_ms === null || run.duration_ms === undefined) return '—';
  return run.duration_ms < 1000
    ? `${run.duration_ms} ms`
    : `${(run.duration_ms / 1000).toFixed(1)} s`;
}

function readable(value) {
  return String(value || '').replace(/_/g, ' ').toLowerCase()
    .replace(/^./, ch => ch.toUpperCase());
}

function renderPager(total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pager = $('#run-pager');
  if (pages <= 1) { pager.innerHTML = ''; return; }

  pager.innerHTML = `
    <span class="muted">${total} run${total === 1 ? '' : 's'} · page ${page} of ${pages}</span>
    <button class="btn btn-sm" id="prev" ${page <= 1 ? 'disabled' : ''} type="button">Previous</button>
    <button class="btn btn-sm" id="next" ${page >= pages ? 'disabled' : ''} type="button">Next</button>`;
  $('#prev')?.addEventListener('click', () => { page -= 1; loadRuns(); });
  $('#next')?.addEventListener('click', () => { page += 1; loadRuns(); });
}

async function showRun(id) {
  showError($('#run-error'), '');
  $('#run-detail').innerHTML = '<div class="empty">Loading…</div>';
  openModal('run-modal');

  try {
    const run = await api.get(`/api/v1/automation/runs/${id}/`);
    $('#run-title').textContent = readable(run.event_display || run.event);
    $('#run-detail').innerHTML = renderDetail(run);
    $('#run-detail').querySelector('[data-retry]')
      ?.addEventListener('click', event => retry(run, event.currentTarget));
  } catch (error) {
    $('#run-detail').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderDetail(run) {
  const actions = (run.actions || []).map(a => a.split('.').pop());

  return `
    <div class="kv-list">
      <div class="kv"><span class="kv-key">Status</span><span class="kv-value">${badge(run.status, run.status_display)}</span></div>
      <div class="kv"><span class="kv-key">Subject</span><span class="kv-value">${escapeHtml(run.subject_type || '—')} ${escapeHtml(run.subject_id || '')}</span></div>
      <div class="kv"><span class="kv-key">Recorded</span><span class="kv-value">${escapeHtml(dateTime(run.created_at))}</span></div>
      <div class="kv"><span class="kv-key">Completed</span><span class="kv-value">${escapeHtml(run.completed_at ? dateTime(run.completed_at) : 'Not yet')}</span></div>
      <div class="kv"><span class="kv-key">Took</span><span class="kv-value">${escapeHtml(took(run))}</span></div>
      ${run.idempotency_key ? `<div class="kv"><span class="kv-key">Key</span><span class="kv-value">${escapeHtml(run.idempotency_key)}</span></div>` : ''}
    </div>

    <div class="detail-block" style="margin-top:14px;">
      <div class="field-label">Actions taken</div>
      <div class="field-value">${actions.length ? escapeHtml(actions.join(', ')) : '—'}</div>
    </div>

    ${Object.keys(run.payload || {}).length ? `
      <div class="detail-block" style="margin-top:14px;">
        <div class="field-label">Payload</div>
        <pre class="field-value" style="white-space:pre-wrap;">${escapeHtml(JSON.stringify(run.payload, null, 2))}</pre>
      </div>` : ''}

    ${run.error ? `
      <div class="detail-block" style="margin-top:14px;">
        <div class="field-label">Error</div>
        <pre class="field-value" style="white-space:pre-wrap;">${escapeHtml(run.error)}</pre>
      </div>` : ''}

    ${RETRYABLE.has(run.status) ? `
      <div class="modal-actions" style="justify-content:flex-start;">
        <button class="btn btn-sm btn-primary" data-retry type="button">Run again</button>
      </div>
      <p class="note">Re-runs every handler for this event. If the customer already
         received the message, they will receive it a second time.</p>`
    : `<p class="note">A ${escapeHtml((run.status_display || run.status).toLowerCase())} run cannot be re-queued.</p>`}`;
}

async function retry(run, button) {
  if (!confirm('Run this automation again?')) return;

  button.disabled = true;
  showError($('#run-error'), '');
  try {
    await api.post(`/api/v1/automation/runs/${run.id}/retry/`, {});
    closeModal('run-modal');
    refresh();
  } catch (error) {
    showError($('#run-error'), error.message);
    button.disabled = false;
  }
}
