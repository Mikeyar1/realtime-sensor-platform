/**
 * features/aar/task-types/discrimination.js
 *
 * After Action Review display module for the two-phase discrimination drills
 * (Drill 3A single-target, Drill 3B dual-lane).
 *
 * DISPLAY ONLY — no metric computation here.
 * Receives pre-computed metric JSON from the Backend and renders cards.
 *
 * Metric payload shape:
 *   { total_trials, duration_s, wild_shots,
 *     pvt:            { ...PVT metric set for phase 1... },
 *     discrimination: { ...threat/non-threat count, rate, and timing metrics... },
 *     timeline:       [...] }
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.Discrimination = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _container = null;
  let _scenarioName = '';

  // ── Mount ──────────────────────────────────────────────────────────────────

  function mount(containerEl) {
    _container = containerEl;
    _scenarioName = '';
    _renderWaiting();
  }

  function unmount() {
    _container = null;
  }

  function reset() {
    _scenarioName = '';
    _renderWaiting();
  }

  // ── Waiting state ──────────────────────────────────────────────────────────

  function _renderWaiting() {
    if (!_container) return;
    _container.innerHTML = `
      <div class="aar-panel" id="aar-disc-panel">
        <div class="aar-waiting" id="aar-disc-waiting">
          <div class="aar-waiting-label">Behavioral Discrimination</div>
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
      <div class="aar-panel" id="aar-disc-panel">

        <!-- Section: Session Context -->
        <div class="aar-section">
          <div class="aar-section-label">Session Context</div>
          <div class="aar-grid">
            ${_card('ctx-scenario',  _scenarioName || '\u2014', 'Scenario', '', 'Name of the VirTra scenario that was played.')}
            ${_card('ctx-simulator', payload.simulator        || '\u2014', 'Simulator', '', 'Simulator software used (e.g. V-AIMS).')}
            ${_card('ctx-simsystem', payload.simulator_system || '\u2014', 'Simulator System', '', 'Simulator hardware/system (e.g. V-300).')}
            ${_card('ctx-weapon',    '\u2014', 'Weapon Type', '', 'Firearm used by the trainee (captured from the first shot).')}
            ${_card('total',         '\u2014', 'Total Trials', '', 'All stimulus presentations across both phases.')}
            ${_card('duration',      '\u2014', 'Session Duration', '', 'Time from scenario start to scenario end (seconds).')}
          </div>
        </div>

        <!-- Section: Phase 1 — PVT -->
        <div class="aar-section">
          <div class="aar-section-label">Phase 1 &mdash; Psychomotor Speed &amp; Sustained Attention</div>

          <div class="aar-grid">
            ${_card('p-median', '\u2014', 'Median RT', 'ms', 'Median response time across phase-1 responses. Robust to outliers — the primary speed metric.')}
            ${_card('p-mean',   '\u2014', 'Mean RT',   'ms', 'Mean response time across phase-1 responses.')}
            ${_card('p-stdev',  '\u2014', 'RT Variability (SD)', 'ms', 'Standard deviation of response time — consistency of responding.')}
            ${_card('p-n',      '\u2014', 'RT Count',  '',   'Number of phase-1 RT samples (hits + misses).')}
          </div>

          <div class="aar-grid">
            ${_card('p-fast10', '\u2014', 'Fastest 10% RT', 'ms', 'Mean RT of the fastest 10% of responses — best-case alertness baseline.')}
            ${_card('p-slow10', '\u2014', 'Slowest 10% RT', 'ms', 'Mean RT of the slowest 10% of responses — the classic PVT lapse indicator.')}
            ${_card('p-cv',     '\u2014', 'Coefficient of Variation', '%', 'Normalized variability: (SD / Mean) × 100. Enables cross-session comparison.')}
            ${_card('p-lapse',  '\u2014', 'Lapse Count', '', 'Responses slower than the lapse threshold (median RT × multiplier) — sustained-attention failures.')}
          </div>

          <div class="aar-grid aar-grid--3col">
            ${_card('p-resprate', '\u2014', 'Response Rate',  '%', '(Hits + Misses) / phase-1 trials × 100 — did the trainee engage at all?')}
            ${_card('p-omit',     '\u2014', 'Omission Count', '',  'Phase-1 trials with no shot fired — failure to respond.')}
            ${_card('p-acc',      '\u2014', 'Accuracy',       '%', 'Hits / (Hits + Misses) × 100 — when they engaged, did they connect?')}
          </div>
        </div>

        <!-- Section: Phase 2 — Threat Discrimination -->
        <div class="aar-section">
          <div class="aar-section-label">Phase 2 &mdash; Threat Discrimination</div>

          <div class="aar-grid aar-grid--3col">
            ${_card('d-hit',   '\u2014', 'Hits',               '', 'Threat targets correctly engaged.')}
            ${_card('d-miss',  '\u2014', 'Misses',             '', 'Threat targets NOT engaged — failure to neutralize a threat.')}
            ${_card('d-fa',    '\u2014', 'False Alarms',       '', 'Non-threat targets engaged — shooting a civilian. The critical error.')}
            ${_card('d-cr',    '\u2014', 'Correct Rejections', '', 'Non-threat targets correctly left alone — successful restraint.')}
          </div>

          <div class="aar-grid aar-grid--3col">
            ${_card('d-hitrate', '\u2014', 'Hit Rate',            '%', 'Hits / threat trials × 100 — threat engagement reliability.')}
            ${_card('d-farate',  '\u2014', 'False Alarm Rate',    '%', 'False alarms / non-threat trials × 100 — civilian engagement rate.')}
            ${_card('d-acc',     '\u2014', 'Discrimination Accuracy', '%', '(Hits + Correct Rejections) / all phase-2 trials × 100 — overall correctness.')}
          </div>

          <div class="aar-grid aar-grid--3col">
            ${_card('d-wild',   '\u2014', 'Wild Shots',         '', 'Trigger pulls that struck no target at all.')}
          </div>

          <!-- False alarm alert row (hidden until false alarms > 0) -->
          <div class="aar-alert-row" id="aar-disc-fa-alert" style="display:none">
            <span class="aar-alert-icon">⚠</span>
            <span><strong class="aar-alert-count" id="aar-disc-fa-count">0</strong> false alarm(s) — non-threat target engaged</span>
          </div>

          <div class="aar-grid aar-grid--3col">
            ${_card('d-trt',  '\u2014', 'Threat RT (Median)',      'ms', 'Median time from threat appearing to the round striking it.')}
            ${_card('d-fart', '\u2014', 'False Alarm RT (Median)', 'ms', 'Median time before firing on a non-threat. Very fast values suggest reflexive firing before identification.')}
            ${_card('d-cost', '\u2014', 'RT Difference',           'ms', 'Threat RT minus false-alarm RT. Negative = errors were made faster than correct engagements, i.e. firing before identifying.')}
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

  function onSensorUpdate(_payload) { /* no-op — MetricCardRenderer handles physio cards */ }

  function onDiscriminationUpdate(msg) {
    if (!_container || !msg || !msg.running_metrics) return;
    _render(msg.running_metrics);
  }

  function onScenarioStopped(_payload) { /* no-op */ }

  function onWeaponInfo(payload) {
    if (!payload || !payload.weapon_type) return;
    _setCard('ctx-weapon', payload.weapon_type, '');
  }

  // ── Shared renderer (live running_metrics + final metrics use identical keys) ──

  function _render(m) {
    if (!m || !_container) return;

    const p = m.pvt || {};
    const d = m.discrimination || {};

    // Session context
    _setCard('total', m.total_trials ?? '—', '');
    if (m.duration_s != null) _setCard('duration', m.duration_s + ' s', '');

    // Phase 1 — PVT
    _setCard('p-median', p.rt_median_ms != null ? p.rt_median_ms + ' ms' : '—',
      `n = ${p.rt_n ?? 0} responses`);
    _setCard('p-mean',   p.rt_mean_ms  != null ? p.rt_mean_ms  + ' ms' : '—', '');
    _setCard('p-stdev',  p.rt_stdev_ms != null ? p.rt_stdev_ms + ' ms' : '—', '');
    _setCard('p-n',      p.rt_n ?? 0, 'hits + misses');
    _setCard('p-fast10', p.rt_fast_10_ms != null ? p.rt_fast_10_ms + ' ms' : '—', '');
    _setCard('p-slow10', p.rt_slow_10_ms != null ? p.rt_slow_10_ms + ' ms' : '—', '');
    _setCard('p-cv',     p.rt_cv != null ? p.rt_cv + '%' : '—', '');
    _setCard('p-lapse',  p.lapse_count ?? 0,
      p.lapse_ms ? `> ${p.lapse_ms} ms` : '',
      (p.lapse_count ?? 0) > 0 ? 'aar-card--warn' : '');

    const omit = p.omissions ?? 0;
    _setCard('p-resprate', p.response_rate_pct != null ? p.response_rate_pct + '%' : '—',
      `${(p.hits ?? 0) + (p.misses ?? 0)} responded`);
    _setCard('p-omit', omit, '');
    _setCard('p-acc',  p.accuracy_pct != null ? p.accuracy_pct + '%' : '—',
      `${p.hits ?? 0} / ${(p.hits ?? 0) + (p.misses ?? 0)}`);

    // Phase 2 — discrimination counts
    const nMiss = d.misses ?? 0;
    const nFa   = d.false_alarms ?? 0;
    _setCard('d-hit',  d.hits ?? 0, `of ${d.threat_trials ?? 0} threat`,
      'aar-card--good');
    _setCard('d-miss', nMiss, `of ${d.threat_trials ?? 0} threat`,
      nMiss > 0 ? 'aar-card--alert' : 'aar-card--good');
    _setCard('d-fa',   nFa, `of ${d.nonthreat_trials ?? 0} non-threat`,
      nFa > 0 ? 'aar-card--alert' : 'aar-card--good');
    _setCard('d-cr',   d.correct_rejections ?? 0, `of ${d.nonthreat_trials ?? 0} non-threat`,
      'aar-card--good');

    // Phase 2 — rates
    _setCard('d-hitrate', d.hit_rate_pct != null ? d.hit_rate_pct + '%' : '—', '');
    _setCard('d-farate',  d.false_alarm_rate_pct != null ? d.false_alarm_rate_pct + '%' : '—', '',
      (d.false_alarm_rate_pct ?? 0) > 0 ? 'aar-card--warn' : '');
    _setCard('d-acc',     d.discrimination_accuracy_pct != null ? d.discrimination_accuracy_pct + '%' : '—', '');

    _setCard('d-wild', d.wild_shots ?? 0, '', (d.wild_shots ?? 0) > 0 ? 'aar-card--warn' : '');

    // Phase 2 — timing
    _setCard('d-trt',  d.threat_rt_median_ms != null ? d.threat_rt_median_ms + ' ms' : '—',
      `n = ${d.threat_rt_n ?? 0}`);
    _setCard('d-fart', d.false_alarm_rt_median_ms != null ? d.false_alarm_rt_median_ms + ' ms' : '—',
      `n = ${d.false_alarm_rt_n ?? 0}`);
    _setCard('d-cost', d.rt_cost_ms != null ? _signed(d.rt_cost_ms) + ' ms' : '—', '',
      d.rt_cost_ms != null && d.rt_cost_ms < 0 ? 'aar-card--warn' : '');

    // False alarm alert row
    const row = document.getElementById('aar-disc-fa-alert');
    const cnt = document.getElementById('aar-disc-fa-count');
    if (row) row.style.display = nFa > 0 ? '' : 'none';
    if (cnt) cnt.textContent = nFa;
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _signed(v) {
    return (v > 0 ? '+' : '') + v;
  }

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
    const card  = document.getElementById(`aar-card-${id}`);
    const val   = document.getElementById(`aar-val-${id}`);
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
    onDiscriminationUpdate,
    onSensorUpdate,
    onScenarioStopped,
    onWeaponInfo,
  };

})();
