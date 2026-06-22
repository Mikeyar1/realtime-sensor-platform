/**
 * trial-sidebar.js — Full-width per-trial table for the unified analysis view.
 *
 * Shows all trials as rows with:
 *   BehDisc:  # | Actor | H/NH | Outcome | RT | Shots | signal Δ columns
 *   Generic:  # | Outcome | RT | Screen | Direction | signal Δ columns
 *
 * Clicking a row selects it (single-select). Clicking again deselects.
 * Emits a custom 'trialselect' event on the container element.
 *   detail: { trialIds: int[] }  (empty = deselected → aggregate)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.TrialSidebar = (function () {

  let _el        = null;
  let _trials    = [];
  let _term      = {};
  let _epochData = null;
  let _selected  = new Set();
  let _trialPhysio = {}; // { trialIdx: { sig: { delta, unit } } }
  let _onSelectCb  = null; // direct callback — faster/simpler than events

  const SIG_LABELS = { hr: 'HR Δ', pupil: 'Pupil Δ', rmssd: 'RMSSD Δ' };
  const SIG_UNITS  = { hr: 'bpm',  pupil: 'mm',       rmssd: 'ms'      };

  function init(containerEl) {
    _el = containerEl;
  }

  function setOnSelect(cb) {
    _onSelectCb = cb;
  }

  function load(trials, terminology, epochData) {
    _trials    = trials   || [];
    _term      = terminology || {};
    _epochData = epochData || null;
    _selected  = new Set();
    _trialPhysio = {};
    _computePhysio();
    _render();
  }

  function clear() {
    _trials = []; _term = {}; _epochData = null; _selected = new Set(); _trialPhysio = {};
    if (_el) _el.innerHTML = '';
  }

  // ── Pre-compute per-trial physio data ───────────────────────────────────────

  function _computePhysio() {
    if (!_epochData) return;
    const signals = Object.keys(_epochData.signals).filter(k => !_epochData.signals[k].error);
    signals.forEach(sig => {
      const sigData = _epochData.signals[sig];
      const times   = sigData.times || [];
      const blMean  = _epochData.session_baseline?.[sig]?.mean ?? null;
      (sigData.epochs || []).forEach(ep => {
        const idx    = ep.trial_index;
        if (!_trialPhysio[idx]) _trialPhysio[idx] = {};
        // Analysis window only (times >= 0)
        const anVals = (ep.values || []).filter((v, i) => v != null && !isNaN(v) && (times[i] ?? -1) >= 0);
        const anMean = anVals.length > 0 ? anVals.reduce((a, b) => a + b, 0) / anVals.length : null;
        const delta  = (anMean != null && blMean != null) ? anMean - blMean : null;
        const pct    = (delta  != null && blMean)         ? (delta / Math.abs(blMean)) * 100 : null;
        _trialPhysio[idx][sig] = {
          anMean, delta, pct, blMean,
          unit:  sigData.unit  || SIG_UNITS[sig]  || '',
          label: sigData.label || SIG_LABELS[sig] || sig,
          color: sigData.color || '#888',
        };
      });
    });
  }

  // ── Inline per-trial detail card ─────────────────────────────────────────────

  function _renderTrialDetail(trial) {
    const isBehDisc = trial.actor_type != null;
    const isHostile = trial.actor_type === 'HOSTILE';
    const rtMs      = trial.rt_s != null ? `${Math.round(trial.rt_s * 1000)} ms` : '—';
    const outcome   = trial.outcome || '—';
    const ocLabel   = _term[
      outcome === 'HIT'              ? 'hit_label'      :
      outcome === 'MISS'             ? 'miss_label'     :
      outcome === 'LAPSE'            ? 'lapse_label'    :
      outcome === 'OMISSION'         ? 'lapse_label'    :
      outcome === 'COMMISSION_ERROR' ? 'fa_label'       :
      outcome === 'CORRECT_WITHHOLD' ? 'withhold_label' : null
    ] || outcome;

    const kvRows = (isBehDisc ? [
      ['Actor',   trial.actor_name || trial.actor_short || `#${trial.index}`],
      ['Type',    isHostile ? 'Hostile' : 'Non-Hostile'],
      ['Outcome', ocLabel],
      ['Reaction Time', rtMs],
      trial.n_shots > 0 ? ['Shots', `${trial.n_shots}×`] : null,
    ] : [
      ['Outcome',  ocLabel],
      ['Reaction Time', rtMs],
      trial.screen      != null ? ['Screen',    String(trial.screen)]                     : null,
      trial.direction_deg != null ? ['Direction',  `${Math.round(trial.direction_deg)}°`]   : null,
      trial.ttff_ms            != null ? ['Time to First Fixation', `${trial.ttff_ms} ms`] : null,
      trial.gaze_dispersion_px != null ? ['Gaze Dispersion', `${trial.gaze_dispersion_px.toFixed(0)} px`] : null,
    ]).filter(Boolean)
      .map(([l, v]) => `<div class="an-pe-kv"><span class="an-pe-kv-label">${l}</span><span class="an-pe-kv-val">${v}</span></div>`)
      .join('');

    const physio = _trialPhysio[trial.index] || {};
    const physioRows = Object.entries(physio).map(([sig, d]) => {
      const anStr = d.anMean != null ? `${d.anMean.toFixed(2)} ${d.unit}` : '—';
      const dStr  = d.delta  != null ? `${d.delta > 0 ? '+' : ''}${d.delta.toFixed(2)} ${d.unit}` : '—';
      const pStr  = d.pct    != null ? `${d.pct   > 0 ? '+' : ''}${d.pct.toFixed(1)}%` : '—';
      const dir   = d.delta  != null ? (d.delta > 0 ? '↑' : d.delta < 0 ? '↓' : '→') : '';
      const cls   = d.delta  != null ? (d.delta > 0.5 ? 'an-tt-delta--up' : d.delta < -0.5 ? 'an-tt-delta--dn' : '') : '';
      return `<tr>
        <td><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${d.color};margin-right:5px;vertical-align:middle"></span>${d.label} <span style="opacity:.5;font-size:10px">${d.unit}</span></td>
        <td>${anStr}</td>
        <td class="${cls}">${dStr}</td>
        <td class="${cls}">${pStr} ${dir}</td>
      </tr>`;
    }).join('');

    const physioSection = physioRows ? `
      <div class="an-pe-section-label">Physio Response (Analysis Window vs Session Baseline)</div>
      <table class="an-pe-physio">
        <thead><tr><th>Signal</th><th>Analysis Mean</th><th>Δ (units)</th><th>Δ %</th></tr></thead>
        <tbody>${physioRows}</tbody>
      </table>` : '';

    return `
      <div class="an-pe-inline">
        <div class="an-pe-inline-header">
          ${_term.drill_name || 'Drill'} — ${_term.trial_label || 'Trial'} #${trial.index}
          <span class="an-pe-inline-close" title="Close">×</span>
        </div>
        <div class="an-pe-inline-body">
          <div class="an-pe-kv-grid">${kvRows}</div>
          ${physioSection}
        </div>
      </div>`;
  }

  // ── Main render ───────────────────────────────────────────────────────────

  function _render() {
    if (!_el) return;

    const isBehDisc = _trials.some(t => t.actor_type != null);
    const signals   = _epochData
      ? Object.keys(_epochData.signals).filter(k => !_epochData.signals[k].error)
      : [];

    const behHeaders = isBehDisc
      ? ['#', 'Actor', 'Type', 'Outcome', 'RT (ms)', 'Shots', '1st Fix (ms)', 'Gaze Disp']
      : ['#', 'Outcome', 'RT (ms)', 'Screen', 'Direction', '1st Fix (ms)', 'Gaze Disp'];

    const sigHeaders = signals.map(s => SIG_LABELS[s] || s);
    const allHeaders = [...behHeaders, ...sigHeaders];

    const headerHtml = allHeaders.map(h => `<th>${h}</th>`).join('');
    const rowsHtml   = _trials.map(t => _renderRow(t, isBehDisc, signals)).join('');

    // BehDisc: H/NH outcome summary; generic: hit count
    const nH   = _trials.filter(t => t.actor_type === 'HOSTILE').length;
    const nNH  = _trials.filter(t => t.actor_type === 'NON_HOSTILE').length;
    const summary = isBehDisc
      ? `${nH} hostile &nbsp;·&nbsp; ${nNH} non-hostile`
      : `${_trials.filter(t => t.outcome === 'HIT' || t.outcome === 'CORRECT_WITHHOLD').length} correct`;

    _el.innerHTML = `
      <div class="an-tt-header">
        <div class="an-tt-title">${_term.trial_label || 'Trial'}s &nbsp;<span class="an-tt-count">${_trials.length}</span></div>
        <div class="an-tt-hint">↓ Click any row for per-trial details</div>
        <div class="an-tt-summary">${summary}</div>
      </div>
      <div class="an-tt-scroll">
        <table class="an-tt-table">
          <thead><tr>${headerHtml}</tr></thead>
          <tbody id="an-tt-body">${rowsHtml}</tbody>
        </table>
      </div>
    `;

    // Row clicks — single-select with toggle + inline detail expansion
    _el.querySelectorAll('.an-tt-row').forEach(row => {
      row.addEventListener('click', () => {
        const idx = parseInt(row.dataset.index, 10);

        // Remove any existing detail row
        const existing = _el.querySelector('.an-tt-detail-row');
        if (existing) {
          const existingFor = parseInt(existing.dataset.for, 10);
          existing.remove();
          if (existingFor === idx) {
            // Clicking the already-open row → collapse (deselect)
            _selected.clear();
            row.classList.remove('an-tt-row--selected');
            _emit();
            return;
          }
        }

        // Select this trial
        _selected = new Set([idx]);
        _updateRowSelection();
        _emit();

        // Find trial data and insert detail row
        const trial = _trials.find(t => t.index === idx);
        if (!trial) return;

        const colCount = row.querySelectorAll('td').length;
        const detailTr = document.createElement('tr');
        detailTr.className = 'an-tt-detail-row';
        detailTr.dataset.for = idx;
        detailTr.innerHTML = `<td colspan="${colCount}">${_renderTrialDetail(trial)}</td>`;

        // Close button inside the detail row
        detailTr.querySelector('.an-pe-inline-close')?.addEventListener('click', (e) => {
          e.stopPropagation();
          detailTr.remove();
          _selected.clear();
          row.classList.remove('an-tt-row--selected');
          _emit();
        });

        row.insertAdjacentElement('afterend', detailTr);

        // Scroll detail into view smoothly
        setTimeout(() => detailTr.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
      });
    });

    _updateRowSelection();
  }

  // ── Row template ──────────────────────────────────────────────────────────

  function _renderRow(trial, isBehDisc, signals) {
    const idx      = trial.index;
    const rt       = trial.rt_s != null ? Math.round(trial.rt_s * 1000) : '—';
    const outcome  = trial.outcome || '—';

    // Outcome badge
    const ocCls   = outcome === 'HIT' || outcome === 'CORRECT_WITHHOLD' ? 'an-tt-outcome--good'
                  : outcome === 'MISS' || outcome === 'LAPSE' || outcome === 'OMISSION' ? 'an-tt-outcome--bad'
                  : '';
    const ocLabel = _term[
      outcome === 'HIT'               ? 'hit_label'      :
      outcome === 'MISS'              ? 'miss_label'      :
      outcome === 'LAPSE'             ? 'lapse_label'     :
      outcome === 'OMISSION'          ? 'lapse_label'     :
      outcome === 'ANTICIPATION_ERROR'? 'fa_label'        :
      outcome === 'COMMISSION_ERROR'  ? 'fa_label'        :
      outcome === 'CORRECT_WITHHOLD'  ? 'withhold_label'  : null
    ] || outcome;

    let behavCells;
    if (isBehDisc) {
      const isH     = trial.actor_type === 'HOSTILE';
      const actor   = trial.actor_short || trial.actor_name || `#${idx}`;
      const typeCls = isH ? 'an-tt-badge--h' : 'an-tt-badge--nh';
      const shots   = trial.n_shots != null && trial.n_shots > 0 ? `${trial.n_shots}×` : '—';
      behavCells = `
        <td class="an-tt-num">${idx}</td>
        <td class="an-tt-actor">${actor}</td>
        <td><span class="an-tt-badge ${typeCls}">${isH ? 'H' : 'NH'}</span></td>
        <td><span class="an-tt-outcome ${ocCls}">${ocLabel}</span></td>
        <td class="an-tt-rt">${rt}</td>
        <td class="an-tt-shots">${shots}</td>
        <td class="an-tt-gaze">${trial.ttff_ms != null ? trial.ttff_ms : '—'}</td>
        <td class="an-tt-gaze">${trial.gaze_dispersion_px != null ? trial.gaze_dispersion_px.toFixed(0) : '—'}</td>`;
    } else {
      const screen = trial.screen != null ? trial.screen : '—';
      const dir    = trial.direction_deg != null ? `${Math.round(trial.direction_deg)}°` : '—';
      behavCells = `
        <td class="an-tt-num">${idx}</td>
        <td><span class="an-tt-outcome ${ocCls}">${ocLabel}</span></td>
        <td class="an-tt-rt">${rt}</td>
        <td class="an-tt-screen">${screen}</td>
        <td class="an-tt-dir">${dir}</td>
        <td class="an-tt-gaze">${trial.ttff_ms != null ? trial.ttff_ms : '—'}</td>
        <td class="an-tt-gaze">${trial.gaze_dispersion_px != null ? trial.gaze_dispersion_px.toFixed(0) : '—'}</td>`;
    }

    // Physio delta cells
    const physio = _trialPhysio[idx] || {};
    const physioCells = signals.map(sig => {
      const d = physio[sig];
      if (!d || d.delta == null) return `<td class="an-tt-delta an-tt-delta--none">—</td>`;
      const v    = d.delta;
      const cls  = v > 0.5 ? 'an-tt-delta--up' : v < -0.5 ? 'an-tt-delta--dn' : '';
      const sign = v > 0 ? '+' : '';
      return `<td class="an-tt-delta ${cls}">${sign}${v.toFixed(1)}</td>`;
    }).join('');

    return `<tr class="an-tt-row" data-index="${idx}">${behavCells}${physioCells}</tr>`;
  }

  // ── Selection ─────────────────────────────────────────────────────────────

  function _updateRowSelection() {
    if (!_el) return;
    _el.querySelectorAll('.an-tt-row').forEach(row => {
      const idx = parseInt(row.dataset.index, 10);
      row.classList.toggle('an-tt-row--selected', _selected.has(idx));
    });
  }

  function _emit() {
    const ids = Array.from(_selected).sort((a, b) => a - b);
    // Direct callback (most reliable)
    if (_onSelectCb) _onSelectCb(ids);
    // Also dispatch on document as fallback
    document.dispatchEvent(new CustomEvent('trialselect', {
      detail: { trialIds: ids },
    }));
  }

  // Kept for compatibility with app.js calls
  function selectAll() { _selected = new Set(_trials.map(t => t.index)); _updateRowSelection(); _emit(); }
  function lock()   {}
  function unlock() {}

  return { init, setOnSelect, load, clear, selectAll, lock, unlock };
})();
