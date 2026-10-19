/**
 * sidebar.js
 *
 * Handles page navigation for the 2-page layout:
 *   live-intel  — Live Human Performance
 *   replay      — Replay Human Performance
 *
 * Manages:
 *   - Nav item activation (highlight active item)
 *   - Page container show/hide
 *   - TopBarManager activation
 *   - EventBus 'page-changed' broadcast
 */

window.LabReview = window.LabReview || {};

LabReview.Sidebar = (function () {

  // Default page on load
  let _activePage = 'live-intel';

  function init() {
    // Bind nav item clicks
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.addEventListener('click', () => switchPage(item.dataset.page));
      item.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          switchPage(item.dataset.page);
        }
      });
    });

    // Activate default page
    switchPage(_activePage);
  }

  /**
   * Switch to a named page.
   * Handles: nav highlight, page visibility, TopBarManager.
   */
  function switchPage(page) {
    _activePage = page;

    // ── Nav highlight ──────────────────────────────────────────
    document.querySelectorAll('.nav-item[data-page]').forEach(el => {
      el.classList.toggle('active', el.dataset.page === page);
      if (el.dataset.page === page) el.setAttribute('aria-current', 'page');
      else el.removeAttribute('aria-current');
    });

    // ── Page containers ────────────────────────────────────────
    document.querySelectorAll('.page').forEach(el => {
      el.classList.toggle('active', el.id === `page-${page}`);
    });

    // ── Top bar slot ───────────────────────────────────────────
    if (LabReview.TopBarManager) {
      LabReview.TopBarManager.activate(page);
    }

    // ── Broadcast ─────────────────────────────────────────────
    LabReview.EventBus.emit('page-changed', page);
  }

  function getActivePage() { return _activePage; }

  return { init, switchPage, getActivePage };
})();
