/**
 * stream-router.js
 *
 * Transport layer: manages the WebSocket connection to the backend.
 * Routes incoming messages to the correct handler (EventBus / ModeManager / plugin instances).
 *
 * Message types match api/message_types.js exactly.
 */

window.LabReview = window.LabReview || {};

LabReview.StreamRouter = (function () {
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
      LabReview.EventBus.emit('ws-connected');
    };

    ws.onclose = () => {
      console.log('[StreamRouter] Disconnected — retrying in', RECONNECT_DELAY, 'ms');
      _setConnectionUI(false);
      LabReview.EventBus.emit('ws-disconnected');
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
        _lastSessionState = msg.state;
        LabReview.ModeManager.onSessionState(msg);
        break;

      // session.list.result removed — Backend no longer handles replay sessions

      // ── Stream catalog ─────────────────────────────────────────────────
      case 'streams.catalog':
        catalog = msg.streams || [];
        LabReview.EventBus.emit('catalog-updated', catalog);
        break;

      case 'streams.subscribed':
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
        console.warn('[StreamRouter] Stream lost:', msg.stream_id, msg.reason || '');
        LabReview.EventBus.emit('stream-lost', { id: msg.stream_id, reason: msg.reason });
        break;

      // ── System ────────────────────────────────────────────────────────
      case 'system.pong':
        // Heartbeat response — no action needed
        break;

      // ── Errors ────────────────────────────────────────────────────────
      case 'api.error':
        console.error('[StreamRouter] API error:', msg.code, '—', msg.message, `(context: ${msg.context || 'unknown'})`);
        LabReview.EventBus.emit('api-error', msg);
        break;

      // ── VirTra scenario lifecycle (live mode) ─────────────────────────────
      // Backend broadcasts these when Scenario Started/Stopped is received
      case 'session.scenario_started':
        LabReview.EventBus.emit('scenario-started', msg);
        break;

      case 'session.scenario_stopped':
        LabReview.EventBus.emit('scenario-stopped', msg);
        break;

      // ── Individual VirTra events (Shot Fired, Shot Hit, Target Activated…) ──
      // Backend broadcasts these for real-time metric computation in drill displays.
      case 'session.virtra_event':
        LabReview.EventBus.emit('virtra-event', msg);
        break;

      // ── Live biosignal stats (HR, HRV, pupil) ──────────────────────────────
      case 'session.biosignal_update':
        LabReview.EventBus.emit('biosignal-update', msg);
        break;

      // ── Live speech transcription utterance ────────────────────────────────
      case 'session.speech_update':
        LabReview.EventBus.emit('speech-update', msg);
        break;

      // ── Post-session SessionInfo ────────────────────────────────────────
      case 'session.info_result':
        LabReview.EventBus.emit('session-info-result', msg);
        break;

      // ── AAR: computed metrics from Analysis (fired after scenario ends) ───
      // Backend sends this after Analysis has computed all drill metrics.
      case 'session.metrics_ready':
        LabReview.EventBus.emit('metrics-ready', msg);
        break;

      // ── Session record saved (operator clicked Save) ────────────────────
      case 'session.record_saved':
        LabReview.EventBus.emit('record-saved', msg);
        break;

      // ── PVT real-time trial update → unified trial-update event ─────────────────
      // All three supported drills use session.trial_update.
      // payload.task_type identifies PVT, GNG, or DISCRIMINATION.
      case 'session.trial_update':
        LabReview.EventBus.emit('trial-update', msg);
        break;

      // ── Weapon type captured from first Shot Fired of this session ──────────
      case 'session.weapon_info':
        LabReview.EventBus.emit('weapon-info', msg);
        break;

      // ── Database: participant list response ────────────────────────────────
      case 'db.participants_list':
        LabReview.EventBus.emit('db-participants-list', msg);
        break;

      // ── Database: session confirmed (name update + save acknowledged) ───────
      case 'db.session_saved':
        LabReview.EventBus.emit('db-session-saved', msg);
        break;

      // ── Database: session discarded (deleted from DB + disk) ─────────────────
      case 'db.session_discarded':
        LabReview.EventBus.emit('db-session-discarded', msg);
        break;

      // -- Database: participant name updated -------------------------------------------
      case 'db.participant_updated':
        LabReview.EventBus.emit('db-participant-updated', msg);
        break;

      // -- Performance Review: session history result -----------------------------------
      case 'perf.result':
        LabReview.EventBus.emit('perf-result', msg);
        break;

      case 'perf.delete_result':
        LabReview.EventBus.emit('perf-delete-result', msg);
        break;

      default:
        console.warn('[StreamRouter] Unknown message type:', msg.type);
    }
  }

  function _routeSample(streamId, timestamp, data, elapsedS) {
    const instances = chartInstances[streamId];

    if (_sessionLslStart === null && timestamp) {
      _sessionLslStart = timestamp;
    }
    const elapsed = (_sessionLslStart !== null && timestamp)
      ? Math.max(0, parseFloat((timestamp - _sessionLslStart).toFixed(3)))
      : null;

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
    LabReview.EventBus.emit('stream-sample-received', { streamId, timestamp, data, elapsed });

    // VirTra events also broadcast to EventBus for the event log
    if (streamId && (streamId.includes('VirTra') || streamId.includes('Marker'))) {
      LabReview.EventBus.emit('virtra-event', { timestamp, elapsedS: elapsed, data, stream: streamId });
    }

    // WP-06b: SessionInfo stream — parse JSON from data[0] and emit session metadata.
    // LSL convention: the SessionInfo stream publishes a single string channel whose
    // value is a JSON object: { participant_id, session_name, drill, ... }
    if (streamId && streamId.toLowerCase().includes('sessioninfo')) {
      try {
        const raw = Array.isArray(data) ? data[0] : data;
        const info = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (info && typeof info === 'object') {
          LabReview.EventBus.emit('session-info-received', {
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

  function activateLive()   { _send({ type: 'live.activate' }); }
  function deactivateLive() { _send({ type: 'live.deactivate' }); }

  function getCatalog() { return catalog; }
  function getState()   { _send({ type: 'system.get_state' }); }
  function ping()       { _send({ type: 'system.ping', ts: Date.now() / 1000 }); }

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
      return true;
    }
    return false;
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

  // ── Post-session SessionInfo query ───────────────────────────────────────────
  // Kept for backward compatibility with any external tooling that calls this.
  function sendGetSessionInfo(db_path) {
    _send({ type: 'session.get_info', db_path });
  }

  // ── Generic raw send (for page controllers) ──────────────────────────────────

  function sendRaw(obj) { return _send(obj); }

  /** Returns the Set of currently-subscribed stream IDs. */
  function getSubscribed() { return subscribed; }

  return {
    connect,
    subscribe, unsubscribe, subscribeAll,
    registerInstance, unregisterInstance,
    activateLive, deactivateLive,
    sendGetSessionInfo,
    sendRaw,
    send: sendRaw,   // generic alias for page controllers
    getCatalog, getSubscribed, getState, ping,
  };
})();
