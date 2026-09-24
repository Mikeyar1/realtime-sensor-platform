/**
 * perf-review.js -- Session History page controller.
 *
 * Exposes: LabReview.PerfReviewPage = { init }
 *
 * Flow:
 *   1. Render left panel (participant input only initially).
 *   2. On participant selection, query ALL sessions for that participant.
 *   3. Populate scenario filter from returned data; apply depth filter client-side.
 *   4. Render table + sparklines in the right panel on results.
 *   5. Scenario/depth changes re-filter client-side (no extra server round-trip).
 */

window.LabReview = window.LabReview || {};

LabReview.PerfReviewPage = (function () {

  // ---- State ------------------------------------------------------------------

  let _allSessions    = [];  // all sessions returned for current participant
  let _activeIdx      = -1;
  let _debounceTimer  = null;
  let _pendingDelete = false;

  // DOM refs
  let _participantInput, _scenarioSelect, _depthInput;
  let _tableWrap, _tableBody, _tableCount;
  let _chartScroll, _chartBreadcrumb;
  let _filterRow;  // the scenario + depth row (hidden until sessions loaded)

  // ---- Metric definitions per drill_key ---------------------------------------

  const METRIC_DEFS = {
    pvt: [
      { field: 'total_trials', label: 'Total Trials', unit: '', lowerBetter: false },
      { field: 'duration_s', label: 'Session Duration', unit: 's', lowerBetter: false },
      { field: 'hits_pct', label: 'Hits', unit: '%', lowerBetter: false },
      { field: 'omissions', label: 'Omissions', unit: '', lowerBetter: true },
      { field: 'response_rate_pct', label: 'Response Rate', unit: '%', lowerBetter: false },
      { field: 'omission_rate_pct', label: 'Omission Rate', unit: '%', lowerBetter: true },
      { field: 'rt_median_ms', label: 'Median RT', unit: 'ms', lowerBetter: true },
      { field: 'rt_mean_ms', label: 'Mean RT', unit: 'ms', lowerBetter: true },
      { field: 'rt_stdev_ms', label: 'RT Std Dev', unit: 'ms', lowerBetter: true },
      { field: 'rt_min_ms', label: 'Minimum RT', unit: 'ms', lowerBetter: true },
      { field: 'rt_max_ms', label: 'Maximum RT', unit: 'ms', lowerBetter: true },
    ],
    gng: [
      { field: 'total_trials', label: 'Total Trials', unit: '', lowerBetter: false },
      { field: 'go_trials', label: 'Go Trials', unit: '', lowerBetter: false },
      { field: 'no_go_trials', label: 'No-Go Trials', unit: '', lowerBetter: false },
      { field: 'hit_rate_pct', label: 'Hit Rate', unit: '%', lowerBetter: false },
      { field: 'omissions', label: 'Omissions', unit: '', lowerBetter: true },
      { field: 'commission_errors', label: 'Commission Errors (False Alarms)', unit: '', lowerBetter: true },
      { field: 'correct_rejection_rate_pct', label: 'Correct Rejection Rate', unit: '%', lowerBetter: false },
      { field: 'overall_accuracy_pct', label: 'Overall Accuracy', unit: '%', lowerBetter: false },
      { field: 'rt_median_ms', label: 'Median RT', unit: 'ms', lowerBetter: true },
      { field: 'rt_mean_ms', label: 'Mean RT', unit: 'ms', lowerBetter: true },
      { field: 'rt_stdev_ms', label: 'RT Std Dev', unit: 'ms', lowerBetter: true },
      { field: 'rt_n', label: 'RT Count', unit: 'n', lowerBetter: false },
    ],
    behavioral_discrimination: [
      { field: 'discrimination.total_trials', label: 'Total Trials', unit: '', lowerBetter: false },
      { field: 'duration_s', label: 'Session Duration', unit: 's', lowerBetter: false },
      { field: 'discrimination.threat_hit_rate_pct', label: 'Threat Hit Rate', unit: '%', lowerBetter: false },
      { field: 'discrimination.threat_omission_rate_pct', label: 'Threat Omission Rate', unit: '%', lowerBetter: true },
      { field: 'discrimination.commission_error_rate_pct', label: 'Commission Errors (False Alarms)', unit: '%', lowerBetter: true },
      { field: 'discrimination.correct_rejection_rate_pct', label: 'Correct Rejection Rate', unit: '%', lowerBetter: false },
      { field: 'discrimination.discrimination_accuracy_pct', label: 'Discrimination Accuracy', unit: '%', lowerBetter: false },
      { field: 'discrimination.threat_rt_median_ms', label: 'Median Threat RT', unit: 'ms', lowerBetter: true },
      { field: 'discrimination.threat_rt_mean_ms', label: 'Mean Threat RT', unit: 'ms', lowerBetter: true },
      { field: 'discrimination.threat_rt_stdev_ms', label: 'Threat RT Std Dev', unit: 'ms', lowerBetter: true },
      { field: 'pvt.total_trials', label: 'PVT Trials', unit: '', lowerBetter: false },
      { field: 'pvt.hits_pct', label: 'PVT Hits', unit: '%', lowerBetter: false },
      { field: 'pvt.omissions', label: 'PVT Omissions', unit: '', lowerBetter: true },
      { field: 'pvt.rt_median_ms', label: 'PVT Median RT', unit: 'ms', lowerBetter: true },
    ],
  };

  const BIOSIGNAL_DEFS = [
    { field: 'hr_mean_bpm',     label: 'Heart Rate',   unit: 'bpm', lowerBetter: false },
    { field: 'hrv_rmssd_ms',    label: 'HRV (RMSSD)',  unit: 'ms',  lowerBetter: false },
    { field: 'pupil_l_mean_mm', label: 'Pupil Left',   unit: 'mm',  lowerBetter: false },
    { field: 'pupil_r_mean_mm', label: 'Pupil Right',  unit: 'mm',  lowerBetter: false },
  ];

  // ---- Init -------------------------------------------------------------------

  function init() {
    if (LabReview.TopBarManager) {
      LabReview.TopBarManager.register('perf-review', _renderTopBar, function () {});
    }
    LabReview.EventBus.on('db-participants-list', _onParticipantsList);
    LabReview.EventBus.on('perf-result', _onPerfResult);
    LabReview.EventBus.on('perf-delete-result', _onDeleteResult);
    LabReview.EventBus.on('page-changed', function (page) {
      if (page === 'perf-review') _renderPageContent();
    });
  }

  function _renderTopBar(container) {
    container.innerHTML = '<div class="topbar" id="perf-review-topbar"><span class="topbar-title">Session History</span></div>';
  }

  // ---- Page shell -------------------------------------------------------------

  function _renderPageContent() {
    const pageEl = document.getElementById('page-perf-review');
    if (!pageEl || pageEl.dataset.rendered) return;
    pageEl.dataset.rendered = '1';

    pageEl.innerHTML = `
<div class="pr-shell">

  <!-- LEFT: Query + session list -->
  <div class="pr-query-panel">

    <div class="pr-panel-header">
      <p class="pr-panel-title">Participant</p>
    </div>

    <div class="pr-form">

      <!-- Participant search -->
      <div class="pr-form-group">
        <label class="pr-form-label" for="pr-participant">Name</label>
        <input
          id="pr-participant"
          class="pr-input"
          type="text"
          list="pr-participant-datalist"
          placeholder="Type or select participant..."
          autocomplete="off"
          spellcheck="false" />
        <datalist id="pr-participant-datalist"></datalist>
      </div>

      <!-- Scenario + depth filters — hidden until sessions are loaded -->
      <div id="pr-filter-row" style="display:none;">
        <div class="pr-form-group" style="margin-bottom:var(--space-md)">
          <label class="pr-form-label" for="pr-scenario">Scenario</label>
          <select id="pr-scenario" class="pr-select">
            <option value="">Select a scenario</option>
          </select>
        </div>
        <div class="pr-form-group">
          <label class="pr-form-label" for="pr-depth">Last N Sessions</label>
          <input
            id="pr-depth"
            class="pr-input"
            type="number"
            min="1"
            step="1"
            placeholder="e.g. 10  (blank = all)" />
        </div>
      </div>

    </div>

    <div id="pr-action-status" class="pr-action-status" role="status" aria-live="polite"></div>

    <!-- Session list — hidden until sessions are loaded -->
    <div id="pr-table-wrap" style="display:none; flex:1; flex-direction:column; overflow:hidden;">
      <div class="pr-table-header">
        <span class="pr-table-title">Sessions</span>
        <span class="pr-table-count" id="pr-table-count"></span>
      </div>
      <div class="pr-history-actions">
        <button type="button" class="pr-delete-participant" id="pr-delete-participant">Delete participant and sessions</button>
      </div>
      <div class="pr-table-scroll">
        <div id="pr-table-body" class="pr-session-list"></div>
        <div id="pr-table-empty" class="pr-table-empty" style="display:none"></div>
      </div>
    </div>

    <!-- Prompt shown when no participant is selected -->
    <div id="pr-prompt" class="pr-table-empty" style="padding:var(--space-3xl) var(--space-lg);text-align:center;color:var(--text-dim);font-size:var(--font-size-sm)">
      Enter a participant name above to load session history.
    </div>

  </div>

  <!-- RIGHT: Charts -->
  <div class="pr-chart-panel">
    <div class="pr-chart-header">
      <span class="pr-chart-header-title">Metric Trends</span>
      <div class="pr-chart-breadcrumb" id="pr-chart-breadcrumb"></div>
    </div>
    <div class="pr-chart-scroll" id="pr-chart-scroll">
      <div class="pr-chart-empty">
        <div class="pr-chart-empty__heading">No Data Selected</div>
        <div class="pr-chart-empty__sub">
          Enter a participant name to view metric trends over sessions.
        </div>
      </div>
    </div>
  </div>

</div>`;

    // Cache refs
    _participantInput = document.getElementById('pr-participant');
    _scenarioSelect   = document.getElementById('pr-scenario');
    _depthInput       = document.getElementById('pr-depth');
    _filterRow        = document.getElementById('pr-filter-row');
    _tableWrap        = document.getElementById('pr-table-wrap');
    _tableBody        = document.getElementById('pr-table-body');
    _tableCount       = document.getElementById('pr-table-count');
    _chartScroll      = document.getElementById('pr-chart-scroll');
    _chartBreadcrumb  = document.getElementById('pr-chart-breadcrumb');
    document.getElementById('pr-delete-participant').addEventListener('click', _deleteParticipant);

    // Participant input — debounce query + refresh autocomplete on keyup
    _participantInput.addEventListener('focus', _refreshParticipants);
    _participantInput.addEventListener('input', function () {
      clearTimeout(_debounceTimer);
      _setActionStatus('');
      _refreshParticipants();
      _debounceTimer = setTimeout(_runQuery, 480);
    });
    _participantInput.addEventListener('change', _runQuery);
    _participantInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { clearTimeout(_debounceTimer); _runQuery(); }
    });

    // Scenario + depth — filter client-side, no server round-trip
    _scenarioSelect.addEventListener('change', _applyFilters);
    _depthInput.addEventListener('input', function () {
      clearTimeout(_debounceTimer);
      _debounceTimer = setTimeout(_applyFilters, 350);
    });

    // Request initial participant list
    _refreshParticipants();
  }

  // ---- Query ------------------------------------------------------------------

  function _refreshParticipants() {
    LabReview.StreamRouter.send({ type: 'db.get_participants' });
  }

  function _runQuery() {
    const name = (_participantInput.value || '').trim();
    if (!name) {
      _allSessions = [];
      _renderTable([]);
      _renderCharts([]);
      if (_filterRow) _filterRow.style.display = 'none';
      if (_tableWrap) _tableWrap.style.display = 'none';
      const prompt = document.getElementById('pr-prompt');
      if (prompt) {
        prompt.textContent = 'Enter a participant name above to load session history.';
        prompt.style.display = '';
      }
      return;
    }
    // Always fetch ALL sessions; filter client-side
    LabReview.StreamRouter.send({ type: 'perf.query', participant_name: name });
  }

  // ---- Receive results --------------------------------------------------------

  function _onParticipantsList(msg) {
    const dl = document.getElementById('pr-participant-datalist');
    if (!dl) return;
    const names = msg.names || [];
    dl.innerHTML = names.map(function (n) { return '<option value="' + _esc(n) + '">'; }).join('');
  }

  function _onPerfResult(msg) {
    if (msg.participant_name !== (_participantInput.value || '').trim()) return;
    _allSessions = msg.sessions || [];
    _activeIdx   = -1;

    const hasResults = _allSessions.length > 0;
    const promptEl   = document.getElementById('pr-prompt');

    if (hasResults) {
      // Show filters + table, hide prompt
      if (_filterRow)  _filterRow.style.display  = '';
      if (_tableWrap)  { _tableWrap.style.display = 'flex'; }
      if (promptEl)    promptEl.style.display = 'none';

      // Populate scenario dropdown with unique names from returned sessions
      _populateScenarioFilter(_allSessions);
    } else {
      if (_filterRow)  _filterRow.style.display  = 'none';
      if (_tableWrap)  _tableWrap.style.display  = 'none';
      if (promptEl) {
        promptEl.style.display = '';
        promptEl.textContent   = msg.participant_name
          ? 'No sessions found for "' + msg.participant_name + '".'
          : 'Enter a participant name above to load session history.';
      }
    }

    if (hasResults) {
      _applyFilters();
    } else {
      _renderTable([]);
      _renderCharts([]);
    }
  }

  function _populateScenarioFilter(sessions) {
    if (!_scenarioSelect) return;
    const current = _scenarioSelect.value;

    // A scenario and drill type form one comparable performance series.
    const seen = new Set();
    const choices = [];
    sessions.forEach(function (s) {
      const key = _scenarioKey(s);
      if (!seen.has(key)) { seen.add(key); choices.push({ key, name: s.scenario_name || 'Unnamed scenario' }); }
    });
    _scenarioSelect.innerHTML = choices.map(function (choice) {
        return '<option value="' + _esc(choice.key) + '">' + _esc(choice.name) + '</option>';
      }).join('');
    _scenarioSelect.value = seen.has(current) ? current : choices[choices.length - 1].key;
  }

  function _scenarioKey(session) {
    return JSON.stringify([session.drill_key || '', session.scenario_name || '']);
  }

  // ---- Client-side filtering --------------------------------------------------

  function _applyFilters() {
    if (!_allSessions.length) {
      _renderTable([]);
      _renderCharts([]);
      return;
    }

    const scenario = _scenarioSelect ? _scenarioSelect.value : '';
    const depthRaw = _depthInput     ? _depthInput.value.trim() : '';
    const depth    = depthRaw ? Math.max(1, parseInt(depthRaw, 10) || 1) : 0;

    // Filter by scenario
    let filtered = _allSessions.filter(function (s) { return _scenarioKey(s) === scenario; });

    // Apply depth (most recent N — sessions come back oldest-first, so take tail)
    if (depth > 0 && filtered.length > depth) {
      filtered = filtered.slice(filtered.length - depth);
    }

    _renderTable(filtered);
    _renderCharts(filtered);
  }

  // ---- Session table ----------------------------------------------------------

  function _renderTable(sessions) {
    if (!_tableBody) return;

    const emptyEl = document.getElementById('pr-table-empty');
    _activeIdx = -1;

    if (sessions.length === 0) {
      _tableBody.innerHTML = '';
      if (_tableCount) _tableCount.textContent = '';
      if (emptyEl) { emptyEl.textContent = 'No sessions match the current filter.'; emptyEl.style.display = ''; }
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    if (_tableCount) _tableCount.textContent = sessions.length + ' session' + (sessions.length !== 1 ? 's' : '');

    _tableBody.innerHTML = sessions.map(function (s, idx) {
      const date = _sessionDateTime(s);
      const scenario = s.scenario_name || 'Unnamed scenario';
      const dur = _formatDur(_readMetric(s.metrics || {}, 'duration_s'));
      const metric = _headlineMetric(s);
      return '<div class="pr-session-row" data-idx="' + idx + '">' +
        '<button type="button" class="pr-session-select" data-select-idx="' + idx + '" aria-label="Select ' + _esc(scenario) + ' session from ' + _esc(date) + '">' +
        '<strong class="pr-session-row__name">' + _esc(scenario) + '</strong>' +
        '<span class="pr-session-row__date">' + _esc(date) + '</span>' +
        '<span class="pr-session-row__stats"><span>Duration <strong>' + _esc(dur) + '</strong></span>' +
        '<span>' + _esc(_headlineLabel(s)) + ' <strong>' + _esc(metric) + '</strong></span></span></button>' +
        '<button type="button" class="pr-delete-session" data-delete-idx="' + idx + '" aria-label="Delete ' + _esc(scenario) + ' session from ' + _esc(date) + '">Delete</button></div>';
    }).join('');

    _tableBody.querySelectorAll('.pr-session-select').forEach(function (button) {
      button.addEventListener('click', function () {
        _selectRow(parseInt(button.dataset.selectIdx, 10));
      });
    });
    _tableBody.querySelectorAll('.pr-delete-session').forEach(function (button) {
      button.addEventListener('click', function () {
        _deleteSession(sessions[Number(button.dataset.deleteIdx)]);
      });
    });
  }

  function _selectRow(idx) {
    _activeIdx = idx;
    if (_tableBody) {
      _tableBody.querySelectorAll('.pr-session-row').forEach(function (r, i) {
        r.classList.toggle('pr-row--active', i === idx);
      });
    }
    _updateChartMarkers(idx);
  }

  function _deleteSession(session) {
    if (_pendingDelete || !session || !session.session_uuid) return;
    const name = (_participantInput.value || '').trim();
    const description = (session.scenario_name || 'Unnamed scenario') + ' on ' + _sessionDateTime(session);
    if (!window.confirm('Delete this saved drill session for ' + name + '?\n\n' + description + '\n\nThis also removes its saved trials and record file.')) return;
    _pendingDelete = true;
    _setActionStatus('Deleting session…');
    if (!LabReview.StreamRouter.send({ type: 'perf.delete_session', participant_name: name, session_uuid: session.session_uuid })) {
      _pendingDelete = false;
      _setActionStatus('Connect to the backend before deleting a session.', true);
    }
  }

  function _deleteParticipant() {
    if (_pendingDelete || !_allSessions.length) return;
    const name = (_participantInput.value || '').trim();
    if (!window.confirm('Delete participant "' + name + '" and all ' + _allSessions.length + ' saved drill sessions?\n\nThis also removes their saved trials and record files.')) return;
    _pendingDelete = true;
    _setActionStatus('Deleting participant…');
    if (!LabReview.StreamRouter.send({ type: 'perf.delete_participant', participant_name: name })) {
      _pendingDelete = false;
      _setActionStatus('Connect to the backend before deleting a participant.', true);
    }
  }

  function _onDeleteResult(msg) {
    _pendingDelete = false;
    _refreshParticipants();
    if (msg.participant_name !== (_participantInput.value || '').trim()) return;
    if (!msg.success) {
      _setActionStatus(msg.detail || 'Could not delete the saved data.', true);
      return;
    }
    _setActionStatus(msg.deleted_count + ' session' + (msg.deleted_count === 1 ? '' : 's') + ' deleted.');
    _runQuery();
  }

  function _setActionStatus(message, error) {
    const element = document.getElementById('pr-action-status');
    if (element) {
      element.textContent = message;
      element.classList.toggle('pr-action-status--error', !!error);
    }
  }

  // ---- Sparkline charts -------------------------------------------------------

  function _renderCharts(sessions) {
    if (!_chartScroll) return;

    const participantName = (_participantInput ? _participantInput.value : '').trim();

    // Breadcrumb
    if (_chartBreadcrumb) {
      if (sessions.length > 0) {
        const scenarioLabel = sessions[0].scenario_name || 'Unnamed scenario';
        _chartBreadcrumb.innerHTML =
          '<span class="pr-breadcrumb-item">' + _esc(participantName) + '</span>' +
          '<span class="pr-breadcrumb-sep">/</span>' +
          '<span class="pr-breadcrumb-item">' + _esc(scenarioLabel) + '</span>' +
          '<span class="pr-breadcrumb-sep">/</span>' +
          '<span class="pr-breadcrumb-item">' + sessions.length + ' session' + (sessions.length !== 1 ? 's' : '') + '</span>';
      } else {
        _chartBreadcrumb.innerHTML = '';
      }
    }

    if (sessions.length === 0) {
      _chartScroll.innerHTML = '<div class="pr-chart-empty">' +
        '<div class="pr-chart-empty__heading">No Sessions</div>' +
        '<div class="pr-chart-empty__sub">No sessions match the current filters. Try changing the scenario or depth.</div>' +
        '</div>';
      return;
    }

    const behavDefs = METRIC_DEFS[sessions[0].drill_key] || [];

    const cards = [];
    behavDefs.forEach(function (def) {
      const values = sessions.map(function (s) {
        const m = s.metrics || {};
        return _readMetric(m, def.field);
      });
      if (values.some(function (v) { return v !== null; })) cards.push({ def, values, isBio: false });
    });
    BIOSIGNAL_DEFS.forEach(function (def) {
      const values = sessions.map(function (s) {
        const b = s.biosignals || {};
        return b[def.field] == null ? null : Number(b[def.field]);
      });
      if (values.some(function (v) { return v !== null; })) cards.push({ def, values, isBio: true });
    });

    if (cards.length === 0) {
      _chartScroll.innerHTML = '<div class="pr-chart-empty">' +
        '<div class="pr-chart-empty__heading">No Metric Data</div>' +
        '<div class="pr-chart-empty__sub">Sessions exist but contain no computed metrics yet.</div>' +
        '</div>';
      return;
    }

    _chartScroll.innerHTML = '';
    cards.forEach(function (card, cardIdx) {
      const el = _buildMetricCard(card.def, card.values, sessions, card.isBio, cardIdx);
      _chartScroll.appendChild(el);
    });
  }

  // ---- Metric card + SVG sparkline --------------------------------------------

  function _buildMetricCard(def, values, sessions, isBio, cardIdx) {
    const card = document.createElement('div');
    card.className = 'pr-metric-card';
    card.dataset.cardIdx = String(cardIdx);

    const valid = values.filter(function (v) { return v !== null && isFinite(v); });
    const minV  = valid.length ? Math.min.apply(null, valid) : 0;
    const maxV  = valid.length ? Math.max.apply(null, valid) : 1;
    const firstV = valid.length ? values.find(function (v) { return v !== null; }) : null;
    const lastV  = valid.length ? values.slice().reverse().find(function (v) { return v !== null; }) : null;

    // Delta badge
    let deltaBadge = '';
    if (firstV !== null && lastV !== null && firstV !== lastV) {
      const pct = ((lastV - firstV) / (Math.abs(firstV) || 1)) * 100;
      const isImp = def.lowerBetter ? pct < 0 : pct > 0;
      const cls   = Math.abs(pct) < 0.5 ? 'flat' : (isImp ? 'up' : 'down');
      deltaBadge  = '<span class="pr-delta pr-delta--' + cls + '">' +
        (pct > 0 ? '+' : '') + pct.toFixed(1) + '%</span>';
    }

    const rangeText = valid.length
      ? _fmtVal(minV, '') + (def.unit ? '\u00a0' + def.unit : '') +
        ' \u2013 ' +
        _fmtVal(maxV, '') + (def.unit ? '\u00a0' + def.unit : '')
      : '\u2014';

    const accentColor = isBio ? 'var(--accent-teal)' : 'var(--accent-blue)';

    card.innerHTML =
      '<div class="pr-metric-card__header" style="border-left:3px solid ' + accentColor + '">' +
        '<span class="pr-metric-card__label">' + _esc(def.label) +
          (def.unit ? ' <span style="font-weight:400;color:var(--text-muted);font-size:var(--font-size-xs)">(' + _esc(def.unit) + ')</span>' : '') +
        '</span>' +
        '<div class="pr-metric-card__meta">' +
          '<span class="pr-metric-card__range">' + rangeText + '</span>' +
          deltaBadge +
        '</div>' +
      '</div>' +
      '<div class="pr-sparkline-wrap">' +
        '<svg class="pr-sparkline" id="pr-sparkline-' + cardIdx + '" aria-label="' + _esc(def.label) + ' trend"></svg>' +
      '</div>';

    requestAnimationFrame(function () {
      const svg = card.querySelector('.pr-sparkline');
      if (svg) _drawSparkline(svg, values, sessions, def, cardIdx);
    });
    return card;
  }

  function _drawSparkline(svg, values, sessions, def, cardIdx) {
    const W    = svg.getBoundingClientRect().width || 300;
    const H    = 100;
    const PADL = 38;
    const PADR = 8;
    const PADT = 10;
    const PADB = 22;
    const plotW = W - PADL - PADR;
    const plotH = H - PADT - PADB;

    const valid = values.filter(function (v) { return v !== null && isFinite(v); });
    if (!valid.length) return;

    const minV  = Math.min.apply(null, valid);
    const maxV  = Math.max.apply(null, valid);
    const range = maxV - minV || 1;
    const n     = values.length;

    function yPx(v) { return PADT + plotH - ((v - minV) / range) * plotH; }
    function xPx(i) { return PADL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW); }

    const NS = 'http://www.w3.org/2000/svg';
    function el(tag, attrs, parent) {
      const e = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(function (kv) { e.setAttribute(kv[0], kv[1]); });
      if (parent) parent.appendChild(e);
      return e;
    }

    svg.innerHTML = '';
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

    // Grid lines
    [0, 0.5, 1].forEach(function (t) {
      const y = PADT + plotH - t * plotH;
      el('line', { x1: PADL, y1: y, x2: W - PADR, y2: y, stroke: 'var(--chart-grid-color)', 'stroke-width': '1' }, svg);
      el('text', { x: PADL - 3, y: y + 4, 'text-anchor': 'end', 'font-size': '9', fill: 'var(--text-dim)', 'font-family': 'var(--font-mono)' }, svg)
        .textContent = _fmtVal(minV + t * range, '');
    });

    // Y-axis baseline
    el('line', { x1: PADL, y1: PADT, x2: PADL, y2: PADT + plotH, stroke: 'var(--chart-axis-color)', 'stroke-width': '1' }, svg);

    // Polyline segments (handles null gaps)
    const lineColor = 'var(--accent-blue)';
    const segments = [];
    let cur = [];
    values.forEach(function (v, i) {
      if (v !== null && isFinite(v)) {
        cur.push(xPx(i).toFixed(1) + ',' + yPx(v).toFixed(1));
      } else {
        if (cur.length) { segments.push(cur); cur = []; }
      }
    });
    if (cur.length) segments.push(cur);

    segments.forEach(function (pts) {
      if (pts.length >= 2) {
        el('polyline', { points: pts.join(' '), fill: 'none', stroke: lineColor,
          'stroke-width': '1.8', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      }
    });

    // Dots + X labels (first + last)
    values.forEach(function (v, i) {
      if (v === null || !isFinite(v)) return;
      const cx = xPx(i).toFixed(1);
      const cy = yPx(v).toFixed(1);
      const dot = el('circle', { cx, cy, r: '3.5', fill: lineColor,
        stroke: 'var(--bg-surface)', 'stroke-width': '1.5',
        'data-idx': i, class: 'pr-dot', style: 'cursor:pointer' }, svg);
      el('title', {}, dot).textContent = _sessionDateTime(sessions[i]) + ': ' + _fmtVal(v, def.unit);
      if (i === 0 || i === n - 1) {
        el('text', { x: cx, y: H - 4, 'text-anchor': i === 0 ? 'start' : 'end',
          'font-size': '9', fill: 'var(--text-dim)', 'font-family': 'var(--font-family)' }, svg)
          .textContent = _shortDate(sessions[i]);
      }
    });

    // Marker line (vertical)
    const marker = el('line', { x1: 0, y1: PADT, x2: 0, y2: PADT + plotH,
      stroke: 'var(--accent-orange)', 'stroke-width': '1.5', 'stroke-dasharray': '3 2',
      opacity: '0', 'data-marker': '1' }, svg);

    // Click on dots
    svg.addEventListener('click', function (e) {
      const dot = e.target.closest('.pr-dot');
      if (dot) _selectRow(parseInt(dot.getAttribute('data-idx'), 10));
    });

    svg._markerLine = marker;
    svg._xPxFn = xPx;
    svg._n = n;
  }

  function _updateChartMarkers(idx) {
    if (!_chartScroll) return;
    _chartScroll.querySelectorAll('.pr-sparkline').forEach(function (svg) {
      const line = svg._markerLine;
      if (!line || !svg._xPxFn) return;
      if (idx < 0 || idx >= svg._n) { line.setAttribute('opacity', '0'); return; }
      const x = svg._xPxFn(idx).toFixed(1);
      line.setAttribute('x1', x); line.setAttribute('x2', x); line.setAttribute('opacity', '0.85');
    });
  }

  // ---- Helpers ----------------------------------------------------------------

  function _headlineMetric(session) {
    const m = session.metrics || {};
    const d = session.drill_key || '';
    const field = d === 'pvt' ? 'hits_pct' : d === 'gng' ? 'overall_accuracy_pct' :
      d === 'behavioral_discrimination' ? 'discrimination.discrimination_accuracy_pct' : '';
    const value = field ? _readMetric(m, field) : null;
    return value == null ? '--' : value.toFixed(1) + '%';
  }

  function _headlineLabel(session) {
    return session.drill_key === 'pvt' ? 'Hits' : 'Accuracy';
  }

  function _readMetric(metrics, field) {
    if (field === 'duration_s' && metrics.duration_basis !== 'first_to_last_trial') return null;
    let value = field.split('.').reduce(function (item, key) {
      return item == null ? undefined : item[key];
    }, metrics);
    if (value == null) {
      const p = metrics.pvt || {};
      const d = metrics.discrimination || {};
      const legacy = {
        hits_pct: metrics.total_trials ? metrics.hits / metrics.total_trials * 100 : null,
        'pvt.hits_pct': p.total_trials ? p.hits / p.total_trials * 100 : null,
        'discrimination.threat_hit_rate_pct': d.hit_rate_pct,
        'discrimination.commission_error_rate_pct': d.false_alarm_rate_pct,
        hit_rate_pct: metrics.hit_rate,
        commission_errors: metrics.false_alarms,
        correct_rejection_rate_pct: metrics.correct_rej_rate,
        total_trials: metrics.go_trials == null ? null : metrics.go_trials + (metrics.no_go_trials || 0),
        omissions: metrics.misses,
        overall_accuracy_pct: metrics.go_trials + metrics.no_go_trials > 0
          ? (metrics.hits + metrics.correct_rejections) / (metrics.go_trials + metrics.no_go_trials) * 100 : null,
        rt_mean_ms: metrics.rt_mean_s == null ? null : metrics.rt_mean_s * 1000,
        rt_median_ms: metrics.rt_median_s == null ? null : metrics.rt_median_s * 1000,
        rt_stdev_ms: metrics.rt_stdev_s == null ? null : metrics.rt_stdev_s * 1000,
      };
      value = legacy[field];
    }
    return value == null || !isFinite(Number(value)) ? null : Number(value);
  }

  function _formatDate(s) {
    if (!s) return '--';
    return s.slice(0, 10);
  }

  function _shortDate(session) {
    const stamp = session.saved_at ? new Date(session.saved_at) : null;
    if (stamp && !isNaN(stamp.getTime())) {
      return stamp.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
    return _formatDate(session.date || '');
  }

  function _sessionDateTime(session) {
    const stamp = session.saved_at ? new Date(session.saved_at) : null;
    if (stamp && !isNaN(stamp.getTime())) {
      return stamp.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
    return _formatDate(session.date || '');
  }

  function _formatDur(s) {
    if (s == null) return '--';
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
  }

  function _fmtVal(v, unit) {
    if (v === null || v === undefined) return '--';
    const n = Number(v);
    if (!isFinite(n)) return '--';
    const s = Math.abs(n) >= 10 ? n.toFixed(1) : n.toFixed(2);
    return unit ? s + '\u00a0' + unit : s;
  }

  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  return { init };

})();
