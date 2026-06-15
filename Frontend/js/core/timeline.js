/**
 * timeline.js
 *
 * Controls the bottom timeline bar: playhead, progress, event markers.
 * In replay mode: scrubbable. In live mode: auto-advancing, not scrubbable.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.Timeline = (function () {
  let duration = 0;
  let position = 0;
  let isDragging = false;

  const els = {};

  function init() {
    els.track = document.getElementById('timeline-track');
    els.progress = document.getElementById('timeline-progress');
    els.head = document.getElementById('timeline-head');
    els.startLabel = document.getElementById('timeline-start');
    els.endLabel = document.getElementById('timeline-end');

    // Scrub interaction
    els.track.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    // Listen for position updates
    LabReplay.EventBus.on('position-updated', onPositionUpdate);
    LabReplay.EventBus.on('mode-changed', onModeChanged);
  }

  function onPositionUpdate(data) {
    if (isDragging) return; // don't update while user is scrubbing
    position = data.position;
    duration = data.duration || duration;
    render();
  }

  function onModeChanged(data) {
    duration = data.duration || duration;
    els.endLabel.textContent = formatTime(duration);
  }

  function render() {
    if (duration <= 0) return;
    const pct = Math.min((position / duration) * 100, 100);
    els.progress.style.width = pct + '%';
    els.head.style.left = pct + '%';
    els.startLabel.textContent = formatTime(position);
    els.endLabel.textContent = formatTime(duration);
  }

  function onMouseDown(e) {
    const state = LabReplay.ModeManager.getState();
    if (state.mode !== 'replay') return; // no scrub in live mode
    isDragging = true;
    updateFromMouse(e);
  }

  function onMouseMove(e) {
    if (!isDragging) return;
    updateFromMouse(e);
  }

  function onMouseUp() {
    if (!isDragging) return;
    isDragging = false;
    // Send seek command to backend
    LabReplay.StreamRouter.sendTransport('seek', position);
  }

  function updateFromMouse(e) {
    const rect = els.track.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    position = pct * duration;
    render();
  }

  /**
   * Add an event marker dot on the timeline.
   * @param {number} time - position in seconds
   * @param {string} color - CSS color
   * @param {string} title - hover text
   */
  function addEventMarker(time, color, title) {
    if (duration <= 0) return;
    const marker = document.createElement('div');
    marker.className = 'timeline-event-marker';
    marker.style.left = ((time / duration) * 100) + '%';
    marker.style.background = color;
    marker.title = title || '';
    els.track.appendChild(marker);
  }

  function clearEventMarkers() {
    els.track.querySelectorAll('.timeline-event-marker').forEach(m => m.remove());
  }

  function formatTime(seconds) {
    if (!seconds || seconds < 0) return '00:00:00';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
  }

  return { init, addEventMarker, clearEventMarkers, formatTime };
})();
