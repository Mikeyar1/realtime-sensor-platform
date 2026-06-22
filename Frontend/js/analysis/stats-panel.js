/**
 * stats-panel.js — Summary statistics panel below the epoch chart.
 *
 * BehDisc layout:
 *   1. Stats Summary — simple two-column list of 7 decision metrics
 *   2. Engagement Breakdown — two compact side-by-side cards
 *      Hostile: RT stats + physio table
 *      Non-Hostile: CE RT stats if participant fired, else N/A row + physio table
 *
 * Generic drill (PVT, etc.) — RT + accuracy + physio table
 */

window.LabReplay = window.LabReplay || {};

LabReplay.StatsPanel = (function () {

  let _el = null;
  let _term = {};

  function init(containerEl) { _el = containerEl; }
  function setTerminology(term) { _term = term; }

  function render(epochData, trialSummary) {
    if (!_el) return;
    const sigKeys = Object.keys(epochData.signals).filter(k => !epochData.signals[k].error);
    // Route to the right renderer by drill
    const isBehDisc  = (trialSummary.n_hostile > 0 || trialSummary.n_nonhostile > 0);
    const isL2GoNoGo = (_term.drill_short === 'L2GoNoGo');
    _el.innerHTML = isBehDisc
      ? _renderBehDisc(epochData, trialSummary, sigKeys)
      : isL2GoNoGo
        ? _renderL2GoNoGo(epochData, trialSummary, sigKeys)
        : _renderGeneric(epochData, trialSummary, sigKeys);
  }

  function clear() { if (_el) _el.innerHTML = ''; }

  // ── BehDisc ─────────────────────────────────────────────────────────────────

  function _renderBehDisc(epochData, s, sigKeys) {
    return `
      ${_statsSummary(s)}
      ${_breakdown(epochData, s, sigKeys)}
      ${_footnote()}
    `;
  }

  // ── Stats Summary: clean two-column list ─────────────────────────────────────

  function _statsSummary(s) {
    const p = v => v != null ? `${v.toFixed(1)}%` : 'N/A';
    const nH = s.n_hostile || 0;
    const nNH = s.n_nonhostile || 0;

    const rows = [
      ['Decision Accuracy', p(s.decision_accuracy_pct), '(hits + withholds) / total'],
      ['Hit Rate', p(s.correct_engagement_rate_pct), 'hits / hostile'],
      ['Miss Rate', p(s.false_negative_rate_pct), 'misses / hostile'],
      ['Correct Engagement Rate', p(s.correct_engagement_rate_pct), 'hits / hostile'],
      ['Correct Restraint Rate', p(s.correct_restraint_rate_pct), 'withholds / non-hostile'],
      ['False Positive Rate', p(s.false_positive_rate_pct), 'errors / non-hostile'],
      ['False Negative Rate', p(s.false_negative_rate_pct), 'misses / hostile'],
      s.mean_ttff_ms != null
        ? ['Time to First Fixation — Mean', `${s.mean_ttff_ms} ms`, 'target onset → first fixation'] : null,
      s.mean_gaze_dispersion_px != null
        ? ['Gaze Dispersion — Mean', `${s.mean_gaze_dispersion_px} px`, 'RMS spread in 500 ms window'] : null,
    ].filter(Boolean);

    const rowsHtml = rows.map(([label, val, formula]) => `
      <tr class="an-ss-row">
        <td class="an-ss-label">${label}</td>
        <td class="an-ss-val">${val}</td>
        <td class="an-ss-formula">${formula}</td>
      </tr>`).join('');

    return `
      <div class="an-stats-section-header">Stats Summary</div>
      <div class="an-ss-card">
        <div class="an-ss-context">N = ${nH + nNH} decisions &nbsp;·&nbsp; ${nH} hostile &nbsp;·&nbsp; ${nNH} non-hostile</div>
        <table class="an-ss-table">
          <thead><tr>
            <th>Metric</th><th>Value</th><th>Formula</th>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    `;
  }

  // ── Engagement Breakdown ─────────────────────────────────────────────────────

  function _breakdown(epochData, s, sigKeys) {
    return `
      <div class="an-stats-section-header">Engagement Breakdown</div>
      <div class="an-stats-split">
        ${_hostileCard(epochData, s, sigKeys)}
        ${_nonhostileCard(epochData, s, sigKeys)}
      </div>
    `;
  }

  function _hostileCard(epochData, s, sigKeys) {
    const ms = v => v != null ? `${(v * 1000).toFixed(0)} ms` : 'N/A';
    const f1 = v => v != null ? v.toFixed(1) : 'N/A';

    const hitPct = s.correct_engagement_rate_pct != null
      ? `${s.correct_engagement_rate_pct.toFixed(1)}%` : 'N/A';

    const kvs = [
      ['Hit rate', hitPct],
      ['Hits', String(s.n_hits || 0)],
      ['Misses', String(s.n_misses || 0)],
      ['Mean RT', ms(s.rt_mean_s)],
      ['RT SD', ms(s.rt_std_s)],
      ['Min RT', ms(s.rt_min_s)],
      ['Max RT', ms(s.rt_max_s)],
      ['Mean shots', f1(s.mean_shots_per_engagement)],
    ];

    return `
      <div class="an-stats-group-card an-stats-group-card--hostile">
        <div class="an-stats-group-title"><strong>HOSTILE</strong>
          <span class="an-stats-group-n">${s.n_hostile || 0} engagements</span></div>
        ${_kvTable(kvs)}
        ${_physioMini(epochData, sigKeys, 'hostile')}
      </div>`;
  }

  function _nonhostileCard(epochData, s, sigKeys) {
    const ms = v => v != null ? `${(v * 1000).toFixed(0)} ms` : 'N/A';
    const f1 = v => v != null ? v.toFixed(1) : 'N/A';

    const ceRate = s.false_positive_rate_pct != null
      ? `${s.false_positive_rate_pct.toFixed(1)}%` : 'N/A';

    const hasCE = (s.n_commission_errors || 0) > 0;

    // Always show CE rate + counts; if shots were fired add RT stats
    const kvs = [
      ['Error rate', ceRate],
      ['Withheld correctly', String(s.n_correct_withholds || 0)],
      ['Commission errors', String(s.n_commission_errors || 0)],
      ['Mean RT (errors)', hasCE ? ms(s.ce_rt_mean_s) : 'N/A'],
      ['RT SD (errors)', hasCE ? ms(s.ce_rt_std_s) : 'N/A'],
      ['Min RT (errors)', hasCE ? ms(s.ce_rt_min_s) : 'N/A'],
      ['Max RT (errors)', hasCE ? ms(s.ce_rt_max_s) : 'N/A'],
      ['Mean shots (errors)', hasCE ? f1(s.ce_mean_shots) : 'N/A'],
    ];

    return `
      <div class="an-stats-group-card an-stats-group-card--nonhostile">
        <div class="an-stats-group-title"><strong>NON-HOSTILE</strong>
          <span class="an-stats-group-n">${s.n_nonhostile || 0} engagements</span></div>
        ${_kvTable(kvs)}
        ${_physioMini(epochData, sigKeys, 'nonhostile')}
      </div>`;
  }

  // ── KV table ─────────────────────────────────────────────────────────────────

  function _kvTable(rows) {
    const rowsHtml = rows.map(([label, val]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-val">${val}</td>
      </tr>`).join('');
    return `<table class="an-kv-table"><tbody>${rowsHtml}</tbody></table>`;
  }

  // ── Per-signal physio mini table ──────────────────────────────────────────────

  function _physioMini(epochData, sigKeys, groupKey) {
    const rows = sigKeys.map(sig => {
      const d = epochData.signals[sig];
      const blV = d[`${groupKey}_baseline_mean`];
      const anV = d[`${groupKey}_analysis_mean`];
      const delt = d[`${groupKey}_delta`];
      const blStr = _fmt(blV, d.unit);
      const anStr = _fmt(anV, d.unit);
      const dStr = delt != null
        ? `${delt > 0 ? '+' : ''}${delt.toFixed(3)}${_unitSuffix(d.unit)}` : '—';
      const dClass = _deltaClass(delt);
      const dir = delt != null ? (delt > 0 ? '↑' : delt < 0 ? '↓' : '→') : '';
      return `
        <tr class="an-stats-physio-row">
          <td><span class="an-stats-dot" style="background:${d.color}"></span>${d.label}</td>
          <td>${blStr}</td><td>${anStr}</td>
          <td class="${dClass}">${dStr} ${dir}</td>
        </tr>`;
    }).join('');

    return `
      <table class="an-stats-physio-mini">
        <thead><tr>
          <th>Signal</th><th>Baseline</th><th>Analysis</th><th>Δ</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── Generic drill (PVT, L2GoNoGo, etc.) ────────────────────────────────────

  function _renderGeneric(epochData, s, sigKeys) {
    const ms   = v => v != null ? `${(v * 1000).toFixed(0)} ms` : '—';
    const pct  = (n, d) => (d > 0) ? `${(n / d * 100).toFixed(1)}%` : '—';
    const rtLabel = _term.rt_label || 'Reaction Time';
    const total   = s.n_trials || 0;
    const nHits   = s.n_hits   || 0;
    const nMisses = s.n_misses || 0;
    const nLapses = s.n_lapses || 0;
    const nAnt    = s.n_anticipation_errors || 0;

    const kvRows = [
      [_term.trial_label ? `${_term.trial_label}s` : 'Trials', String(total)],
      ['Hit Rate',         pct(nHits,   total)],
      ['Miss Rate',        pct(nMisses, total)],
      ['Lapse Rate',       pct(nLapses, total)],
      nAnt ? ['Anticipation Errors', String(nAnt)] : null,
      [rtLabel + ' — Mean', ms(s.rt_mean_s)],
      [rtLabel + ' — SD',   ms(s.rt_std_s)],
      [rtLabel + ' — Min',  ms(s.rt_min_s)],
      [rtLabel + ' — Max',  ms(s.rt_max_s)],
      // Gaze metrics (when Neon data available)
      s.mean_ttff_ms != null
        ? ['Time to First Fixation — Mean', `${s.mean_ttff_ms} ms`] : null,
      s.mean_gaze_dispersion_px != null
        ? ['Gaze Dispersion — Mean', `${s.mean_gaze_dispersion_px} px`] : null,
    ].filter(Boolean);

    const kvHtml = kvRows.map(([label, val]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-val">${val}</td>
      </tr>`).join('');

    // Per-signal physio table (analysis window vs session baseline)
    const sigRows = sigKeys.map(sig => {
      const d     = epochData.signals[sig];
      const anVal = _fmt(d.analysis_mean, d.unit);
      const delta = d.delta;
      const dStr  = delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(2)}` : '—';
      const pctD  = d.delta_pct != null ? `${d.delta_pct > 0 ? '+' : ''}${d.delta_pct.toFixed(1)}%` : '—';
      const dir   = delta != null ? (delta > 0 ? '↑' : delta < 0 ? '↓' : '→') : '';
      return `
        <tr>
          <td class="an-stats-signal">
            <span class="an-stats-dot" style="background:${d.color}"></span>
            ${d.label} <span class="an-stats-unit">${d.unit}</span>
          </td>
          <td class="an-stats-val">${anVal}</td>
          <td class="an-stats-delta ${_deltaClass(delta)}">${dStr} <span class="an-stats-unit">${d.unit}</span></td>
          <td class="an-stats-pct  ${_deltaClass(delta)}">${pctD} ${dir}</td>
        </tr>`;
    }).join('');

    return `
      <div class="an-stats-section-header">${_term.drill_name || 'Drill'} — Session Summary</div>
      <div class="an-stats-group-card" style="margin-bottom:8px">
        <table class="an-kv-table"><tbody>${kvHtml}</tbody></table>
      </div>
      <div class="an-stats-section-header">Physio Response (Analysis Window vs Session Baseline)</div>
      <table class="an-stats-table">
        <thead><tr>
          <th>Signal</th><th>Analysis Mean</th>
          <th>&#916; (units)</th><th>&#916; %</th>
        </tr></thead>
        <tbody>${sigRows}</tbody>
      </table>
      ${_footnote()}
    `;
  }



  function _renderL2GoNoGo(epochData, s, sigKeys) {
    const ms  = v => v != null ? `${(v * 1000).toFixed(0)} ms` : '—';
    const pct = v => v != null ? `${v.toFixed(1)}%` : '—';
    const rt  = _term.rt_label || 'Response Time';
    const nGO = s.n_go_trials != null ? s.n_go_trials : (s.n_trials || 0);


    // ── GO performance ──
    // Count + % rows
    const goCountRows = [
      [_term.hit_label   || 'Correct GO (Hit)',       s.n_hits   || 0, pct(s.go_hit_pct)],
      [_term.lapse_label || 'Omission (No Response)', s.n_lapses || 0, pct(s.go_omission_pct)],
      [_term.miss_label  || 'Errant Shot (Miss)',     s.n_misses || 0, pct(s.go_miss_pct)],
    ];
    // Plain stat rows (no %)
    const goStatRows = [
      s.mean_shots_per_engagement != null
        ? ['Shots per Engagement',    `${s.mean_shots_per_engagement} avg`] : null,
      s.mean_distance_m != null
        ? ['Target Distance — Mean', `${s.mean_distance_m} m`]             : null,
    ].filter(Boolean);

    const goCountHtml = goCountRows.map(([label, n, p]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-count">${n}</td>
        <td class="an-kv-pct">${p}</td>
      </tr>`).join('');

    const goStatHtml = goStatRows.map(([label, val]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-val" colspan="2">${val}</td>
      </tr>`).join('');

    // ── NO-GO performance ──
    const nogoBlock = (s.n_nogo_trials > 0) ? `
      <div class="an-stats-section-header">NO-GO Performance</div>
      <div class="an-stats-group-card" style="margin-bottom:8px">
        <table class="an-kv-table">
          <thead><tr><th>Metric</th><th>n</th><th>%</th></tr></thead>
          <tbody>
            <tr class="an-kv-row">
              <td class="an-kv-label">NO-GO Trials (neutral)</td>
              <td class="an-kv-count">${s.n_nogo_trials}</td>
              <td class="an-kv-pct">—</td>
            </tr>
            <tr class="an-kv-row">
              <td class="an-kv-label">${_term.withhold_label || 'Correct Rejection'}</td>
              <td class="an-kv-count">${s.n_correct_withholds || 0}</td>
              <td class="an-kv-pct an-kv-val--good">${pct(s.nogo_correct_withhold_pct)}</td>
            </tr>
            <tr class="an-kv-row">
              <td class="an-kv-label">${_term.fa_label || 'Commission Error (false alarm)'}</td>
              <td class="an-kv-count">${s.n_commission_errors || 0}</td>
              <td class="an-kv-pct ${(s.n_commission_errors || 0) > 0 ? 'an-kv-val--bad' : ''}">${(s.n_commission_errors || 0) > 0 ? pct(s.nogo_commission_error_pct) : '—'}</td>
            </tr>
          </tbody>
        </table>
      </div>` : '';

    // ── RT & Gaze rows ──
    const rtRows = [
      [`${rt} — Mean`, ms(s.rt_mean_s)],
      [`${rt} — SD`,   ms(s.rt_std_s)],
      [`${rt} — Min`,  ms(s.rt_min_s)],
      [`${rt} — Max`,  ms(s.rt_max_s)],
      s.mean_ttff_ms != null
        ? ['Time to First Fixation — Mean', `${s.mean_ttff_ms} ms`]           : null,
      s.mean_gaze_dispersion_px != null
        ? ['Gaze Dispersion — Mean',        `${s.mean_gaze_dispersion_px} px`] : null,
    ].filter(Boolean);

    const rtHtml = rtRows.map(([label, val]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-val" colspan="2">${val}</td>
      </tr>`).join('');

    // ── Physio table ──
    const sigRows = sigKeys.map(sig => {
      const d     = epochData.signals[sig];
      const anVal = _fmt(d.analysis_mean, d.unit);
      const delta = d.delta;
      const dStr  = delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(2)}` : '—';
      const pctD  = d.delta_pct != null ? `${d.delta_pct > 0 ? '+' : ''}${d.delta_pct.toFixed(1)}%` : '—';
      const dir   = delta != null ? (delta > 0 ? '↑' : delta < 0 ? '↓' : '→') : '';
      return `<tr>
        <td class="an-stats-signal">
          <span class="an-stats-dot" style="background:${d.color}"></span>
          ${d.label} <span class="an-stats-unit">${d.unit}</span>
        </td>
        <td class="an-stats-val">${anVal}</td>
        <td class="an-stats-delta ${_deltaClass(delta)}">${dStr} <span class="an-stats-unit">${d.unit}</span></td>
        <td class="an-stats-pct ${_deltaClass(delta)}">${pctD} ${dir}</td>
      </tr>`;
    }).join('');

    const hasPhysioData = sigKeys.some(sig => epochData.signals[sig].analysis_mean != null);
    const physioNote = !hasPhysioData
      ? `<p class="an-no-data-note">Session baseline (Pupil: ${epochData.session_baseline?.pupil?.mean != null ? epochData.session_baseline.pupil.mean.toFixed(1) + ' mm' : '—'}) was captured from the pre-drill window. Physio data was not streamed during drill trials in this recording &mdash; HR, Pupil, and RMSSD require active sensor connections throughout the drill to appear here.</p>`
      : '';

    const hasGaze = s.mean_ttff_ms != null || s.mean_gaze_dispersion_px != null;
    const rtHeader = hasGaze ? 'Response Time &amp; Gaze' : 'Response Time';

    return `
      <div class="an-stats-section-header">${_term.drill_name || 'L2 Go/No-Go'} — Session Summary</div>

      <div class="an-stats-section-header">GO Performance</div>
      <div class="an-stats-group-card" style="margin-bottom:8px">
        <table class="an-kv-table">
          <thead><tr><th>Metric</th><th>n</th><th>%</th></tr></thead>
          <tbody>
            <tr class="an-kv-row">
              <td class="an-kv-label">GO Trials (Ivan appeared)</td>
              <td class="an-kv-count">${nGO}</td>
              <td class="an-kv-pct" style="color:#9CA3AF">total</td>
            </tr>
            ${goCountHtml}
          </tbody>
        </table>
        ${goStatHtml ? `<table class="an-kv-table" style="border-top:1px solid rgba(0,0,0,.06);margin-top:2px">
          <tbody>${goStatHtml}</tbody></table>` : ''}
      </div>

      ${nogoBlock}

      <div class="an-stats-section-header">${rtHeader}</div>
      <div class="an-stats-group-card" style="margin-bottom:8px">
        <table class="an-kv-table"><tbody>${rtHtml}</tbody></table>
      </div>

      <div class="an-stats-section-header">Physio Response (Analysis Window vs Session Baseline)</div>
      ${physioNote}
      <table class="an-stats-table">
        <thead><tr>
          <th>Signal</th><th>Analysis Mean</th>
          <th>&#916; (units)</th><th>&#916; %</th>
        </tr></thead>
        <tbody>${sigRows}</tbody>
      </table>
      ${_footnote()}
    `;
  }


  // ── Footnote ──────────────────────────────────────────────────────────────────

  function _footnote() {
    return `
      <div class="an-stats-footnote">
        <strong>Note:</strong> Physio values are presented in native units.
        Baseline = pre-event; Analysis starts at t = 0. Δ = mean change baseline → analysis.
      </div>`;
  }

  // ── Per-Engagement: single trial ────────────────────────────────────────────

  function renderPerEngagement(epochData, trial) {
    if (!_el) return;
    const sigKeys = Object.keys(epochData.signals).filter(k => !epochData.signals[k].error);
    _el.innerHTML = _renderSingleTrial(epochData, trial, sigKeys);
  }

  // Render per-trial stats into an arbitrary element (e.g. an-pe-panel).
  // The aggregate _el stays untouched.
  function renderPerEngagementInto(el, epochData, trial) {
    if (!el) return;
    const sigKeys = Object.keys(epochData.signals).filter(k => !epochData.signals[k].error);
    el.innerHTML = _renderSingleTrial(epochData, trial, sigKeys);
  }

  function _renderSingleTrial(epochData, trial, sigKeys) {
    const isBehDisc = trial.actor_type != null;
    const isHostile = trial.actor_type === 'HOSTILE';
    const hasCE     = trial.outcome === 'COMMISSION_ERROR';
    const rtMs      = trial.rt_s != null ? `${(trial.rt_s * 1000).toFixed(0)} ms` : 'N/A';
    const shots     = trial.n_shots != null && trial.n_shots > 0 ? `${trial.n_shots}\u00d7` : 'N/A';

    let kvs;
    if (isBehDisc) {
      const outcomeB = _term[
        trial.outcome === 'HIT'              ? 'hit_label'      :
        trial.outcome === 'MISS'             ? 'miss_label'     :
        trial.outcome === 'COMMISSION_ERROR' ? 'fa_label'       :
        trial.outcome === 'CORRECT_WITHHOLD' ? 'withhold_label' : null
      ] || trial.outcome;
      kvs = [
        ['Actor',   trial.actor_name || trial.actor_short || `#${trial.index}`],
        ['Type',    isHostile ? 'Hostile' : 'Non-Hostile'],
        ['Outcome', outcomeB],
        ['Reaction Time (1st shot)', (isHostile || hasCE) ? rtMs : 'N/A'],
        ['Shots', (isHostile || hasCE) ? shots : 'N/A'],
        trial.ttff_ms            != null ? ['Time to First Fixation', `${trial.ttff_ms} ms`] : null,
        trial.gaze_dispersion_px != null ? ['Gaze Dispersion', `${trial.gaze_dispersion_px.toFixed(0)} px`] : null,
      ].filter(Boolean);
    } else {
      // Generic drill (PVT, L2GoNoGo)
      const outcomeLabel = _term[
        trial.outcome === 'HIT'              ? 'hit_label'      :
        trial.outcome === 'MISS'             ? 'miss_label'     :
        trial.outcome === 'LAPSE'            ? 'lapse_label'    :
        trial.outcome === 'OMISSION'         ? 'lapse_label'    :
        trial.outcome === 'ANTICIPATION_ERROR' ? 'fa_label'     :
        trial.outcome === 'COMMISSION_ERROR' ? 'fa_label'       :
        trial.outcome === 'CORRECT_WITHHOLD' ? 'withhold_label' : null
      ] || trial.outcome;
      kvs = [
        ['Outcome', outcomeLabel],
        [_term.rt_label || 'Reaction Time', rtMs],
        trial.screen      != null ? ['Screen',    String(trial.screen)] : null,
        trial.direction_deg != null ? ['Direction', `${Math.round(trial.direction_deg)}°`] : null,
        trial.ttff_ms            != null ? ['Time to First Fixation', `${trial.ttff_ms} ms`] : null,
        trial.gaze_dispersion_px != null ? ['Gaze Dispersion', `${trial.gaze_dispersion_px.toFixed(0)} px`] : null,
      ].filter(Boolean);
    }

    const kvHtml = kvs.map(([label, val]) => `
      <tr class="an-kv-row">
        <td class="an-kv-label">${label}</td>
        <td class="an-kv-val">${val}</td>
      </tr>`).join('');

    return `
      <div class="an-stats-section-header">${_term.drill_name || 'Drill'} — ${_term.trial_label || 'Trial'} #${trial.index}</div>
      <div class="an-stats-group-card" style="margin-bottom:8px">
        <table class="an-kv-table"><tbody>${kvHtml}</tbody></table>
      </div>
      <div class="an-stats-section-header">Physio Response (Analysis Window vs Session Baseline)</div>
      ${_singleTrialPhysio(epochData, trial.index, sigKeys)}
      ${_footnote()}
    `;
  }

  function _singleTrialPhysio(epochData, trialIdx, sigKeys) {
    const rows = sigKeys.map(sig => {
      const d     = epochData.signals[sig];
      const times = d.times || [];
      const ep    = (d.epochs || []).find(e => e.trial_index === trialIdx);

      if (!ep) {
        return `
          <tr>
            <td class="an-stats-signal">
              <span class="an-stats-dot" style="background:${d.color}"></span>
              ${d.label} <span class="an-stats-unit">${d.unit}</span>
            </td>
            <td class="an-stats-val">—</td>
            <td class="an-stats-delta an-tt-delta--none">—</td>
            <td class="an-stats-pct  an-tt-delta--none">—</td>
          </tr>`;
      }

      // Analysis window: times >= 0
      const values = ep.values || [];
      const anVals = [];
      for (let i = 0; i < Math.min(values.length, times.length); i++) {
        const v = values[i];
        if (v == null || isNaN(v) || times[i] < 0) continue;
        anVals.push(v);
      }

      const mean = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
      const anV  = mean(anVals);

      // Baseline: session-level baseline (consistent with aggregate view)
      const blV  = epochData.session_baseline?.[sig]?.mean ?? null;

      const delt  = (blV != null && anV != null) ? anV - blV : null;
      const pctD  = (delt != null && blV)  ? (delt / Math.abs(blV)) * 100 : null;

      const anStr  = _fmt(anV, d.unit);
      const dStr   = delt != null ? `${delt > 0 ? '+' : ''}${delt.toFixed(2)}` : '—';
      const pctStr = pctD != null ? `${pctD > 0 ? '+' : ''}${pctD.toFixed(1)}%` : '—';
      const dir    = delt != null ? (delt > 0 ? '↑' : delt < 0 ? '↓' : '→') : '';

      return `
        <tr>
          <td class="an-stats-signal">
            <span class="an-stats-dot" style="background:${d.color}"></span>
            ${d.label} <span class="an-stats-unit">${d.unit}</span>
          </td>
          <td class="an-stats-val">${anStr}</td>
          <td class="an-stats-delta ${_deltaClass(delt)}">${dStr} <span class="an-stats-unit">${d.unit}</span></td>
          <td class="an-stats-pct  ${_deltaClass(delt)}">${pctStr} ${dir}</td>
        </tr>`;
    }).join('');

    return `
      <table class="an-stats-table">
        <thead><tr>
          <th>Signal</th><th>Analysis Mean</th>
          <th>&#916; (units)</th><th>&#916; %</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── Per-Engagement: multi-trial selection ────────────────────────────────────

  function renderPerEngagementMulti(epochData, trials) {
    if (!_el) return;
    const sigKeys = Object.keys(epochData.signals).filter(k => !epochData.signals[k].error);
    const nH  = trials.filter(t => t.actor_type === 'HOSTILE').length;
    const nNH = trials.filter(t => t.actor_type === 'NON_HOSTILE').length;
    const isBehDisc = nH > 0 || nNH > 0;

    let breakdownHtml;
    if (isBehDisc) {
      breakdownHtml = `
        <tr class="an-kv-row"><td class="an-kv-label">Hostile</td><td class="an-kv-val">${nH}</td></tr>
        <tr class="an-kv-row"><td class="an-kv-label">Non-Hostile</td><td class="an-kv-val">${nNH}</td></tr>`;
    } else {
      const oc = {};
      trials.forEach(t => { oc[t.outcome] = (oc[t.outcome] || 0) + 1; });
      breakdownHtml = Object.entries(oc).map(([k, v]) =>
        `<tr class="an-kv-row"><td class="an-kv-label">${k}</td><td class="an-kv-val">${v}</td></tr>`
      ).join('');
    }

    _el.innerHTML = `
      <div class="an-stats-section-header">Selection \u2014 ${trials.length} ${_term.trial_label || 'trial'}s</div>
      <div class="an-pe-card">
        <table class="an-kv-table"><tbody>
          ${breakdownHtml}
        </tbody></table>
        <p class="an-pe-hint">Click a single row to see per-trial details.</p>
      </div>
      ${_footnote()}
    `;
  }

  // ── Session Comparison Stats Table ──────────────────────────────────────────

  function renderComparison(epochDataA, trialsDataA, epochDataB, trialsDataB, drillKey) {
    if (!_el) return;
    const drillModule = window.LabReplay && window.LabReplay.Drills && window.LabReplay.Drills[drillKey];
    if (drillModule && typeof drillModule.renderComparison === 'function') {
      drillModule.renderComparison(epochDataA, trialsDataA, epochDataB, trialsDataB, _el, _compare, _footnote);
    } else {
      _el.innerHTML = `
        <div style="padding:48px; text-align:center; color:var(--text-muted); font-size:var(--font-size-sm);">
          <div style="font-size:24px; margin-bottom:12px;"></div>
          <strong>Comparison Not Implemented</strong>
          <p style="margin:8px 0 0 0; opacity:0.7;">The comparison view for the drill "${drillKey}" has not been implemented yet.</p>
        </div>
      `;
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function _fmt(v, unit) {
    if (v == null || (typeof v === 'number' && isNaN(v))) return '—';
    const n = parseFloat(v);
    if (isNaN(n)) return '—';
    if (unit === 'bpm') return `${n.toFixed(1)} bpm`;
    if (unit === 'mm') return `${n.toFixed(2)} mm`;
    return n.toFixed(3);
  }

  // Returns a clean unit suffix — suppresses the raw [0–1] motion label
  function _unitSuffix(unit) {
    if (!unit || unit === '[0–1]' || unit === '[0-1]') return '';
    return ` ${unit}`;
  }

  function _deltaClass(delta) {
    if (delta == null || isNaN(delta)) return '';
    return delta > 0 ? 'an-stats-pos' : delta < 0 ? 'an-stats-neg' : '';
  }

  function _compare(valA, valB, type) {
    if (valA == null || valB == null || isNaN(valA) || isNaN(valB)) {
      return '<span class="an-compare-change-neutral">—</span>';
    }
    const diff = valB - valA;
    if (Math.abs(diff) < 1e-5) {
      return '<span class="an-compare-change-neutral">0</span>';
    }

    let unit = '';
    let formattedVal = '';

    if (type === 'count') {
      formattedVal = (diff > 0 ? '+' : '') + diff;
    } else if (type === 'pct' || type === 'pct_reverse') {
      unit = '%';
      formattedVal = (diff > 0 ? '+' : '') + diff.toFixed(1) + unit;
    } else if (type === 'ms') {
      unit = ' ms';
      formattedVal = (diff > 0 ? '+' : '') + diff.toFixed(0) + unit;
    } else if (type === 'bpm') {
      unit = ' bpm';
      formattedVal = (diff > 0 ? '+' : '') + diff.toFixed(1) + unit;
    } else if (type === 'mm') {
      unit = ' mm';
      formattedVal = (diff > 0 ? '+' : '') + diff.toFixed(2) + unit;
    } else {
      // motion [0–1] or any raw unitless float
      formattedVal = (diff > 0 ? '+' : '') + diff.toFixed(3);
    }

    const arrow = diff > 0 ? '↑' : '↓';
    const cls = diff > 0 ? 'an-compare-change-pos' : 'an-compare-change-neg';
    return `<span class="${cls}">${formattedVal} ${arrow}</span>`;
  }

  return { init, setTerminology, render, renderPerEngagement, renderPerEngagementInto, renderPerEngagementMulti, renderComparison, clear };
})();
