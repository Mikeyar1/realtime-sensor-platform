/**
 * api-client.js — Thin fetch() wrapper for the Analysis API on port 8081.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.AnalysisAPI = (function () {
  const BASE = 'http://127.0.0.1:8081';

  async function _get(path) {
    const res = await fetch(BASE + path);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  }

  async function _post(path, body) {
    const res = await fetch(BASE + path, {
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

  function health()                     { return _get('/api/health'); }
  function sessions(drill)              { return _get(`/api/sessions?drill=${drill}`); }
  function trials(session, drill)       { return _get(`/api/trials?session=${encodeURIComponent(session)}&drill=${drill}`); }
  function epoch(payload)               { return _post('/api/epoch', payload); }

  return { health, sessions, trials, epoch };
})();
