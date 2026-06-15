/**
 * js/shared/api.js — Shared API client utilities.
 *
 * Exports:
 *   analysisGet(path)      — GET /api/* on the Analysis FastAPI (port 8081)
 *   analysisPost(path, body) — POST /api/* on the Analysis FastAPI
 *   wsSend(ws, obj)        — Safe JSON send to a WebSocket
 *
 * These are pure utility functions — they do NOT hold state.
 * The LabReplay.AnalysisAPI namespace object (in analysis/api-client.js)
 * is built on top of these for backward compat.
 *
 * @module shared/api
 */

import { ANALYSIS_API_BASE } from './constants.js';

/**
 * GET from the Analysis FastAPI.
 * @param {string} path — e.g. '/api/health'
 * @returns {Promise<any>}
 */
export async function analysisGet(path) {
  const res = await fetch(ANALYSIS_API_BASE + path);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

/**
 * POST to the Analysis FastAPI.
 * @param {string} path — e.g. '/api/epoch'
 * @param {object} body
 * @returns {Promise<any>}
 */
export async function analysisPost(path, body) {
  const res = await fetch(ANALYSIS_API_BASE + path, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

/**
 * Safe JSON send to an open WebSocket.
 * No-ops silently if the socket is not OPEN.
 * @param {WebSocket} ws
 * @param {object} obj
 */
export function wsSend(ws, obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}
