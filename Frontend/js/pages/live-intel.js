/**
 * live-intel.js — LiveIntelPage (WP-08)
 *
 * Live Performance Intelligence — active capture + real-time rolling stats
 * + post-capture epoch analysis triggered from the buffered .db file.
 *
 * State machine:
 *   IDLE → CAPTURING → COUNTDOWN → SUMMARIZING → RESULTS
 *
 * Dependencies (all must be loaded first):
 *  - LabReplay.TopBarManager
 *  - LabReplay.StreamRouter (sendIntelStart, sendIntelStop, etc.)
 *  - LabReplay.EventBus
 *  - LabReplay.AnalysisAPI (for post-capture epoch analysis)
 *  - LabReplay.EpochChart, LabReplay.StatsPanel (for results rendering)
 *
 * WP-07 backend must be complete before the session capture features work.
 * Until WP-07 is done, this page shows the IDLE state with all UI controls
 * properly rendered and wired (they will no-op until backend responds).
 */

window.LabReplay = window.LabReplay || {};

LabReplay.LiveIntelPage = (function () {

  // ── State ────────────────────────────────────────────────────────────────────

  let _state          = 'IDLE';    // IDLE | CAPTURING | COUNTDOWN | SUMMARIZING | RESULTS
  let _participantId  = '';
  let _sessionName    = '';
  let _drill          = 'behdisc';
  let _baselineS      = 2.0;
  let _analysisS      = 2.0;
  let _autoStopS      = 300;
  let _countdownSec   = 0;
  let _elapsedS       = 0;
  let _rollingStats   = { hr: null, pupil: null, motion: null };
  let _savedDb        = null;   // { db_filename, db_path } from live.intel.saved

  // Elapsed RAF
  let _elapsedRAF     = null;
  let _elapsedMono    = null;
  let _elapsedAtAnchor = 0;

  const DRILLS = [
    { key: 'behdisc',  label: 'BehDisc' },
    { key: 'pvt',      label: 'PVT' },
    { key: 'l2gonogo', label: 'L2 Go/No-Go' },
  ];

  // ── Initialization ───────────────────────────────────────────────────────────

  function init() {
    LabReplay.TopBarManager.register('live-intel', _renderTopBar, _teardown);

    // Wire EventBus
    LabReplay.EventBus.on('session-info-received',  _onSessionInfoReceived);
    LabReplay.EventBus.on('live-intel-state',       _onIntelState);
    LabReplay.EventBus.on('live-intel-countdown',   _onCountdown);
    LabReplay.EventBus.on('live-intel-saved',       _onSaved);
    LabReplay.EventBus.on('live-intel-error',       _onError);
    LabReplay.EventBus.on('virtra-event',           _onVirtraEvent);

    // Subscribe to all live LSL streams when catalog appears/changes.
    // Without this, the WebSocket bridge never forwards VirTra samples here.
    LabReplay.EventBus.on('catalog-updated', (catalog) => {
      if (catalog && catalog.length > 0) {
        LabReplay.StreamRouter.subscribeAll();
      }
    });

    // Wire BehDiscEngine callbacks
    const E = LabReplay.BehDiscEngine;
    if (E) {
      E.onScenarioStart = (session) => {
        if (_state !== 'CAPTURING') return;
        LabReplay.LiveIntelUI?.setScenarioName(session.scenarioName || 'BehDisc');
        LabReplay.LiveIntelUI?.reset();
      };
      E.onEngagementStart = (eng) => {
        LabReplay.LiveIntelUI?.updateCurrentEngagement(eng);
      };
      E.onEngagementUpdate = (eng) => {
        LabReplay.LiveIntelUI?.updateCurrentEngagement(eng);
      };
      E.onEngagementComplete = (eng, stats) => {
        LabReplay.LiveIntelUI?.addEngagementRow(eng);
        LabReplay.LiveIntelUI?.updateMetrics(stats);
        LabReplay.LiveIntelUI?.clearCurrentEngagement();
      };
      E.onStatsUpdated = (stats) => {
        LabReplay.LiveIntelUI?.updateMetrics(stats);
      };
      E.onScenarioStop = (session) => {
        // Auto-stop capture when VirTra signals Scenario Stopped
        if (_state === 'CAPTURING' || _state === 'COUNTDOWN') {
          LabReplay.LiveIntelUI?.renderSessionSummary(session);
          // Persist engagement JSON to localStorage
          try {
            localStorage.setItem(
              `bd_session_${session.sessionId || Date.now()}`,
              JSON.stringify(session)
            );
          } catch (e) { /* storage full */ }
          _stopCapture();
        }
      };
    }

    LabReplay.EventBus.on('page-changed', (page) => {
      if (page === 'live-intel') _renderPageContent();
    });
  }

  // ── Top bar ──────────────────────────────────────────────────────────────────

  function _renderTopBar(slot) {
    // Top bar content depends on state
    slot.innerHTML = _buildTopBarHtml();
    _wireTopBar(slot);
    _applyStateToTopBar();
  }

  function _buildTopBarHtml() {
    return `
      <div class="topbar" id="live-intel-topbar">

        <!-- Participant & Session fields (IDLE / CAPTURING) -->
        <div class="topbar-section" id="li-meta-fields">
          <input class="topbar-input" id="li-participant" type="text"
                 placeholder="Participant ID" value="${_participantId}"
                 style="width:140px" spellcheck="false">
          <input class="topbar-input" id="li-session-name" type="text"
                 placeholder="Session name" value="${_sessionName}"
                 style="width:130px" spellcheck="false">
          <select class="topbar-select" id="li-drill" style="width:110px">
            ${DRILLS.map(d => `<option value="${d.key}" ${_drill===d.key?'selected':''}>${d.label}</option>`).join('')}
          </select>
        </div>

        <div class="topbar-sep"></div>

        <!-- BL / AN spinners -->
        <div class="topbar-section" id="li-window-section">
          <div class="topbar-spinner">
            <label class="topbar-spinner-label">Baseline Window</label>
            <input type="number" id="li-baseline" value="${_baselineS}" min="0.5" max="10" step="0.5">
            <span style="font-size:10px;color:var(--text-dim)">s</span>
          </div>
          <div class="topbar-spinner">
            <label class="topbar-spinner-label">Analysis Window</label>
            <input type="number" id="li-analysis" value="${_analysisS}" min="0.5" max="10" step="0.5">
            <span style="font-size:10px;color:var(--text-dim)">s</span>
          </div>
        </div>

        <div class="topbar-sep" id="li-elapsed-sep" style="display:none"></div>

        <!-- Elapsed (CAPTURING) -->
        <div class="topbar-elapsed" id="li-elapsed-section" style="display:none">
          <span class="topbar-elapsed-label">Session Elapsed</span>
          <span class="topbar-elapsed-value" id="li-elapsed-value">00:00:00</span>
        </div>

        <!-- Stop button (CAPTURING) -->
        <button class="topbar-btn-stop" id="li-stop-btn" style="display:none">
          Stop Recording
        </button>

        <!-- Start button -->
        <div class="topbar-section topbar-section--right">
          <button class="topbar-btn-primary" id="li-start-btn">▶ Start</button>
        </div>

      </div>
    `;
  }

  function _wireTopBar(slot) {
    // Participant / session inputs — sync on change
    slot.querySelector('#li-participant')?.addEventListener('input', (e) => {
      _participantId = e.target.value;
      if (_state === 'CAPTURING') {
        LabReplay.StreamRouter.sendIntelUpdateMeta?.(_participantId, _sessionName);
      }
    });

    slot.querySelector('#li-session-name')?.addEventListener('input', (e) => {
      _sessionName = e.target.value;
      if (_state === 'CAPTURING') {
        LabReplay.StreamRouter.sendIntelUpdateMeta?.(_participantId, _sessionName);
      }
    });

    slot.querySelector('#li-drill')?.addEventListener('change', (e) => {
      _drill = e.target.value;
    });

    slot.querySelector('#li-baseline')?.addEventListener('change', (e) => {
      _baselineS = parseFloat(e.target.value) || 2.0;
    });

    slot.querySelector('#li-analysis')?.addEventListener('change', (e) => {
      _analysisS = parseFloat(e.target.value) || 2.0;
    });

    // Start button
    slot.querySelector('#li-start-btn')?.addEventListener('click', _startCapture);

    // Stop button
    slot.querySelector('#li-stop-btn')?.addEventListener('click', _stopCapture);
  }

  function _applyStateToTopBar() {
    const isCapturing  = _state === 'CAPTURING';
    const isCountdown  = _state === 'COUNTDOWN';
    const isResults    = _state === 'RESULTS' || _state === 'SUMMARIZING';

    _toggleEl('li-meta-fields',     !isResults);
    _toggleEl('li-window-section',  !isCapturing && !isCountdown);
    _toggleEl('li-elapsed-sep',     isCapturing || isCountdown);
    _toggleEl('li-elapsed-section', isCapturing || isCountdown);
    _toggleEl('li-stop-btn',        isCapturing || isCountdown);
    _toggleEl('li-start-btn',       !isCapturing && !isCountdown && !isResults);

    if (isCapturing || isCountdown) {
      _startElapsedRAF();
    } else {
      _stopElapsedRAF();
    }
  }

  // ── Page content ─────────────────────────────────────────────────────────────

  let _pageBuilt = false;

  function _renderPageContent() {
    const pageEl = document.getElementById('page-live-intel');
    if (!pageEl) return;

    // Only rebuild the shell once — re-navigating during CAPTURING would
    // wipe the live dashboard otherwise.
    if (_pageBuilt) { _applyStateToPage(); return; }
    _pageBuilt = true;

    pageEl.innerHTML = `
      <div class="live-intel-content" id="li-content">
        <!-- State banner (shown for CAPTURING / COUNTDOWN / SUMMARIZING) -->
        <div id="li-banner" style="display:none"></div>

        <!-- Rolling stats bar (CAPTURING only) -->
        <div class="rolling-stats-bar" id="li-rolling-stats" style="display:none">
          <div class="rolling-stat-card rolling-stat-card--hr">
            <div class="rolling-stat-label">Heart Rate</div>
            <div class="rolling-stat-value" id="li-stat-hr">—</div>
            <div class="rolling-stat-meta" id="li-stat-hr-n"></div>
          </div>
          <div class="rolling-stat-card rolling-stat-card--pupil">
            <div class="rolling-stat-label">Pupil Diameter</div>
            <div class="rolling-stat-value" id="li-stat-pupil">—</div>
            <div class="rolling-stat-meta" id="li-stat-pupil-n"></div>
          </div>
          <div class="rolling-stat-card rolling-stat-card--motion">
            <div class="rolling-stat-label">Motion</div>
            <div class="rolling-stat-value" id="li-stat-motion">—</div>
            <div class="rolling-stat-meta" id="li-stat-motion-n"></div>
          </div>
        </div>

        <!-- Main area: switches between IDLE placeholder, chart grid, and results -->
        <div id="li-main-area" style="flex:1;overflow:hidden;display:flex;flex-direction:column">

          <!-- IDLE state -->
          <div class="live-intel-idle" id="li-idle-overlay">
            <div class="live-intel-idle-title">Human Performance Intelligence</div>
            <div class="live-intel-idle-sub">
              Select a drill and press ▶ Start. The session begins automatically
              when VirTra fires "Scenario Started".
            </div>
          </div>

          <!-- CAPTURING: BehDisc live dashboard -->
          <div id="li-chart-area" style="display:none;flex:1;overflow-y:auto;padding:var(--space-md)">
            <div class="chart-grid" id="li-chart-grid"></div>
          </div>

          <!-- RESULTS -->
          <div class="live-intel-results" id="li-results-area" style="display:none">
            <div class="intel-results-content" id="li-results-content">
              <!-- Populated by renderSessionSummary + _renderResults() -->
            </div>
          </div>

        </div>
      </div>
    `;

    // Wire view tabs
    pageEl.querySelectorAll('.intel-view-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        pageEl.querySelectorAll('.intel-view-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        _renderResultsView(tab.dataset.view);
      });
    });

    // Mount the BehDisc dashboard into li-chart-grid
    if (LabReplay.LiveIntelUI) {
      LabReplay.LiveIntelUI.mount();
      LabReplay.LiveIntelUI.showWaiting('Press ▶ Start, then launch the scenario in VirTra.');
    }

    _applyStateToPage();
  }


  function _applyStateToPage() {
    const idle    = _state === 'IDLE';
    const capture = _state === 'CAPTURING';
    const results = _state === 'RESULTS' || _state === 'SUMMARIZING';

    _toggleEl('li-idle-overlay', idle);
    _toggleEl('li-results-area', results);

    // Use explicit display:flex for li-chart-area so it works regardless of
    // CSS cascade — avoids depending on #li-chart-area { display:flex } in CSS.
    const chartArea = document.getElementById('li-chart-area');
    if (chartArea) chartArea.style.display = capture ? 'flex' : 'none';

    // li-rolling-stats permanently hidden — physio shown inside bd-dashboard
    _toggleEl('li-rolling-stats', false);

    _updateBanner();
    _updateRollingStats(_rollingStats);
  }

  // ── Capture control ──────────────────────────────────────────────────────────

  function _startCapture() {
    // Ensure we are subscribed to all live streams (belt-and-suspenders —
    // catalog-updated already does this, but if the page was navigated to
    // after streams were discovered we need to re-subscribe here).
    LabReplay.StreamRouter.subscribeAll();

    // Participant / session fields are optional — VirTra auto-supplies them
    LabReplay.StreamRouter.sendIntelStart?.({
      drill:          _drill,
      baseline_s:     _baselineS,
      analysis_s:     _analysisS,
      auto_stop_s:    _autoStopS,
      participant_id: _participantId || 'UNKNOWN',
      session_name:   _sessionName   || 'live_session',
    });

    // Reset engine and show waiting-for-VirTra state
    LabReplay.BehDiscEngine?.reset();
    LabReplay.LiveIntelUI?.showWaiting('Waiting for VirTra \u201cScenario Started\u201d event\u2026');

    _setState('CAPTURING');
    _elapsedAtAnchor = 0;
    _elapsedMono     = performance.now();
  }

  function _stopCapture() {
    LabReplay.StreamRouter.sendIntelStop?.();
    _setState('SUMMARIZING');
  }

  // ── EventBus handlers ────────────────────────────────────────────────────────

  function _onSessionInfoReceived({ participant_id, session_name }) {
    // Auto-populate if fields are empty (live SessionInfo stream)
    if (!_participantId && participant_id) {
      _participantId = participant_id;
      const el = document.getElementById('li-participant');
      if (el) el.value = participant_id;
    }
    if (!_sessionName && session_name) {
      _sessionName = session_name;
      const el = document.getElementById('li-session-name');
      if (el) el.value = session_name;
    }
  }

  function _onIntelState(msg) {
    _elapsedS      = msg.elapsed_s || 0;
    _participantId = msg.participant_id || _participantId;
    _sessionName   = msg.session_name   || _sessionName;
    _autoStopS     = msg.auto_stop_s    || _autoStopS;

    if (msg.rolling_stats) {
      _rollingStats = msg.rolling_stats;
      _updateRollingStats(_rollingStats);

      // Relay latest physio into the engine for per-engagement snapshots
      const E = LabReplay.BehDiscEngine;
      if (E) {
        E.ingestPhysio('hr',    _rollingStats.hr?.mean    ?? null);
        E.ingestPhysio('pupil', _rollingStats.pupil?.mean ?? null);
      }
      // Update live physio strip in the dashboard
      LabReplay.LiveIntelUI?.updateLivePhysio(
        _rollingStats.hr?.mean    ?? null,
        _rollingStats.pupil?.mean ?? null
      );
    }
  }

  function _onCountdown({ seconds_remaining }) {
    _countdownSec = seconds_remaining;
    _setState('COUNTDOWN');
    _updateBanner();
  }

  function _onSaved({ db_filename, db_path }) {
    _savedDb = { db_filename, db_path };
    _setState('SUMMARIZING');
    // Render engagement session summary before post-capture analysis
    const session = LabReplay.BehDiscEngine?.getSession?.();
    if (session && session.engagements.length > 0) {
      LabReplay.LiveIntelUI?.renderSessionSummary(session);
    }
    _runPostCaptureAnalysis(db_filename);
  }

  function _onError(msg) {
    _showBanner('countdown', `⚠ ${msg.message || 'Unknown error'}`);
  }

  // ── State machine ─────────────────────────────────────────────────────────────

  function _setState(newState) {
    _state = newState;
    _applyStateToTopBar();
    _applyStateToPage();
    _updateBanner();
  }

  // ── Banner ───────────────────────────────────────────────────────────────────

  function _updateBanner() {
    const bannerEl = document.getElementById('li-banner');
    if (!bannerEl) return;

    if (_state === 'CAPTURING') {
      _showBanner('capturing', 'Session Active');
    } else if (_state === 'COUNTDOWN') {
      _showBanner('countdown', `Finalizing — ${_countdownSec}s remaining
        <button class="intel-banner-cancel-btn" id="li-cancel-stop">Cancel</button>`);
      document.getElementById('li-cancel-stop')?.addEventListener('click', () => {
        LabReplay.StreamRouter.sendIntelSetAutoStop?.(9999);
        _setState('CAPTURING');
      });
    } else if (_state === 'SUMMARIZING') {
      // No banner during summarizing — session cards are already visible
      bannerEl.style.display = 'none';
    } else {
      bannerEl.style.display = 'none';
    }
  }

  function _showBanner(type, html) {
    const bannerEl = document.getElementById('li-banner');
    if (!bannerEl) return;
    bannerEl.className   = `intel-state-banner ${type}`;
    bannerEl.innerHTML   = html;
    bannerEl.style.display = '';
  }

  // ── Rolling stats ─────────────────────────────────────────────────────────────

  function _updateRollingStats(stats) {
    if (!stats) return;
    _updateStatCard('li-stat-hr',     'li-stat-hr-n',     stats.hr);
    _updateStatCard('li-stat-pupil',  'li-stat-pupil-n',  stats.pupil);
    _updateStatCard('li-stat-motion', 'li-stat-motion-n', stats.motion);
  }

  function _updateStatCard(valueId, metaId, data) {
    const valEl  = document.getElementById(valueId);
    const metaEl = document.getElementById(metaId);
    if (!valEl || !data) return;
    valEl.textContent  = data.mean != null ? data.mean.toFixed(1) : '—';
    if (metaEl) metaEl.textContent = data.samples != null ? `n=${data.samples}` : '';
  }

  // ── Elapsed RAF ──────────────────────────────────────────────────────────────

  function _startElapsedRAF() {
    if (_elapsedRAF) return;
    const tick = () => {
      const liveElapsed = _elapsedAtAnchor + (performance.now() - (_elapsedMono || performance.now())) / 1000;
      _setElapsedDisplay(liveElapsed);
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
    el.textContent = `${String(h).padStart(2,'0')}:${String(m%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  }

  // ── Post-capture analysis ─────────────────────────────────────────────────────

  async function _runPostCaptureAnalysis(dbFilename) {
    // Session cards were already written into li-results-content by renderSessionSummary.
    // Do NOT wipe them — we append the analysis output below them.
    _setState('RESULTS');

    const resultsContent = document.getElementById('li-results-content');
    if (!resultsContent) return;

    // Remove any stale analysis section from a previous run
    document.getElementById('li-analysis-output')?.remove();

    try {
      const trialsRes = await LabReplay.AnalysisAPI.trials(dbFilename, _drill);
      const epochRes  = await LabReplay.AnalysisAPI.epoch({
        session:    dbFilename,
        drill:      _drill,
        signals:    ['hr', 'pupil', 'motion'],
        baseline_s: _baselineS,
        analysis_s: _analysisS,
        bin_s:      0.1,
        do_zscore:  true,
      });

      if (!trialsRes.trials || trialsRes.trials.length === 0) return; // session cards already shown

      _renderResults(trialsRes, epochRes);

    } catch (err) {
      // Analysis failed — session cards already shown above, just note the save
      if (_savedDb) {
        const chip = document.createElement('div');
        chip.className = 'intel-save-chip';
        chip.style.cssText = 'margin:var(--space-md) var(--space-md) 0;display:inline-flex';
        chip.innerHTML = `✓ Saved: ${_savedDb.db_filename}`;
        resultsContent.appendChild(chip);
      }
    }
  }

  function _renderResults(trialsData, epochData) {
    // Appends analysis output AFTER the session cards from renderSessionSummary.
    // Does NOT wipe li-results-content.
    const container = document.getElementById('li-results-content');
    if (!container) return;

    // Remove stale analysis section
    document.getElementById('li-analysis-output')?.remove();

    const section = document.createElement('div');
    section.id = 'li-analysis-output';
    section.style.cssText = 'border-top:1px solid rgba(255,255,255,0.07);padding:var(--space-md)';
    container.appendChild(section);

    // Save chip
    if (_savedDb) {
      const chip = document.createElement('div');
      chip.className = 'intel-save-chip';
      chip.style.cssText = 'margin-bottom:var(--space-md);display:inline-flex';
      chip.innerHTML = `✓ Saved as ${_savedDb.db_filename}`;
      section.appendChild(chip);
    }

    // Epoch chart — reuse existing EpochChart component
    if (LabReplay.EpochChart && epochData) {
      const chartWrapper = document.createElement('div');
      chartWrapper.style.marginBottom = 'var(--space-lg)';
      section.appendChild(chartWrapper);
      const chart = new LabReplay.EpochChart(chartWrapper);
      chart.render(epochData, 'aggregate', 'all');
    }

    // Stats panel — reuse existing StatsPanel
    if (LabReplay.StatsPanel && trialsData) {
      const statsWrapper = document.createElement('div');
      section.appendChild(statsWrapper);
      const stats = new LabReplay.StatsPanel(statsWrapper);
      stats.render(trialsData.summary, trialsData.terminology);
    }
  }

  function _renderResultsView(/* view */) {
    // Placeholder — full per-event and compare views come in a later polish pass
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function _toggleEl(id, visible) {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  }

  // ── VirTra event handler ──────────────────────────────────────────────────────

  function _onVirtraEvent(evt) {
    if (_state !== 'CAPTURING' && _state !== 'COUNTDOWN') return;
    LabReplay.BehDiscEngine?.ingest(evt);
  }

  // ── Teardown ──────────────────────────────────────────────────────────────────

  function _teardown() {
    _stopElapsedRAF();
  }

  return { init };

})();
