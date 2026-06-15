/**
 * app.js — Application Bootstrap
 *
 * ES module entry point. Imports shared utilities, then wires the
 * LabReplay namespace-based modules that are loaded via classic <script> tags.
 *
 * Load order:
 *   1. Classic <script> tags (core/, charts/, analysis/, pages/) run first
 *      because they are synchronous and placed before this module.
 *      type="module" scripts are deferred by the browser automatically.
 *   2. This module runs after DOMContentLoaded, after all classic scripts.
 *
 * The shared/ ES modules (constants, format, api) are imported here so the
 * browser preloads them eagerly via <link rel="modulepreload"> in index.html.
 */

// ── Shared ES module imports ─────────────────────────────────────────────────
// These are only imported for side-effect preloading here.
// Individual consuming modules will import what they need directly.
import { WS_URL, ANALYSIS_API_BASE } from './shared/constants.js';
import { formatElapsed, formatDelta, formatDuration } from './shared/format.js';

// Expose shared utilities on the LabReplay namespace so legacy code can
// use them without requiring ES module imports themselves.
window.LabReplay = window.LabReplay || {};
LabReplay.Shared = {
  WS_URL,
  ANALYSIS_API_BASE,
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
  console.log('[App] LabReplay initializing… (ES module entry)');
  console.log('[App] WS backend:', WS_URL);

  // 1. TopBarManager — must come before Sidebar
  LabReplay.TopBarManager.init(document.getElementById('topbar-slot'));

  // 2. Core modules
  LabReplay.Timeline.init();
  LabReplay.EventTicker.init();

  // 3. Page controllers
  if (LabReplay.LiveMonitorPage)          LabReplay.LiveMonitorPage.init();
  if (LabReplay.ReplaySessionsPage)       LabReplay.ReplaySessionsPage.init();
  if (LabReplay.LiveIntelPage)            LabReplay.LiveIntelPage.init();
  if (LabReplay.PerformanceWorkspacePage) LabReplay.PerformanceWorkspacePage.init();
  if (LabReplay.DebriefPage)              LabReplay.DebriefPage.init();

  // 4. Sidebar — switches to default page (live-monitor)
  LabReplay.Sidebar.init();

  // 5. Shared utilities
  wireEventBarResize();

  // 6. Connect to WebSocket backend
  LabReplay.StreamRouter.connect();

  console.log('[App] Initialization complete.');
}

// type="module" scripts are deferred — DOM is always ready by the time
// this module executes. But guard just in case.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
