/* Shared API client.
 *
 * One authorization path for the whole frontend: a DRF token plus the
 * X-Business-ID tenant header, both held in localStorage. The dashboard sends
 * both; the public storefront sends only the business header.
 */

const TOKEN_KEY = 'wc.token';
const BUSINESS_KEY = 'wc.businessId';
const BUSINESS_NAME_KEY = 'wc.businessName';
const CURRENCY_KEY = 'wc.currency';
const USER_KEY = 'wc.user';

export const store = {
  get token() { return localStorage.getItem(TOKEN_KEY); },
  set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },

  get businessId() { return localStorage.getItem(BUSINESS_KEY); },
  set businessId(v) { v ? localStorage.setItem(BUSINESS_KEY, v) : localStorage.removeItem(BUSINESS_KEY); },

  get businessName() { return localStorage.getItem(BUSINESS_NAME_KEY) || ''; },
  set businessName(v) { v ? localStorage.setItem(BUSINESS_NAME_KEY, v) : localStorage.removeItem(BUSINESS_NAME_KEY); },

  get currency() { return localStorage.getItem(CURRENCY_KEY) || 'KSh'; },
  set currency(v) { v ? localStorage.setItem(CURRENCY_KEY, v) : localStorage.removeItem(CURRENCY_KEY); },

  get user() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  },
  set user(v) { v ? localStorage.setItem(USER_KEY, JSON.stringify(v)) : localStorage.removeItem(USER_KEY); },

  clear() {
    [TOKEN_KEY, BUSINESS_KEY, BUSINESS_NAME_KEY, CURRENCY_KEY, USER_KEY]
      .forEach(k => localStorage.removeItem(k));
  },
};

export class ApiError extends Error {
  constructor(status, payload) {
    super(ApiError.readable(payload, status));
    this.status = status;
    this.payload = payload;
  }

  /* DRF reports errors in several shapes; flatten them into one line. */
  static readable(payload, status) {
    if (!payload) return `Request failed (${status})`;
    if (typeof payload === 'string') return payload;
    if (payload.detail) return payload.detail;
    if (payload.error) return payload.error;
    if (Array.isArray(payload)) return payload.join(' ');

    const parts = [];
    for (const [field, value] of Object.entries(payload)) {
      const text = Array.isArray(value) ? value.join(' ') : String(value);
      parts.push(field === 'non_field_errors' ? text : `${field}: ${text}`);
    }
    return parts.join(' · ') || `Request failed (${status})`;
  }
}

async function request(path, { method = 'GET', body, business = true, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };

  if (auth && store.token) headers['Authorization'] = `Token ${store.token}`;
  if (business && store.businessId) headers['X-Business-ID'] = store.businessId;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && auth) {
    // Token expired or revoked - drop it and restart at login.
    store.clear();
    if (!location.pathname.startsWith('/login')) {
      location.href = `/login/?next=${encodeURIComponent(location.pathname)}`;
    }
    throw new ApiError(401, { detail: 'Your session has ended. Please sign in again.' });
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }

  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

export const api = {
  get: (p, o) => request(p, { ...o, method: 'GET' }),
  post: (p, body, o) => request(p, { ...o, method: 'POST', body }),
  patch: (p, body, o) => request(p, { ...o, method: 'PATCH', body }),
  put: (p, body, o) => request(p, { ...o, method: 'PUT', body }),
  delete: (p, o) => request(p, { ...o, method: 'DELETE' }),
};

/* Paginated list endpoints return {count, next, previous, results}; custom
 * actions return bare arrays. Normalise so callers never branch. */
export function rows(payload) {
  if (!payload) return [];
  return Array.isArray(payload) ? payload : (payload.results || []);
}

export function count(payload, fallbackRows) {
  if (payload && typeof payload.count === 'number') return payload.count;
  return (fallbackRows || rows(payload)).length;
}

/* ---- formatting ---------------------------------------------------------- */

export function currency(value, code) {
  const symbol = code || store.currency || 'KSh';
  const n = Number(value || 0);
  return `${symbol} ${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

/* Auto-compact for stat tiles: 1,284 / 12.9K / 4.2M */
export function compact(value) {
  const n = Number(value || 0);
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return n.toLocaleString();
}

export function dateTime(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

export function dateOnly(value) {
  if (!value) return '—';
  return new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

/* Status -> badge tone. Colour is paired with the label text in the badge, so
 * meaning is never carried by colour alone. */
const TONES = {
  PENDING: 'warning', CONFIRMED: 'neutral', PAID: 'good', PROCESSING: 'neutral',
  READY: 'neutral', OUT_FOR_DELIVERY: 'serious', DELIVERED: 'good',
  CANCELLED: 'critical', FAILED: 'critical', REFUNDED: 'critical',
  COMPLETED: 'good', ASSIGNED: 'neutral', PICKED_UP: 'neutral',
  IN_TRANSIT: 'serious', TIMEOUT: 'critical', ACTIVE: 'good', RESOLVED: 'neutral',
};

export function badge(status, label) {
  const tone = TONES[status] || 'neutral';
  const text = label || String(status || '').replace(/_/g, ' ').toLowerCase();
  return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
}

/* ---- small DOM helpers --------------------------------------------------- */

export const $ = sel => document.querySelector(sel);
export const $$ = sel => Array.from(document.querySelectorAll(sel));

export function showError(el, message) {
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
}

/* Hold the previous render at reduced opacity during a refetch - never a
 * skeleton flash, never a layout jump. */
export function busy(el, isBusy) {
  if (el) el.classList.toggle('loading', !!isBusy);
}

export function requireAuth() {
  if (!store.token) {
    location.href = `/login/?next=${encodeURIComponent(location.pathname)}`;
    return false;
  }
  return true;
}
