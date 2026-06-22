/**
 * event-bus.js
 *
 * Lightweight pub/sub for broadcasting VirTra events to all chart plugins
 * so they can render vertical annotation markers without tight coupling.
 *
 * Usage:
 *   LabReplay.EventBus.on('virtra-event', callback);
 *   LabReplay.EventBus.emit('virtra-event', { timestamp, type, text });
 */

window.LabReplay = window.LabReplay || {};

LabReplay.EventBus = (function () {
  const listeners = {};

  return {
    /**
     * Subscribe to an event.
     * @param {string} event
     * @param {Function} callback
     */
    on(event, callback) {
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(callback);
    },

    /**
     * Unsubscribe from an event.
     * @param {string} event
     * @param {Function} callback
     */
    off(event, callback) {
      if (!listeners[event]) return;
      listeners[event] = listeners[event].filter(cb => cb !== callback);
    },

    /**
     * Emit an event to all subscribers.
     * @param {string} event
     * @param {*} data
     */
    emit(event, data) {
      if (!listeners[event]) return;
      for (const cb of listeners[event]) {
        try {
          cb(data);
        } catch (err) {
          console.error(`[EventBus] Error in listener for "${event}":`, err);
        }
      }
    }
  };
})();
