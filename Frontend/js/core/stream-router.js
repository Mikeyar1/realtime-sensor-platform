/**
 * stream-router.js
 *
 * Transport layer: manages the WebSocket connection to the backend.
 * Routes incoming messages to the correct handler (EventBus / ModeManager / plugin instances).
 *
 * Message types match api/message_types.js exactly.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.StreamRouter = (function () {
  // Auto-detect the backend host from the URL the page was served from.
  // This means the app works on localhost, a LAN IP, or any other address
  // with zero configuration — just serve the frontend from the backend machine.
  const WS_HOST = window.location.hostname || 'localhost';
  const WS_URL  = `ws://${WS_HOST}:8500`;
  const RECONNECT_DELAY = 3000;

  let ws = null;
  let catalog = [];
  let subscribed = new Set();
  // streamId → array of plugin instances
  let chartInstances = {};

  // Shared LSL-domain session start timestamp.
  // Set from the FIRST sample received after play.
  // This avoids the macOS clock domain mismatch between
  // pylsl.local_clock() (uptime) and time.time() (Unix epoch).
  let _sessionLslStart = null;
  let _lastSessionState = null;

  // ── Connection ──────────────────────────────────────────────────────────────

  function connect() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      console.log('[StreamRouter] Connected to', WS_URL);
      _setConnectionUI(true);
      LabReplay.EventBus.emit('ws-connected');
    };

    ws.onclose = () => {
      console.log('[StreamRouter] Disconnected — retrying in', RECONNECT_DELAY, 'ms');
      _setConnectionUI(false);
      LabReplay.EventBus.emit('ws-disconnected');
      setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => {
      console.error('[StreamRouter] WebSocket error');
    };

    ws.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch (e) { return; }
      _handleMessage(msg);
    };
  }

  // ── Message dispatch ────────────────────────────────────────────────────────

  function _handleMessage(msg) {
    switch (msg.type) {

      // ── State: single source of truth for the whole UI ─────────────────
      case 'session.state':
        // Store start_unix globally so chart plugins can compute real wall-clock time
        if (msg.start_unix) LabReplay.sessionStartUnix = msg.start_unix;
        // Reset shared LSL start reference when a new play begins
        if (msg.state === 'playing' && _lastSessionState !== 'playing') {
          _sessionLslStart = null;
          console.log('[StreamRouter] New play — resetting LSL session clock.');
        }
        _lastSessionState = msg.state;
        console.log('[StreamRouter] session.state:', msg.state, `${msg.elapsed_seconds}s / ${msg.duration_seconds}s`);
        LabReplay.ModeManager.onSessionState(msg);
        break;

      // ── Session list ───────────────────────────────────────────────────
      case 'session.list.result':
        console.log('[StreamRouter] Session list:', msg.sessions?.length, 'files');
        LabReplay.EventBus.emit('session-list-received', msg.sessions || []);
        break;

      // ── Stream catalog ─────────────────────────────────────────────────
      case 'streams.catalog':
        catalog = msg.streams || [];
        console.log('[StreamRouter] Catalog:', catalog.length, 'streams');
        LabReplay.EventBus.emit('catalog-updated', catalog);
        break;

      case 'streams.subscribed':
        console.log('[StreamRouter] Subscription confirmed:', msg.stream_ids);
        break;

      // ── Sample data (single) ───────────────────────────────────────────
      case 'stream.sample':
        _routeSample(msg.stream_id, msg.timestamp, msg.data, msg.elapsed_s);
        break;

      // ── Sample data (batch) — high-Hz streams (Neon Gaze, Motion) ──────
      // Backend buffers 50ms of samples and sends them as one WS frame
      // instead of 200 individual frames/s. Same _routeSample path per entry.
      case 'stream.samples':
        if (Array.isArray(msg.samples)) {
          for (const s of msg.samples) {
            _routeSample(msg.stream_id, s.timestamp, s.data, s.elapsed_s);
          }
        }
        break;


      case 'stream.lost':
        console.log('[StreamRouter] Stream lost:', msg.stream_id, '—', msg.reason || '');
        LabReplay.EventBus.emit('stream-lost', { id: msg.stream_id, reason: msg.reason });
        break;

      // ── System ────────────────────────────────────────────────────────
      case 'system.pong':
        // Heartbeat response — no action needed
        break;

      // ── Errors ────────────────────────────────────────────────────────
      case 'api.error':
        console.error('[StreamRouter] API error:', msg.code, '—', msg.message, `(context: ${msg.context || 'unknown'})`);
        LabReplay.EventBus.emit('api-error', msg);
        break;

      // ── CSV data (for EventTicker) ─────────────────────────────────────
      case 'session.csv_data':
        LabReplay.EventBus.emit('session-csv-data', msg);
        break;

      // ── Analysis data (for Analysis tab) ──────────────────────────────
      case 'analysis.data':
        LabReplay.EventBus.emit('analysis-data', msg);
        break;

      // ── Live Intel (Server → Client) ───────────────────────────────────────
      case 'live.intel.state':
        LabReplay.EventBus.emit('live-intel-state', msg);
        break;

      case 'live.intel.countdown':
        LabReplay.EventBus.emit('live-intel-countdown', { seconds_remaining: msg.seconds_remaining });
        break;

      case 'live.intel.saved':
        LabReplay.EventBus.emit('live-intel-saved', { db_filename: msg.db_filename, db_path: msg.db_path });
        break;

      case 'live.intel.error':
        console.error('[StreamRouter] live.intel error:', msg.code, msg.message);
        LabReplay.EventBus.emit('live-intel-error', msg);
        break;

      // ── Post-session SessionInfo ────────────────────────────────────────
      case 'session.info_result':
        LabReplay.EventBus.emit('session-info-result', msg);
        break;

      default:
        console.warn('[StreamRouter] Unknown message type:', msg.type);
    }
  }

  const _sampleCounters = {};
  function _routeSample(streamId, timestamp, data, elapsedS) {
    _sampleCounters[streamId] = (_sampleCounters[streamId] || 0) + 1;
    const count = _sampleCounters[streamId];
    const instances = chartInstances[streamId];

    // Compute elapsed using LSL timestamps exclusively.
    // The backend elapsed_s uses time.time() but LSL uses pylsl.local_clock()
    // (macOS uptime), so they are in different clock domains and cannot be mixed.
    // Instead, the FIRST LSL timestamp received after play sets the shared t=0.
    if (_sessionLslStart === null && timestamp) {
      _sessionLslStart = timestamp;
      console.log('[StreamRouter] Session LSL start anchored at', timestamp.toFixed(3));
    }
    const elapsed = (_sessionLslStart !== null && timestamp)
      ? Math.max(0, parseFloat((timestamp - _sessionLslStart).toFixed(3)))
      : null;

    if (count <= 3 || count % 500 === 0) {
      console.log(`[StreamRouter] sample #${count} for "${streamId}" → ${instances ? instances.length : 0} instance(s), elapsed=${elapsed?.toFixed(1)}s, data=`, Array.isArray(data) ? data.slice(0, 3) : data);
    }

    if (instances) {
      for (const inst of instances) {
        try {
          inst.pushSample(timestamp, data, elapsed);
        } catch (e) {
          console.error('[StreamRouter] pushSample error on', streamId, e);
        }
      }
    }

    // Notify any listener that wants to track sample receipt (e.g. LiveMonitorPage)
    LabReplay.EventBus.emit('stream-sample-received', { streamId, timestamp, data, elapsed });

    // VirTra events also broadcast to EventBus for the event log
    if (streamId && (streamId.includes('VirTra') || streamId.includes('Marker'))) {
      LabReplay.EventBus.emit('virtra-event', { timestamp, elapsedS: elapsed, data, stream: streamId });
    }

    // WP-06b: SessionInfo stream — parse JSON from data[0] and emit session metadata.
    // LSL convention: the SessionInfo stream publishes a single string channel whose
    // value is a JSON object: { participant_id, session_name, drill, ... }
    if (streamId && streamId.toLowerCase().includes('sessioninfo')) {
      try {
        const raw = Array.isArray(data) ? data[0] : data;
        const info = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (info && typeof info === 'object') {
          LabReplay.EventBus.emit('session-info-received', {
            participant_id: info.participant_id || info.ParticipantID || '',
            session_name:   info.session_name   || info.SessionName   || '',
            drill:          info.drill           || info.Drill         || '',
            raw:            info,
          });
        }
      } catch (e) {
        // Not JSON — ignore
      }
    }
  }

  // ── Outgoing messages ───────────────────────────────────────────────────────

  function subscribe(streamIds) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const ids = Array.isArray(streamIds) ? streamIds : [streamIds];
    ids.forEach(id => subscribed.add(id));
    _send({ type: 'streams.subscribe', stream_ids: ids });
    console.log('[StreamRouter] Subscribed:', ids);
  }

  function unsubscribe(streamIds) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const ids = Array.isArray(streamIds) ? streamIds : [streamIds];
    ids.forEach(id => subscribed.delete(id));
    _send({ type: 'streams.unsubscribe', stream_ids: ids });
  }

  function subscribeAll() {
    subscribe(catalog.map(s => s.name || s.id));
  }

  function loadSession(path) {
    _send({ type: 'session.load', path });
    console.log('[StreamRouter] Loading session:', path);
  }

  function unloadSession() {
    _send({ type: 'session.unload' });
  }

  function listSessions() {
    _send({ type: 'session.list' });
  }

  function sendPlay()  { _send({ type: 'playback.play' }); }
  function sendPause() { _send({ type: 'playback.pause' }); }
  function sendStop()  { _send({ type: 'playback.stop' }); }
  function sendSeek(positionSeconds) {
    _send({ type: 'playback.seek', position_seconds: positionSeconds });
  }

  function activateLive()   { _send({ type: 'live.activate' }); }
  function deactivateLive() { _send({ type: 'live.deactivate' }); }
  function requestCsvData() { _send({ type: 'session.get_csv' }); }

  function getCatalog()  { return catalog; }
  function getState()    { _send({ type: 'system.get_state' }); }
  function ping()        { _send({ type: 'system.ping', ts: Date.now() / 1000 }); }

  // ── Plugin instance registry ────────────────────────────────────────────────

  function registerInstance(streamId, instance) {
    if (!chartInstances[streamId]) chartInstances[streamId] = [];
    chartInstances[streamId].push(instance);
  }

  function unregisterInstance(streamId, instance) {
    if (!chartInstances[streamId]) return;
    chartInstances[streamId] = chartInstances[streamId].filter(i => i !== instance);
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function _send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function _setConnectionUI(connected) {
    // StreamRouter no longer owns a specific connection dot — it emits events;
    // the active page's top bar renders its own status indicator.
    // Retain for backward compat with any code that still queries #connection-dot.
    const dot   = document.getElementById('connection-dot');
    const label = document.getElementById('connection-label');
    if (dot) {
      dot.classList.toggle('connected', connected);
      dot.title = connected ? 'Connected' : 'Disconnected';
    }
    if (label) label.textContent = connected ? 'Connected' : 'Disconnected';
  }

  // ── Legacy shims — keep app.js working without changes ──────────────────────
  // These map old names to new API calls. Remove after app.js is updated.

  function scanDb()        { listSessions(); }
  function loadDb(path)    { loadSession(path); }
  function sendTransport(action, value) {
    if (action === 'play')   sendPlay();
    else if (action === 'pause') sendPause();
    else if (action === 'stop')  sendStop();
    else if (action === 'seek')  sendSeek(value);
  }

  // ── Live Intel control ──────────────────────────────────────────────────────

  function sendIntelStart(payload) {
    _send({ type: 'live.intel.start', ...payload });
  }

  function sendIntelStop() {
    _send({ type: 'live.intel.stop' });
  }

  function sendIntelSetAutoStop(seconds) {
    _send({ type: 'live.intel.set_auto_stop', seconds });
  }

  function sendIntelUpdateMeta(participant_id, session_name) {
    _send({ type: 'live.intel.update_meta', participant_id, session_name });
  }

  // ── Post-session SessionInfo query ───────────────────────────────────────────

  function sendGetSessionInfo(db_path) {
    _send({ type: 'session.get_info', db_path });
  }

  // ── Generic raw send (for page controllers) ──────────────────────────────────

  function sendRaw(obj) { _send(obj); }

  /** Returns the Set of currently-subscribed stream IDs. */
  function getSubscribed() { return subscribed; }

  return {
    connect,
    subscribe, unsubscribe, subscribeAll,
    registerInstance, unregisterInstance,
    loadSession, unloadSession, listSessions,
    sendPlay, sendPause, sendStop, sendSeek,
    activateLive, deactivateLive, requestCsvData,
    sendIntelStart, sendIntelStop, sendIntelSetAutoStop, sendIntelUpdateMeta,
    sendGetSessionInfo,
    sendRaw,
    getCatalog, getSubscribed, getState, ping,
    // legacy shims
    scanDb, loadDb, sendTransport,
  };
})();
