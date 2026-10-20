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
  let _filteredSessions = [];
  let _selectedSessionUuid = '';
  let _chartScrollPosition = 0;
  let _debounceTimer  = null;
  let _pendingDelete = false;
  let _pendingRoundDeleteId = '';
  let _chartRedrawRAF = null;

  // DOM refs
  let _participantInput, _scenarioSelect, _presetSelect, _depthInput;
  let _tableWrap, _tableBody, _tableCount;
  let _chartScroll, _chartBreadcrumb;
  let _filterRow;  // the scenario + depth row (hidden until sessions loaded)
  let _selectedRoundKey = '';
  let _historyQueryName = '';
  let _historyRequestId = '';
  let _legacyHistory = null;
  let _assessmentHistory = null;
  let _selectedAssessmentRoundId = '';

  // ---- Metric definitions per drill_key ---------------------------------------

  const METRIC_DEFS = {
    pvt_fitts: [
      { field: 'completed_rounds', label: 'Completed Rounds', unit: '', lowerBetter: false },
      { field: 'duration_s', label: 'Session Duration', unit: 's', lowerBetter: false },
    ],
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
      { field: 'pvt.duration_s', label: 'PVT Duration', unit: 's', lowerBetter: false },
      { field: 'pvt.hits_pct', label: 'PVT Hits', unit: '%', lowerBetter: false },
      { field: 'pvt.omissions', label: 'PVT Omissions', unit: '', lowerBetter: true },
      { field: 'pvt.response_rate_pct', label: 'PVT Response Rate', unit: '%', lowerBetter: false },
      { field: 'pvt.omission_rate_pct', label: 'PVT Omission Rate', unit: '%', lowerBetter: true },
      { field: 'pvt.rt_median_ms', label: 'PVT Median RT', unit: 'ms', lowerBetter: true },
      { field: 'pvt.rt_mean_ms', label: 'PVT Mean RT', unit: 'ms', lowerBetter: true },
      { field: 'pvt.rt_stdev_ms', label: 'PVT RT Std Dev', unit: 'ms', lowerBetter: true },
      { field: 'pvt.rt_min_ms', label: 'PVT Minimum RT', unit: 'ms', lowerBetter: true },
      { field: 'pvt.rt_max_ms', label: 'PVT Maximum RT', unit: 'ms', lowerBetter: true },
    ],
  };
  function _metricDefsFor(record) {
    const profile = LabReview.AAR.Standardized.assessmentProfile(record);
    return profile ? LabReview.AAR.Standardized.specs[profile.metricSet].groups
      .flatMap(([, cards]) => cards.filter(card => card.scope !== 'run'))
      .map(card => ({ field: card.path, label: card.label, unit: card.unit,
        lowerBetter: ['rt_', 'omission', 'miss'].some(prefix => card.path.startsWith(prefix)) }))
      : METRIC_DEFS[record.drill_key] || [];
  }

  const BIOSIGNAL_DEFS = LabReview.AAR.Standardized.physiology;

  // ---- Init -------------------------------------------------------------------

  function init() {
    if (LabReview.TopBarManager) {
      LabReview.TopBarManager.register('perf-review', _renderTopBar, function () {});
    }
    LabReview.EventBus.on('db-participants-list', _onParticipantsList);
    LabReview.EventBus.on('perf-result', _onPerfResult);
    LabReview.EventBus.on('assessment-command-result', _onAssessmentHistoryResult);
    LabReview.EventBus.on('perf-session-detail-result', _onSessionDetail);
    LabReview.EventBus.on('perf-delete-result', _onDeleteResult);
    LabReview.EventBus.on('page-changed', function (page) {
      if (page === 'perf-review') {
        _renderPageContent();
        _scheduleChartRedraw();
      }
    });
    window.addEventListener('resize', _scheduleChartRedraw);
  }

  function _renderTopBar(container) {
    container.innerHTML = '<div class="topbar" id="perf-review-topbar"><span class="pr-topbar-title">Longitudinal Data</span></div>';
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
        <div class="pr-form-group" style="margin-bottom:var(--space-md)">
          <label class="pr-form-label" for="pr-preset">Preset</label>
          <select id="pr-preset" class="pr-select"><option value="">All presets</option></select>
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
        <button type="button" class="pr-delete-participant" id="pr-delete-participant">Delete participant and all results</button>
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
    _presetSelect     = document.getElementById('pr-preset');
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
    _scenarioSelect.addEventListener('change', () => { _populatePresetFilter(); _applyFilters(); });
    _presetSelect.addEventListener('change', _applyFilters);
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
    _historyQueryName = name;
    _historyRequestId = `history-${Date.now()}-${Math.random()}`;
    _legacyHistory = null;
    _assessmentHistory = null;
    // The old session store and new round store remain independent during migration.
    LabReview.StreamRouter.send({ type: 'perf.query', participant_name: name,
      request_id: _historyRequestId });
    LabReview.StreamRouter.send({ type: 'history.rounds.query', participant_name: name,
      request_id: _historyRequestId, limit: 1000 });
  }

  // ---- Receive results --------------------------------------------------------

  function _onParticipantsList(msg) {
    const dl = document.getElementById('pr-participant-datalist');
    if (!dl) return;
    const names = msg.names || [];
    dl.innerHTML = names.map(function (n) { return '<option value="' + _esc(n) + '">'; }).join('');
  }

  function _onPerfResult(msg) {
    if (msg.participant_name !== (_participantInput.value || '').trim() ||
        msg.request_id !== _historyRequestId) return;
    _legacyHistory = msg.sessions || [];
    _commitHistoryQuery();
  }

  function _onAssessmentHistoryResult(msg) {
    if (msg.command === 'history.round.delete') {
      if (msg.round_id !== _pendingRoundDeleteId) return;
      _pendingDelete = false;
      _pendingRoundDeleteId = '';
      if (!msg.success || msg.result !== true) {
        _setActionStatus(msg.error || 'Could not delete the saved round.', true);
        return;
      }
      _selectedAssessmentRoundId = '';
      _setActionStatus('Round deleted.');
      _refreshParticipants();
      _runQuery();
      return;
    }
    if (msg.command === 'history.round.get') {
      if (msg.round_id !== _selectedAssessmentRoundId) return;
      if (msg.success && msg.result?.id === _selectedAssessmentRoundId) {
        _renderAssessmentRoundDetail(msg.result);
      } else if (_chartScroll) {
        _chartScroll.innerHTML = '<div class="pr-chart-empty">Round detail is unavailable.</div>';
      }
      return;
    }
    if (msg.command !== 'history.rounds.query' || msg.request_id !== _historyRequestId) return;
    _assessmentHistory = msg.success ? (msg.result || []).map(row => ({
      result_scope: 'round', round_id: row.id, session_uuid: row.run_id,
      round_number: row.round_number, round_attempt: row.attempt,
      scenario_name: row.scenario_name, drill_key: row.drill_key,
      assessment_profile: row.assessment_profile,
      source: 'custom',
      started_at: row.started_at, stopped_at: row.ended_at,
      saved_at: row.performed_at,
      preset_label: row.preset_label, preset_mixed: row.preset_mixed,
      metrics: row.metrics || {}, biosignals: row.physiology || {},
      capture_status: row.status,
    })) : [];
    if (!msg.success) _setActionStatus(msg.error || 'Round history could not be loaded.', true);
    _commitHistoryQuery();
  }

  function _commitHistoryQuery() {
    if (_legacyHistory === null || _assessmentHistory === null ||
        _historyQueryName !== (_participantInput?.value || '').trim()) return;
    _allSessions = [..._legacyHistory, ..._assessmentHistory].sort((a, b) =>
      (Date.parse(a.saved_at || a.date || '') || 0) -
      (Date.parse(b.saved_at || b.date || '') || 0));
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
      _populatePresetFilter();
    } else {
      if (_filterRow)  _filterRow.style.display  = 'none';
      if (_tableWrap)  _tableWrap.style.display  = 'none';
      if (promptEl) {
        promptEl.style.display = '';
        promptEl.textContent   = _historyQueryName
          ? 'No sessions found for "' + _historyQueryName + '".'
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

    // Keep scoring sources separate when the same scenario name was reused.
    const seen = new Set();
    const choices = [];
    sessions.forEach(function (s) {
      const key = _scenarioKey(s);
      if (!seen.has(key)) {
        seen.add(key);
        choices.push({ key, name: (s.scenario_name || 'Unnamed scenario') +
          ` · ${(s.metrics_source || s.source) === 'custom' ? 'Custom' : 'Native'}` });
      }
    });
    _scenarioSelect.innerHTML = choices.map(function (choice) {
        return '<option value="' + _esc(choice.key) + '">' +
          _esc(choice.name) + '</option>';
      }).join('');
    _scenarioSelect.value = seen.has(current) ? current : choices[choices.length - 1].key;
  }

  function _scenarioKey(session) {
    return [session.scenario_name || '', session.drill_key || '',
      session.metrics_source || session.source || 'native'].join('|');
  }

  function _populatePresetFilter() {
    if (!_presetSelect) return;
    const previous = _presetSelect.value;
    const rows = _allSessions.filter(row => _scenarioKey(row) === _scenarioSelect.value);
    const labels = [...new Set(rows.filter(row => row.result_scope === 'round')
      .map(row => row.preset_mixed ? '__mixed__' : row.preset_label || '__unreported__'))].sort();
    _presetSelect.innerHTML = '<option value="">All presets</option>' +
      labels.map(label => `<option value="${_esc(label)}">${_esc(label === '__mixed__'
        ? 'Mixed presets' : label === '__unreported__' ? 'Not reported' : label)}</option>`).join('');
    _presetSelect.value = labels.includes(previous) ? previous : '';
    _presetSelect.disabled = labels.length === 0;
  }

  function _drillDisplayName(drillKey) {
    return { pvt: 'PVT', pvt_fitts: 'PVT / Fitts', gng: 'Go/No-Go', behavioral_discrimination: 'Behavioral Discrimination' }[drillKey] || drillKey || 'Unknown drill';
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
    if (_presetSelect?.value) {
      filtered = filtered.filter(s => s.result_scope === 'round' &&
        (s.preset_mixed ? '__mixed__' : s.preset_label || '__unreported__') === _presetSelect.value);
    }

    // Apply depth (most recent N — sessions come back oldest-first, so take tail)
    if (depth > 0 && filtered.length > depth) {
      filtered = filtered.slice(filtered.length - depth);
    }

    _filteredSessions = filtered;
    _selectedSessionUuid = '';
    _selectedRoundKey = '';
    _selectedAssessmentRoundId = '';
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
    if (_tableCount) _tableCount.textContent = sessions.length + ' result' + (sessions.length !== 1 ? 's' : '');

    _tableBody.innerHTML = sessions.map(function (s, idx) {
      const date = _sessionDateTime(s);
      const scenario = (s.scenario_name || 'Unnamed scenario') +
        (s.result_scope === 'round' ? ` · Round ${s.round_number}${s.round_attempt > 1 ? ` attempt ${s.round_attempt}` : ''}` : '');
      const dur = _formatDur(_readMetric(s.metrics || {},
        s.round_id ? 'round_duration_s' : 'duration_s'));
      const metric = _headlineMetric(s);
      return '<div class="pr-session-row" data-idx="' + idx + '">' +
        '<button type="button" class="pr-session-select" data-select-idx="' + idx + '" aria-label="Select ' + _esc(scenario) + ' session from ' + _esc(date) + '">' +
        '<strong class="pr-session-row__name">' + _esc(scenario) + '</strong>' +
        '<span class="pr-session-row__date">' + _esc(date) +
        (s.result_scope === 'round' ? ' · Preset: ' + _esc(s.preset_mixed ? 'Mixed' : s.preset_label || 'Not reported') : '') + '</span>' +
        '<span class="pr-session-row__stats"><span>Duration <strong>' + _esc(dur) + '</strong></span>' +
        '<span>' + _esc(_headlineLabel(s)) + ' <strong>' + _esc(metric) + '</strong></span></span></button>' +
        '<button type="button" class="pr-delete-session" data-delete-idx="' + idx + '" aria-label="Delete ' + _esc(scenario) + ' result from ' + _esc(date) + '">Delete</button></div>';
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
    const session = _filteredSessions[idx];
    if (!session) return;
    _selectedSessionUuid = session.session_uuid;
    _selectedAssessmentRoundId = session.round_id || '';
    _selectedRoundKey = session.result_scope === 'round'
      ? `${session.round_number}:${session.round_attempt || 1}` : '';
    _chartScrollPosition = _chartScroll.scrollTop;
    _chartScroll.innerHTML = '<div class="pr-chart-empty">Loading session detail…</div>';
    if (session.round_id) {
      LabReview.StreamRouter.send({ type: 'history.round.get', round_id: session.round_id,
        participant_name: (_participantInput.value || '').trim() });
      return;
    }
    LabReview.StreamRouter.send({
      type: 'perf.session_detail',
      session_uuid: session.session_uuid,
      participant_name: (_participantInput.value || '').trim(),
      ...(session.result_scope === 'round' ? {
        round_number: session.round_number, round_attempt: session.round_attempt || 1,
      } : {}),
    });
  }

  function _onSessionDetail(message) {
    if (_selectedAssessmentRoundId) return;
    if (message.session_uuid !== _selectedSessionUuid || !_chartScroll) return;
    if (`${message.round_number ?? ''}:${message.round_attempt ?? ''}` !==
        (_selectedRoundKey || ':')) return;
    const session = message.session;
    if (!session) {
      _chartScroll.innerHTML = '<div class="pr-chart-empty">Session detail is unavailable.</div>';
      return;
    }
    const definitions = _metricDefsFor(session);
    const metricCards = definitions.length
      ? definitions.map(def => _detailCard(def.label, _readMetric(session.metrics || {}, def.field), def.unit)).join('')
      : Object.entries(session.metrics || {}).filter(([, value]) => value != null && typeof value !== 'object')
          .map(([key, value]) => _detailCard(key.replace(/_/g, ' '), value, '')).join('');
    const bioCards = BIOSIGNAL_DEFS.map(def =>
      _detailCard(def.label, _readMetric(session.biosignals || {}, def.field), def.unit)).join('');
    const nativeContext = session.native_context || {};
    const contextCards = [
      ['Simulator', nativeContext.simulator],
      ['Simulator System', nativeContext.simulator_system],
      ['Weapon Type', nativeContext.weapon_type],
    ].filter(([, value]) => value).map(([label, value]) => _detailCard(label, value, '')).join('');
    const roundCards = (session.rounds || []).map(round => {
      const settings = round.settings || {};
      const profile = LabReview.AAR.Standardized.assessmentProfile(session);
      const difficulty = profile
        ? profile.settings.filter(([, key]) => settings[key] != null)
          .map(([label, key, unit]) => `${label}: ${settings[key]}${unit}`).join(' · ')
        : '';
      const components = Object.entries(round.components || {}).map(([name, detail]) => {
        const cards = profile
          ? METRIC_DEFS.pvt.filter(def => _readMetric(detail.metrics || {}, def.field) != null)
            .map(def => _detailCard(def.label, _readMetric(detail.metrics, def.field), def.unit))
          : Object.entries(detail.metrics || {})
            .filter(([, value]) => value != null && typeof value !== 'object')
            .map(([key, value]) => _detailCard(key.replace(/_/g, ' '), value, ''));
        return `<div class="pr-detail-component"><h4>${_esc(name.toUpperCase())}</h4><div class="pr-detail-grid">${cards.join('') || '<p>No scored metrics recorded.</p>'}</div></div>`;
      }).join('');
      const trials = (session.trials || []).filter(t => t.round_number === round.round_number &&
        t.round_attempt === (round.attempt || 1));
      return `<details class="pr-detail-round"><summary>Round ${_esc(round.round_number)}${round.attempt > 1 ? ` · Attempt ${round.attempt}` : ''} · ${_esc(round.ended_reason || 'incomplete')} · ${trials.length} trials</summary><p>Participant: ${_esc(round.participant_name || 'Unassigned')} · Preset: ${_esc(round.preset_mixed ? 'Mixed presets' : round.preset_label || 'Not reported')}</p>${difficulty ? `<p>Round settings: ${_esc(difficulty)}</p>` : ''}${components}</details>`;
    }).join('');
    _chartScroll.innerHTML = `<div class="pr-detail"><button type="button" class="pr-detail-back" id="pr-detail-back">← All session trends</button>
      <h2>${_esc(session.scenario_name || 'Session')}</h2><p>${_esc(_sessionDateTime(session))} · ${_esc(session.capture_status || 'complete')} capture</p>
      ${contextCards ? `<h3>VirTra context</h3><div class="pr-detail-grid">${contextCards}</div>` : ''}
      <h3>Behavioral results</h3><div class="pr-detail-grid">${metricCards || '<p>No scored metrics available.</p>'}</div>
      <h3>Physiological data</h3><div class="pr-detail-grid">${bioCards}</div>
      ${roundCards ? `<h3>Rounds</h3>${roundCards}` : ''}</div>`;
    _chartScroll.scrollTop = 0;
    _chartScroll.querySelector('#pr-detail-back')?.addEventListener('click', () => {
      _selectedSessionUuid = '';
      _selectedRoundKey = '';
      _activeIdx = -1;
      _renderCharts(_filteredSessions);
      _chartScroll.scrollTop = _chartScrollPosition;
      _tableBody?.querySelectorAll('.pr-row--active').forEach(row => row.classList.remove('pr-row--active'));
    });
  }

  function _detailCard(label, value, unit) {
    const shown = _detailValue(value, unit);
    return `<div class="pr-detail-card"><span>${_esc(label)}</span><strong>${_esc(shown)}</strong></div>`;
  }

  function _detailValue(value, unit = '') {
    if (value == null || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return '—';
      return LabReview.AAR.Standardized.display(value, unit);
    }
    if (typeof value === 'string') return value + (unit ? ` ${unit}` : '');
    if (Array.isArray(value)) return value.map(item => _detailValue(item)).join(', ');
    return '—';
  }

  function _roundSettingCards(round) {
    const settings = round.settings || {};
    const profile = LabReview.AAR.Standardized.assessmentProfile(round);
    if (profile) {
      return profile.settings.filter(([, key]) => settings[key] != null)
        .map(([label, key, unit]) => _detailCard(label, settings[key], unit)).join('');
    }
    return Object.entries(settings)
      .filter(([, value]) => value != null && typeof value !== 'object')
      .map(([key, value]) => _detailCard(key.replace(/^active_/, '').replace(/_/g, ' '), value, '')).join('');
  }

  function _roundMetricSections(round) {
    const profile = LabReview.AAR.Standardized.assessmentProfile(round);
    if (profile) {
      return LabReview.AAR.Standardized.specs[profile.metricSet].groups.map(([name, cards]) => {
        const visible = cards.filter(def => def.scope !== 'run');
        return `<section class="pr-detail-section"><h3>${_esc(name)}</h3><div class="pr-detail-grid">${visible.map(def =>
          _detailCard(def.label, _readMetric(round.metrics || {}, def.path), def.unit)).join('')}</div></section>`;
      }).join('');
    }
    const cards = _metricDefsFor(round).map(def =>
      _detailCard(def.label, _readMetric(round.metrics || {}, def.field), def.unit)).join('') || '';
    return `<section class="pr-detail-section"><h3>Behavioral results</h3><div class="pr-detail-grid">${cards || '<p>No scored metrics available.</p>'}</div></section>`;
  }

  function _renderAssessmentRoundDetail(round) {
    if (!_chartScroll || round.id !== _selectedAssessmentRoundId) return;
    const bioCards = BIOSIGNAL_DEFS.map(def =>
      _detailCard(def.label, _readBiosignal(round.physiology || {}, def.field), def.unit)).join('');
    const settings = _roundSettingCards(round);
    const preset = round.preset_mixed ? 'Mixed presets' : round.preset_label || 'Not reported';
    const quality = String(round.quality?.physiology_status || 'unavailable').replace(/_/g, ' ');
    _chartScroll.innerHTML = `<div class="pr-detail">
      <button type="button" class="pr-detail-back" id="pr-detail-back">← All round trends</button>
      <h2>${_esc(round.scenario_name || 'Round')} · Round ${_esc(round.round_number)}</h2>
      <p class="pr-detail-meta">${_esc(_sessionDateTime(round))} · Participant: ${_esc(round.participant_name || 'Unassigned')} · Preset: ${_esc(preset)}</p>
      ${settings ? `<section class="pr-detail-section"><h3>Preset settings</h3><div class="pr-detail-grid">${settings}</div></section>` : ''}
      ${_roundMetricSections(round)}
      <section class="pr-detail-section"><div class="pr-detail-section-heading"><h3>Physiological data</h3><span>${_esc(quality)}</span></div><div class="pr-detail-grid">${bioCards}</div></section></div>`;
    _chartScroll.scrollTop = 0;
    _chartScroll.querySelector('#pr-detail-back')?.addEventListener('click', () => {
      _selectedAssessmentRoundId = '';
      _selectedSessionUuid = '';
      _activeIdx = -1;
      _renderCharts(_filteredSessions);
      _chartScroll.scrollTop = _chartScrollPosition;
    });
  }

  function _deleteSession(session) {
    if (_pendingDelete || !session ||
        (!session.round_id && !session.legacy_round_id && !session.session_uuid)) return;
    const name = (_participantInput.value || '').trim();
    const description = (session.scenario_name || 'Unnamed scenario') + ' on ' + _sessionDateTime(session);
    const kind = session.round_id || session.legacy_round_id ? 'round' : 'session';
    if (!window.confirm('Delete this saved ' + kind + ' for ' + name + '?\n\n' + description +
      (session.round_id ? '\n\nThis also removes its saved trial and physiology data.' :
        session.legacy_round_id ? '\n\nThis also removes its saved trial data.' :
        '\n\nThis also removes its saved trials and record file.'))) return;
    _pendingDelete = true;
    _setActionStatus('Deleting ' + kind + '…');
    if (session.round_id) _pendingRoundDeleteId = session.round_id;
    const command = session.round_id
      ? { type: 'history.round.delete', participant_name: name, round_id: session.round_id }
      : session.legacy_round_id
        ? { type: 'perf.delete_round', participant_name: name, round_id: session.legacy_round_id }
        : { type: 'perf.delete_session', participant_name: name, session_uuid: session.session_uuid };
    if (!LabReview.StreamRouter.send(command)) {
      _pendingDelete = false;
      _pendingRoundDeleteId = '';
      _setActionStatus('Connect to the backend before deleting a result.', true);
    }
  }

  function _deleteParticipant() {
    if (_pendingDelete || !_allSessions.length) return;
    const name = (_participantInput.value || '').trim();
    if (!window.confirm('Delete participant "' + name + '" and all ' + _allSessions.length + ' saved results?\n\nThis also removes their saved trials, physiology data, and record files.')) return;
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
    _setActionStatus(msg.deleted_count + ' result' + (msg.deleted_count === 1 ? '' : 's') + ' deleted.');
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
          '<span class="pr-breadcrumb-item">' + sessions.length + ' result' + (sessions.length !== 1 ? 's' : '') + '</span>';
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

    const fittsRounds = sessions.filter(row => row.result_scope === 'round');
    const presets = new Set(fittsRounds.map(row => row.preset_mixed ? '__mixed__' : row.preset_label || '__unknown__'));
    if (fittsRounds.length && !_presetSelect?.value &&
        (presets.size > 1 || presets.has('__unknown__') || presets.has('__mixed__'))) {
      _chartScroll.innerHTML = '<div class="pr-chart-empty"><div class="pr-chart-empty__heading">Choose a preset for trends</div>' +
        '<div class="pr-chart-empty__sub">Rounds from different or unreported presets are listed on the left. Select one preset to compare like with like.</div></div>';
      return;
    }

    const drillKeys = [...new Set(sessions.map(s => s.drill_key))];
    const behavDefs = drillKeys.flatMap(drillKey => _metricDefsFor(sessions.find(s => s.drill_key === drillKey)).map(def => ({
      ...def, drillKey,
      label: drillKeys.length > 1 ? `${_drillDisplayName(drillKey)} · ${def.label}` : def.label,
    })));

    const behavioralCards = [];
    behavDefs.forEach(function (def) {
      const values = sessions.map(function (s) {
        if (s.drill_key !== def.drillKey) return null;
        const m = s.metrics || {};
        if (s.round_id) {
          const value = m[def.field];
          return value == null || !Number.isFinite(Number(value)) ? null : Number(value);
        }
        return _readMetric(m, def.field);
      });
      if (values.some(function (v) { return v !== null && isFinite(v); })) behavioralCards.push({ def, values, isBio: false });
    });

    const physiologicalCards = [];
    BIOSIGNAL_DEFS.forEach(function (def) {
      const values = sessions.map(function (s) {
        return _readBiosignal(s.biosignals || {}, def.field);
      });
      if (values.some(function (v) { return v !== null && isFinite(v); })) physiologicalCards.push({ def, values, isBio: true });
    });

    if (behavioralCards.length === 0 && physiologicalCards.length === 0) {
      _chartScroll.innerHTML = fittsRounds.length
        ? '<div class="pr-chart-empty"><div class="pr-chart-empty__heading">No comparable rounds</div>' +
          '<div class="pr-chart-empty__sub">These rounds have unresolved shot attribution. Their metrics remain available in round detail.</div></div>'
        : '<div class="pr-chart-empty"><div class="pr-chart-empty__heading">No Metric Data</div>' +
          '<div class="pr-chart-empty__sub">Sessions exist but contain no computed metrics yet.</div></div>';
      return;
    }

    _chartScroll.innerHTML = '';
    let cardIdx = 0;
    if (behavioralCards.length) {
      cardIdx = _appendChartSection(
        'Performance Metrics',
        fittsRounds.length ? 'Rounds share the emitted preset label. Review round settings before interpreting change.' :
          'Scenario-specific results across the selected sessions.',
        behavioralCards,
        sessions,
        cardIdx
      );
    }
    if (physiologicalCards.length) {
      _appendChartSection(
        'Physiological Data',
        fittsRounds.length ? 'Physiological summaries captured within each round.' :
          'Full-session physiological summaries captured during each scenario.',
        physiologicalCards,
        sessions,
        cardIdx
      );
    } else {
      const emptyPhysio = document.createElement('section');
      emptyPhysio.className = 'pr-chart-section pr-chart-section--physio';
      emptyPhysio.innerHTML =
        '<div class="pr-chart-section__heading">' +
          '<h2>Physiological Data</h2>' +
          '<p>No physiological averages were recorded for these sessions.</p>' +
        '</div>';
      _chartScroll.appendChild(emptyPhysio);
    }
    _scheduleChartRedraw();
  }

  function _appendChartSection(title, subtitle, cards, sessions, startIdx) {
    const section = document.createElement('section');
    section.className = 'pr-chart-section' + (cards[0]?.isBio ? ' pr-chart-section--physio' : '');
    section.innerHTML =
      '<div class="pr-chart-section__heading">' +
        '<h2>' + _esc(title) + '</h2>' +
        '<p>' + _esc(subtitle) + '</p>' +
      '</div>';
    cards.forEach(function (card, offset) {
      section.appendChild(_buildMetricCard(card.def, card.values, sessions, card.isBio, startIdx + offset));
    });
    _chartScroll.appendChild(section);
    return startIdx + cards.length;
  }

  function _scheduleChartRedraw() {
    if (_chartRedrawRAF !== null) return;
    _chartRedrawRAF = requestAnimationFrame(function () {
      _chartRedrawRAF = null;
      if (!_chartScroll) return;
      _chartScroll.querySelectorAll('.pr-metric-card').forEach(function (card) {
        const svg = card.querySelector('.pr-sparkline');
        if (svg) _drawSparkline(svg, card._chartValues, card._chartSessions, card._chartDef, card._chartIsBio);
      });
      _updateChartMarkers(_activeIdx);
    });
  }

  // ---- Metric card + SVG sparkline --------------------------------------------

  function _buildMetricCard(def, values, sessions, isBio, cardIdx) {
    const card = document.createElement('div');
    card.className = 'pr-metric-card';
    card.dataset.cardIdx = String(cardIdx);
    card.tabIndex = 0;
    card._chartValues = values;
    card._chartSessions = sessions;
    card._chartDef = def;
    card._chartIsBio = isBio;

    const valid = values.filter(function (v) { return v !== null && isFinite(v); });
    const minV  = valid.length ? Math.min.apply(null, valid) : 0;
    const maxV  = valid.length ? Math.max.apply(null, valid) : 1;
    const firstV = valid.length ? values.find(function (v) { return v !== null; }) : null;
    const lastV  = valid.length ? values.slice().reverse().find(function (v) { return v !== null; }) : null;

    // Delta badge
    let deltaBadge = '';
    if (!isBio && firstV !== null && lastV !== null && firstV !== lastV) {
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
        '<svg class="pr-sparkline" id="pr-sparkline-' + cardIdx + '" role="img" aria-label="' + _esc(def.label) + ' by session, oldest to newest"></svg>' +
      '</div>' +
      '<div class="pr-metric-card__selection" aria-live="polite">Select a session to see its value</div>' +
      '<div class="pr-metric-card__tooltip" aria-hidden="true"></div>';

    return card;
  }

  function _drawSparkline(svg, values, sessions, def, isBio) {
    const W    = Math.max(240, svg.getBoundingClientRect().width || 300);
    const H    = 156;
    const PADL = 52;
    const PADR = 18;
    const PADT = 16;
    const PADB = 38;
    const plotW = W - PADL - PADR;
    const plotH = H - PADT - PADB;

    const valid = values.filter(function (v) { return v !== null && isFinite(v); });
    if (!valid.length) return;

    const minV  = Math.min.apply(null, valid);
    const maxV  = Math.max.apply(null, valid);
    const padding = (maxV - minV || Math.max(Math.abs(maxV), 1)) * 0.12;
    const axisMin = Math.max(0, minV - padding);
    const axisMax = maxV + padding;
    const range = axisMax - axisMin;
    const n     = values.length;

    function yPx(v) { return PADT + plotH - ((v - axisMin) / range) * plotH; }
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

    // Grid and labeled value axis
    [0, 0.5, 1].forEach(function (t) {
      const y = PADT + plotH - t * plotH;
      el('line', { x1: PADL, y1: y, x2: W - PADR, y2: y, stroke: 'var(--chart-grid-color)', 'stroke-width': '1' }, svg);
      el('text', { x: PADL - 7, y: y + 4, 'text-anchor': 'end', 'font-size': '10', fill: 'var(--text-secondary)' }, svg)
        .textContent = _fmtAxisTick(axisMin + t * range);
    });

    // Both axes remain inside the SVG, including their labels.
    el('line', { x1: PADL, y1: PADT, x2: PADL, y2: PADT + plotH, stroke: 'var(--chart-axis-color)', 'stroke-width': '1.2' }, svg);
    el('line', { x1: PADL, y1: PADT + plotH, x2: W - PADR, y2: PADT + plotH, stroke: 'var(--chart-axis-color)', 'stroke-width': '1.2' }, svg);

    const tickIndices = Array.from(new Set([0, Math.floor((n - 1) / 2), n - 1]));
    tickIndices.forEach(function (i) {
      const x = xPx(i);
      el('line', { x1: x, y1: PADT + plotH, x2: x, y2: PADT + plotH + 5, stroke: 'var(--chart-axis-color)', 'stroke-width': '1' }, svg);
      el('text', { x, y: H - 18, 'text-anchor': 'middle', 'font-size': '10', fill: 'var(--text-secondary)' }, svg)
        .textContent = String(i + 1);
    });
    el('text', { x: PADL + plotW / 2, y: H - 3, 'text-anchor': 'middle', 'font-size': '10', fill: 'var(--text-muted)' }, svg)
      .textContent = 'Session (oldest to newest)';

    // Polyline segments (handles null gaps)
    const lineColor = isBio ? 'var(--accent-teal)' : 'var(--accent-blue)';
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

    // Session points. Their native titles also expose exact values.
    values.forEach(function (v, i) {
      if (v === null || !isFinite(v)) return;
      const cx = xPx(i).toFixed(1);
      const cy = yPx(v).toFixed(1);
      const dot = el('circle', { cx, cy, r: '3.5', fill: lineColor,
        stroke: 'var(--bg-surface)', 'stroke-width': '1.5',
        'data-idx': i, class: 'pr-dot', style: 'cursor:pointer' }, svg);
      el('title', {}, dot).textContent = _sessionDateTime(sessions[i]) + ': ' + _fmtVal(v, def.unit);
    });

    // Marker line (vertical)
    const marker = el('line', { x1: 0, y1: PADT, x2: 0, y2: PADT + plotH,
      stroke: 'var(--accent-orange)', 'stroke-width': '1.5', 'stroke-dasharray': '3 2',
      opacity: '0', 'data-marker': '1' }, svg);

    // Click on dots
    svg.onclick = function (e) {
      const dot = e.target.closest('.pr-dot');
      if (dot) _selectRow(parseInt(dot.getAttribute('data-idx'), 10));
    };

    svg._markerLine = marker;
    svg._xPxFn = xPx;
    svg._n = n;
  }

  function _updateChartMarkers(idx) {
    if (!_chartScroll) return;
    _chartScroll.querySelectorAll('.pr-metric-card').forEach(function (card) {
      const svg = card.querySelector('.pr-sparkline');
      if (!svg) return;
      const line = svg._markerLine;
      const selected = idx >= 0 && idx < svg._n;
      if (line && svg._xPxFn) {
        line.setAttribute('opacity', selected ? '0.85' : '0');
        if (selected) {
          const x = svg._xPxFn(idx).toFixed(1);
          line.setAttribute('x1', x); line.setAttribute('x2', x);
        }
      }
      svg.querySelectorAll('.pr-dot').forEach(function (dot) {
        const active = selected && Number(dot.getAttribute('data-idx')) === idx;
        dot.setAttribute('r', active ? '5' : '3.5');
        dot.classList.toggle('pr-dot--selected', active);
      });
      const selection = card.querySelector('.pr-metric-card__selection');
      const tooltip = card.querySelector('.pr-metric-card__tooltip');
      if (!selection || !tooltip) return;
      if (!selected) {
        selection.textContent = 'Select a session to see its value';
        tooltip.textContent = '';
        card.classList.remove('pr-metric-card--selected');
        return;
      }
      const value = card._chartValues[idx];
      const number = value === null || !isFinite(value) ? 'No value recorded' : _fmtVal(value, card._chartDef.unit);
      const date = _sessionDateTime(card._chartSessions[idx]);
      selection.textContent = 'Session ' + (idx + 1) + ': ' + number;
      tooltip.textContent = card._chartDef.label + ' · ' + date + ' · ' + number;
      card.classList.add('pr-metric-card--selected');
    });
  }

  function _fmtAxisTick(value) {
    const abs = Math.abs(value);
    return abs >= 100 ? value.toFixed(0) : abs >= 10 ? value.toFixed(1) : value.toFixed(2);
  }

  // ---- Helpers ----------------------------------------------------------------

  function _headlineMetric(session) {
    const m = session.metrics || {};
    const d = session.drill_key || '';
    const profile = LabReview.AAR.Standardized.assessmentProfile(session);
    if (profile) {
      const value = _readMetric(m, profile.headlineField);
      return value == null ? '--' : LabReview.AAR.Standardized.display(value, '%');
    }
    if (d === 'pvt_fitts') return String(m.completed_rounds ?? '--');
    const field = d === 'pvt' ? 'hits_pct' : d === 'gng' ? 'overall_accuracy_pct' :
      d === 'behavioral_discrimination' ? 'discrimination.discrimination_accuracy_pct' : '';
    const value = field ? _readMetric(m, field) : null;
    return value == null ? '--' : value.toFixed(1) + '%';
  }

  function _headlineLabel(session) {
    const profile = LabReview.AAR.Standardized.assessmentProfile(session);
    if (profile) return profile.headlineLabel;
    return session.drill_key === 'pvt' ? 'Hits' : session.drill_key === 'pvt_fitts' ? 'Rounds' :
      'Accuracy';
  }

  function _readMetric(metrics, field) {
    if (field === 'duration_s' && metrics.duration_basis &&
        !['first_to_last_trial', 'scenario_elapsed'].includes(metrics.duration_basis)) return null;
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

  function _readBiosignal(biosignals, field) {
    let value = biosignals[field];
    // Sessions saved before pupil_mean_mm was introduced still have both eye means.
    if (field === 'pupil_mean_mm' && value == null) {
      const left = biosignals.pupil_l_mean_mm;
      const right = biosignals.pupil_r_mean_mm;
      if (left != null && right != null && isFinite(Number(left)) && isFinite(Number(right))) {
        value = (Number(left) + Number(right)) / 2;
      }
    }
    return value == null || !isFinite(Number(value)) ? null : Number(value);
  }

  function _formatDate(s) {
    if (!s) return '--';
    return s.slice(0, 10);
  }

  function _sessionDateTime(session) {
    const recordedAt = session.saved_at || session.performed_at;
    const stamp = recordedAt ? new Date(recordedAt) : null;
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
