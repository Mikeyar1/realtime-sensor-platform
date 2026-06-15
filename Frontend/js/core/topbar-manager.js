/**
 * topbar-manager.js — Per-page top bar controller.
 *
 * Manages the #topbar-slot <header> element.
 * Each page registers a render function and an optional teardown.
 * Activating a page clears the slot, runs any prior teardown, then
 * calls the new page's render function.
 *
 * Usage:
 *   LabReplay.TopBarManager.register('live-monitor', (el) => { el.innerHTML = '...'; });
 *   LabReplay.TopBarManager.activate('live-monitor');
 */

window.LabReplay = window.LabReplay || {};

LabReplay.TopBarManager = (function () {

  let _slot        = null;   // the #topbar-slot element
  let _currentPage = null;   // active page id

  const _renderers  = {};    // pageId → fn(slotEl)
  const _teardowns  = {};    // pageId → fn()

  /**
   * Call once on DOMContentLoaded to bind the slot element.
   * @param {HTMLElement} slotEl
   */
  function init(slotEl) {
    _slot = slotEl;
  }

  /**
   * Register a page's top bar renderer (and optional teardown).
   * @param {string}   pageId       — matches data-page attribute
   * @param {Function} renderFn     — called with (slotEl) to fill the top bar
   * @param {Function} [teardownFn] — called when leaving this page
   */
  function register(pageId, renderFn, teardownFn) {
    _renderers[pageId] = renderFn;
    if (teardownFn) _teardowns[pageId] = teardownFn;
  }

  /**
   * Switch the top bar to the given page's renderer.
   * Runs the previous page's teardown first.
   * @param {string} pageId
   */
  function activate(pageId) {
    // Teardown old
    if (_currentPage && _teardowns[_currentPage]) {
      try { _teardowns[_currentPage](); } catch (e) {
        console.warn('[TopBarManager] teardown error for', _currentPage, e);
      }
    }

    // Clear slot
    if (_slot) _slot.innerHTML = '';

    _currentPage = pageId;

    // Render new
    if (_slot && _renderers[pageId]) {
      try { _renderers[pageId](_slot); } catch (e) {
        console.error('[TopBarManager] render error for', pageId, e);
      }
    }
  }

  /** Returns the currently active page id (or null). */
  function current() {
    return _currentPage;
  }

  return { init, register, activate, current };
})();
