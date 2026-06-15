/**
 * chart-virtra.js — VirTra Scenario Event Log
 *
 * Displays a live scrolling feed of VirTra tactical scenario events.
 * Source: V_300_VirTraEvents LSL stream, type=VirTraEvents, irregular rate.
 *
 * Works in two modes:
 *   Live:   LSL samples arrive via pushSample() — each sample is one event string.
 *   Replay: Same path — the replay engine emits stream.sample for VirTra rows,
 *           which are routed here through StreamRouter like any other stream.
 *
 * NO CSV reading. NO EventTicker dependency. Just raw LSL sample strings.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartVirTra = (function () {

  const MAX_EVENTS = 300;   // max entries kept in memory and DOM

  const EVENT_STYLES = {
    'Shot Fired': { tag: 'SHOT', color: '#60A5FA' },
    'Shot Hit': { tag: 'HIT', color: '#34D399' },
    'Shot Miss': { tag: 'MISS', color: '#F87171' },
    'Unscored Hit': { tag: 'UNSC', color: '#FBBF24' },
    'Actor State Changed': { tag: 'ACTOR', color: '#A78BFA' },
    'Actor IsFrozen Changed': { tag: 'FRZN', color: '#A78BFA' },
    'Event Triggered': { tag: 'TRIG', color: '#F59E0B' },
    'Scenario Started': { tag: 'SYS', color: '#6EE7B7' },
    'Scenario Stopped': { tag: 'SYS', color: '#6EE7B7' },
    'Stage Changed': { tag: 'STG', color: '#67E8F9' },
  };
  const DEFAULT_STYLE = { tag: 'EVT', color: '#9CA3AF' };

  const SUPPRESS = new Set([
    'Lane Targets Changed',
    'Lane Count Changed',
    'Set Starting Environment',
    'Target Deactivated',
    'Target Activated',
    'Score Changed',
  ]);

  // ── Factory ────────────────────────────────────────────────────────────────

  function create(container, card, descriptor) {
    const _events = [];   // { elapsed, text, style }

    // ── DOM ────────────────────────────────────────────────────────────────
    container.style.padding = '0';
    container.style.overflow = 'hidden';

    const _root = document.createElement('div');
    _root.style.cssText = 'display:flex;flex-direction:column;height:100%;';

    // Empty state
    const _empty = document.createElement('div');
    _empty.style.cssText =
      'flex:1;display:flex;align-items:center;justify-content:center;' +
      'font:400 12px/1.4 "Source Sans 3",system-ui;color:rgba(0,0,0,0.35);text-align:center;padding:16px;';
    _empty.textContent = 'Waiting for VirTra scenario events…';

    // Entries feed
    const _feed = document.createElement('div');
    _feed.style.cssText =
      'flex:1;overflow-y:auto;display:none;' +
      'font:400 11px/1.5 "Source Sans 3",system-ui;';

    _root.appendChild(_empty);
    _root.appendChild(_feed);
    container.appendChild(_root);

    // ── Helpers ────────────────────────────────────────────────────────────

    function _detectStyle(text) {
      if (!text) return DEFAULT_STYLE;
      for (const [type, style] of Object.entries(EVENT_STYLES)) {
        if (text.includes(type)) return style;
      }
      return DEFAULT_STYLE;
    }

    function _isSuppressed(text) {
      if (!text) return false;
      for (const s of SUPPRESS) {
        if (text.includes(s)) return true;
      }
      return false;
    }

    function _fmtElapsed(sec) {
      if (sec == null || sec < 0) return '--:--';
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function _appendEntry(evt) {
      const row = document.createElement('div');
      row.style.cssText =
        'display:flex;align-items:baseline;gap:6px;padding:3px 10px;' +
        'border-bottom:1px solid rgba(0,0,0,0.05);';

      const time = document.createElement('span');
      time.style.cssText =
        'flex-shrink:0;font-size:10px;color:rgba(0,0,0,0.38);' +
        'font-variant-numeric:tabular-nums;white-space:nowrap;';
      time.textContent = _fmtElapsed(evt.elapsed);

      const tag = document.createElement('span');
      tag.style.cssText =
        `flex-shrink:0;font-size:9px;font-weight:700;letter-spacing:.5px;` +
        `color:${evt.style.color};white-space:nowrap;`;
      tag.textContent = `[${evt.style.tag}]`;

      const text = document.createElement('span');
      text.style.cssText =
        'flex:1;font-size:11px;color:rgba(0,0,0,0.75);word-break:break-word;';
      text.textContent = evt.text.length > 120 ? evt.text.slice(0, 117) + '…' : evt.text;

      row.appendChild(time);
      row.appendChild(tag);
      row.appendChild(text);
      _feed.appendChild(row);

      // Auto-scroll
      _feed.scrollTop = _feed.scrollHeight;

      // Trim DOM
      while (_feed.children.length > MAX_EVENTS) {
        _feed.removeChild(_feed.firstChild);
      }
    }

    // ── Data ingestion ─────────────────────────────────────────────────────

    function pushSample(timestamp, data, elapsedS) {
      // VirTra sends a single string channel per event
      const raw = Array.isArray(data) ? data[0] : data;
      const text = typeof raw === 'string' ? raw.trim()
        : raw != null ? String(raw)
          : null;

      if (!text || _isSuppressed(text)) return;

      const style = _detectStyle(text);
      const evt = { elapsed: elapsedS, text, style };
      _events.push(evt);
      if (_events.length > MAX_EVENTS) _events.shift();

      // Show feed, hide empty state
      _empty.style.display = 'none';
      _feed.style.display = '';

      _appendEntry(evt);

      // Update live value in card header with latest event tag
      if (card && card.updateLiveValue) {
        card.updateLiveValue(`[${style.tag}]`);
      }
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────

    function resize() { }   // no-op — CSS flex handles layout

    function destroy() {
      _root.remove();
    }

    return { pushSample, resize, destroy };
  }

  return { create };
})();
