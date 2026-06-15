/**
 * workspace.js — PerformanceWorkspacePage (WP-05)
 *
 * Post-session physiological analysis. Wraps the existing AnalysisApp
 * (analysis-app.js) into the new page architecture with a proper top bar.
 *
 * This is a thin adapter — all analysis logic stays in AnalysisApp.
 * This file:
 *  - Registers a top bar with TopBarManager for the 'workspace' page
 *  - Initializes AnalysisApp into #page-workspace on first activation
 *  - Hides the event bar (workspace doesn't need VirTra/Speech panels)
 *  - WP-09: Displays SessionInfo meta chip when available
 *
 * Dependency: analysis-app.js must be loaded before this file.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.PerformanceWorkspacePage = (function () {

  let _initialized = false;

  // ── Initialization ──────────────────────────────────────────────────────────

  function init() {
    LabReplay.TopBarManager.register('workspace', _renderTopBar, _teardown);

    LabReplay.EventBus.on('page-changed', (page) => {
      if (page === 'workspace') {
        _renderPageContent();
      }
    });
  }

  // ── Top bar ─────────────────────────────────────────────────────────────────

  function _renderTopBar(slot) {
    // The workspace top bar is rendered by AnalysisApp._buildShell() which
    // creates its own control bar inside #page-workspace. We only inject a
    // minimal topbar here for the slot, referencing that the analysis controls
    // are embedded in the page content area.
    slot.innerHTML = `
      <div class="topbar" id="workspace-topbar">

        <span class="topbar-page-title">Performance Workspace</span>

        <div class="topbar-sep"></div>

        <!-- Analysis controls are inline within the page (from AnalysisApp) -->
        <div class="topbar-section topbar-section--grow" id="ws-inline-controls">
          <!-- AnalysisApp injects its controls via _buildShell() into #page-workspace -->
        </div>

        <!-- Right: API status indicator -->
        <div class="topbar-section topbar-section--right">
          <span class="topbar-status-label" id="ws-api-status" style="font-size:var(--font-size-xs);color:var(--text-dim)"></span>
        </div>

      </div>
    `;

    // Check API health
    if (LabReplay.AnalysisAPI) {
      LabReplay.AnalysisAPI.health()
        .then(() => {
          const el = document.getElementById('ws-api-status');
          if (el) { el.textContent = '● Analysis API'; el.style.color = '#10B981'; }
        })
        .catch(() => {
          const el = document.getElementById('ws-api-status');
          if (el) { el.textContent = '⚠ Analysis API offline'; el.style.color = '#F59E0B'; }
        });
    }
  }

  // ── Page content ─────────────────────────────────────────────────────────────

  function _renderPageContent() {
    if (_initialized) return;
    _initialized = true;

    if (LabReplay.AnalysisApp) {
      LabReplay.AnalysisApp.init();
    } else {
      console.warn('[WorkspacePage] AnalysisApp not loaded — is analysis-app.js included?');
    }
  }

  // ── Teardown ─────────────────────────────────────────────────────────────────

  function _teardown() {
    // AnalysisApp is persistent — no teardown needed
  }

  return { init };

})();
