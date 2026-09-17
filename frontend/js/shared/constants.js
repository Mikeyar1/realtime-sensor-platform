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

/** Reconnect delay after WebSocket disconnect, ms. */
export const WS_RECONNECT_DELAY_MS = 3000;

/** Supported physiological signals. */
export const SIGNALS = ['hr', 'pupil', 'motion'];

/** Drill definitions: key, short label, long label.
 * Keys match config/drill_registry.toml drill keys exactly. */
export const DRILLS = [
  { key: 'general',                   label: 'General',     longLabel: 'General' },
  { key: 'behavioral_discrimination', label: 'BehDisc',     longLabel: 'Behavioral Discrimination' },
  { key: 'pvt',                       label: 'PVT',         longLabel: 'Psychomotor Vigilance Task' },
  { key: 'inhibition_assessment',     label: 'Inhibition',  longLabel: 'Inhibition Assessment' },
  { key: 'marksmanship',              label: 'Marksmanship',longLabel: 'Marksmanship' },
];
