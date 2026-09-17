/**
 * js/shared/format.js — Shared formatting utilities.
 *
 * Pure functions — no DOM access, no side effects.
 * Import individually to avoid bundling unused helpers.
 *
 * @module shared/format
 */

/**
 * Format elapsed seconds as MM:SS or HH:MM:SS.
 * @param {number} totalSeconds
 * @returns {string}
 */
export function formatElapsed(totalSeconds) {
  if (totalSeconds == null || isNaN(totalSeconds)) return '--:--';
  const s = Math.floor(totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  }
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

/**
 * Format a floating-point delta with sign and fixed decimal places.
 * @param {number|null} val
 * @param {number} [decimals=2]
 * @param {string} [unit='']
 * @returns {string}
 */
export function formatDelta(val, decimals = 2, unit = '') {
  if (val == null || isNaN(val)) return '—';
  const sign = val >= 0 ? '+' : '';
  return `${sign}${val.toFixed(decimals)}${unit ? ' ' + unit : ''}`;
}

/**
 * Format a duration in seconds to a human-readable string.
 * Examples: "1m 30s", "45s", "2h 3m"
 * @param {number} seconds
 * @returns {string}
 */
export function formatDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM > 0 ? `${h}h ${remM}m` : `${h}h`;
}

/**
 * Clamp a number between min and max, rounded to a given step.
 * @param {number} value
 * @param {number} min
 * @param {number} max
 * @param {number} [step=0.1]
 * @returns {number}
 */
export function clamp(value, min, max, step = 0.1) {
  const factor = 1 / step;
  return Math.min(max, Math.max(min, Math.round(value * factor) / factor));
}

/**
 * Truncate a string to maxLen characters, appending an ellipsis if needed.
 * @param {string} str
 * @param {number} maxLen
 * @returns {string}
 */
export function truncate(str, maxLen) {
  if (!str || str.length <= maxLen) return str || '';
  return str.slice(0, maxLen - 1) + '…';
}

/**
 * Format a Unix timestamp (seconds) as a local date-time string.
 * @param {number} unix
 * @returns {string}
 */
export function formatUnixTime(unix) {
  if (!unix) return '—';
  return new Date(unix * 1000).toLocaleString();
}
