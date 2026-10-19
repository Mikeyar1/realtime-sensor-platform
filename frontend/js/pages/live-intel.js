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
 *               — Confirm / Discard panel appears after a successful auto-save
 *
 * Metric cards are config-driven via LabReview.DrillMetrics (drill_metrics.js).
 * Rendering is handled by LabReview.MetricCardRenderer (metric-card-renderer.js).
 *
 * Flow:
 *   1. System starts → IDLE state, stream panel shows connected LSL streams
 *   2. VirTra fires "Scenario Started" (or operator clicks  Start) → CAPTURING
 *   3. trial-update / biosignal-update events → metric cards update live
 *   4. VirTra stop event or operator Stop → backend finalizes the drill
 *   5. Analysis returns metrics and auto-save result → RESULTS
 *   6. Operator confirms a participant name or discards the session
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
  let _runSource = '';
  let _rounds = [];
  let _roundStore = new Map();
  let _roundErrors = new Map();
  let _roundDraftTimers = new Map();
  let _roundRequestIds = new Map();
  let _liveRoundPhysio = new Map();
  let _roundNames = {};
  let _finalFittsMetrics = null;
  let _fittsActiveKey = '';
  let _fittsActiveCards = null;
  let _fittsFinalCards = null;
  let _fittsHistoryEntries = new Map();
  let _scenarioPayload = null;
  let _sessionId = '';
  let _captureStatus = 'complete';
  let _scoringNotice = '';

  function _isRoundAssessment() {
    return !!_scenarioPayload?.assessment_profile && _runSource === 'custom';
  }

  function _assessmentMetricType() {
    return _scenarioPayload?.assessment_profile === 'pvt_fitts_core' ? 'PVT_FITTS' : _taskType;
  }

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
    LabReview.EventBus.on('scenario-context-updated', _onScenarioContextUpdated);
    LabReview.EventBus.on('scoring-waiting', msg => {
      if ((msg.scenario_name || '').toLowerCase() === 'fitts-pvt_clay') return;
      if (_runSource === 'custom' && _drillKey === 'fitts_pvt_clay') return;
      _scoringNotice = `Waiting for Custom metrics: ${msg.scenario_name || 'scenario'}`;
      _refreshStatusDot();
    });
    LabReview.EventBus.on('scoring-unavailable', msg => {
      _scoringNotice = `Custom metrics unavailable: ${msg.scenario_name || 'scenario'}`;
      _refreshStatusDot();
    });

    // Raw VirTra events → AAR dashboard (event counting etc.)
    LabReview.EventBus.on('virtra-event', _onVirtraEvent);
    LabReview.EventBus.on('custom-lsl-event', _onCustomLslEvent);
    LabReview.EventBus.on('assessment-round-updated', _onAssessmentRoundUpdated);
    LabReview.EventBus.on('assessment-round-physiology', _onAssessmentRoundPhysiology);
    LabReview.EventBus.on('assessment-round-projection', message =>
      _onCustomLslEvent({ ...message, session_id: message.run_id }));
    LabReview.EventBus.on('assessment-command-result', _onAssessmentCommandResult);

    // Trial updates for the three supported drills → metric cards
    LabReview.EventBus.on('trial-update', _onTrialUpdate);

    // Post-scenario analysis metrics (auto-saved to DB already)
    LabReview.EventBus.on('metrics-ready', _onMetricsReady);
    LabReview.EventBus.on('api-error', _onApiError);

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

    // DB responses
    LabReview.EventBus.on('db-participants-list', _onParticipantsList);
    LabReview.EventBus.on('db-session-saved',     _onSessionSaved);
    LabReview.EventBus.on('db-session-discarded', _onSessionDiscarded);

    // Legacy record-saved (backwards compat)
    LabReview.EventBus.on('record-saved', _onLegacyRecordSaved);

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
          <button class="li-start-btn" id="li-start-btn" type="button"
                  style="${isCapturing || isResults ? 'display:none' : ''}">
            Start listening
          </button>
        </div>

      </div>
    `;
  }

  function _wireTopBar(slot) {
    slot.querySelector('#li-start-btn')?.addEventListener('click', () => _startCapture());
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
    label.textContent = _scoringNotice || (connected
      ? `${_streamCatalog.length} stream${_streamCatalog.length > 1 ? 's' : ''} connected`
      : 'Waiting for streams…');
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
              Or press <strong>Start listening</strong> to begin manually.
            </p>
          </div>

          <!-- STATE: CAPTURING / RESULTS — session ribbon + metric cards -->
          <div class="li-session-view" id="li-session-view" style="display:none">

            <!-- Session ribbon: LIVE indicator, elapsed, stop -->
            <div class="li-ribbon" id="li-ribbon">
              <div class="li-ribbon__left">
                <span class="li-ribbon__live-dot" id="li-live-dot"></span>
                <span class="li-ribbon__live-label" id="li-live-label">LIVE</span>
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
            <div class="li-round-section" id="li-round-section" style="display:none">
              <div class="li-round-final" id="li-round-final" style="display:none"></div>
              <div class="li-round-current" id="li-round-current"></div>
              <p class="li-round-between" id="li-round-between" style="display:none"></p>
              <div class="li-round-history" id="li-round-history"></div>
              <datalist id="li-round-name-list"></datalist>
            </div>
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
    _toggleEl('li-behavioral-section', !_isRoundAssessment());
    _toggleEl('li-physio-section', !_isRoundAssessment());

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
      liveLabelEl.textContent = _scenarioPayload?.scenario_name || (isCapturing ? 'LIVE' : 'STOPPED');
    }
    if (stopBtn) {
      stopBtn.style.display = isCapturing ? '' : 'none';
    }

    // Live elapsed is only meaningful while capturing; once stopped the AAR
    // "Session Duration" card is the single source of truth.
    const centerEl = document.getElementById('li-ribbon-center');
    if (centerEl) centerEl.style.display = isActive ? '' : 'none';

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

  function _startCapture(alreadyStarted = false) {
    LabReview.StreamRouter.subscribeAll();
    if (!alreadyStarted) LabReview.StreamRouter.activateLive();

    _elapsedAtAnchor = 0;
    _elapsedMono = performance.now();
    _setState('CAPTURING');
  }

  function _stopCapture() {
    if (_state !== 'CAPTURING') return;
    if (!_drillKey) {
      LabReview.StreamRouter.deactivateLive();
      _resetToIdle();
      return;
    }
    _setState('SUMMARIZING');
    _showResultsBanner('Stopping drill — computing final metrics…');
    LabReview.StreamRouter.send({ type: 'session.stop' });
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

    if (!['PVT', 'GNG', 'DISCRIMINATION', 'PVT_FITTS', 'COMPOSITE'].includes(payload.task_type) || !payload.drill_key) {
      console.warn('[LiveIntel] Ignoring unsupported drill', payload);
      return;
    }

    if (_state === 'IDLE') _startCapture(true);

    _drillKey = payload.drill_key;
    _drillLabel = payload.drill_label || _drillKey;
    _taskType = payload.task_type;
    _runSource = payload.metrics_source || payload.source || 'native';
    _scoringNotice = '';
    _refreshStatusDot();
    const sameRun = !!_sessionId && payload.session_id === _sessionId;
    _scenarioPayload = payload;
    _sessionId = payload.session_id || '';
    _captureStatus = payload.capture_status || 'complete';
    if (!sameRun) {
      for (const timer of _roundDraftTimers.values()) clearTimeout(timer);
      _roundDraftTimers = new Map();
      _roundRequestIds = new Map();
      _roundNames = {};
      _roundStore = new Map();
      _roundErrors = new Map();
      _liveRoundPhysio = new Map();
    }
    if (Array.isArray(payload.assessment_run?.rounds)) {
      for (const round of payload.assessment_run.rounds) _roundStore.set(round.id, round);
    }
    _rounds = (Array.isArray(payload.rounds) ? payload.rounds : [])
      .map(round => _mergeRound(round));
    _finalFittsMetrics = null;
    _fittsActiveKey = '';
    _fittsActiveCards = null;
    _fittsFinalCards = null;
    _fittsHistoryEntries = new Map();
    document.getElementById('li-round-history')?.replaceChildren();
    document.getElementById('li-round-final')?.replaceChildren();
    _renderRounds();
    if (_isRoundAssessment()) LabReview.StreamRouter.send({ type: 'db.get_participants' });

    if (_state === 'CAPTURING') {
      _applyStateToPage();

      // Physiological cards only — the behavioral display is owned by the AAR module.
      const physioGrid = document.getElementById('li-physio-grid');
      if (!_isRoundAssessment()) LabReview.MetricCardRenderer.renderCards(null, physioGrid, _drillKey);

      // Tell AAR Dashboard which drill is active (for charts)
      _mountAARDashboard();
      if (_drillKey !== 'fitts_pvt_clay') _activeDisplay?.onScenarioStarted?.(payload);
    }
  }

  function _onScenarioStopped(payload) {
    _activeDisplay?.onScenarioStopped?.(payload);
    if (_state === 'CAPTURING' || _state === 'SUMMARIZING') {
      if (_state === 'CAPTURING') _setState('SUMMARIZING');
      _renderRounds();
      LabReview.MetricCardRenderer.freezeCards();
      _showResultsBanner(payload.manual
        ? 'Drill stopped manually — computing final metrics…'
        : 'Scenario complete — computing final metrics…');
    }
  }

  function _onMetricsReady(msg) {
    if (_state === 'IDLE') return;
    if (_sessionId && msg.session_uuid && msg.session_uuid !== _sessionId) return;
    if (Array.isArray(msg.rounds)) {
      for (const round of msg.rounds) if (_roundId(round)) _roundStore.set(_roundId(round), round);
      _rounds = msg.rounds.map(round => _mergeRound(round));
    }
    if (_isRoundAssessment()) _finalFittsMetrics = msg.error ? null : msg.metrics || null;
    // Overwrite live card values with the final computed metrics from Analysis
    const metrics = msg.metrics || msg;
    if (msg.metrics) LabReview.MetricCardRenderer.updateAllCards(metrics, 'trial');

    // Also let the AAR Dashboard render any charts it produces
    if (msg.metrics && !_isRoundAssessment()) _activeDisplay?.onMetricsReady?.(metrics);

    _setState('RESULTS');
    _renderRounds();
    if (msg.error || !msg.auto_saved || !msg.session_uuid) {
      const detail = msg.error === 'no_trials'
        ? 'No valid trials were recorded, so this drill was not saved.'
        : 'Final metrics could not be saved. Check the backend log.';
      _showResultsBanner(detail);
      _showNoResultsPanel(detail);
      return;
    }
    _showResultsBanner('Scenario complete — auto-saved');

    // Store session identity for confirm/discard panel
    _pendingSessionUuid = msg.session_uuid || '';
    _pendingParticipantName = msg.participant_name || '';

    // Show Confirm/Discard panel and request participant list for dropdown
    if (_isRoundAssessment() && !_rounds.some(round => round.ended_reason === 'completed')) {
      _showNoResultsPanel('No completed rounds to save.');
    } else {
      _showConfirmPanel(_pendingParticipantName);
    }
    LabReview.StreamRouter.send({ type: 'db.get_participants' });
  }

  function _onApiError(msg) {
    if (msg.context === 'live.activate') {
      const label = document.getElementById('li-conn-label');
      if (label) label.textContent = msg.message || 'Selected VirTra event source is unavailable.';
      return;
    }
    if (msg.context !== 'session.stop' || _state !== 'SUMMARIZING') return;
    LabReview.StreamRouter.deactivateLive();
    _setState('RESULTS');
    _showResultsBanner('The drill could not be stopped.');
    _showNoResultsPanel(msg.message || 'The drill could not be stopped.');
  }

  // ── Trial / biosignal update routing ──────────────────────────────────────

  /**
   * Handles trial updates from all supported drills.
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
    _pulseLiveEvent();
    _activeDisplay?.onEvent?.(evt);
  }

  function _onScenarioContextUpdated(payload) {
    if (!_scenarioPayload || payload.session_id !== _sessionId) return;
    Object.assign(_scenarioPayload, {
      scenario_name: payload.scenario_name || _scenarioPayload.scenario_name,
      simulator: payload.simulator || _scenarioPayload.simulator,
      simulator_system: payload.simulator_system || _scenarioPayload.simulator_system,
      weapon_type: payload.weapon_type || _scenarioPayload.weapon_type,
    });
    _applyStateToPage();
    _activeDisplay?.onContextUpdated?.(payload);
  }

  function _onCustomLslEvent(evt) {
    if (_runSource !== 'custom' || evt.session_id !== _sessionId || !Array.isArray(evt.rounds)) return;
    if (_scoringNotice) { _scoringNotice = ''; _refreshStatusDot(); }
    const previous = _rounds.find(r => r.ended_at == null);
    _rounds = evt.rounds.map(round => _mergeRound(round));
    if (_isRoundAssessment()) {
      const active = _rounds.find(round => round.ended_at == null);
      if (active && Number.isFinite(evt.timestamp) && Number.isFinite(active.started_at)) {
        active.metrics = { ...(active.metrics || {}),
          round_duration_s: Math.max(0, evt.timestamp - active.started_at) };
      }
      _renderRounds();
      _pulseLiveEvent();
      return;
    }
    const current = _rounds.find(r => r.ended_at == null);
    if (current && (!previous || _roundKey(current) !== _roundKey(previous))) {
      _activeDisplay?.onScenarioStarted?.(_scenarioPayload);
    }
    if (_scenarioPayload?.task_type === 'PVT_FITTS' && current?.metrics) {
      _activeDisplay?.onMetricsReady?.(current.metrics);
    }
    _renderRounds();
  }

  function _roundId(round) { return round?.round_id || round?.id || ''; }

  function _mergeRound(round) {
    const stored = _roundStore.get(_roundId(round));
    if (!stored) return round;
    return {
      ...stored, ...round,
      round_id: _roundId(round),
      ended_at: round.ended_at ?? (stored.status !== 'active' ? stored.ended_at : null),
      ended_reason: round.ended_reason || (stored.status !== 'active' ? stored.ended_reason : ''),
      save_status: stored.save_status,
      participant_name: stored.participant_name,
      draft_participant_name: stored.draft_participant_name,
      physiology: stored.physiology,
      quality: { ...(round.quality || {}), ...(stored.quality || {}) },
      metrics: round.ended_at == null ? (round.metrics || stored.metrics) :
        (stored.status === 'completed' ? stored.metrics : round.metrics || stored.metrics),
      components: round.components || stored.components,
    };
  }

  function _onAssessmentRoundUpdated(message) {
    if (!_isRoundAssessment() || message.run_id !== _sessionId || !message.round) return;
    const round = message.round;
    const id = _roundId(round);
    _roundStore.set(id, round);
    const index = _rounds.findIndex(item => _roundId(item) === id);
    if (index >= 0) _rounds[index] = _mergeRound(_rounds[index]);
    else _rounds.push(_mergeRound(round));
    const key = _roundKey(round);
    const entry = _fittsHistoryEntries.get(key);
    const wasOpen = !!entry?.details.open;
    if (entry) {
      entry.details.remove();
      _fittsHistoryEntries.delete(key);
    }
    if (_fittsActiveKey === key) {
      const assignment = document.querySelector('#li-round-current .li-round-assignment');
      if (assignment && document.activeElement !== assignment.querySelector('input')) {
        assignment.outerHTML = _roundParticipant(_rounds[index]);
        _wireFittsParticipant(document.getElementById('li-round-current'));
      }
    }
    _renderRounds();
    if (wasOpen) _fittsHistoryEntries.get(key).details.open = true;
  }

  function _onAssessmentRoundPhysiology(message) {
    if (!_isRoundAssessment() || message.run_id !== _sessionId) return;
    const physiology = message.physiology;
    if (!physiology?.round_id) return;
    _liveRoundPhysio.set(physiology.round_id, physiology);
    const round = _rounds.find(item => _roundId(item) === physiology.round_id);
    if (round?.ended_at == null) {
      const host = document.querySelector('#li-round-current .li-round-physiology');
      if (host) host.innerHTML = _physiologyContent(round);
    }
  }

  function _onAssessmentCommandResult(message) {
    if (!_isRoundAssessment()) return;
    if (!message.success) {
      if (message.round_id) {
        _setRoundError(message.round_id, message.error || 'Could not save this round.');
        _roundRequestIds.delete(message.round_id);
        const entry = _fittsHistoryEntries.get(_roundKey(_rounds.find(item =>
          _roundId(item) === message.round_id) || {}));
        if (entry) { entry.details.remove(); _fittsHistoryEntries.delete(entry.details.dataset.roundKey); }
        const status = document.querySelector('#li-round-current .li-round-save-status');
        if (status) status.textContent = message.error || 'Could not save this round.';
        _renderRounds();
      } else if (message.command === 'run.save_remaining') {
        const error = document.getElementById('sp-save-error');
        if (error) error.textContent = message.error || 'Could not save remaining rounds.';
        const button = document.getElementById('sp-confirm-btn');
        if (button) button.disabled = false;
      }
      return;
    }
    const rows = Array.isArray(message.result) ? message.result :
      message.result?.id ? [message.result] : [];
    for (const round of rows) {
      _roundErrors.delete(_roundId(round));
      _roundRequestIds.delete(_roundId(round));
      _onAssessmentRoundUpdated({ run_id: round.run_id, round });
      if (message.command === 'round.assignment.update' &&
          round.status === 'completed' && round.save_status === 'draft' &&
          round.draft_participant_name) {
        LabReview.StreamRouter.send({ type: 'round.save', round_id: round.id,
          participant_name: round.draft_participant_name,
          request_id: window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}` });
      }
    }
    if (rows.some(round => round.save_status === 'saved')) {
      LabReview.StreamRouter.send({ type: 'db.get_participants' });
    }
    if (message.command === 'run.save_remaining') {
      const panel = document.getElementById('li-save-panel');
      if (panel) panel.innerHTML = `<div class="save-panel-inner save-panel-inner--saved">
        <span class="save-panel-saved-text">Completed rounds saved.</span>
        <button class="save-btn-new-session" id="sp-new-session-btn-ok">New Session</button></div>`;
      document.getElementById('sp-new-session-btn-ok')?.addEventListener('click', _resetToIdle);
    }
  }

  function _pulseLiveEvent() {
    if (_isRoundAssessment()) _fittsActiveCards?.flashVisible();
  }

  function _metricTiles(metrics) {
    if (_scenarioPayload?.task_type === 'PVT_FITTS') {
      const defs = LabReview.AAR.Standardized.specs.PVT.groups.flatMap(([, cards]) => cards)
        .filter(def => def.source !== 'scenario' && metrics?.[def.path] != null);
      const tiles = defs.map(def => `<div class="li-round-metric"><span>${_escHtml(def.label)}</span><strong>${_escHtml(metrics[def.path])}${def.unit ? ` ${_escHtml(def.unit)}` : ''}</strong></div>`);
      return tiles.length ? `<div class="li-round-metrics">${tiles.join('')}</div>` : '';
    }
    const tiles = Object.entries(metrics || {}).filter(([, value]) =>
      typeof value === 'number' || typeof value === 'string').slice(0, 12)
      .map(([key, value]) => `<div class="li-round-metric"><span>${_escHtml(key.replace(/_/g, ' '))}</span><strong>${_escHtml(value)}</strong></div>`);
    return tiles.length ? `<div class="li-round-metrics">${tiles.join('')}</div>` : '';
  }

  function _renderRoundAggregate(round) {
    const metrics = round.metrics || {};
    if (!Object.keys(metrics).length) return '';
    const components = Object.values(round.components || {});
    if (components.length === 1 &&
        JSON.stringify(components[0].metrics || {}) === JSON.stringify(metrics)) return '';
    return `<div class="li-round-aggregate"><h4>Round summary</h4>${_metricTiles(metrics)}</div>`;
  }

  function _roundDifficulty(round) {
    if (!_isRoundAssessment()) return '';
    const settings = round.settings || {};
    const fields = [
      ['Distance', 'active_distance_meters', 'm'],
      ['Visibility', 'active_visibility_duration_seconds', 's'],
      ['Spawn interval', 'active_spawn_interval_seconds', 's'],
      ['Targets', 'active_targets_per_round', ''],
      ['Concurrent', 'active_concurrent_targets', ''],
    ];
    const values = fields.filter(([, key]) => settings[key] != null)
      .map(([label, key, unit]) => `<div><dt>${_escHtml(label)}</dt><dd>${_escHtml(settings[key])}${unit}</dd></div>`);
    const preset = round.preset_mixed ? 'Mixed presets' : (round.preset_label || 'Not reported');
    const provenance = round.preset_provenance === 'defaulted' ? 'Assigned default' :
      round.preset_provenance === 'reported' ? 'Reported by scenario' : '';
    return `<section class="li-preset-panel" aria-label="Round preset">
      <div class="li-preset-panel__heading"><span>Preset</span><strong>${_escHtml(preset)}</strong>
      <small>${_escHtml(provenance)}</small></div>
      <dl class="li-preset-grid">${values.join('')}</dl></section>`;
  }

  function _roundKey(round) { return `${round.round_number}:${round.attempt || 1}`; }

  function _roundParticipant(round) {
    if (!_isRoundAssessment()) return '';
    const key = _roundKey(round);
    const name = _roundNames[key] ?? round.participant_name ?? round.draft_participant_name ?? '';
    const saved = round.save_status === 'saved';
    const active = round.ended_at == null;
    const canSave = active || round.ended_reason === 'completed';
    const status = _roundErrors.get(_roundId(round)) ||
      (saved ? `Saved to ${round.participant_name}` : active ? 'Name can be entered during the round' :
       canSave ? 'Completed round is ready to save' : 'Round was not completed');
    return `<div class="li-round-assignment"><label class="li-round-participant">Participant for this round
      <span class="li-round-assignment__row"><input class="pr-input li-round-participant-input" type="text" list="li-round-name-list"
        data-round-key="${_escHtml(key)}" data-round-id="${_escHtml(_roundId(round))}" value="${_escHtml(name)}"
        placeholder="Type or choose a name" autocomplete="off">
        ${canSave ? `<button type="button" class="li-round-save-btn">${active ? 'Set name' : saved ? 'Change name' : 'Save round'}</button>` : ''}
      </span></label><span class="li-round-save-status ${saved ? 'is-saved' : ''}">${_escHtml(status)}</span></div>`;
  }

  function _wireFittsParticipant(container) {
    const input = container.querySelector('.li-round-participant-input');
    if (!input) return;
    input.addEventListener('input', () => {
      _roundNames[input.dataset.roundKey] = input.value;
      const round = _rounds.find(item => _roundId(item) === input.dataset.roundId);
      if (round?.ended_at == null && round?.save_status !== 'saved') {
        _scheduleDraftName(input.dataset.roundId, input.value);
      }
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); _saveRound(input.dataset.roundId); }
    });
    container.querySelector('.li-round-save-btn')?.addEventListener('click', () =>
      _saveRound(input.dataset.roundId));
  }

  function _scheduleDraftName(roundId, name) {
    clearTimeout(_roundDraftTimers.get(roundId));
    _roundDraftTimers.set(roundId, setTimeout(() => {
      if (!LabReview.StreamRouter.send({ type: 'round.assignment.update', round_id: roundId,
        participant_name: name.trim() })) {
        _setRoundError(roundId, 'Connection lost. Retry setting the name.');
      }
      _roundDraftTimers.delete(roundId);
    }, 450));
  }

  function _setRoundError(roundId, message) {
    _roundErrors.set(roundId, message);
    const input = [...document.querySelectorAll('.li-round-participant-input')]
      .find(element => element.dataset.roundId === roundId);
    const status = input?.closest('.li-round-assignment')?.querySelector('.li-round-save-status');
    if (status) status.textContent = message;
  }

  function _saveRound(roundId) {
    const round = _rounds.find(item => _roundId(item) === roundId);
    if (!round) return;
    const name = (_roundNames[_roundKey(round)] ?? round.participant_name ??
      round.draft_participant_name ?? '').trim();
    if (!name) {
      _setRoundError(roundId, 'Enter a participant name first.');
      return;
    }
    clearTimeout(_roundDraftTimers.get(roundId));
    _roundDraftTimers.delete(roundId);
    _roundErrors.delete(roundId);
    if (round.ended_at == null) {
      if (!LabReview.StreamRouter.send({ type: 'round.assignment.update', round_id: roundId,
        participant_name: name })) {
        _setRoundError(roundId, 'Connection lost. Retry setting the name.');
      }
      return;
    }
    const command = round.save_status === 'saved' ? 'round.reassign' : 'round.save';
    const requestId = _roundRequestIds.get(roundId) ||
      (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`);
    _roundRequestIds.set(roundId, requestId);
    if (!LabReview.StreamRouter.send({ type: command, round_id: roundId,
      participant_name: name, request_id: requestId })) {
      _roundRequestIds.delete(roundId);
      _setRoundError(roundId, 'Connection lost. Retry saving this round.');
    }
  }

  function _physiologyContent(round) {
    const physiology = round.ended_at == null
      ? (_liveRoundPhysio.get(_roundId(round)) || round.physiology || {})
      : (round.physiology || _liveRoundPhysio.get(_roundId(round)) || {});
    const quality = round.quality || {};
    const display = (value, unit, digits = 1) => value == null ? '—' :
      `${Number(value).toFixed(digits)} ${unit}`;
    const values = [
      ['Heart Rate', display(physiology.hr_mean_bpm, 'bpm'), physiology.hr_n_samples],
      ['HRV RMSSD', display(physiology.hrv_rmssd_ms, 'ms'), physiology.hrv_n_intervals],
      ['Pupil Diameter', display(physiology.pupil_mean_mm, 'mm', 2),
        Math.min(physiology.pupil_l_n || 0, physiology.pupil_r_n || 0)],
    ];
    return `<h3>Physiological Data <small>${_escHtml(quality.physiology_status ||
      (round.ended_at == null ? 'Live round' : 'Finalizing'))}</small></h3>
      <div class="li-round-physiology__grid">${values.map(([label, value, count]) =>
        `<div class="li-round-physiology__item"><span>${label}</span><strong>${value}</strong>
        <small>${count || 0} samples</small></div>`).join('')}</div>`;
  }

  function _fittsRoundTitle(round) {
    return `Round ${round.round_number}${round.attempt > 1 ? ` · Attempt ${round.attempt}` : ''}`;
  }

  function deriveFittsView(rounds, state, finalMetrics) {
    const rows = Array.isArray(rounds) ? rounds : [];
    const current = state === 'CAPTURING' ? rows.find(round => round.ended_at == null) || null : null;
    const ended = rows.filter(round => round.ended_at != null);
    const completed = ended.filter(round => round.ended_reason === 'completed');
    const count = finalMetrics?.completed_rounds ?? completed.length;
    return {
      current, ended, completed, count,
      showBetween: state === 'CAPTURING' && !current && ended.length > 0,
      showFinal: state === 'RESULTS' && !!finalMetrics,
      showFinalCards: state === 'RESULTS' && count > 0 &&
        ['scored', 'partial_attribution'].includes(finalMetrics?.scoring_status),
    };
  }

  function _roundMetricNote(round) {
    const metrics = round.metrics || {};
    const notes = [];
    if (metrics.rt_count === 0) {
      notes.push(metrics.total_shots_fired
        ? 'No response time was attributable to a target.'
        : 'Response times require an attributable shot.');
    } else if (metrics.rt_count === 1) {
      notes.push('Response time standard deviation requires two responses.');
    }
    if (round.ended_at != null) {
      if (metrics.unresolved_trials > 0) {
        notes.push(`${metrics.unresolved_trials} target presentation${metrics.unresolved_trials === 1 ? '' : 's'} unresolved; response and omission rates are unavailable.`);
      }
      if (metrics.total_shots_fired > 0 && metrics.shot_misses == null) {
        notes.push('Shot misses are unavailable because some shot outcomes were not reported.');
      }
      if (metrics.total_shots_fired === 0) {
        notes.push('Hit accuracy requires a fired shot.');
      }
    }
    return notes.join(' ');
  }

  function _renderFittsRounds(section, currentEl, historyEl) {
    const finalEl = document.getElementById('li-round-final');
    const betweenEl = document.getElementById('li-round-between');
    const { current, ended, count, showBetween, showFinal, showFinalCards } =
      deriveFittsView(_rounds, _state, _finalFittsMetrics);
    section.classList.add('li-round-section--standard');
    section.style.display = _rounds.length || _state === 'SUMMARIZING' || _state === 'RESULTS' ? '' : 'none';

    if (current) {
      const key = _roundKey(current);
      if (_fittsActiveKey !== key) {
        _fittsActiveKey = key;
        currentEl.innerHTML = `<div class="li-round-head"><h2>${_escHtml(_fittsRoundTitle(current))}</h2></div>` +
          `<div class="li-round-meta"></div>${_roundParticipant(current)}` +
          `<div class="li-round-card-host"></div><p class="li-round-live-note"></p>` +
          `<section class="li-round-physiology"></section>`;
        _wireFittsParticipant(currentEl);
        _fittsActiveCards = LabReview.AAR.Standardized.createMetricCards(_assessmentMetricType(), 'round');
        _fittsActiveCards.mount(currentEl.querySelector('.li-round-card-host'), current.metrics);
      }
      currentEl.querySelector('.li-round-meta').innerHTML = _roundDifficulty(current);
      const pending = current.metrics?.pending_presentations || 0;
      const pendingShots = current.metrics?.pending_shots || 0;
      currentEl.querySelector('.li-round-live-note').textContent =
        pending || pendingShots
          ? `Live values · ${pending} open target${pending === 1 ? '' : 's'} · ${pendingShots} shot${pendingShots === 1 ? '' : 's'} awaiting result`
          : 'Live values update as events arrive.';
      const metricNote = _roundMetricNote(current);
      if (metricNote) currentEl.querySelector('.li-round-live-note').textContent += ` ${metricNote}`;
      currentEl.querySelector('.li-round-physiology').innerHTML = _physiologyContent(current);
      _fittsActiveCards.update(current.metrics);
      currentEl.style.display = '';
    } else {
      if (_fittsActiveKey) currentEl.replaceChildren();
      _fittsActiveKey = '';
      _fittsActiveCards = null;
      currentEl.style.display = 'none';
    }

    if (ended.length && !_fittsHistoryEntries.size) {
      historyEl.innerHTML = '<h3>Previous rounds</h3>';
    }
    for (const round of ended) {
      const key = _roundKey(round);
      let entry = _fittsHistoryEntries.get(key);
      if (!entry) {
        const details = document.createElement('details');
        details.className = 'li-round-previous';
        details.dataset.roundKey = key;
        details.open = round.ended_reason === 'completed' && round.save_status !== 'saved';
        details.innerHTML = `<summary></summary><div class="li-round-detail-body">` +
          `<div class="li-round-meta"></div>${_roundParticipant(round)}` +
          `<div class="li-round-card-host"></div><p class="li-round-metric-note"></p>` +
          `<section class="li-round-physiology"></section></div>`;
        historyEl.appendChild(details);
        const cards = LabReview.AAR.Standardized.createMetricCards(_assessmentMetricType(), 'round');
        cards.mount(details.querySelector('.li-round-card-host'), round.metrics);
        _wireFittsParticipant(details);
        entry = { details, cards };
        _fittsHistoryEntries.set(key, entry);
      }
      const preset = round.preset_mixed ? 'Mixed presets' : round.preset_label || 'Preset not reported';
      entry.details.querySelector('summary').textContent =
        `${_fittsRoundTitle(round)} · ${round.ended_reason || 'incomplete'} · ${preset}`;
      entry.details.querySelector('.li-round-meta').innerHTML = _roundDifficulty(round);
      entry.details.querySelector('.li-round-metric-note').textContent = _roundMetricNote(round);
      entry.details.querySelector('.li-round-physiology').innerHTML = _physiologyContent(round);
      entry.cards.update(round.metrics);
    }
    historyEl.style.display = ended.length ? '' : 'none';

    if (betweenEl) {
      betweenEl.textContent = 'Waiting for next round';
      betweenEl.style.display = showBetween ? '' : 'none';
    }
    if (finalEl) {
      finalEl.style.display = showFinal ? '' : 'none';
      if (showFinalCards) {
        if (!_fittsFinalCards) {
          finalEl.innerHTML = '<div class="li-round-head"><h2>Grand Total / Average</h2></div>' +
            '<p class="li-round-final-context"></p><div class="li-round-card-host"></div>';
          _fittsFinalCards = LabReview.AAR.Standardized.createMetricCards(_assessmentMetricType(), 'run');
          _fittsFinalCards.mount(finalEl.querySelector('.li-round-card-host'), _finalFittsMetrics);
        }
        const partial = _finalFittsMetrics.scoring_status === 'partial_attribution';
        finalEl.querySelector('.li-round-final-context').textContent =
          `${count} completed ${count === 1 ? 'round' : 'rounds'} · Session aggregate${partial ? ' · Partial shot attribution' : ''}`;
        _fittsFinalCards.update(_finalFittsMetrics);
      } else if (showFinal) {
        finalEl.textContent = 'No scored completed rounds to summarize.';
      }
    }
    const list = document.getElementById('li-round-name-list');
    if (list) list.innerHTML = _knownParticipants.map(name => `<option value="${_escHtml(name)}">`).join('');
  }

  function _renderRounds() {
    const section = document.getElementById('li-round-section');
    const currentEl = document.getElementById('li-round-current');
    const historyEl = document.getElementById('li-round-history');
    if (!section || !currentEl || !historyEl) return;
    if (_isRoundAssessment() && _runSource === 'custom') {
      _renderFittsRounds(section, currentEl, historyEl);
      return;
    }
    section.classList.remove('li-round-section--standard');
    section.style.display = _runSource === 'custom' && _rounds.length ? '' : 'none';
    if (_runSource !== 'custom') return;
    const focused = document.activeElement?.classList?.contains('li-round-participant-input')
      ? { key: document.activeElement.dataset.roundKey,
          start: document.activeElement.selectionStart,
          end: document.activeElement.selectionEnd } : null;
    const expanded = new Set([...historyEl.querySelectorAll('.li-round-previous[open]')]
      .map(el => el.dataset.roundKey));
    const current = _rounds.find(r => r.ended_at == null);
    const completed = _rounds.filter(r => r.ended_at != null).reverse();
    currentEl.style.display = current ? '' : 'none';
    currentEl.innerHTML = current
      ? `<h3>Round ${_escHtml(current.round_number)}${current.attempt > 1 ? ` · Attempt ${current.attempt}` : ''}</h3>` +
        _roundDifficulty(current) + _roundParticipant(current) + _renderComponents(current)
      : '';
    if (current && _captureStatus === 'partial') currentEl.insertAdjacentHTML('afterbegin', '<p>Partial capture: stream joined after the scenario began.</p>');
    historyEl.innerHTML = completed.length
      ? '<h3>Previous rounds</h3>' + completed.map(r =>
        `<details class="li-round-previous" data-round-key="${_escHtml(_roundKey(r))}" ${expanded.has(_roundKey(r)) ? 'open' : ''}><summary>Round ${_escHtml(r.round_number)}${r.attempt > 1 ? ` · Attempt ${r.attempt}` : ''} · ${_escHtml(r.ended_reason)} · ${_escHtml(r.preset_mixed ? 'Mixed presets' : r.preset_label || 'Preset not reported')}</summary>${_roundDifficulty(r)}${_roundParticipant(r)}${_renderComponents(r)}${_renderRoundAggregate(r)}</details>`).join('')
      : '';
    document.querySelectorAll('.li-round-participant-input').forEach(input => {
      input.addEventListener('input', () => { _roundNames[input.dataset.roundKey] = input.value; });
    });
    const list = document.getElementById('li-round-name-list');
    if (list) list.innerHTML = _knownParticipants.map(name => `<option value="${_escHtml(name)}">`).join('');
    if (focused) {
      const input = [...document.querySelectorAll('.li-round-participant-input')]
        .find(el => el.dataset.roundKey === focused.key);
      if (input) { input.focus(); input.setSelectionRange(focused.start, focused.end); }
    }
  }

  function _renderComponents(round) {
    const entries = Object.entries(round.components || {});
    return entries.length ? `<div class="li-round-components">${entries.map(([name, detail]) =>
      `<div class="li-round-component ${name === round.component && !round.ended_at ? 'is-current' : ''}"><h4>${_escHtml(name.toUpperCase())}</h4>${_metricTiles(detail.metrics)}</div>`).join('')}</div>` : '';
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
    if (_scenarioPayload && msg.weapon_type) _scenarioPayload.weapon_type = msg.weapon_type;
    _activeDisplay?.onWeaponInfo?.(msg);
  }

  // ── Confirm / Discard panel ────────────────────────────────────────────────

  /** State for the active confirm/discard panel */
  let _pendingSessionUuid    = '';
  let _pendingParticipantName = '';
  let _knownParticipants     = [];

  function _showConfirmPanel(autoName) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;

    const isAssessment = _isRoundAssessment();
    if (isAssessment && !_rounds.some(round =>
      round.ended_reason === 'completed' && round.save_status !== 'saved')) {
      panel.innerHTML = `<div class="save-panel-inner save-panel-inner--saved">
        <span class="save-panel-saved-text">All completed rounds are saved.</span>
        <button class="save-btn-new-session" id="sp-new-session-btn-ok">New Session</button></div>`;
      panel.style.display = '';
      document.getElementById('sp-new-session-btn-ok')?.addEventListener('click', _resetToIdle);
      return;
    }
    const safeName = _escHtml(isAssessment ? '' : (autoName || ''));

    panel.innerHTML = `
      <div class="save-panel-inner" id="sp-inner">
        <div class="save-panel-header">
          <h3 class="save-panel-title">${isAssessment ? 'Save completed rounds' : 'Session Auto-Saved'}</h3>
          <p class="save-panel-subtitle">${isAssessment
            ? 'Enter one name for completed rounds that have no participant. Names entered on individual rounds take priority.'
            : 'Review and confirm participant details, or discard this session.'}</p>
        </div>

        <div class="save-panel-fields">
          <div class="save-field-group save-field-group--full">
            <label class="save-field-label" for="sp-pname">${isAssessment ? 'Fallback participant' : 'Participant Name'}</label>
            <div class="save-field-combo">
              <input id="sp-pname" class="save-field-input" type="text" list="sp-pname-list"
                     placeholder="Type name or choose from list"
                     value="${safeName}" autocomplete="off">
              <datalist id="sp-pname-list"></datalist>
            </div>
            ${isAssessment ? '' : `<span class="save-field-hint">Auto-assigned: <code>${safeName || 'unknown_participant_N'}</code>. Update if needed.</span>`}
          </div>
          ${isAssessment ? '' : `<div class="save-field-group save-field-group--full">
            <label class="save-field-label" for="sp-notes">Notes</label>
            <textarea id="sp-notes" class="save-field-textarea"
                      placeholder="Optional: fatigue level, sleep hours, study condition…"
                      rows="2"></textarea>
          </div>`}
        </div>

        <div class="save-panel-actions">
          <button class="save-btn-primary" id="sp-confirm-btn">${isAssessment ? 'Save remaining rounds' : 'Confirm'}</button>
          <span class="li-save-error" id="sp-save-error" role="alert"></span>
          <span class="save-panel-spacer"></span>
          ${isAssessment && _rounds.some(round => round.save_status === 'saved')
            ? '' : '<button class="save-btn-discard" id="sp-discard-btn">Discard</button>'}
        </div>
      </div>
    `;

    panel.style.display = '';
    document.getElementById('sp-confirm-btn')
      ?.addEventListener('click', _onConfirmClicked);
    document.getElementById('sp-discard-btn')
      ?.addEventListener('click', _onDiscardClicked);

    // Populate datalist if we already have the list
    _populateDatalist(_knownParticipants);
  }

  function _showNoResultsPanel(message) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;
    panel.innerHTML = `<div class="save-panel-inner save-panel-inner--no-results">
      <span class="save-panel-no-results-text">${_escHtml(message)}</span>
      <button class="save-btn-new-session" id="sp-new-session-btn-empty">New Session</button>
    </div>`;
    panel.style.display = '';
    document.getElementById('sp-new-session-btn-empty')
      ?.addEventListener('click', _resetToIdle);
  }

  function _onParticipantsList(msg) {
    _knownParticipants = msg.names || [];
    _populateDatalist(_knownParticipants);
    const list = document.getElementById('li-round-name-list');
    if (list) list.innerHTML = _knownParticipants.map(name => `<option value="${_escHtml(name)}">`).join('');
  }

  function _populateDatalist(names) {
    const dl = document.getElementById('sp-pname-list');
    if (!dl) return;
    dl.innerHTML = names
      .map(n => `<option value="${_escHtml(n)}">`)
      .join('');
  }

  function _onConfirmClicked() {
    const name  = document.getElementById('sp-pname')?.value.trim() || '';
    const notes = document.getElementById('sp-notes')?.value.trim() || '';
    const btn   = document.getElementById('sp-confirm-btn');
    if (_isRoundAssessment()) {
      const remaining = _rounds.filter(round => round.ended_reason === 'completed' &&
        round.save_status !== 'saved');
      const explicitNames = Object.fromEntries(remaining.map(round => [
        _roundId(round), (_roundNames[_roundKey(round)] ?? round.draft_participant_name ?? '').trim(),
      ]).filter(([, value]) => value));
      if (remaining.some(round => !explicitNames[_roundId(round)]) && !name) {
        const error = document.getElementById('sp-save-error');
        if (error) error.textContent = 'Enter a fallback participant for unnamed rounds.';
        document.getElementById('sp-pname')?.focus();
        return;
      }
      if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
      const sent = LabReview.StreamRouter.send({ type: 'run.save_remaining', run_id: _sessionId,
        participant_name: name, explicit_names: explicitNames,
        request_id: window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}` });
      if (!sent) {
        if (btn) { btn.disabled = false; btn.textContent = 'Save remaining rounds'; }
        const error = document.getElementById('sp-save-error');
        if (error) error.textContent = 'Connection lost. Retry saving the remaining rounds.';
      }
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Confirming…'; }

    LabReview.StreamRouter.send({
      type:             'db.confirm_session',
      session_uuid:     _pendingSessionUuid,
      participant_name: name,
      notes,
    });
  }

  function _onDiscardClicked() {
    const btn = document.getElementById('sp-discard-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Discarding…'; }
    if (!confirm('Discard this session? It will be deleted from the database and disk.')) {
      if (btn) { btn.disabled = false; btn.textContent = 'Discard'; }
      return;
    }
    LabReview.StreamRouter.send({
      type:         'db.discard_session',
      session_uuid: _pendingSessionUuid,
    });
  }

  function _onSessionSaved(msg) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;
    if (msg.success) {
      const confirmation = _drillKey === 'fitts_pvt_clay'
        ? 'Round participants confirmed.'
        : `Session confirmed for <strong>${_escHtml(msg.participant_name)}</strong>.`;
      panel.innerHTML = `
        <div class="save-panel-inner save-panel-inner--saved">
          <span class="save-panel-check">✓</span>
          <span class="save-panel-saved-text">${confirmation}</span>
          <button class="save-btn-new-session" id="sp-new-session-btn-ok">New Session</button>
        </div>
      `;
      document.getElementById('sp-new-session-btn-ok')
        ?.addEventListener('click', _resetToIdle);
    } else {
      const btn = document.getElementById('sp-confirm-btn');
      if (btn) { btn.disabled = false; btn.textContent = 'Confirm'; }
      console.error('[LiveIntel] db.confirm_session failed:', msg.detail);
    }
  }

  function _onSessionDiscarded(msg) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;
    if (msg.success) {
      panel.innerHTML = `
        <div class="save-panel-inner save-panel-inner--discarded">
          <span class="save-panel-check save-panel-check--discard"></span>
          <span class="save-panel-saved-text">Session discarded.</span>
          <button class="save-btn-new-session" id="sp-new-session-btn-disc">New Session</button>
        </div>
      `;
      document.getElementById('sp-new-session-btn-disc')
        ?.addEventListener('click', _resetToIdle);
    } else {
      const btn = document.getElementById('sp-discard-btn');
      if (btn) { btn.disabled = false; btn.textContent = 'Discard'; }
    }
  }

  // Legacy record-saved compat
  function _onLegacyRecordSaved(msg) {
    const panel = document.getElementById('li-save-panel');
    if (!panel) return;
    if (msg.success) {
      panel.innerHTML = `
        <div class="save-panel-inner save-panel-inner--saved">
          <span class="save-panel-check">✓</span>
          <span class="save-panel-saved-text">Session record saved.</span>
          <button class="save-btn-new-session" id="sp-new-session-btn-ok2">New Session</button>
        </div>
      `;
      document.getElementById('sp-new-session-btn-ok2')
        ?.addEventListener('click', _resetToIdle);
    } else {
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
    _runSource = '';
    const metricsSource = document.getElementById('li-metrics-source');
    if (metricsSource) metricsSource.textContent = '';
    _rounds = [];
    for (const timer of _roundDraftTimers.values()) clearTimeout(timer);
    _roundStore = new Map();
    _roundErrors = new Map();
    _roundDraftTimers = new Map();
    _roundRequestIds = new Map();
    _liveRoundPhysio = new Map();
    _roundNames = {};
    _finalFittsMetrics = null;
    _fittsActiveKey = '';
    _fittsActiveCards = null;
    _fittsFinalCards = null;
    _fittsHistoryEntries = new Map();
    _scenarioPayload = null;
    _sessionId = '';
    _captureStatus = 'complete';
    _scoringNotice = '';
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

  return { init, deriveFittsView };

})();
