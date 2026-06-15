/**
 * replay-sessions.js — ReplaySessionsPage (WP-04)
 *
 * Post-session replay of recorded .db files.
 * Renders a top bar with session picker, transport controls, and timing display.
 * Populates the chart grid from the stream catalog received during playback.
 *
 * This is a full extraction + reorganization of the replay logic that was
 * previously spread across app.js and mode-manager.js. No net-new behavior.
 *
 * WP-09 extension: SessionInfo auto-populate (participant/session name from DB)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ReplaySessionsPage = (function () {

  // ── State ────────────────────────────────────────────────────────────────────

  let _sessions         = [];
  let _currentSession   = null;
  let _sessionState     = 'idle';   // idle | loading | playing | paused | stopped | finished
  let _elapsedS         = 0;
  let _durationS        = 0;
  let _sessionStartUnix = null;
  let _elapsedRAF       = null;
  let _elapsedMono      = null;   // performance.now() anchor for live-update RAF
  let _elapsedAtAnchor  = 0;

  // SessionInfo (from WP-09)
  let _participantId    = '';
  let _sessionName      = '';

  // Chart instances
  let _activeInstances  = [];
  let _panelsByStream   = {};
  let _fusionInstances  = {};

  // ── Initialization ───────────────────────────────────────────────────────────

  function init() {
    LabReplay.TopBarManager.register('replay', _renderTopBar, _teardown);

    LabReplay.EventBus.on('session-list-received',  _onSessionList);
    LabReplay.EventBus.on('session-state-changed',  _onSessionState);
    LabReplay.EventBus.on('catalog-updated',        _onCatalogUpdated);
    LabReplay.EventBus.on('session-info-result',    _onSessionInfoResult);
    LabReplay.EventBus.on('ws-connected',           _onWsConnected);

    LabReplay.EventBus.on('page-changed', (page) => {
      if (page === 'replay') {
        _renderPageContent();
        // Request session list from backend
        LabReplay.StreamRouter.listSessions();
      }
    });
  }

  // ── Top bar ──────────────────────────────────────────────────────────────────

  function _renderTopBar(slot) {
    slot.innerHTML = `
      <div class="topbar" id="replay-topbar">

        <!-- Session picker -->
        <div class="topbar-section" id="rp-session-section">
          <select class="topbar-select" id="rp-session-select" style="min-width:200px">
            <option value="">— Select session —</option>
          </select>
          <button class="topbar-icon-btn" id="rp-clear-btn" title="Unload session">✕ Clear</button>
        </div>

        <div class="topbar-sep"></div>

        <!-- Original session time -->
        <div class="topbar-section" id="rp-time-section" style="display:none">
          <div class="topbar-session-time">
            <span class="topbar-session-time-label">SESSION TIME</span>
            <span class="topbar-session-time-value" id="rp-session-time">—</span>
          </div>
        </div>

        <div class="topbar-sep" id="rp-time-sep" style="display:none"></div>

        <!-- Elapsed -->
        <div class="topbar-elapsed">
          <span class="topbar-elapsed-label">ELAPSED</span>
          <span class="topbar-elapsed-value" id="rp-elapsed-value">00:00:00</span>
        </div>

        <div class="topbar-sep"></div>

        <!-- Transport controls -->
        <div class="topbar-transport" id="rp-transport">
          <button class="topbar-transport-btn" id="rp-btn-skip-back"  title="Skip to start">|◀</button>
          <button class="topbar-transport-btn" id="rp-btn-rewind"     title="Rewind">◀◀</button>
          <button class="topbar-transport-btn play-pause" id="rp-btn-play" title="Play / Pause">▶</button>
          <button class="topbar-transport-btn stop-btn"  id="rp-btn-stop"  title="Stop">■</button>
          <button class="topbar-transport-btn" id="rp-btn-forward"    title="Fast forward">▶▶</button>
          <button class="topbar-transport-btn" id="rp-btn-skip-end"   title="Skip to end">▶|</button>
        </div>

        <!-- Right: connection + SessionInfo chips -->
        <div class="topbar-section topbar-section--right" id="rp-meta-section">
          <span class="topbar-meta-chip" id="rp-participant-chip" style="display:none">
            <span class="topbar-meta-chip-icon">👤</span>
            <span id="rp-participant-text"></span>
          </span>
          <span class="topbar-meta-chip" id="rp-session-chip" style="display:none">
            <span class="topbar-meta-chip-icon">📋</span>
            <span id="rp-session-text"></span>
          </span>
          <div class="status-dot disconnected" id="rp-connection-dot" title="Disconnected"></div>
        </div>

      </div>
    `;

    _wireTopBarButtons(slot);
    _applySessionState(_sessionState);
    _populateSessionSelect(slot.querySelector('#rp-session-select'));

    // Restore SessionInfo chips if available
    if (_participantId) _showMetaChip('rp-participant-chip', 'rp-participant-text', _participantId);
    if (_sessionName)   _showMetaChip('rp-session-chip', 'rp-session-text', _sessionName);
  }

  function _wireTopBarButtons(slot) {
    const SR = LabReplay.StreamRouter;

    const bind = (id, fn) => slot.querySelector(`#${id}`)?.addEventListener('click', fn);

    bind('rp-btn-play',      () => {
      if (_sessionState === 'playing') SR.sendPause();
      else SR.sendPlay();
    });
    bind('rp-btn-stop',      () => SR.sendStop());
    bind('rp-btn-skip-back', () => SR.sendSeek(0));
    bind('rp-btn-skip-end',  () => SR.sendSeek(Infinity));
    bind('rp-btn-rewind',    () => {});   // future
    bind('rp-btn-forward',   () => {});   // future
    bind('rp-clear-btn',     () => _clearSession());

    // Session select
    const selectEl = slot.querySelector('#rp-session-select');
    if (selectEl) {
      selectEl.addEventListener('change', (e) => {
        const path = e.target.value;
        if (!path) { _clearSession(); return; }
        _loadSession(path, selectEl.options[selectEl.selectedIndex].text);
      });
    }
  }

  // ── Page content ─────────────────────────────────────────────────────────────

  function _renderPageContent() {
    const pageEl = document.getElementById('page-replay');
    if (!pageEl) return;

    pageEl.innerHTML = `
      <div class="replay-content" id="rp-content">
        <div class="chart-grid" id="rp-chart-grid"></div>
        <div class="live-monitor-idle" id="rp-idle-overlay"
             style="height:100%;align-items:center;justify-content:center">
          <div class="live-monitor-idle-title">Select a session to replay</div>
          <div class="live-monitor-idle-sub">Use the session picker in the top bar.</div>
        </div>
      </div>
    `;

    // If we already have charts, re-build
    if (_activeInstances.length > 0) {
      const existingGrid = pageEl.querySelector('#rp-chart-grid');
      // Re-attach panels to new grid
      for (const ai of _activeInstances) {
        if (ai.panel?.el) existingGrid?.appendChild(ai.panel.el);
      }
    }
  }

  // ── Session management ───────────────────────────────────────────────────────

  function _loadSession(path, label) {
    _currentSession = { path, label };
    _participantId = '';
    _sessionName   = '';
    _hideMetaChips();
    _destroyCharts();

    LabReplay.StreamRouter.loadSession(path);

    // Request SessionInfo from backend (WP-09)
    if (LabReplay.StreamRouter.sendGetSessionInfo) {
      LabReplay.StreamRouter.sendGetSessionInfo(path);
    }
  }

  function _clearSession() {
    LabReplay.StreamRouter.unloadSession();
    _currentSession = null;
    _destroyCharts();
    _setElapsed(0);
    _applySessionState('idle');
    const selectEl = document.getElementById('rp-session-select');
    if (selectEl) selectEl.value = '';
  }

  // ── EventBus handlers ────────────────────────────────────────────────────────

  function _onWsConnected() {
    const dot = document.getElementById('rp-connection-dot');
    if (dot) {
      dot.className = 'status-dot live';
      dot.title = 'Connected';
    }
    LabReplay.StreamRouter.listSessions();
  }

  function _onSessionList(sessions) {
    _sessions = sessions || [];
    _populateSessionSelect(document.getElementById('rp-session-select'));
  }

  function _onSessionState(msg) {
    if (LabReplay.Sidebar.getActivePage() !== 'replay') {
      // Store state for when we switch back
      _sessionState = msg.state;
      _elapsedS     = msg.elapsed_seconds || 0;
      _durationS    = msg.duration_seconds || 0;
      return;
    }
    _sessionState = msg.state;
    _elapsedS     = msg.elapsed_seconds || 0;
    _durationS    = msg.duration_seconds || 0;
    _applySessionState(msg.state);
  }

  function _onCatalogUpdated(catalog) {
    if (LabReplay.Sidebar.getActivePage() !== 'replay') return;
    _buildChartGrid(catalog);
  }

  function _onSessionInfoResult(msg) {
    if (!msg.participant_id && !msg.session_name) return;
    _participantId = msg.participant_id || '';
    _sessionName   = msg.session_name   || '';
    if (_participantId) _showMetaChip('rp-participant-chip', 'rp-participant-text', _participantId);
    if (_sessionName)   _showMetaChip('rp-session-chip',    'rp-session-text',     _sessionName);
  }

  // ── State application ────────────────────────────────────────────────────────

  function _applySessionState(state) {
    const playBtn = document.getElementById('rp-btn-play');
    if (playBtn) {
      playBtn.textContent = state === 'playing' ? '⏸' : '▶';
    }

    // Disable transport when no session loaded
    const noSession = state === 'idle';
    ['rp-btn-play','rp-btn-stop','rp-btn-skip-back','rp-btn-skip-end',
     'rp-btn-rewind','rp-btn-forward'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = noSession;
    });

    _setElapsed(_elapsedS);

    if (state === 'playing') {
      // Start live elapsed RAF
      _elapsedMono    = performance.now();
      _elapsedAtAnchor = _elapsedS;
      _startElapsedRAF();
    } else {
      _stopElapsedRAF();
    }

    // Show idle overlay when no session
    const idleEl = document.getElementById('rp-idle-overlay');
    const gridEl = document.getElementById('rp-chart-grid');
    if (idleEl) idleEl.style.display = (state === 'idle') ? '' : 'none';
    if (gridEl) gridEl.style.display = (state === 'idle') ? 'none' : '';
  }

  // ── Elapsed timer ─────────────────────────────────────────────────────────────

  function _startElapsedRAF() {
    if (_elapsedRAF) return;
    const tick = () => {
      const liveElapsed = _elapsedAtAnchor + (performance.now() - _elapsedMono) / 1000;
      _setElapsed(liveElapsed);
      _elapsedRAF = requestAnimationFrame(tick);
    };
    _elapsedRAF = requestAnimationFrame(tick);
  }

  function _stopElapsedRAF() {
    if (_elapsedRAF) { cancelAnimationFrame(_elapsedRAF); _elapsedRAF = null; }
  }

  function _setElapsed(sec) {
    const el = document.getElementById('rp-elapsed-value');
    if (!el) return;
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    el.textContent = `${String(h).padStart(2,'0')}:${String(m%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  }

  // ── Session select ────────────────────────────────────────────────────────────

  function _populateSessionSelect(selectEl) {
    if (!selectEl) return;
    const currentVal = selectEl.value;
    selectEl.innerHTML = '<option value="">— Select session —</option>';
    for (const s of _sessions) {
      if (!s.valid) continue;
      const opt = document.createElement('option');
      opt.value = s.path;
      opt.textContent = `${s.name} (${s.size_mb} MB)`;
      selectEl.appendChild(opt);
    }
    selectEl.value = currentVal;
  }

  // ── SessionInfo chips ─────────────────────────────────────────────────────────

  function _showMetaChip(chipId, textId, value) {
    const chip = document.getElementById(chipId);
    const text = document.getElementById(textId);
    if (chip) chip.style.display = '';
    if (text) text.textContent = value;
  }

  function _hideMetaChips() {
    ['rp-participant-chip', 'rp-session-chip'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
  }

  // ── Chart grid ────────────────────────────────────────────────────────────────

  function _buildChartGrid(catalog) {
    const grid = document.getElementById('rp-chart-grid');
    if (!grid) return;

    if (!catalog || catalog.length === 0) {
      _destroyCharts();
      return;
    }

    const shownKeys = new Set(_activeInstances.map(ai => ai.descriptorKey).filter(Boolean));

    for (const stream of catalog) {
      if (_panelsByStream[stream.name]) continue;
      const resolved = LabReplay.StreamRegistry.resolve(stream);
      if (!resolved) continue;
      const descriptors = Array.isArray(resolved) ? resolved : [resolved];

      try {
        let firstEl = null;
        for (const descriptor of descriptors) {
          const key = descriptor._key;
          if (descriptor.fusion) {
            let fe = _fusionInstances[key];
            if (!fe) {
              const { el, instance } = LabReplay.ChartFactory.create(descriptor);
              grid.appendChild(el);
              fe = { instance, el };
              _fusionInstances[key] = fe;
            }
            if (descriptor._fusionTag && fe.instance) {
              const tag    = descriptor._fusionTag;
              const fi     = fe.instance;
              const wrapper = {
                pushSample: (ts, data, elapsed) => fi.pushTagged(tag, ts, data, elapsed),
                resize:     () => fi.resize(),
                destroy:    () => {},
              };
              LabReplay.StreamRouter.registerInstance(stream.name, wrapper);
              _activeInstances.push({ streamName: stream.name, instance: wrapper, panel: { el: fe.el } });
            }
            firstEl = firstEl || fe?.el;
            continue;
          }
          if (shownKeys.has(key)) {
            const existing = _activeInstances.find(ai => ai.descriptorKey === key);
            if (existing?.instance) LabReplay.StreamRouter.registerInstance(stream.name, existing.instance);
            firstEl = firstEl || existing?.panel?.el;
          } else {
            shownKeys.add(key);
            const { el, instance } = LabReplay.ChartFactory.create(descriptor);
            grid.appendChild(el);
            if (instance) {
              LabReplay.StreamRouter.registerInstance(stream.name, instance);
              _activeInstances.push({ streamName: stream.name, descriptorKey: key, instance, panel: { el } });
            }
            firstEl = firstEl || el;
          }
        }
        _panelsByStream[stream.name] = firstEl || '__handled__';
      } catch (err) {
        console.error(`[ReplaySessions] Chart error for "${stream.name}":`, err);
      }
    }

    LabReplay.StreamRouter.subscribeAll();
  }

  function _destroyCharts() {
    for (const { streamName, instance } of _activeInstances) {
      LabReplay.StreamRouter.unregisterInstance(streamName, instance);
      if (instance.destroy) instance.destroy();
    }
    for (const { instance } of Object.values(_fusionInstances)) {
      if (instance?.destroy) instance.destroy();
    }
    const grid = document.getElementById('rp-chart-grid');
    if (grid) grid.innerHTML = '';
    _activeInstances = [];
    _panelsByStream  = {};
    _fusionInstances = {};
  }

  // ── Teardown ─────────────────────────────────────────────────────────────────

  function _teardown() {
    _stopElapsedRAF();
  }

  return { init };

})();
