/* Minimal modal behaviour shared by the dashboard pages:
 * open/close, backdrop click, Escape, and focus into the dialog.
 */

export function openModal(id) {
  const backdrop = document.getElementById(id);
  if (!backdrop) return;
  backdrop.hidden = false;
  backdrop.querySelector('input, select, textarea, button')?.focus();
}

export function closeModal(id) {
  const backdrop = document.getElementById(id);
  if (backdrop) backdrop.hidden = true;
}

/* Wire every [data-close] button plus backdrop and Escape dismissal. */
export function bindModals() {
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.querySelectorAll('[data-close]').forEach(button => {
      button.addEventListener('click', () => { backdrop.hidden = true; });
    });
    backdrop.addEventListener('mousedown', event => {
      if (event.target === backdrop) backdrop.hidden = true;
    });
  });

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.modal-backdrop:not([hidden])')
      .forEach(backdrop => { backdrop.hidden = true; });
  });
}
