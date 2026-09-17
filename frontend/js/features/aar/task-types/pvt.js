/**
 * features/aar/task-types/pvt.js
 *
 * After Action Review display module for Psychomotor Vigilance Task (PVT)
 * and Simple Reaction Time (SRT) task types.
 *
 * DISPLAY ONLY — no metric computation here.
 * Receives pre-computed metric JSON from the Backend and renders cards.
 *
 * Metric cards shown:
 *   Session Context: Total Trials, Session Duration
 *   Performance:    Median RT, Mean RT, RT Variability (SD), Omissions
 *   Chart:          RT per trial (temporal trend — are they getting slower?)
 *   Phys:           HR, HRV (when available)
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.PVT = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _container = null;
  let _scenarioName = '';

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
  }

  function reset() {
    _reset();
    _renderWaiting();
  }

  // ── Waiting state ──────────────────────────────────────────────────────────

  function _renderWaiting() {
    if (!_container) return;
    _container.innerHTML = `
      <div class="aar-panel" id="aar-pvt-panel">
        <div class="aar-waiting" id="aar-pvt-waiting">
          <div class="aar-waiting-label">Psychomotor Vigilance Task</div>
          <div class="aar-waiting-sub">Waiting for VirTra scenario to start…</div>
        </div>
      </div>
    `;
  }

  // ── Scenario started ───────────────────────────────────────────────────────

  function onScenarioStarted(payload) {
    if (!_container) return;
    _scenarioName = payload.scenario_name || '';

    _container.innerHTML = `
      <div class="aar-panel" id="aar-pvt-panel">

        <!-- Section: Session Context -->
        <div class="aar-section">
          <div class="aar-section-label">Session Context</div>
          <div class="aar-grid">
            ${_card('ctx-scenario',  _scenarioName || '\u2014', 'Scenario',          '', 'Name of the VirTra scenario that was played.')}
            ${_card('ctx-simulator', payload.simulator        || '\u2014', 'Simulator',         '', 'Simulator software used (e.g. V-AIMS).')}
            ${_card('ctx-simsystem', payload.simulator_system || '\u2014', 'Simulator System',  '', 'Simulator hardware/system (e.g. V-300).')}
            ${_card('ctx-weapon',    '\u2014',                              'Weapon Type',       '', 'Firearm used by the trainee (captured from the first shot).')}
            ${_card('total',         '\u2014', 'Total Trials',     '', 'Total stimulus presentations (all outcomes).')}
            ${_card('duration',      '\u2014', 'Session Duration', '', 'Time from scenario start to scenario end (seconds).')}
          </div>
        </div>

        <!-- Section: Psychomotor Speed & Sustained Attention -->
        <div class="aar-section">
          <div class="aar-section-label">Psychomotor Speed &amp; Sustained Attention</div>

          <div class="aar-grid">
            ${_card('rt-median', '\u2014', 'Median RT', 'ms', 'Median response time across all responses (hits + misses). Robust to outliers — the primary speed metric.')}
            ${_card('rt-mean',   '\u2014', 'Mean RT',   'ms', 'Mean response time across all responses (hits + misses).')}
            ${_card('rt-stdev',  '\u2014', 'RT Variability (SD)', 'ms', 'Standard deviation of response time — consistency of responding.')}
            ${_card('rt-n',      '\u2014', 'RT Count',  '',   'Number of RT samples (hits + misses).')}
          </div>

          <div class="aar-grid">
            ${_card('rt-fast10', '\u2014', 'Fastest 10% RT', 'ms', 'Mean RT of the fastest 10% of responses — best-case alertness baseline.')}
            ${_card('rt-slow10', '\u2014', 'Slowest 10% RT', 'ms', 'Mean RT of the slowest 10% of responses — the classic PVT lapse indicator.')}
            ${_card('rt-range',  '\u2014', 'RT Range',       '',   'Fastest and slowest individual response times.')}
            ${_card('rt-cv',     '\u2014', 'Coefficient of Variation', '%', 'Normalized variability: (SD / Mean) × 100. Enables cross-session comparison.')}
            ${_card('lapse',     '\u2014', 'Lapse Count',    '',   'Responses slower than the lapse threshold (session median RT × multiplier) — sustained-attention failures.')}
          </div>

          <div class="aar-grid">
            ${_card('resp-rate', '\u2014', 'Response Rate',       '%', '(Hits + Misses) / Total Trials × 100 — did the trainee engage at all?')}
            ${_card('omissions', '\u2014', 'Omission Count',      '',  'Trials with no shot fired — failure to respond.')}
            ${_card('omit-rate', '\u2014', 'Omission Rate',       '%', 'Omissions / Total Trials × 100.')}
          </div>

          <div class="aar-grid aar-grid--3col">
            ${_card('rt-first',  '\u2014', 'First-Half Median RT',  'ms', 'Median RT across the first half of trials — baseline performance.')}
            ${_card('rt-second', '\u2014', 'Second-Half Median RT', 'ms', 'Median RT across the second half — an increase indicates vigilance decrement.')}
            ${_card('rt-slope',  '\u2014', 'RT Slope', '', 'Linear slope of RT over trial number. Positive = performance degrading over time.')}
          </div>
        </div>

        <!-- Section: Accuracy (Marksmanship) -->
        <div class="aar-section">
          <div class="aar-section-label">Accuracy (Marksmanship)</div>
          <div class="aar-grid aar-grid--3col">
            ${_card('accuracy', '\u2014', 'Accuracy', '%', 'Hits / (Hits + Misses) × 100 — when the trainee engaged, did they connect? Isolated from vigilance.')}
            ${_card('hits',     '\u2014', 'Hit Count',  '', 'Rounds that struck the target.')}
            ${_card('misses',   '\u2014', 'Miss Count', '', 'Rounds fired that did not strike the target.')}
            ${_card('mre',      '\u2014', 'Mean Radial Error', 'cm', 'Average distance between point of impact and target center across hits — shot precision, independent of speed.')}
          </div>
        </div>

      </div>
    `;
  }

  // ── Metrics ready ──────────────────────────────────────────────────────────

  function onMetricsReady(metrics) {
    if (!metrics || !_container) return;
    _render(metrics);
  }

  // ── Sensor update ──────────────────────────────────────────────────────────

  // onSensorUpdate — data is forwarded to MetricCardRenderer by live-intel.js
  function onSensorUpdate(_payload) { /* no-op — MetricCardRenderer handles physio cards */ }

  // ── Real-time PVT trial update ──────────────────────────────────────────────────

  /**
   * Called by the AAR Dashboard after every completed PVT trial.
   *
   * msg shape: {
   *   type:            "session.trial_update",
   *   task_type:       "PVT",
   *   drill_key:       "pvt",
   *   trial:           { trial_number, target_id, rt_ms, result, score },
   *   running_metrics: { total_trials, hits, misses, omissions,
   *                      hit_pct, miss_pct, omit_pct,
   *                      median_rt_ms, mean_rt_ms, sd_rt_ms },
   *   all_rts:         [ Number|null, ... ]   — full RT list for the trend chart
   * }
   */
  function onPvtUpdate(msg) {
    if (!_container || !msg || !msg.running_metrics) return;
    _render(msg.running_metrics);
  }


  // ── Scenario stopped ───────────────────────────────────────────────────────

  function onScenarioStopped(payload) {
    const exportRow = document.getElementById('aar-pvt-export');
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

  // ── Shared renderer (live running_metrics + final metrics use identical keys) ──

  function _render(m) {
    if (!m || !_container) return;

    // Session context
    _setCard('total', m.total_trials ?? '—', '');
    if (m.duration_s != null) _setCard('duration', m.duration_s + ' s', '');

    // Response Time — core (pool = hits + misses)
    _setCard('rt-median', m.rt_median_ms != null ? m.rt_median_ms + ' ms' : '—',
      `n = ${m.rt_n ?? 0} responses`);
    _setCard('rt-mean',   m.rt_mean_ms   != null ? m.rt_mean_ms   + ' ms' : '—', '');
    _setCard('rt-stdev',  m.rt_stdev_ms  != null ? m.rt_stdev_ms  + ' ms' : '—', '');
    _setCard('rt-n',      m.rt_n ?? 0, 'hits + misses');

    // Response Time — advanced
    _setCard('rt-fast10', m.rt_fast_10_ms != null ? m.rt_fast_10_ms + ' ms' : '—', '');
    _setCard('rt-slow10', m.rt_slow_10_ms != null ? m.rt_slow_10_ms + ' ms' : '—', '');
    _setCard('rt-range',
      (m.rt_min_ms != null && m.rt_max_ms != null) ? `${m.rt_min_ms}–${m.rt_max_ms} ms` : '—', '');
    _setCard('rt-cv',     m.rt_cv != null ? m.rt_cv + '%' : '—', '');
    _setCard('lapse',     m.lapse_count ?? 0,
      m.lapse_ms ? `> ${m.lapse_ms} ms (median × ${m.lapse_multiplier ?? ''})` : '',
      (m.lapse_count ?? 0) > 0 ? 'aar-card--warn' : '');

    // Vigilance & engagement
    const omit = m.omissions ?? 0;
    _setCard('resp-rate', m.response_rate_pct != null ? m.response_rate_pct + '%' : '—',
      `${(m.hits ?? 0) + (m.misses ?? 0)} responded`);
    _setCard('omissions', omit, '');
    _setCard('omit-rate', m.omission_rate_pct != null ? m.omission_rate_pct + '%' : '—', '');

    // Accuracy (marksmanship)
    _setCard('accuracy', m.accuracy_pct != null ? m.accuracy_pct + '%' : '—',
      `${m.hits ?? 0} / ${(m.hits ?? 0) + (m.misses ?? 0)}`);
    _setCard('hits',   m.hits ?? 0, '');
    _setCard('misses', m.misses ?? 0, '');
    _setCard('mre', m.mre_cm != null ? m.mre_cm + ' cm' : '—', m.mre_n ? `n = ${m.mre_n} hits` : '');

    // Time-on-task (vigilance decrement)
    _setCard('rt-first',  m.rt_median_first_half_ms  != null ? m.rt_median_first_half_ms  + ' ms' : '—', '');
    _setCard('rt-second', m.rt_median_second_half_ms != null ? m.rt_median_second_half_ms + ' ms' : '—', '');
    const slope = m.rt_slope_ms_per_trial;
    _setCard('rt-slope',
      slope != null ? `${slope > 0 ? '+' : ''}${slope} ms/trial` : '—', '',
      slope != null && slope > 0 ? 'aar-card--warn' : '');

  }

  // ── Helpers ────────────────────────────────────────────────────────────────

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

  function _setCard(id, value, sub, stateClass) {
    const card = document.getElementById(`aar-card-${id}`);
    const val = document.getElementById(`aar-val-${id}`);
    const subEl = document.getElementById(`aar-sub-${id}`);
    if (val) {
      val.textContent = value;
      val.title = value;
    }
    if (subEl && sub !== undefined) subEl.textContent = sub;
    if (card) {
      card.classList.remove('aar-card--good', 'aar-card--alert', 'aar-card--warn');
      if (stateClass) card.classList.add(stateClass);
      card.classList.remove('aar-card--flash');
      void card.offsetWidth;
      card.classList.add('aar-card--flash');
    }
  }

  function _esc(str) {
    return (str || '').replace(/"/g, '&quot;');
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return {
    mount,
    unmount,
    reset,
    onScenarioStarted,
    onMetricsReady,
    onPvtUpdate,
    onSensorUpdate,
    onScenarioStopped,
    onWeaponInfo,
  };

})();
