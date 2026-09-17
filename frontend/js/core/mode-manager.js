/**
 * mode-manager.js
 *
 * Session state manager. Driven entirely by session.state messages from the backend.
 * Stores latest session state, emits events on EventBus, and provides getState().
 */

window.LabReview = window.LabReview || {};

LabReview.ModeManager = (function () {

  // Last known state from backend
  let _state = {
    mode: 'live',
    state: 'idle',
    elapsed_seconds: 0,
    duration_seconds: 0,
    session_id: '',
    session_name: '',
    db_path: '',
    stream_count: 0,
  };

  // ── Init ────────────────────────────────────────────────────────────────────

  function init() {
    // On connect → request current state
    LabReview.EventBus.on('ws-connected', () => {
      LabReview.StreamRouter.getState();
    });
  }

  // ── Main entry point: called by stream-router for every session.state message ──

  function onSessionState(msg) {
    _state = { ...msg };

    // Emit to other components (timeline, live-intel, etc.)
    LabReview.EventBus.emit('session-state-changed', msg);

    if (msg.duration_seconds > 0) {
      LabReview.EventBus.emit('position-updated', {
        position: msg.elapsed_seconds,
        duration: msg.duration_seconds,
        speed: 1.0,
        paused: msg.state === 'paused',
      });
    }
  }

  // ── Public ───────────────────────────────────────────────────────────────────

  function getState() { return { ..._state }; }

  return { init, onSessionState, getState };
})();
