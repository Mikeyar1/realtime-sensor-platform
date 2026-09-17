/**
 * live-intel.js — Live Human Performance page controller
 *
 * State machine:
 *   IDLE → CAPTURING → SUMMARIZING → RESULTS
 *
 * Consistent page layout across all drill types:
 *   IDLE        — Stream status panel (left, collapsible) + readiness message
 *   CAPTURING   — Session ribbon (LIVE dot, elapsed, drill badge, stop)
 *               — Two card sections: Behavioral Metrics | Physiological Data
 *               — VirTra event ticker (in footer)
 *   RESULTS     — Cards freeze, final computed metrics overwrite live values
 *               — Save session record panel appears below
 *
 * Metric cards are config-driven via LabReview.DrillMetrics (drill_metrics.js).
 * Rendering is handled by LabReview.MetricCardRenderer (metric-card-renderer.js).
 *
 * Flow:
 *   1. System starts → IDLE state, stream panel shows connected LSL streams
 *   2. VirTra fires "Scenario Started" (or operator clicks ▶ Start) → CAPTURING
 *   3. trial-update / biosignal-update events → metric cards update live
 *   4. VirTra fires "Scenario Stopped" → SUMMARIZING → cards freeze
 *   5. Analysis returns metrics → RESULTS, final values overwrite cards
 *   6. Operator fills Save panel → session_record.json written to Data/SessionData/
 *   7. Operator clicks New Session → IDLE reset
 */

window.LabReview = window.LabReview || {};

LabReview.LiveIntelPage = (function () {

  // ── State ─────────────────────────────────────────────────────────────────

  let _state = 'IDLE';   // IDLE | CAPTURING | SUMMARIZING | RESULTS

  // Participant / session identity (pre-filled from topbar or LSL stream)
  let _participantId = '';
  let _sessionName = '';

  // Drill resolved by Backend from the VirTra scenario name
  let _drillKey = '';
  let _drillLabel = '';
  let _taskType = '';

  // Elapsed time tracking (RAF-based for smooth display)
  let _elapsedS = 0;
  let _elapsedRAF = null;
  let _elapsedMono = null;
  let _elapsedAtAnchor = 0;

  // AAR Dashboard (chart-level display — separate from metric cards)
  let _activeDisplay = null;

  // Stream catalog — kept fresh from catalog-updated events
  let _streamCatalog = [];
  let _streamPanelCollapsed = false;

  // Whether the page shell has been built (avoid re-building on re-navigation)
  let _pageBuilt = false;

  // ── Initialization ────────────────────────────────────────────────────────

  function init() {
    LabReview.TopBarManager.register('live-intel', _renderTopBar, _teardown);

    // VirTra scenario lifecycle
    LabReview.EventBus.on('scenario-started', _onScenarioStarted);
    LabReview.EventBus.on('scenario-stopped', _onScenarioStopped);

    // Raw VirTra events → AAR dashboard (event counting etc.)
    LabReview.EventBus.on('virtra-event', _onVirtraEvent);

    // Trial updates (PVT, GNG) → metric cards
    LabReview.EventBus.on('trial-update', _onTrialUpdate);

    // Post-scenario analysis metrics
    LabReview.EventBus.on('metrics-ready', _onMetricsReady);

    // Biosignal updates → physiological cards
    LabReview.EventBus.on('biosignal-update', _onBiosignalUpdate);

    // Weapon type from first shot of the session
    LabReview.EventBus.on('weapon-info', _onWeaponInfo);

    // Session state heartbeat (carries elapsed_seconds + rolling_stats)
    LabReview.EventBus.on('session-state-changed', _onSessionState);

    // LSL catalog → update stream status panel
    LabReview.EventBus.on('catalog-updated', _onCatalogUpdated);

    // Auto-fill participant / session from LSL Participant stream
    LabReview.EventBus.on('session-info-received', _onSessionInfoReceived);

    // Operator saved the session record → show confirmation
    LabReview.EventBus.on('record-saved', _onRecordSaved);

    // Page navigation
    LabReview.EventBus.on('page-changed', (page) => {
      if (page === 'live-intel') _renderPageContent();
    });
  }

  // ── Top bar ───────────────────────────────────────────────────────────────

  function _renderTopBar(slot) {
    slot.innerHTML = _buildTopBarHtml();
    _wireTopBar(slot);
    _applyStateToTopBar();
  }

  function _buildTopBarHtml() {
    const isCapturing = _state === 'CAPTURING';
    const isResults = _state === 'RESULTS' || _state === 'SUMMARIZING';

    return `
      <div class="topbar" id="live-intel-topbar">

        <!-- Connection status chip -->
        <div class="topbar-section" id="li-status-section">
          <span class="li-status-dot" id="li-conn-dot"></span>
          <span class="li-status-label" id="li-conn-label">Connecting…</span>
        </div>

        <!-- Start button — IDLE only -->
        <div class="topbar-section topbar-section--right">
          <button class="topbar-btn-primary" id="li-start-btn"
                  style="${isCapturing || isResults ? 'display:none' : ''}">
            ▶ Start
          </button>
        </div>

      </div>
    `;
  }

  function _wireTopBar(slot) {
    slot.querySelector('#li-start-btn')?.addEventListener('click', _startCapture);
  }

  function _applyStateToTopBar() {
    _toggleEl('li-start-btn', !(_state === 'CAPTURING') && !(_state === 'RESULTS' || _state === 'SUMMARIZING'));
    _refreshStatusDot();
  }

  function _refreshStatusDot() {
    const dot = document.getElementById('li-conn-dot');
    const label = document.getElementById('li-conn-label');
    if (!dot || !label) return;

    const connected = _streamCatalog.length > 0;
    dot.className = `li-status-dot ${connected ? 'li-status-dot--connected' : 'li-status-dot--waiting'}`;
    label.textContent = connected
      ? `${_streamCatalog.length} stream${_streamCatalog.length > 1 ? 's' : ''} connected`
      : 'Waiting for streams…';
    label.className = `li-status-label ${connected ? 'li-status-label--connected' : ''}`;
  }

  // ── Page content ──────────────────────────────────────────────────────────

  function _renderPageContent() {
    const pageEl = document.getElementById('page-live-intel');
    if (!pageEl) return;

    if (_pageBuilt) { _applyStateToPage(); return; }
    _pageBuilt = true;

    pageEl.innerHTML = `
      <div class="li-shell" id="li-shell">

        <!-- ── Main content area ── -->
        <div class="li-main" id="li-main">

          <!-- STATE: IDLE — readiness message -->
          <div class="li-idle" id="li-idle-overlay">
            <h2 class="li-idle__heading">System Ready</h2>
            <p class="li-idle__sub">
              Start the VirTra scenario and the system will activate automatically.<br>
              Or press <strong>▶ Start</strong> to begin listening manually.
            </p>
          </div>

          <!-- STATE: CAPTURING / RESULTS — session ribbon + metric cards -->
          <div class="li-session-view" id="li-session-view" style="display:none">

            <!-- Session ribbon: LIVE indicator, elapsed, drill badge, stop -->
            <div class="li-ribbon" id="li-ribbon">
              <div class="li-ribbon__left">
                <span class="li-ribbon__live-dot" id="li-live-dot"></span>
                <span class="li-ribbon__live-label" id="li-live-label">LIVE</span>
                <span class="li-ribbon__drill-badge" id="li-drill-badge"></span>
              </div>
              <div class="li-ribbon__center" id="li-ribbon-center">
                <span class="li-ribbon__elapsed-label">Elapsed</span>
                <span class="li-ribbon__elapsed-value" id="li-elapsed-value">00:00:00</span>
              </div>
              <div class="li-ribbon__right">
                <button class="topbar-btn-stop li-ribbon__stop" id="li-stop-btn">
                  ■ Stop
                </button>
              </div>
            </div>

            <!-- Behavioral metric cards -->
            <div class="li-section" id="li-behavioral-section">
              <div class="li-metric-grid li-metric-grid--behavioral"
                   id="li-behavioral-grid"></div>
            </div>

            <!-- Physiological data cards -->
            <div class="li-section li-section--physio" id="li-physio-section">
              <div class="li-section__header">
                <span class="li-section__title">Physiological Data</span>
              </div>
              <div class="li-metric-grid li-metric-grid--physio"
                   id="li-physio-grid"></div>
            </div>

            <!-- RESULTS state divider (shown after scenario stops) -->
            <div class="li-results-banner" id="li-results-banner" style="display:none">
              <span class="li-results-banner__text" id="li-results-banner-text">Scenario Complete</span>
            </div>

            <!-- Save session record panel (appears in RESULTS after metrics arrive) -->
            <div id="li-save-panel" style="display:none"></div>

          </div>

        </div>
      </div>
    `;

    // Wire the sidebar stream accordion toggle (lives in index.html sidebar, not page shell)
    const sidebarToggle = document.getElementById('sidebar-streams-toggle');
    if (sidebarToggle && !sidebarToggle._wired) {
      sidebarToggle.addEventListener('click', _toggleStreamPanel);
      sidebarToggle._wired = true;
    }

    // Wire stop and new-session buttons
    document.getElementById('li-stop-btn')
      ?.addEventListener('click', _stopCapture);
    document.getElementById('li-results-new-session')
      ?.addEventListener('click', _resetToIdle);

    // Mount AAR Dashboard for chart-level rendering
    _mountAARDashboard();

    // Render stream list with current catalog
    _renderStreamList();

    _applyStateToPage();
  }

  function _applyStateToPage() {
    const isIdle = _state === 'IDLE';
    const isCapturing = _state === 'CAPTURING';
    const isSummarizing = _state === 'SUMMARIZING';
    const isResults = _state === 'RESULTS';
    const isActive = isCapturing || isSummarizing || isResults;

    _toggleEl('li-idle-overlay', isIdle);
    _toggleEl('li-session-view', isActive);
    _toggleEl('li-results-banner', isResults || isSummarizing);

    // Ribbon appearance changes based on state
    const ribbonEl = document.getElementById('li-ribbon');
    const liveDotEl = document.getElementById('li-live-dot');
    const liveLabelEl = document.getElementById('li-live-label');
    const stopBtn = document.getElementById('li-stop-btn');

    if (ribbonEl) {
      ribbonEl.className = isResults || isSummarizing
        ? 'li-ribbon li-ribbon--frozen'
        : 'li-ribbon li-ribbon--live';
    }
    if (liveDotEl) {
      liveDotEl.className = isCapturing
        ? 'li-ribbon__live-dot li-ribbon__live-dot--pulsing'
        : 'li-ribbon__live-dot li-ribbon__live-dot--stopped';
    }
    if (liveLabelEl) {
      liveLabelEl.textContent = isCapturing ? 'LIVE' : 'STOPPED';
    }
    if (stopBtn) {
      stopBtn.style.display = isCapturing ? '' : 'none';
    }

    // Live elapsed is only meaningful while capturing; once stopped the AAR
    // "Session Duration" card is the single source of truth.
    const centerEl = document.getElementById('li-ribbon-center');
    if (centerEl) centerEl.style.display = isCapturing ? '' : 'none';

    // Update drill badge text
    const badge = document.getElementById('li-drill-badge');
    if (badge) badge.textContent = _drillLabel || _drillKey;

    // Update behavioral section subtitle
    const sub = document.getElementById('li-behavioral-subtitle');
    if (sub) sub.textContent = _drillLabel || '';

    if (isCapturing) {
      _startElapsedRAF();
    } else {
      _stopElapsedRAF();
    }
  }

  // ── Capture control ───────────────────────────────────────────────────────

  function _startCapture() {
    LabReview.StreamRouter.subscribeAll();
    LabReview.StreamRouter.activateLive();

    _elapsedAtAnchor = 0;
    _elapsedMono = performance.now();
    _setState('CAPTURING');
  }

  function _stopCapture() {
    LabReview.StreamRouter.deactivateLive();
    _activeDisplay?.onScenarioStopped?.({ manual: true });
    LabReview.MetricCardRenderer.freezeCards();
    _setState('RESULTS');
    _showResultsBanner('Scenario stopped manually — results below.');
  }

  // ── EventBus handlers ─────────────────────────────────────────────────────

  function _onCatalogUpdated(catalog) {
    _streamCatalog = catalog || [];
    _renderStreamList();
    _refreshStatusDot();
    if (_streamCatalog.length > 0) {
      LabReview.StreamRouter.subscribeAll();
    }
  }

  function _onSessionInfoReceived({ participant_id, session_name }) {
    // Store internally so the save panel can pre-fill from LSL data
    if (!_participantId && participant_id) _participantId = participant_id;
    if (!_sessionName && session_name) _sessionName = session_name;
  }

  function _onSessionState(msg) {
    if (msg.elapsed_seconds != null) {
      _elapsedAtAnchor = msg.elapsed_seconds;
      _elapsedMono = performance.now();
    }
    if (msg.rolling_stats) {
      LabReview.MetricCardRenderer.updateAllCards(msg.rolling_stats, 'biosignal');
      _activeDisplay?.onSensorUpdate?.(msg.rolling_stats);
    }
  }

  // ── Scenario lifecycle ────────────────────────────────────────────────────

  function _onScenarioStarted(payload) {
    console.log(`[LiveIntel] scenario-started — state=${_state}`, payload);

    if (_state === 'IDLE') _startCapture();

    _drillKey = payload.drill_key || 'general';
    _drillLabel = payload.drill_label || _drillKey;
    _taskType = payload.task_type || 'GENERAL';

    if (_state === 'CAPTURING') {
      _applyStateToPage();

      // Physiological cards only — the behavioral display is owned by the AAR module.
      const physioGrid = document.getElementById('li-physio-grid');
      LabReview.MetricCardRenderer.renderCards(null, physioGrid, _drillKey);

      // Tell AAR Dashboard which drill is active (for charts)
      _mountAARDashboard();
      _activeDisplay?.onScenarioStarted?.(payload);
    }
  }

  function _onScenarioStopped(payload) {
    _activeDisplay?.onScenarioStopped?.(payload);
    if (_state === 'CAPTURING') {
      _setState('SUMMARIZING');
      LabReview.MetricCardRenderer.freezeCards();
      _showResultsBanner('Scenario complete — computing final metrics…');
    }
  }

  function _onMetricsReady(msg) {
    // Overwrite live card values with the final computed metrics from Analysis
    const metrics = msg.metrics || msg;
    LabReview.MetricCardRenderer.updateAllCards(metrics, 'trial');

    // Also let the AAR Dashboard render any charts it produces
    _activeDisplay?.onMetricsReady?.(metrics);

    _setState('RESULTS');
    _showResultsBanner('Scenario complete');

    // Show the save panel so the operator can attach participant metadata
    _showSavePanel();
  }

  // ── Trial / biosignal update routing ──────────────────────────────────────

  /**
   * Handles trial-update from both PVT and GNG drills.
   * If it arrives while IDLE (mid-scenario connect), synthesises scenario_started.
   */
  function _onTrialUpdate(msg) {
    if (_state !== 'CAPTURING' && _state !== 'RESULTS') return;

    // Update behavioral metric cards
    LabReview.MetricCardRenderer.updateAllCards(msg, 'trial');

    // Also forward to AAR Dashboard for chart-level rendering
    _activeDisplay?.onTrialUpdate?.(msg);
  }

  function _onVirtraEvent(evt) {
    if (_state !== 'CAPTURING') return;
    _activeDisplay?.onEvent?.(evt);
  }

  function _onBiosignalUpdate(msg) {
    // Always pass through the final session-average broadcast (is_final=true)
    // so physio cards reflect true session means, not just the last rolling value.
    const isFinal = msg.is_final === true;
    if (_state !== 'CAPTURING' && !isFinal) return;
    LabReview.MetricCardRenderer.updateAllCards(msg, 'biosignal');
    _activeDisplay?.onSensorUpdate?.(msg);
  }

  function _onWeaponInfo(msg) {
    if (_state !== 'CAPTURING') return;
    _activeDisplay?.onWeaponInfo?.(msg);
  }

  // ── Save panel ────────────────────────────────────────────────────────────

  function _showSavePanel() {
    const prefillId = _participantId || '';
    const prefillName = '';
    const prefillNote = '';

    let panel = document.getElementById('li-save-panel');
    if (!panel) return;

    panel.innerHTML = `
      <div class="save-panel-inner">
        <div class="save-panel-header">
          <h3 class="save-panel-title">Save Session Record</h3>
        </div>

        <div class="save-panel-fields">
          <div class="save-field-group">
            <label class="save-field-label" for="sp-pid">Participant ID</label>
            <input id="sp-pid" class="save-field-input" type="text"
                   placeholder="e.g. P001" value="${_escHtml(prefillId)}">
          </div>
          <div class="save-field-group">
            <label class="save-field-label" for="sp-pname">Participant Name</label>
            <input id="sp-pname" class="save-field-input" type="text"
                   placeholder="e.g. Jane Doe" value="${_escHtml(prefillName)}">
          </div>
          <div class="save-field-group save-field-group--full">
            <label class="save-field-label" for="sp-notes">Notes</label>
            <textarea id="sp-notes" class="save-field-textarea"
                      placeholder="Optional: fatigue level, sleep hours, study condition…"
                      rows="2">${_escHtml(prefillNote)}</textarea>
          </div>
        </div>

        <div class="save-panel-actions">
          <button class="save-btn-primary" id="sp-save-btn">Save Record</button>
          <span class="save-panel-spacer"></span>
          <button class="save-btn-new-session" id="sp-new-session-btn">↳ New Session</button>
        </div>
      </div>
    `;

    panel.style.display = '';
    document.getElementById('sp-save-btn')
      ?.addEventListener('click', _onSaveClicked);
    document.getElementById('sp-new-session-btn')
      ?.addEventListener('click', _resetToIdle);
  }

  function _onSaveClicked() {
    const pid = document.getElementById('sp-pid')?.value.trim() || '';
    const pname = document.getElementById('sp-pname')?.value.trim() || '';
    const notes = document.getElementById('sp-notes')?.value.trim() || '';

    const btn = document.getElementById('sp-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

    LabReview.StreamRouter.send({
      type: 'session.save_record',
      participant_id: pid,
      participant_name: pname,
      notes,
    });
  }

  function _onRecordSaved(msg) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;

    if (msg.success) {
      panel.innerHTML = `
        <div class="save-panel-inner save-panel-inner--saved">
          <span class="save-panel-check">✓</span>
          <span class="save-panel-saved-text">Session record saved.</span>
          <button class="save-btn-new-session" id="sp-new-session-btn-ok">↳ New Session</button>
        </div>
      `;
      document.getElementById('sp-new-session-btn-ok')
        ?.addEventListener('click', _resetToIdle);
    } else {
      const btn = document.getElementById('sp-save-btn');
      if (btn) { btn.disabled = false; btn.textContent = 'Save Record'; }
      console.error('[LiveIntel] session_record save failed:', msg.error);
    }
  }

  // ── Results banner ────────────────────────────────────────────────────────

  function _showResultsBanner(text) {
    const bannerEl = document.getElementById('li-results-banner');
    const textEl = document.getElementById('li-results-banner-text');
    if (!bannerEl) return;
    if (textEl) textEl.textContent = text;
    bannerEl.style.display = '';

    // Ensure New Session button is wired (banner is static HTML, not re-rendered)
    const btn = document.getElementById('li-results-new-session');
    if (btn && !btn._wired) {
      btn.addEventListener('click', _resetToIdle);
      btn._wired = true;
    }
  }

  // ── Stream status panel ───────────────────────────────────────────────────

  /**
   * Match a backend catalog stream entry against a StreamCatalog descriptor.
   * A match occurs when namePattern OR typePattern matches (whichever is defined).
   *
   * @param {Object} stream     - Catalog entry from the backend (has .name, .stream_type)
   * @param {Object} descriptor - Entry from LabReview.StreamCatalog
   * @returns {boolean}
   */
  function _streamMatches(stream, descriptor) {
    const name = stream.name || stream.stream_name || stream.stream_id || '';
    const type = stream.stream_type || stream.type || '';
    if (descriptor.namePattern && descriptor.namePattern.test(name)) return true;
    if (descriptor.typePattern && descriptor.typePattern.test(type)) return true;
    return false;
  }

  function _renderStreamList() {
    // Target the sidebar stream list (lives in #sidebar in index.html)
    const listEl = document.getElementById('sidebar-stream-list');
    if (!listEl) return;

    // Read descriptors from the central stream config (js/config/stream_catalog.js).
    // Fall back to an empty array if the config hasn't loaded yet.
    const descriptors = (window.LabReview && LabReview.StreamCatalog) || [];

    // Match each catalog entry against descriptors, filter to display:true, deduplicate by label.
    const seenLabels = new Set();
    const visible = [];
    _streamCatalog.forEach(stream => {
      const descriptor = descriptors.find(d => _streamMatches(stream, d));
      if (!descriptor) return;                    // unknown stream — ignore in sidebar
      if (descriptor.display === false) return;   // explicitly suppressed
      if (seenLabels.has(descriptor.label)) return; // duplicate
      seenLabels.add(descriptor.label);
      visible.push(descriptor);
    });

    if (visible.length === 0) {
      listEl.innerHTML = '<span class="sidebar-stream-empty">No mapped streams detected.</span>';
      return;
    }

    listEl.innerHTML = visible.map(d => `
      <div class="sidebar-stream-row">
        <span class="sidebar-stream-dot sidebar-stream-dot--connected"></span>
        <span class="sidebar-stream-label">${_escHtml(d.label)}</span>
      </div>
    `).join('');
  }

  function _toggleStreamPanel() {
    _streamPanelCollapsed = !_streamPanelCollapsed;
    const body = document.getElementById('sidebar-streams-body');
    const chevron = document.getElementById('sidebar-streams-chevron');
    const toggle = document.getElementById('sidebar-streams-toggle');

    if (body) body.style.display = _streamPanelCollapsed ? 'none' : '';
    if (chevron) chevron.textContent = _streamPanelCollapsed ? '▸' : '▾';
    if (toggle) toggle.setAttribute('aria-expanded', String(!_streamPanelCollapsed));
  }

  // ── AAR Dashboard management ──────────────────────────────────────────────

  function _mountAARDashboard() {
    // The AAR Dashboard renders charts into li-behavioral-grid as well.
    // It operates alongside the metric cards (cards are rendered above charts).
    const grid = document.getElementById('li-behavioral-grid');
    if (!grid) return;
    if (_activeDisplay === LabReview.AAR?.Dashboard) return;

    if (_activeDisplay?.unmount) _activeDisplay.unmount();
    _activeDisplay = LabReview.AAR?.Dashboard;

    if (_activeDisplay?.mount) {
      _activeDisplay.mount(grid);
    }
  }

  // ── State machine ─────────────────────────────────────────────────────────

  function _setState(newState) {
    _state = newState;
    _applyStateToTopBar();
    _applyStateToPage();
  }

  // ── Elapsed RAF ───────────────────────────────────────────────────────────

  function _startElapsedRAF() {
    if (_elapsedRAF) return;
    const tick = () => {
      const live = _elapsedAtAnchor + (performance.now() - (_elapsedMono || performance.now())) / 1000;
      _setElapsedDisplay(live);
      _elapsedRAF = requestAnimationFrame(tick);
    };
    _elapsedRAF = requestAnimationFrame(tick);
  }

  function _stopElapsedRAF() {
    if (_elapsedRAF) { cancelAnimationFrame(_elapsedRAF); _elapsedRAF = null; }
  }

  function _setElapsedDisplay(sec) {
    const el = document.getElementById('li-elapsed-value');
    if (!el) return;
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    el.textContent =
      `${String(h).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  // ── Reset ─────────────────────────────────────────────────────────────────

  function _resetToIdle() {
    const panel = document.getElementById('li-save-panel');
    if (panel) { panel.style.display = 'none'; panel.innerHTML = ''; }

    LabReview.MetricCardRenderer.clearCards();

    _pageBuilt = false;
    _drillKey = '';
    _drillLabel = '';
    _taskType = '';
    _participantId = '';
    _sessionName = '';
    _activeDisplay?.reset?.();
    _activeDisplay = null;

    _setState('IDLE');
    _renderPageContent();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _toggleEl(id, visible) {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  }

  function _escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Teardown ──────────────────────────────────────────────────────────────

  function _teardown() {
    _stopElapsedRAF();
    _activeDisplay?.unmount?.();
  }

  return { init };

})();
