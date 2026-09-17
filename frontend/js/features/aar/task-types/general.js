/**
 * features/aar/task-types/general.js
 *
 * Real-Time and Post-Session AAR display module for GENERAL / unmapped drills
 * (e.g. Marksmanship, custom scenarios, unregistered drills).
 *
 * Provides full real-time metrics during the scenario:
 *   - Live Session Context: elapsed timer, target activations, targets active
 *   - Live Shooting Performance: shots fired, hits, misses, accuracy %
 *   - Live Event Breakdown: dynamic frequency table of all VirTra events
 *   - Live Event Stream: rolling feed of the most recent events
 *   - Physiological signals: live HR, HRV, pupil diameter
 *   - Post-session: merges official computed metrics from Analysis when available.
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.General = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _container        = null;
  let _scenarioName     = '';
  let _taskType         = 'GENERAL';
  let _startTs          = null;      // start timestamp (Unix epoch or performance)
  let _timerInterval    = null;

  // Real-time counters
  let _shotsFired       = 0;
  let _shotsHit         = 0;
  let _shotsMissed      = 0;
  let _targetActivations   = 0;
  let _targetDeactivations = 0;
  let _eventSummary     = {};        // { "Shot Fired": 12, ... }
  let _eventFeed        = [];        // [ { timeStr, type, desc, badgeClass }, ... ]
  const MAX_FEED_ITEMS  = 25;

  // ── Mount / unmount ────────────────────────────────────────────────────────

  function mount(containerEl) {
    _container = containerEl;
    _reset();
    _renderWaiting();
  }

  function unmount() {
    _stopTimer();
    _container = null;
  }

  function _reset() {
    _stopTimer();
    _scenarioName        = '';
    _taskType            = 'GENERAL';
    _startTs             = null;
    _shotsFired          = 0;
    _shotsHit            = 0;
    _shotsMissed         = 0;
    _targetActivations   = 0;
    _targetDeactivations = 0;
    _eventSummary        = {};
    _eventFeed           = [];
  }

  function reset() {
    _reset();
    _renderWaiting();
  }

  function _stopTimer() {
    if (_timerInterval) {
      clearInterval(_timerInterval);
      _timerInterval = null;
    }
  }

  // ── Waiting state ──────────────────────────────────────────────────────────

  function _renderWaiting() {
    if (!_container) return;
    _container.innerHTML = `
      <div class="aar-panel" id="aar-gen-panel">
        <div class="aar-waiting">
          <div class="aar-waiting-label">General Drill</div>
          <div class="aar-waiting-sub">Waiting for VirTra scenario to start…</div>
        </div>
      </div>
    `;
  }

  // ── Scenario started ───────────────────────────────────────────────────────

  function onScenarioStarted(payload) {
    if (!_container) return;
    _reset();

    _scenarioName = payload.scenario_name || 'General Drill';
    _taskType     = payload.task_type || 'GENERAL';
    _startTs      = payload.timestamp || (Date.now() / 1000);

    _container.innerHTML = `
      <div class="aar-panel" id="aar-gen-panel">

        <!-- Live Session Context Cards -->
        <div class="aar-section" id="aar-gen-context-section">
          <div class="aar-section-label">Session Context</div>
          <div class="aar-grid" id="aar-gen-context-grid">
            ${_card('ctx-scenario',  _scenarioName || '\u2014',         'Scenario',         'Name of the VirTra scenario that was played.')}
            ${_card('ctx-simulator', payload.simulator        || '\u2014', 'Simulator',        'Simulator software used (e.g. V-AIMS).')}
            ${_card('ctx-simsystem', payload.simulator_system || '\u2014', 'Simulator System', 'Simulator hardware/system (e.g. V-300).')}
            ${_card('ctx-weapon',    '\u2014',                             'Weapon Type',      'Weapon type used by the trainee (from first shot).')}
            ${_card('duration',      '0s',   'Session Duration',  'Elapsed time since scenario started')}
            ${_card('targets-act',   '0',    'Targets Activated', 'Stimuli / target appearances')}
            ${_card('targets-live',  '0',    'Active Targets',    'Currently visible / active targets')}
          </div>
        </div>

        <!-- Live Shooting Performance -->
        <div class="aar-section" id="aar-gen-shots-section">
          <div class="aar-section-label">Shooting Performance</div>
          <div class="aar-grid aar-grid--4col" id="aar-gen-shots-grid">
            ${_card('shots-fired', '0', 'Shots Fired', 'Total trigger presses detected')}
            ${_card('shots-hit', '0', 'Shots Hit', 'Shots on target', 'aar-card--good')}
            ${_card('shots-miss', '0', 'Shots Missed', 'Shots off target')}
            ${_card('shot-acc', '—', 'Shot Accuracy', 'Hits ÷ Shots Fired × 100')}
          </div>
        </div>

        <!-- Live Event Breakdown Table -->
        <div class="aar-section" id="aar-gen-events-section">
          <div class="aar-section-label">Live Event Breakdown</div>
          <div class="aar-gen-event-table" id="aar-gen-event-table">
            <div style="padding:10px;font-size:0.8rem;color:var(--text-muted);text-align:center;">
              Listening for events…
            </div>
          </div>
        </div>

        <!-- Live Event Stream Feed -->
        <div class="aar-section" id="aar-gen-feed-section">
          <div class="aar-section-label">Live Event Stream</div>
          <div class="aar-gen-feed" id="aar-gen-feed">
            <div style="padding:10px;font-size:0.8rem;color:var(--text-muted);text-align:center;">
              Event stream active. Events will appear in real time.
            </div>
          </div>
        </div>

        <!-- Catch-all for post-scenario extra metrics -->
        <div class="aar-section" id="aar-gen-extra-section" style="display:none">
          <div class="aar-section-label">Additional Metrics</div>
          <div class="aar-gen-kv-list" id="aar-gen-kv-list"></div>
        </div>

        <!-- Export / Session Complete row -->
        <div class="aar-export-row" id="aar-gen-export" style="display:none">
          <span class="aar-export-hint" id="aar-gen-export-hint">✓ Scenario Complete</span>
        </div>

      </div>
    `;

    // Start live elapsed timer
    _startTimer();
  }

  function _startTimer() {
    _stopTimer();
    _timerInterval = setInterval(() => {
      if (_startTs == null) return;
      const elapsed = Math.max(0, Math.floor((Date.now() / 1000) - _startTs));
      const mins = Math.floor(elapsed / 60);
      const secs = elapsed % 60;
      const fmt = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
      _setCardValue('duration', fmt);
    }, 1000);
  }

  // ── Real-time event handling ───────────────────────────────────────────────

  function onEvent(evt) {
    if (!evt || !_container) return;
    const type = evt.event_type || '';
    const desc = evt.description  || '';
    const ts   = evt.timestamp    || (Date.now() / 1000);

    // 1. Tally in event summary
    if (type) {
      _eventSummary[type] = (_eventSummary[type] || 0) + 1;
    }

    // 2. Classify shooting & target events
    let badgeCls = '';
    if (type === 'Shot Fired') {
      _shotsFired++;
      badgeCls = 'aar-gen-feed-badge--shot';
    } else if (type === 'Shot Hit' || type === 'Unscored Hit') {
      _shotsHit++;
      badgeCls = 'aar-gen-feed-badge--hit';
    } else if (type === 'Shot Miss') {
      _shotsMissed++;
      badgeCls = 'aar-gen-feed-badge--miss';
    } else if (type === 'Target Activated' || type === 'New Target') {
      _targetActivations++;
      badgeCls = 'aar-gen-feed-badge--target';
    } else if (type === 'Target Deactivated' || type === 'Target Removed') {
      _targetDeactivations++;
    }

    // 3. Append to rolling event feed
    const relSec = _startTs ? Math.max(0, (ts - _startTs)).toFixed(1) : '0.0';
    _eventFeed.unshift({
      timeStr: `+${relSec}s`,
      type:    type,
      desc:    desc || type,
      badge:   badgeCls,
    });
    if (_eventFeed.length > MAX_FEED_ITEMS) {
      _eventFeed.pop();
    }

    // 4. Update UI
    _updateLiveUI();
  }

  function _updateLiveUI() {
    // Context
    _setCardValue('targets-act', _targetActivations);
    const active = Math.max(0, _targetActivations - _targetDeactivations);
    _setCardValue('targets-live', active);

    // Shooting
    _setCardValue('shots-fired', _shotsFired);
    _setCardValue('shots-hit',   _shotsHit);
    _setCardValue('shots-miss',  _shotsMissed);

    let accStr = '—';
    let accCls = '';
    if (_shotsFired > 0) {
      const acc = Math.round((_shotsHit / _shotsFired) * 100);
      accStr = `${acc}%`;
      accCls = acc >= 75 ? 'aar-card--good' : (acc >= 50 ? '' : 'aar-card--warn');
    }
    _setCardValue('shot-acc', accStr, accCls);

    // Event table
    _renderEventTable();

    // Event feed
    _renderEventFeed();
  }

  function _renderEventTable() {
    const tableEl = document.getElementById('aar-gen-event-table');
    if (!tableEl) return;

    const entries = Object.entries(_eventSummary)
      .sort(([, a], [, b]) => b - a);

    if (entries.length === 0) return;

    tableEl.innerHTML = entries.map(([name, count]) => `
      <div class="aar-gen-event-row">
        <span class="aar-gen-event-name">${_esc(name)}</span>
        <span class="aar-gen-event-count">${count}</span>
      </div>
    `).join('');
  }

  function _renderEventFeed() {
    const feedEl = document.getElementById('aar-gen-feed');
    if (!feedEl) return;

    if (_eventFeed.length === 0) return;

    feedEl.innerHTML = _eventFeed.map(item => `
      <div class="aar-gen-feed-item">
        <span class="aar-gen-feed-time">${_esc(item.timeStr)}</span>
        <span class="aar-gen-feed-badge ${item.badge}">${_esc(item.type)}</span>
        <span class="aar-gen-feed-desc">${_esc(item.desc)}</span>
      </div>
    `).join('');
  }

  // ── Metrics ready (Analysis post-scenario results) ─────────────────────────

  function onMetricsReady(metrics) {
    _stopTimer();

    const indicator = document.getElementById('aar-gen-live-indicator');
    if (indicator) {
      indicator.innerHTML = `<span style="color:var(--text-muted);">SESSION COMPLETE</span>`;
    }

    if (!metrics || !_container) return;

    // Merge duration
    if (metrics.duration_s != null) {
      _setCardValue('duration', `${metrics.duration_s}s`);
    }
    if (metrics.target_activations != null) {
      _setCardValue('targets-act', metrics.target_activations);
    }
    if (metrics.shots_fired != null) {
      _setCardValue('shots-fired', metrics.shots_fired);
    }
    if (metrics.shots_hit != null) {
      _setCardValue('shots-hit', metrics.shots_hit);
    }
    if (metrics.shots_missed != null) {
      _setCardValue('shots-miss', metrics.shots_missed);
    }
    if (metrics.shot_accuracy != null) {
      _setCardValue('shot-acc', `${metrics.shot_accuracy}%`, _rateClass(metrics.shot_accuracy, 50, 75));
    }

    // Merge event summary from Analysis if richer
    if (metrics.event_summary && typeof metrics.event_summary === 'object') {
      for (const [k, v] of Object.entries(metrics.event_summary)) {
        _eventSummary[k] = Math.max(_eventSummary[k] || 0, v);
      }
      _renderEventTable();
    }

    // Render any extra scalar metrics
    const KNOWN = new Set([
      'shots_fired', 'shots_hit', 'shots_missed', 'shot_accuracy',
      'target_activations', 'duration_s', 'event_summary', 'biosignals',
    ]);
    const extras = Object.entries(metrics)
      .filter(([k, v]) => !KNOWN.has(k) && v != null && typeof v !== 'object');

    if (extras.length > 0) {
      const listEl = document.getElementById('aar-gen-kv-list');
      if (listEl) {
        listEl.innerHTML = extras.map(([k, v]) => {
          const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
          return `
            <div class="aar-gen-kv-row">
              <span class="aar-gen-kv-label">${_esc(label)}</span>
              <span class="aar-gen-kv-value">${_esc(String(v))}</span>
            </div>`;
        }).join('');
        const sec = document.getElementById('aar-gen-extra-section');
        if (sec) sec.style.display = '';
      }
    }
  }

  // ── Sensor update ──────────────────────────────────────────────────────────

  // onSensorUpdate — MetricCardRenderer handles physio cards
  function onSensorUpdate(_payload) { /* no-op */ }

  // ── Scenario stopped ───────────────────────────────────────────────────────

  function onScenarioStopped(payload) {
    _stopTimer();
    const row = document.getElementById('aar-gen-export');
    if (row) row.style.display = '';
  }

  // ── Weapon info ────────────────────────────────────────────────────────────

  /**
   * Called when Backend broadcasts session.weapon_info (first Shot Fired).
   * Updates the Weapon Type context card.
   */
  function onWeaponInfo(payload) {
    if (!payload || !payload.weapon_type) return;
    _setCardValue('ctx-weapon', payload.weapon_type);
  }

  // ── Card helpers ───────────────────────────────────────────────────────────

  function _card(id, value, label, tip, stateClass) {
    const isText = id.startsWith('ctx-');
    const cls = [stateClass, isText ? 'aar-card--text' : ''].filter(Boolean).join(' ');
    const clsAttr = cls ? ` class="aar-card ${cls}"` : ' class="aar-card"';
    const tipAttr = tip ? ` data-tip="${_esc(tip)}"` : '';
    const valText = value ?? '—';
    return `
      <div${clsAttr} id="aar-card-gen-${id}">
        ${tip ? `<button class="aar-info-btn"${tipAttr}>ℹ</button>` : ''}
        <div class="aar-card-value ${isText ? 'aar-card-value--text' : ''}" id="aar-val-gen-${id}" title="${_esc(String(valText))}">${valText}</div>
        <div class="aar-card-label">${label}</div>
      </div>`;
  }

  function _setCardValue(id, value, stateClass) {
    const valEl  = document.getElementById(`aar-val-gen-${id}`);
    const cardEl = document.getElementById(`aar-card-gen-${id}`);
    const valText = value ?? '—';
    if (valEl) {
      valEl.textContent = valText;
      valEl.title = valText;
    }
    if (cardEl && stateClass !== undefined) {
      cardEl.classList.remove('aar-card--good', 'aar-card--warn', 'aar-card--alert');
      if (stateClass) cardEl.classList.add(stateClass);
    }
  }

  function _setPhys(_id, _value, _fmt) { /* no-op — MetricCardRenderer handles physio */ }

  function _esc(str) {
    return (str || '').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _rateClass(rate, warnThreshold, goodThreshold) {
    if (rate == null) return '';
    if (rate >= goodThreshold) return 'aar-card--good';
    if (rate >= warnThreshold) return '';
    return 'aar-card--warn';
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return {
    mount,
    unmount,
    reset,
    onScenarioStarted,
    onEvent,
    onMetricsReady,
    onSensorUpdate,
    onScenarioStopped,
    onWeaponInfo,
  };

})();
