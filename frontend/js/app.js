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

// ── Initialize ─────────────────────────────────────────────────────────────
function init() {
  console.log('[App] LabReview initializing…');
  console.log('[App] WS backend:', WS_URL);

  // 1. TopBarManager — must come before Sidebar
  LabReview.TopBarManager.init(document.getElementById('topbar-slot'));

  // 2. Page controllers
  if (LabReview.LiveIntelPage)  LabReview.LiveIntelPage.init();
  if (LabReview.PerfReviewPage) LabReview.PerfReviewPage.init();

  // 3. Sidebar — switches to default page (live-intel)
  LabReview.Sidebar.init();

  // 4. Connect to WebSocket backend
  LabReview.StreamRouter.connect();
}

// type="module" scripts are deferred — DOM is always ready.
// Guard just in case.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
