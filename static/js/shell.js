/* Dashboard chrome: guards the page, fills the business switcher, signs out.
 *
 * Every dashboard page loads this before its own module. It dispatches
 * `business-ready` on document once a tenant is selected, which page modules
 * wait for rather than racing it.
 */

import { api, rows, store, $, requireAuth } from './api.js';

/* Resolves once a validated tenant is active. Page modules await this rather
 * than reading store.businessId directly: a stale id left in localStorage (the
 * user was removed from that business, or it was deleted) would otherwise let a
 * page fire its requests against the wrong tenant before the switcher corrects
 * it. If no business can be selected the promise never resolves and the page
 * stays empty behind the error - which is the right outcome. */
let markReady;
export const businessReady = new Promise(resolve => { markReady = resolve; });

if (requireAuth()) {
  initTopbar();
  initShell();
}

/* Top bar: account chip from the stored login, a search that drives whatever
 * filter the current page owns, and a bell whose count is real work waiting -
 * orders that still need action. Nothing here is ornamental. */
function initTopbar() {
  const user = store.user || {};
  const label = [user.first_name, user.last_name].filter(Boolean).join(' ')
    || user.username || 'Signed in';
  const nameEl = $('#user-name');
  if (nameEl) nameEl.textContent = label;
  const subEl = $('#user-sub');
  if (subEl) subEl.textContent = user.email || (user.username ? `@${user.username}` : '');
  const initialEl = $('#user-initial');
  if (initialEl) initialEl.textContent = label.trim().charAt(0).toUpperCase() || '·';

  const form = $('#topsearch');
  const input = $('#topsearch-input');
  // Pages that own a search box get driven in place; the rest hand off to
  // Products, which seeds itself from ?q=.
  const pageSearch = document.getElementById('filter-search');
  if (pageSearch) input?.setAttribute('placeholder', 'Search this page');

  form?.addEventListener('submit', event => {
    event.preventDefault();
    const term = input.value.trim();
    if (pageSearch) {
      pageSearch.value = term;
      pageSearch.dispatchEvent(new Event('input', { bubbles: true }));
      pageSearch.focus();
      return;
    }
    location.href = `/dashboard/products/?q=${encodeURIComponent(term)}`;
  });
}

/* Orders awaiting action, shown on the bell. Failure is silent: a missing
 * badge is better than an error banner over a page that otherwise worked. */
async function loadPendingCount() {
  const badgeEl = $('#bell-count');
  if (!badgeEl) return;
  try {
    const data = await api.get('/api/v1/analytics/dashboard/summary/');
    const pending = Number(data?.pending?.orders || 0);
    badgeEl.textContent = pending > 99 ? '99+' : String(pending);
    badgeEl.hidden = pending === 0;
    $('#bell')?.setAttribute(
      'aria-label',
      pending === 0 ? 'No orders awaiting action' : `${pending} orders awaiting action`,
    );
  } catch {
    badgeEl.hidden = true;
  }
}

async function initShell() {
  const switcher = $('#business-switcher');
  const brand = $('#brand-name');

  document.getElementById('sign-out')?.addEventListener('click', async () => {
    try {
      await api.post('/api/auth/logout/', {});
    } catch {
      // Signing out locally is what matters; a failed call must not trap the user.
    }
    store.clear();
    location.href = '/login/';
  });

  let memberships = [];
  try {
    memberships = rows(await api.get('/api/v1/businesses/'));
  } catch (error) {
    showPageError(error.message);
    return;
  }

  if (!memberships.length) {
    showPageError(
      'This account is not a member of any business yet. Create one via the API ' +
      'or ask an owner to add you.',
    );
    switcher.innerHTML = '<option>No businesses</option>';
    switcher.disabled = true;
    return;
  }

  // Keep the stored selection if it is still valid, else fall back to the first.
  const stored = store.businessId;
  const current = memberships.find(b => String(b.id) === String(stored)) || memberships[0];

  switcher.innerHTML = memberships
    .map(b => `<option value="${b.id}"${b.id === current.id ? ' selected' : ''}>${escapeAttr(b.name)}</option>`)
    .join('');

  applyBusiness(current);

  switcher.addEventListener('change', () => {
    const picked = memberships.find(b => String(b.id) === switcher.value);
    if (!picked) return;
    applyBusiness(picked);
    // Tenant changed: reload so every page module refetches against it cleanly.
    location.reload();
  });

  function applyBusiness(business) {
    store.businessId = business.id;
    store.businessName = business.name;
    store.currency = business.currency || 'KSh';
    if (brand) brand.textContent = business.name;
    document.title = `${business.name} · ${document.title.split('·').pop().trim()}`;

    const plan = $('#brand-plan');
    if (plan) plan.textContent = business.slug ? `@${business.slug}` : 'Workspace';

    loadPendingCount();

    const shopLink = $('#view-shop');
    if (shopLink && business.slug) {
      shopLink.href = `/shop/${business.slug}/`;
      shopLink.hidden = false;
    }
    markReady(business);
  }
}

function showPageError(message) {
  const box = $('#page-error');
  if (box) {
    box.textContent = message;
    box.hidden = false;
  }
}

function escapeAttr(value) {
  return String(value ?? '').replace(/[&<>"]/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]
  ));
}

/* Page modules call this instead of running immediately, so their requests
 * never go out before a validated tenant header exists. */
export function onBusinessReady(handler) {
  businessReady.then(business => handler(business));
}
