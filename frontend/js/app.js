/**
 * app.js — Application Bootstrap
 *
 * ES module entry point. Wires core infrastructure and the two page controllers:
 *   live-intel  — Live Human Performance
 *   replay      — Replay Human Performance
 *
 * Load order:
 *   1. Classic <script> tags (core/, pages/) run first (synchronous).
 *   2. This module runs after DOMContentLoaded (type="module" is deferred).
 */

// ── Shared ES module imports ─────────────────────────────────────────────────
import { WS_URL } from './shared/constants.js';
import { formatElapsed, formatDelta, formatDuration } from './shared/format.js';

// Expose shared utilities on the LabReview namespace so classic-script
// modules can access them without ES module imports.
window.LabReview = window.LabReview || {};
LabReview.Shared = {
  WS_URL,
  formatElapsed,
  formatDelta,
  formatDuration,
};

// ── Event bar drag-resize ──────────────────────────────────────────────────
function wireEventBarResize() {
  const app    = document.getElementById('app');
  const bar    = document.getElementById('event-bar');
  const handle = document.getElementById('event-bar-handle');
  if (!app || !bar || !handle) return;

  const MIN_H = 80;
  const MAX_H = () => Math.floor(window.innerHeight * 0.6);
  const STORAGE_KEY = 'labReplay.eventBarHeight';

  function applyHeight(h) {
    h = Math.min(MAX_H(), Math.max(MIN_H, h));
    app.style.setProperty('--event-bar-h', `${h}px`);
  }

  const saved = parseInt(localStorage.getItem(STORAGE_KEY), 10);
  applyHeight(saved && saved >= MIN_H ? saved : 150);

  let startY = 0;
  let startH = 0;

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    startY = e.clientY;
    startH = bar.getBoundingClientRect().height;
    handle.classList.add('dragging');
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', (e) => {
    if (!handle.classList.contains('dragging')) return;
    const dy = e.clientY - startY;
    applyHeight(startH - dy);
  });

  document.addEventListener('mouseup', () => {
    if (!handle.classList.contains('dragging')) return;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    localStorage.setItem(STORAGE_KEY, bar.getBoundingClientRect().height);
  });
}

// ── Initialize ─────────────────────────────────────────────────────────────
function init() {
  console.log('[App] LabReview initializing…');
  console.log('[App] WS backend:', WS_URL);

  // 1. TopBarManager — must come before Sidebar
  LabReview.TopBarManager.init(document.getElementById('topbar-slot'));

  // 2. Core modules
  LabReview.Timeline.init();
  LabReview.EventTicker.init();

  // 3. Page controller
  if (LabReview.LiveIntelPage) LabReview.LiveIntelPage.init();

  // 4. Sidebar — switches to default page (live-intel)
  LabReview.Sidebar.init();

  // 5. Event bar resize handle
  wireEventBarResize();

  // 6. Connect to WebSocket backend
  LabReview.StreamRouter.connect();
}

// type="module" scripts are deferred — DOM is always ready.
// Guard just in case.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
