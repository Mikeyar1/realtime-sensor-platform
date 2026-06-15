/**
 * js/shared/constants.js — Application-wide constants.
 *
 * Single source of truth for URLs and configuration that is shared
 * between multiple modules. Import from here instead of hard-coding.
 *
 * @module shared/constants
 */

/** WebSocket backend URL — auto-detects host from the page origin. */
export const WS_URL = `ws://${window.location.hostname || 'localhost'}:8500`;

/** Analysis API base URL (FastAPI, port 8081). */
export const ANALYSIS_API_BASE = 'http://127.0.0.1:8081';

/** Reconnect delay after WebSocket disconnect, ms. */
export const WS_RECONNECT_DELAY_MS = 3000;

/** Supported physiological signals. */
export const SIGNALS = ['hr', 'pupil', 'motion'];

/** Drill definitions: key, short label, long label. */
export const DRILLS = [
  { key: 'behdisc',  label: 'BehDisc',   longLabel: 'Behavioral Discrimination' },
  { key: 'pvt',      label: 'PVT',        longLabel: 'Psychomotor Vigilance Task' },
  { key: 'l2gonogo', label: 'L2GoNoGo',   longLabel: 'L2 Go/No-Go' },
];
