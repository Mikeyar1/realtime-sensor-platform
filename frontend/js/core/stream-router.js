/**
 * stream-router.js
 *
 * Transport layer: manages the WebSocket connection to the backend.
 * Routes backend messages to the UI event bus. LSL ingestion and scoring stay
 * in the backend; this client displays their structured results.
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

  // ── Connection ──────────────────────────────────────────────────────────────

  function connect() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      console.log('[StreamRouter] Connected to', WS_URL);
      LabReview.EventBus.emit('ws-connected');
      _send({ type: 'system.get_state' });
    };

    ws.onclose = () => {
      console.log('[StreamRouter] Disconnected — retrying in', RECONNECT_DELAY, 'ms');
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
        LabReview.EventBus.emit('session-state-changed', msg);
        break;

      // session.list.result removed — Backend no longer handles replay sessions

      // ── Stream catalog ─────────────────────────────────────────────────
      case 'streams.catalog':
        LabReview.EventBus.emit('catalog-updated', msg.streams || []);
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

      case 'session.scenario_unsupported':
        LabReview.EventBus.emit('scenario-unsupported', msg);
        break;

      case 'session.context_updated':
        LabReview.EventBus.emit('scenario-context-updated', msg);
        break;

      case 'session.scoring_waiting':
        // The live round panel already shows its waiting state.
        break;

      case 'session.scoring_unavailable':
        LabReview.EventBus.emit('scoring-unavailable', msg);
        break;

      // ── Individual VirTra events (Shot Fired, Shot Hit, Target Activated…) ──
      // Backend broadcasts these for real-time metric computation in drill displays.
      case 'session.virtra_event':
        LabReview.EventBus.emit('virtra-event', msg);
        break;

      case 'session.custom_lsl_event':
        LabReview.EventBus.emit('custom-lsl-event', msg);
        break;

      case 'assessment.round_updated':
        LabReview.EventBus.emit('assessment-round-updated', msg);
        break;

      case 'assessment.round_physiology':
        LabReview.EventBus.emit('assessment-round-physiology', msg);
        break;

      case 'assessment.round_projection':
        LabReview.EventBus.emit('assessment-round-projection', msg);
        break;

      case 'assessment.catalog':
        LabReview.AssessmentCatalog = msg;
        LabReview.EventBus.emit('assessment-catalog', msg);
        break;

      case 'assessment.command_result':
        LabReview.EventBus.emit('assessment-command-result', msg);
        break;

      case 'perf.session_detail_result':
        LabReview.EventBus.emit('perf-session-detail-result', msg);
        break;

      // ── Live biosignal stats (HR, HRV, pupil) ──────────────────────────────
      case 'session.biosignal_update':
        LabReview.EventBus.emit('biosignal-update', msg);
        break;

      case 'session.speech_update':
        // Speech has no active frontend view.
        break;

      // AAR metrics computed after the scenario ends.
      case 'session.metrics_ready':
        LabReview.EventBus.emit('metrics-ready', msg);
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

  // ── Outgoing messages ───────────────────────────────────────────────────────

  function activateLive() { _send({ type: 'live.activate' }); }
  function deactivateLive() { _send({ type: 'live.deactivate' }); }


  // ── Helpers ─────────────────────────────────────────────────────────────────

  function _send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  // ── Generic raw send (for page controllers) ──────────────────────────────────

  function sendRaw(obj) { return _send(obj); }

  return {
    connect,
    activateLive, deactivateLive,
    send: sendRaw,
  };
})();
