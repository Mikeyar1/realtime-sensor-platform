/**
 * features/aar/task-types/gng.js
 *
 * After Action Review display module for Go/No-Go (GNG) task type.
 * Also covers Behavioral Discrimination — same metrics, same layout.
 *
 * DISPLAY ONLY — no metric computation here.
 * Receives pre-computed metric JSON from the Backend and renders cards.
 *
 * Lifecycle:
 *   mount(containerEl)              — render waiting state
 *   onScenarioStarted(payload)      — show live structure, activate chart
 *   onMetricsReady(metrics)         — populate all cards with final numbers
 *   onSensorUpdate(payload)         — update physiological cards
 *   onScenarioStopped(payload)      — show export row
 *   reset()                         — return to waiting state
 *   unmount()                       — detach
 *
 * Metric cards shown:
 *   Session Context: Go Trials, No-Go Trials, Go Proportion
 *   Threat Engagement: Hit Rate, Miss Rate
 *   Civilian Discrimination: False Alarm Rate, Correct Rejection Rate
 *   Timing: Median RT, Mean RT, RT SD
 *   Chart: Speed-accuracy overlay (rolling hit rate + rolling RT)
 *   Phys: HR, HRV (when available)
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.GNG = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _container = null;
  let _scenarioName = '';
  let _taskType = 'GNG';
  let _timeline = [];   // per-trial data for alert tracking

  // ── Mount ──────────────────────────────────────────────────────────────────

  function mount(containerEl) {
    _container = containerEl;
    _reset();
    _renderWaiting();
  }

  function unmount() {
    _container = null;
    _chartCanvas = null;
    _chartCtx = null;
  }

  // ── Reset ──────────────────────────────────────────────────────────────────

  function _reset() {
    _scenarioName = '';
    _timeline = [];
  }

  function reset() {
    _reset();
    _renderWaiting();
  }

  // ── Waiting state ──────────────────────────────────────────────────────────

  function _renderWaiting() {
    if (!_container) return;
    _container.innerHTML = `
      <div class="aar-panel" id="aar-gng-panel">
        <div class="aar-waiting" id="aar-gng-waiting">
          <div class="aar-waiting-label">Go / No-Go Assessment</div>
          <div class="aar-waiting-sub">Waiting for VirTra scenario to start…</div>
        </div>
      </div>
    `;
  }

  // ── Scenario started — build the live metric shell ─────────────────────────

  function onScenarioStarted(payload) {
    if (!_container) return;
    _scenarioName = payload.scenario_name || '';
    _taskType = payload.task_type || 'GNG';

    _container.innerHTML = `
      <div class="aar-panel" id="aar-gng-panel">

        <!-- Section: Session Context -->
        <div class="aar-section" id="aar-gng-context">
          <div class="aar-section-label">Session Context</div>
          <div class="aar-grid">
            ${_card('ctx-scenario',  _scenarioName || '\u2014', 'Scenario',         '', 'Name of the VirTra scenario that was played.')}
            ${_card('ctx-simulator', payload.simulator        || '\u2014', 'Simulator',        '', 'Simulator software used (e.g. V-AIMS).')}
            ${_card('ctx-simsystem', payload.simulator_system || '\u2014', 'Simulator System', '', 'Simulator hardware/system (e.g. V-300).')}
            ${_card('ctx-weapon',    '\u2014',                              'Weapon Type',      '', 'Weapon type used by the trainee (from first shot).')}
            ${_card('ctx-go',   '\u2014', 'Go Trials (Threats)',      '', 'Number of threat targets presented during this session.')}
            ${_card('ctx-nogo', '\u2014', 'No-Go Trials (Civilians)', '', 'Number of civilian targets presented. Different proportions affect expected results.')}
            ${_card('ctx-prop', '\u2014', 'Go Proportion',            '', 'Ratio of threat vs. civilian targets. High proportions bias toward shooting.')}
          </div>
        </div>

        <!-- Section: Threat Engagement -->
        <div class="aar-section">
          <div class="aar-section-label">Threat Engagement (Go Targets)</div>
          <div class="aar-grid">
            ${_card('hit-rate', '—', 'Hit Rate', '0 hits / 0 go trials',
              'Threats correctly engaged: Hits ÷ Total Threat Targets × 100. A miss is a failure to respond to an enemy.')}
            ${_card('miss-rate', '—', 'Miss Rate', '0 misses',
              'Threats not engaged (Errors of Omission): Misses ÷ Total Threat Targets × 100.')}
          </div>
        </div>

        <!-- Section: Civilian Discrimination -->
        <div class="aar-section">
          <div class="aar-section-label">Civilian Discrimination (No-Go Targets)</div>
          <div class="aar-grid">
            ${_card('fa-rate', '—', 'False Alarm Rate', '0 false alarms',
              'Civilians shot (Errors of Commission): False Alarms ÷ Total Civilian Targets × 100. Shooting a civilian is the highest-priority error.')}
            ${_card('cr-rate', '—', 'Correct Rejection Rate', '0 correct withholds',
              'Civilians correctly not shot: Correct Rejections ÷ Total Civilian Targets × 100.')}
          </div>
        </div>

        <!-- False alarm alert row (hidden until FAs > 0) -->
        <div class="aar-alert-row" id="aar-gng-fa-alert" style="display:none">
          <span class="aar-alert-icon">⚠</span>
          <span><strong class="aar-alert-count" id="aar-gng-fa-count">0</strong> civilian(s) shot — Errors of Commission</span>
        </div>

        <!-- Section: Timing -->
        <div class="aar-section">
          <div class="aar-section-label">Response Timing (Go Hits Only)</div>
          <div class="aar-grid aar-grid--3col">
            ${_card('rt-median', '—', 'Median RT', '',
              'Middle response time across all Hit trials. Less sensitive to outliers than the mean.')}
            ${_card('rt-mean',   '—', 'Mean RT',   '',
              'Average response time across all Hit trials.')}
            ${_card('rt-stdev',  '—', 'RT Variability (SD)', '',
              'Standard deviation of response times. Higher = less consistent responding.')}
          </div>
        </div>

        <!-- Export row (shown after scenario stops) -->
        <div class="aar-export-row" id="aar-gng-export" style="display:none">
          <button class="aar-export-btn" id="aar-gng-export-btn">⬇ Download Results</button>
          <span class="aar-export-hint" id="aar-gng-export-hint">Session complete</span>
        </div>

      </div>
    `;

    // Wire the chart canvas (removed)
  }

  // ── Metrics ready (final computed results from Analysis) ───────────────────

  function onMetricsReady(metrics) {
    if (!metrics || !_container) return;

    // Session context
    _setCard('ctx-go',   metrics.go_trials    ?? '—', `${metrics.go_trials ?? 0} presented`);
    _setCard('ctx-nogo', metrics.no_go_trials ?? '—', `${metrics.no_go_trials ?? 0} presented`);
    _setCard('ctx-prop', metrics.go_proportion != null ? metrics.go_proportion + '%' : '—', '');

    // Threat engagement
    const hitRate  = metrics.hit_rate  != null ? metrics.hit_rate  + '%' : '—';
    const missRate = metrics.miss_rate != null ? metrics.miss_rate + '%' : '—';
    _setCard('hit-rate',  hitRate,  `${metrics.hits ?? 0} hits / ${metrics.go_trials ?? 0} go trials`, _rateClass(metrics.hit_rate,  60, 85));
    _setCard('miss-rate', missRate, `${metrics.misses ?? 0} misses`, _invertClass(metrics.miss_rate, 40, 15));

    // Civilian discrimination
    const faRate = metrics.false_alarm_rate != null ? metrics.false_alarm_rate + '%' : '—';
    const crRate = metrics.correct_rej_rate != null ? metrics.correct_rej_rate + '%' : '—';
    _setCard('fa-rate', faRate, `${metrics.false_alarms ?? 0} false alarm(s)`, metrics.false_alarms > 0 ? 'aar-card--alert' : 'aar-card--good');
    _setCard('cr-rate', crRate, `${metrics.correct_rejections ?? 0} correct rejections`, _rateClass(metrics.correct_rej_rate, 70, 90));

    // False alarm alert row
    if ((metrics.false_alarms ?? 0) > 0) {
      const row = document.getElementById('aar-gng-fa-alert');
      const cnt = document.getElementById('aar-gng-fa-count');
      if (row) row.style.display = '';
      if (cnt) cnt.textContent = metrics.false_alarms;
    }

    // Timing
    const rtMedian = metrics.rt_median_s != null ? metrics.rt_median_s.toFixed(2) + 's' : '—';
    const rtMean   = metrics.rt_mean_s   != null ? metrics.rt_mean_s.toFixed(2)   + 's' : '—';
    const rtStdev  = metrics.rt_stdev_s  != null ? metrics.rt_stdev_s.toFixed(3)  + 's' : '—';
    _setCard('rt-median', rtMedian, `n = ${metrics.rt_n ?? 0} hits`);
    _setCard('rt-mean',   rtMean,   '');
    _setCard('rt-stdev',  rtStdev,  '');

    // Chart
    if (metrics.timeline && metrics.timeline.length > 0) {
      _timeline = metrics.timeline;
      _drawChart();
    }
  }

  // ── Biosignal updates ──────────────────────────────────────────────────────

  // onSensorUpdate — MetricCardRenderer handles physio cards
  function onSensorUpdate(_payload) { /* no-op */ }

  // ── Real-time GNG trial update ─────────────────────────────────────────────

  /**
   * Called by the AAR Dashboard after every completed GNG trial.
   *
   * msg shape: {
   *   type:            "session.trial_update",
   *   task_type:       "GNG",
   *   drill_key:       string,
   *   trial:           { trial_number, target_name, target_type, result, rt_ms },
   *   running_metrics: { total_go, total_no_go, hits, misses, false_alarms,
   *                      correct_rejections, hit_rate_pct, miss_rate_pct,
   *                      fa_rate_pct, cr_rate_pct, mean_rt_ms, median_rt_ms, sd_rt_ms }
   * }
   */
  function onGngUpdate(msg) {
    if (!_container || !msg) return;
    const { trial, running_metrics } = msg;
    if (!running_metrics) return;

    const nGo   = running_metrics.total_go ?? 0;
    const nNogo = running_metrics.total_no_go ?? 0;
    const total = nGo + nNogo;
    const goProp= total > 0 ? Math.round((nGo / total) * 100) + '%' : '—';

    // Session Context
    _setCard('ctx-go',   nGo,   `${running_metrics.hits ?? 0} hits`);
    _setCard('ctx-nogo', nNogo, `${running_metrics.correct_rejections ?? 0} correct rejections`);
    _setCard('ctx-prop', goProp, total > 0 ? `${nGo} / ${total} trials` : '');

    // Threat Engagement (Go Targets)
    const hitRate  = running_metrics.hit_rate_pct  != null ? running_metrics.hit_rate_pct  + '%' : '—';
    const missRate = running_metrics.miss_rate_pct != null ? running_metrics.miss_rate_pct + '%' : '—';
    _setCard('hit-rate',  hitRate,  `${running_metrics.hits ?? 0} hits / ${nGo} go trials`, _rateClass(running_metrics.hit_rate_pct, 60, 85));
    _setCard('miss-rate', missRate, `${running_metrics.misses ?? 0} misses`, _invertClass(running_metrics.miss_rate_pct, 40, 15));

    // Civilian Discrimination (No-Go Targets)
    const faRate = running_metrics.fa_rate_pct != null ? running_metrics.fa_rate_pct + '%' : '—';
    const crRate = running_metrics.cr_rate_pct != null ? running_metrics.cr_rate_pct + '%' : '—';
    const fas    = running_metrics.false_alarms ?? 0;
    _setCard('fa-rate', faRate, `${fas} civilian(s) shot`, fas > 0 ? 'aar-card--alert' : 'aar-card--good');
    _setCard('cr-rate', crRate, `${running_metrics.correct_rejections ?? 0} held fire`, _rateClass(running_metrics.cr_rate_pct, 70, 90));

    // False alarm alert row
    const faRow = document.getElementById('aar-gng-fa-alert');
    const faCnt = document.getElementById('aar-gng-fa-count');
    if (faRow) faRow.style.display = fas > 0 ? '' : 'none';
    if (faCnt) faCnt.textContent = fas;

    // Timing (Hit trials only)
    const rtMedian = running_metrics.median_rt_ms != null ? (running_metrics.median_rt_ms / 1000).toFixed(2) + 's' : '—';
    const rtMean   = running_metrics.mean_rt_ms   != null ? (running_metrics.mean_rt_ms   / 1000).toFixed(2) + 's' : '—';
    const rtStdev  = running_metrics.sd_rt_ms     != null ? (running_metrics.sd_rt_ms     / 1000).toFixed(3) + 's' : '—';
    _setCard('rt-median', rtMedian, nGo > 0 ? `n = ${running_metrics.hits ?? 0} hits` : '');
    _setCard('rt-mean',   rtMean,   '');
    _setCard('rt-stdev',  rtStdev,  '');

    // Chart removed — MetricCardRenderer handles all card display
  }


  // ── Scenario stopped ───────────────────────────────────────────────────────

  function onScenarioStopped(payload) {
    const exportRow = document.getElementById('aar-gng-export');
    if (exportRow) exportRow.style.display = '';
  }

  // ── Weapon info ────────────────────────────────────────────────────────────

  /**
   * Called when Backend broadcasts session.weapon_info (first Shot Fired).
   * Updates the Weapon Type context card.
   */
  function onWeaponInfo(payload) {
    if (!payload || !payload.weapon_type) return;
    _setCard('ctx-weapon', payload.weapon_type, '');
  }

  // Chart rendering removed — MetricCardRenderer handles display

  function _card(id, value, label, sub, tip) {
    const isText = id.startsWith('ctx-');
    return `
      <div class="aar-card ${isText ? 'aar-card--text' : ''}" id="aar-card-${id}">
        <button class="aar-info-btn" data-tip="${_esc(tip)}">ℹ</button>
        <div class="aar-card-value ${isText ? 'aar-card-value--text' : ''}" id="aar-val-${id}" title="${_esc(String(value || ''))}">${value}</div>
        <div class="aar-card-label">${label}</div>
        <div class="aar-card-sub"  id="aar-sub-${id}">${sub}</div>
      </div>`;
  }

  function _physCard(id, value, name, unit) {
    return `
      <div class="aar-phys-card">
        <div class="aar-phys-value" id="aar-phys-${id}">${value}</div>
        <div class="aar-phys-name">${name}</div>
        <div class="aar-phys-unit">${unit}</div>
      </div>`;
  }

  function _setCard(id, value, sub, stateClass) {
    const card = document.getElementById(`aar-card-${id}`);
    const val  = document.getElementById(`aar-val-${id}`);
    const subEl= document.getElementById(`aar-sub-${id}`);
    if (val)  {
      val.textContent  = value;
      val.title = value;
    }
    if (subEl && sub !== undefined) { subEl.textContent = sub; }
    if (card) {
      card.classList.remove('aar-card--good', 'aar-card--alert', 'aar-card--warn', 'aar-card--neutral');
      if (stateClass) card.classList.add(stateClass);
      card.classList.remove('aar-card--flash');
      void card.offsetWidth;
      card.classList.add('aar-card--flash');
    }
  }

  function _esc(str) {
    return (str || '').replace(/"/g, '&quot;');
  }

  // Rate coloring helpers
  function _rateClass(rate, goodThreshold, greatThreshold) {
    if (rate == null) return '';
    if (rate >= greatThreshold) return 'aar-card--good';
    if (rate >= goodThreshold)  return '';
    return 'aar-card--warn';
  }

  function _invertClass(rate, alertThreshold, warnThreshold) {
    if (rate == null) return '';
    if (rate > alertThreshold) return 'aar-card--alert';
    if (rate > warnThreshold)  return 'aar-card--warn';
    return 'aar-card--good';
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return {
    mount,
    unmount,
    reset,
    onScenarioStarted,
    onGngUpdate,
    onMetricsReady,
    onSensorUpdate,
    onScenarioStopped,
    onWeaponInfo,
  };

})();
