import { api, rows, store, $, showError } from './api.js';

const form = $('#login-form');
const submit = $('#login-submit');
const errorBox = $('#login-error');

// Already signed in? Skip straight through.
if (store.token) location.href = nextUrl();

form.addEventListener('submit', async event => {
  event.preventDefault();
  showError(errorBox, '');

  const username = $('#username').value.trim();
  const password = $('#password').value;
  if (!username || !password) {
    showError(errorBox, 'Enter your username and password.');
    return;
  }

  submit.disabled = true;
  submit.textContent = 'Signing in…';

  try {
    const result = await api.post(
      '/api/auth/login/', { username, password }, { auth: false, business: false },
    );

    store.token = result.token;
    store.user = result.user;

    // LoginView returns the caller's memberships; pre-select a tenant here so
    // the first dashboard request already carries X-Business-ID.
    const memberships = rows(result.businesses);
    if (memberships.length) {
      const first = memberships[0];
      store.businessId = first.business;
      store.businessName = first.business_name || '';
    }

    location.href = nextUrl();
  } catch (error) {
    // 429 is the auth throttle; say so plainly rather than "invalid credentials".
    showError(
      errorBox,
      error.status === 429
        ? 'Too many attempts. Wait a minute and try again.'
        : error.status === 401
          ? 'That username and password did not match.'
          : error.message,
    );
    submit.disabled = false;
    submit.textContent = 'Sign in';
  }
});

function nextUrl() {
  const next = new URLSearchParams(location.search).get('next');
  // Only ever redirect within this site.
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard/';
}
