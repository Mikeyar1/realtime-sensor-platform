/**
 * event-ticker.js
 *
 * Reads VirTra event CSV + speech transcription CSV for the active session
 * via the WebSocket backend (session.get_csv / session.csv_data messages).
 *
 * Populates the bottom event bar:
 *   LEFT  — VirTra event feed  (scrolling log, auto-highlights current event)
 *   RIGHT — Speech panel       (shows the latest utterance at current position)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.EventTicker = (function () {

  // ── Config ───────────────────────────────────────────────────────────────
  // Event types to SUPPRESS from the log (too noisy / low meaning)
  const SUPPRESS = new Set([
    'Lane Targets Changed',
    'Lane Count Changed',
    'Set Starting Environment',
    'Target Deactivated',
    'Target Activated',
    'Score Changed',
  ]);

  // Color codes per event type (CSS variables from variables.css)
  const EVENT_COLORS = {
    'Shot Fired':       'var(--accent-blue)',
    'Shot Hit':         'var(--accent-green)',
    'Shot Miss':        'var(--accent-red)',
    'Unscored Hit':     'var(--accent-orange)',
    'Scenario Started': 'var(--accent-purple)',
    'Stage Changed':    'var(--accent-cyan)',
    'Start Of':         'var(--accent-cyan)',
    'default':          'var(--text-muted)',
  };

  const EVENT_ICONS = {
    'Shot Fired':       '🔫',
    'Shot Hit':         '🎯',
    'Shot Miss':        '💨',
    'Unscored Hit':     '◎',
    'Scenario Started': '▶',
    'Stage Changed':    '↩',
    'default':          '·',
  };

  // ── State ────────────────────────────────────────────────────────────────
  let _virtraEvents   = [];   // [{ ts, type, description }]
  let _speechEvents   = [];   // [{ ts, text }]
  let _currentElapsed = 0;
  let _activeIdx      = -1;
  let _loadedDbPath   = '';   // prevent duplicate loads
  let _sessionStartUnix = 0; // unix epoch (seconds) of session start — for real-time display

  const els = {};

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    els.feed          = document.getElementById('event-feed');
    els.speechText     = document.getElementById('speech-text');
    els.speechTime     = document.getElementById('speech-time');
    els.realtimeBlock  = document.getElementById('session-realtime-block');
    els.realtimeValue  = document.getElementById('session-realtime');

    // Elapsed position updates — drive the highlight cursor
    LabReplay.EventBus.on('position-updated', ({ position }) => {
      _currentElapsed = position;
      _tick();
      // Update real session clock in top bar
      if (els.realtimeValue && _sessionStartUnix) {
        els.realtimeValue.textContent = _fmtRealTime(position);
      }
    });

    // Session state changes — clear on idle/stopped, request CSV on playing/paused
    LabReplay.EventBus.on('session-state-changed', (msg) => {
      if (msg.state === 'idle' || msg.state === 'stopped') {
        _clear();
        _loadedDbPath = '';
        return;
      }
      // Request CSV data once per session load (db_path acts as the key)
      if ((msg.state === 'playing' || msg.state === 'paused' || msg.state === 'stopped') &&
          msg.db_path && msg.db_path !== _loadedDbPath) {
        _loadedDbPath = msg.db_path;
        LabReplay.StreamRouter.requestCsvData();
      }
    });

    // Receive the CSV payload from the backend
    LabReplay.EventBus.on('session-csv-data', (msg) => {
      const startUnix = msg.start_unix || 0;
      _sessionStartUnix = startUnix;

      // VirTra rows: [{ ts, type, description, tag }] — ts is unix, convert to elapsed
      _virtraEvents = (msg.virtra_rows || []).map(r => ({
        ts:          r.ts - startUnix,
        type:        r.type,
        description: r.description,
        tag:         r.tag || '',
      }));

      // Speech rows: [{ ts, text }] — ts is unix, convert to elapsed
      _speechEvents = (msg.speech_rows || []).map(r => ({
        ts:   r.ts - startUnix,
        text: r.text,
      }));

      _renderFeed();
      if (els.realtimeBlock && _sessionStartUnix) {
        els.realtimeBlock.style.display = '';
      }
      console.log(`[EventTicker] Loaded ${_virtraEvents.length} VirTra events, ` +
                  `${_speechEvents.length} speech events`);
    });

    // ── Live VirTra stream samples (LSL — live mode and replay) ──────────
    // stream-router.js emits 'virtra-event' for every sample from any stream
    // whose name includes 'VirTra'. Wire directly into the bottom bar feed.
    LabReplay.EventBus.on('virtra-event', (evt) => {
      _appendLiveEntry(evt);
    });

    console.log('[EventTicker] Initialized');
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  function _renderFeed() {
    if (!els.feed) return;
    els.feed.innerHTML = '';
    _activeIdx = -1;

    if (_virtraEvents.length === 0) {
      els.feed.innerHTML =
        '<span class="ticker-empty">No VirTra events found for this session.</span>';
      return;
    }

    for (let i = 0; i < _virtraEvents.length; i++) {
      const ev    = _virtraEvents[i];
      const color = _colorFor(ev.type);
      const icon  = _iconFor(ev.type);
      const tStr  = _fmtElapsed(ev.ts);

      const row = document.createElement('div');
      row.className   = 'ticker-row ticker-future';
      row.dataset.idx = i;

      row.innerHTML =
        `<span class="ticker-icon" style="color:${color}">${icon}</span>` +
        `<span class="ticker-time">${tStr}</span>` +
        `<span class="ticker-type" style="color:${color}">${ev.type}</span>` +
        `<span class="ticker-desc">${_esc(_shortDesc(ev.description))}</span>`;

      els.feed.appendChild(row);
    }
  }

  // ── Playback position tracking ───────────────────────────────────────────

  function _tick() {
    _updateVirtra();
    _updateSpeech();
  }

  function _updateVirtra() {
    if (!els.feed || _virtraEvents.length === 0) return;

    // Binary-search the last event at or before _currentElapsed
    let newIdx = -1;
    let lo = 0, hi = _virtraEvents.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (_virtraEvents[mid].ts <= _currentElapsed) { newIdx = mid; lo = mid + 1; }
      else hi = mid - 1;
    }

    if (newIdx === _activeIdx) return;
    _activeIdx = newIdx;

    els.feed.querySelectorAll('.ticker-row').forEach(r => {
      const idx = parseInt(r.dataset.idx, 10);
      r.className = 'ticker-row ' + (
        idx < newIdx  ? 'ticker-past'   :
        idx === newIdx ? 'ticker-active' :
                         'ticker-future'
      );
    });

    // Scroll active row into view
    const activeRow = els.feed.querySelector('.ticker-active');
    if (activeRow) activeRow.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function _updateSpeech() {
    if (!els.speechText) return;
    if (_speechEvents.length === 0) return;

    let last = null;
    for (const ev of _speechEvents) {
      if (ev.ts <= _currentElapsed) last = ev;
      else break;
    }

    if (!last) {
      els.speechText.textContent = '—';
      if (els.speechTime) els.speechTime.textContent = '';
      return;
    }
    els.speechText.textContent = last.text;
    if (els.speechTime) els.speechTime.textContent = _fmtElapsed(last.ts);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────

  function _clear() {
    _virtraEvents   = [];
    _speechEvents   = [];
    _activeIdx      = -1;
    _currentElapsed = 0;
    _sessionStartUnix = 0;
    if (els.realtimeBlock) els.realtimeBlock.style.display = 'none';
    if (els.realtimeValue) els.realtimeValue.textContent = '—';
    if (els.feed) {
      els.feed.innerHTML =
        '<span class="ticker-empty">Load a session to see events.</span>';
    }
    if (els.speechText) els.speechText.textContent = '—';
    if (els.speechTime) els.speechTime.textContent = '';
  }

  // ── Live LSL entry renderer ───────────────────────────────────────────────
  // Called for every virtra-event from the EventBus (live stream OR replay stream).
  // Writes directly into #event-feed without touching the replay-driven state.

  function _appendLiveEntry(evt) {
    if (!els.feed) return;

    // Extract text — data is [string] from LSL string channel
    const raw  = Array.isArray(evt.data) ? evt.data[0] : evt.data;
    const text = typeof raw === 'string' ? raw.trim() : String(raw ?? '');
    if (!text) return;

    // Suppress noisy housekeeping events
    const SUPPRESS_LIVE = [
      'Lane Targets Changed', 'Lane Count Changed', 'Set Starting Environment',
      'Target Deactivated', 'Target Activated', 'Score Changed',
    ];
    if (SUPPRESS_LIVE.some(s => text.includes(s))) return;

    // Clear placeholder on first live event
    const placeholder = els.feed.querySelector('.ticker-empty');
    if (placeholder) placeholder.remove();

    const color = _colorFor(_typeFrom(text));
    const icon  = _iconFor(_typeFrom(text));
    const tStr  = evt.elapsedS != null ? _fmtElapsed(evt.elapsedS) : '--:--:--';

    const row = document.createElement('div');
    row.className = 'ticker-row ticker-active';

    row.innerHTML =
      `<span class="ticker-icon" style="color:${color}">${icon}</span>` +
      `<span class="ticker-time">${tStr}</span>` +
      `<span class="ticker-type" style="color:${color}">${_typeFrom(text)}</span>` +
      `<span class="ticker-desc">${_esc(_shortDesc(text))}</span>`;

    els.feed.appendChild(row);
    els.feed.scrollTop = els.feed.scrollHeight;

    // Fade active highlight after 3 s
    setTimeout(() => row.classList.replace('ticker-active', 'ticker-past'), 3000);

    // Trim DOM to last 200 rows
    while (els.feed.children.length > 200) {
      els.feed.removeChild(els.feed.firstChild);
    }
  }

  function _typeFrom(text) {
    for (const type of Object.keys(EVENT_COLORS)) {
      if (type !== 'default' && text.includes(type)) return type;
    }
    if (text.includes('Stage') || text.startsWith('Start Of')) return 'Stage Changed';
    return 'Event Triggered';
  }


  function _colorFor(type) {
    return EVENT_COLORS[type] ||
           (type.startsWith('Start Of') ? EVENT_COLORS['Start Of'] : EVENT_COLORS.default);
  }

  function _iconFor(type) {
    return EVENT_ICONS[type] ||
           (type.startsWith('Start Of') ? EVENT_ICONS['Stage Changed'] : EVENT_ICONS.default);
  }

  /** Format elapsed seconds as HH:MM:SS.mmm */
  function _fmtElapsed(sec) {
    if (sec == null || sec < 0) sec = 0;
    const h   = Math.floor(sec / 3600);
    const m   = Math.floor((sec % 3600) / 60);
    const s   = Math.floor(sec % 60);
    const ms  = Math.round((sec % 1) * 1000);
    return [h, m, s].map(v => String(v).padStart(2, '0')).join(':') +
           '.' + String(ms).padStart(3, '0');
  }

  /** Format the real wall-clock time when the event occurred in the original session.
   *  Returns e.g. "Feb 18, 2026 · 11:41:23 AM"
   */
  function _fmtRealTime(elapsedSec) {
    if (!_sessionStartUnix) return '';
    const d = new Date((_sessionStartUnix + elapsedSec) * 1000);
    const date = d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
    return `${date} · ${time}`;
  }

  function _shortDesc(desc) {
    if (!desc) return '';
    return desc.length > 90 ? desc.substring(0, 87) + '…' : desc;
  }

  // Minimal XSS-safe text escaping for innerHTML
  function _esc(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * Minimal CSV line splitter: handles quoted fields with embedded commas and
   * escaped double-quotes ("").
   */
  function _splitCsvLine(line) {
    const result = [];
    let inQuote = false;
    let cur = '';
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuote && line[i + 1] === '"') { cur += '"'; i++; }
        else inQuote = !inQuote;
      } else if (ch === ',' && !inQuote) {
        result.push(cur); cur = '';
      } else {
        cur += ch;
      }
    }
    result.push(cur);
    return result;
  }

  return { init };
})();
