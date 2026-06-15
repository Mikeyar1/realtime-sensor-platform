/**
 * mode-manager.js
 *
 * Pure display layer. Driven entirely by session.state messages from the backend.
 *
 * Elapsed display uses a local requestAnimationFrame stopwatch running at 60fps.
 * The backend session.state (1s tick) seeds + corrects the local counter.
 * Backend is still the single source of truth — the RAF is display-only.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ModeManager = (function () {

  // Last known state from backend
  let _state = {
    mode: 'replay',
    state: 'idle',
    elapsed_seconds: 0,
    duration_seconds: 0,
    session_id: '',
    session_name: '',
    db_path: '',
    stream_count: 0,
  };

  const els = {};

  // ── Local RAF stopwatch (display only — backend is still source of truth) ──
  // When playing, a requestAnimationFrame loop runs at 60fps.
  // _rafOriginElapsed = backend elapsed at seed time
  // _rafOriginWall    = performance.now() at seed time
  // localElapsed      = _rafOriginElapsed + (performance.now() - _rafOriginWall) / 1000
  // Every 1s backend tick re-seeds both values → drift stays at zero.

  let _rafId = null;
  let _rafOriginElapsed = 0;
  let _rafOriginWall = 0;

  function _startRaf(fromElapsed) {
    _stopRaf();
    _rafOriginElapsed = fromElapsed;
    _rafOriginWall = performance.now();

    function tick() {
      const localElapsed = _rafOriginElapsed + (performance.now() - _rafOriginWall) / 1000;
      if (els.elapsed) els.elapsed.textContent = _formatTime(localElapsed);
      _rafId = requestAnimationFrame(tick);
    }
    _rafId = requestAnimationFrame(tick);
  }

  function _stopRaf() {
    if (_rafId !== null) {
      cancelAnimationFrame(_rafId);
      _rafId = null;
    }
  }

  // ── Init ────────────────────────────────────────────────────────────────────

  function init() {
    // Bind legacy DOM elements — may not exist in the new 4-page layout.
    // All listeners are guarded with null checks.
    els.btnLive     = document.getElementById('btn-mode-live');
    els.btnReplay   = document.getElementById('btn-mode-replay');
    els.transport   = document.getElementById('transport-controls');
    els.dbLoader    = document.getElementById('db-loader');
    els.dbSelect    = document.getElementById('db-select');
    els.btnClear    = document.getElementById('btn-clear-session');
    els.btnPlayPause = document.getElementById('btn-play-pause');
    els.elapsed     = document.getElementById('elapsed-time');
    els.sessionName = document.getElementById('session-name');

    // Mode toggle buttons (legacy topbar — may not exist)
    els.btnLive?.addEventListener('click', () => {
      LabReplay.StreamRouter.activateLive();
    });
    els.btnReplay?.addEventListener('click', () => {
      LabReplay.StreamRouter.deactivateLive();
    });

    // DB dropdown → load session (legacy)
    els.dbSelect?.addEventListener('change', () => {
      const path = els.dbSelect.value;
      if (path) {
        console.log('[ModeManager] Session selected:', path);
        LabReplay.StreamRouter.loadSession(path);
      }
    });

    // Clear → unload session (legacy)
    els.btnClear?.addEventListener('click', () => {
      if (els.dbSelect) els.dbSelect.value = '';
      LabReplay.StreamRouter.unloadSession();
    });

    // Play/Pause toggle (legacy)
    els.btnPlayPause?.addEventListener('click', _onPlayPauseClick);

    // Session list arrives → populate dropdown (legacy)
    LabReplay.EventBus.on('session-list-received', _populateDropdown);

    // On connect → request state + session list
    LabReplay.EventBus.on('ws-connected', () => {
      LabReplay.StreamRouter.getState();
      LabReplay.StreamRouter.listSessions();
    });
  }

  // ── Main entry point: called by stream-router for every session.state message ──

  function onSessionState(msg) {
    _state = { ...msg };

    // Elapsed: RAF stopwatch while playing, frozen display otherwise
    if (msg.state === 'playing' || msg.state === 'listening') {
      _startRaf(msg.elapsed_seconds);   // re-seed every 1s → zero drift
    } else if (msg.state === 'loading') {
      _stopRaf();
      // Keep whatever elapsed was already showing — no 'Loading...' text here
    } else {
      _stopRaf();
      if (els.elapsed) els.elapsed.textContent = _formatTime(msg.elapsed_seconds);
    }

    // 2. Play/Pause button icon
    _updatePlayPauseIcon(msg.state);

    // 3. Mode-specific UI (controls, db loader visibility)
    _updateModeUI(msg.mode);
    _updateLoadingUI(msg.state);

    // 4. Session name in header
    if (msg.session_name && els.sessionName) {
      els.sessionName.value = msg.session_name;
    }

    // 5. Emit to other components (timeline, etc.)
    LabReplay.EventBus.emit('session-state-changed', msg);

    // Also emit the legacy position-updated event so timeline.js still works
    if (msg.duration_seconds > 0) {
      LabReplay.EventBus.emit('position-updated', {
        position: msg.elapsed_seconds,
        duration: msg.duration_seconds,
        speed: 1.0,
        paused: msg.state === 'paused',
      });
    }
  }

  // ── Dropdown ────────────────────────────────────────────────────────────────

  function _populateDropdown(sessions) {
    if (!els.dbSelect) return;
    const prev = els.dbSelect.value;
    els.dbSelect.innerHTML = '<option value="">-- Select session --</option>';

    for (const s of sessions) {
      const opt = document.createElement('option');
      opt.value = s.path;
      opt.disabled = !s.valid;

      const label = s.name || s.session_id || s.path;
      const mb = s.size_mb != null ? ` (${s.size_mb} MB)` : '';
      const tag = !s.valid ? ' ⚠ invalid' : '';
      opt.textContent = `${label}${mb}${tag}`;

      if (s.path === prev) opt.selected = true;
      els.dbSelect.appendChild(opt);
    }
    console.log(`[ModeManager] Dropdown populated with ${sessions.length} session(s)`);
  }

  // ── Play/Pause click ─────────────────────────────────────────────────────────

  function _onPlayPauseClick() {
    const st = _state.state;
    if (st === 'playing') {
      _stopRaf();                          // freeze display immediately
      _updatePlayPauseIcon('paused');      // optimistic icon
      LabReplay.StreamRouter.sendPause();
    } else if (st === 'paused' || st === 'stopped') {
      _startRaf(_state.elapsed_seconds);   // start counting immediately
      _updatePlayPauseIcon('playing');     // optimistic icon
      LabReplay.StreamRouter.sendPlay();
    }
    // Backend responds with session.state → icon re-syncs via onSessionState
  }

  // ── UI helpers ───────────────────────────────────────────────────────────────

  function _updatePlayPauseIcon(state) {
    if (!els.btnPlayPause) return;
    if (state === 'loading') {
      els.btnPlayPause.textContent = '⋯';   // horizontal ellipsis — loading indicator
      els.btnPlayPause.classList.remove('active');
      els.btnPlayPause.title = 'Loading session…';
      return;
    }
    const playing = state === 'playing';
    els.btnPlayPause.textContent = playing ? '❚❚' : '▶';
    els.btnPlayPause.classList.toggle('active', playing);
    els.btnPlayPause.title = playing ? 'Pause' : 'Play';
  }

  function _updateLoadingUI(state) {
    const isLoading = state === 'loading';

    // Play button: disabled + spinner text while loading
    if (els.btnPlayPause) {
      els.btnPlayPause.disabled = isLoading;
    }

    // Dropdown + clear: disabled while loading so user can't double-trigger
    if (els.dbSelect) els.dbSelect.disabled = isLoading;
    if (els.btnClear) els.btnClear.disabled = isLoading;

    // Visual class for CSS pulse animation
    document.getElementById('db-loader')?.classList.toggle('is-loading', isLoading);
  }

  function _updateModeUI(mode) {
    if (!els.btnLive) return;
    if (mode === 'live') {
      els.btnLive.classList.add('active', 'live-active');
      els.btnReplay.classList.remove('active', 'live-active');
      els.transport.classList.add('hidden');
      els.dbLoader.classList.add('hidden');
    } else {
      els.btnReplay.classList.add('active');
      els.btnLive.classList.remove('active', 'live-active');
      els.transport.classList.remove('hidden');
      els.dbLoader.classList.remove('hidden');
    }
  }

  // HH:MM:SS.cc — stopwatch format with centiseconds
  function _formatTime(seconds) {
    if (!seconds || seconds < 0) seconds = 0;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const cs = Math.floor((seconds % 1) * 100);
    return [h, m, s].map(v => String(v).padStart(2, '0')).join(':')
      + '.' + String(cs).padStart(2, '0');
  }

  // ── Public ───────────────────────────────────────────────────────────────────

  function getState() { return { ..._state }; }

  // Legacy shims — called by old code paths
  function setMode(mode, data = {}) {
    // Translates old setMode calls into new session.state shape
    onSessionState({
      mode,
      state: data.duration_seconds > 0 ? 'playing' : 'idle',
      elapsed_seconds: 0,
      duration_seconds: data.duration_seconds || 0,
      session_id: '',
      session_name: '',
      db_path: data.db_path || '',
      stream_count: 0,
    });
  }

  function updatePosition(pos, spd, isPaused) {
    LabReplay.EventBus.emit('position-updated', {
      position: pos, duration: _state.duration_seconds, speed: spd, paused: isPaused,
    });
  }

  return { init, onSessionState, setMode, updatePosition, getState };
})();
