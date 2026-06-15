/**
 * comparison.js — Behavioral Discrimination (BehDisc) drill comparison plugin.
 */

window.LabReplay = window.LabReplay || {};
window.LabReplay.Drills = window.LabReplay.Drills || {};

LabReplay.Drills.behdisc = (function () {

  function renderComparison(epochDataA, trialsDataA, epochDataB, trialsDataB, containerEl, compareHelper, footnoteHelper) {
    const sA = trialsDataA.summary;
    const sB = trialsDataB.summary;

    const sigKeys = Object.keys(epochDataA.signals).filter(k => !epochDataA.signals[k].error);

    const fmtPct = v => v != null ? `${v.toFixed(1)}%` : '—';
    const fmtMs  = v => v != null ? `${(v * 1000).toFixed(0)} ms` : '—';

    const getGroupDelta = (epochData, sig, group) => {
      return epochData.signals[sig]?.[`${group}_delta`] ?? null;
    };

    // Returns the baseline→analysis delta for a signal/group (e.g. '+0.20 bpm')
    const getGroupDeltaStr = (epochData, sig, group) => {
      const d = epochData.signals[sig];
      if (!d) return '—';
      const delta = d[`${group}_delta`];
      if (delta == null) return '—';
      if (d.unit === 'bpm') return `${delta > 0 ? '+' : ''}${delta.toFixed(2)} bpm`;
      if (d.unit === 'mm')  return `${delta > 0 ? '+' : ''}${delta.toFixed(3)} mm`;
      // motion [0–1]: no bracket unit suffix
      return `${delta > 0 ? '+' : ''}${delta.toFixed(3)}`;
    };

    let rowsHtml = '';

    // Section 1: Decision Performance
    rowsHtml += `
      <tr class="an-compare-tr-section"><td colspan="4">Decision Performance</td></tr>
      <tr>
        <td>Number of Decisions (N)</td>
        <td>${sA.n_trials || 0}</td>
        <td>${sB.n_trials || 0}</td>
        <td>${compareHelper(sA.n_trials, sB.n_trials, 'count')}</td>
      </tr>
      <tr>
        <td>Decision Accuracy</td>
        <td>${fmtPct(sA.decision_accuracy_pct)}</td>
        <td>${fmtPct(sB.decision_accuracy_pct)}</td>
        <td>${compareHelper(sA.decision_accuracy_pct, sB.decision_accuracy_pct, 'pct')}</td>
      </tr>
      <tr>
        <td>Hit Rate (Hostile)</td>
        <td>${fmtPct(sA.correct_engagement_rate_pct)}</td>
        <td>${fmtPct(sB.correct_engagement_rate_pct)}</td>
        <td>${compareHelper(sA.correct_engagement_rate_pct, sB.correct_engagement_rate_pct, 'pct')}</td>
      </tr>
      <tr>
        <td>Miss Rate (Hostile)</td>
        <td>${fmtPct(sA.false_negative_rate_pct)}</td>
        <td>${fmtPct(sB.false_negative_rate_pct)}</td>
        <td>${compareHelper(sA.false_negative_rate_pct, sB.false_negative_rate_pct, 'pct_reverse')}</td>
      </tr>
      <tr>
        <td>Correct Restraint (NH)</td>
        <td>${fmtPct(sA.correct_restraint_rate_pct)}</td>
        <td>${fmtPct(sB.correct_restraint_rate_pct)}</td>
        <td>${compareHelper(sA.correct_restraint_rate_pct, sB.correct_restraint_rate_pct, 'pct')}</td>
      </tr>
      <tr>
        <td>False Positive Rate (NH)</td>
        <td>${fmtPct(sA.false_positive_rate_pct)}</td>
        <td>${fmtPct(sB.false_positive_rate_pct)}</td>
        <td>${compareHelper(sA.false_positive_rate_pct, sB.false_positive_rate_pct, 'pct_reverse')}</td>
      </tr>
    `;

    // Section 2: Reaction Time
    rowsHtml += `
      <tr class="an-compare-tr-section"><td colspan="4">Reaction Time (Hostile Hits)</td></tr>
      <tr>
        <td>Mean Reaction Time</td>
        <td>${fmtMs(sA.rt_mean_s)}</td>
        <td>${fmtMs(sB.rt_mean_s)}</td>
        <td>${compareHelper(sA.rt_mean_s ? sA.rt_mean_s * 1000 : null, sB.rt_mean_s ? sB.rt_mean_s * 1000 : null, 'ms')}</td>
      </tr>
      <tr>
        <td>Reaction Time SD</td>
        <td>${fmtMs(sA.rt_std_s)}</td>
        <td>${fmtMs(sB.rt_std_s)}</td>
        <td>${compareHelper(sA.rt_std_s ? sA.rt_std_s * 1000 : null, sB.rt_std_s ? sB.rt_std_s * 1000 : null, 'ms')}</td>
      </tr>
      <tr>
        <td>Min Reaction Time</td>
        <td>${fmtMs(sA.rt_min_s)}</td>
        <td>${fmtMs(sB.rt_min_s)}</td>
        <td>${compareHelper(sA.rt_min_s ? sA.rt_min_s * 1000 : null, sB.rt_min_s ? sB.rt_min_s * 1000 : null, 'ms')}</td>
      </tr>
      <tr>
        <td>Max Reaction Time</td>
        <td>${fmtMs(sA.rt_max_s)}</td>
        <td>${fmtMs(sB.rt_max_s)}</td>
        <td>${compareHelper(sA.rt_max_s ? sA.rt_max_s * 1000 : null, sB.rt_max_s ? sB.rt_max_s * 1000 : null, 'ms')}</td>
      </tr>
    `;

    const buildPhysioRows = (group) => {
      return sigKeys.map(sig => {
        const d = epochDataA.signals[sig];
        const valA = getGroupDelta(epochDataA, sig, group);
        const valB = getGroupDelta(epochDataB, sig, group);
        return `
          <tr>
            <td><span class="an-stats-dot" style="background:${d.color}"></span>${d.label}</td>
            <td>${getGroupDeltaStr(epochDataA, sig, group)}</td>
            <td>${getGroupDeltaStr(epochDataB, sig, group)}</td>
            <td>${compareHelper(valA, valB, (d.unit === '[0–1]' || d.unit === '[0-1]' || !d.unit) ? 'motion' : d.unit)}</td>
          </tr>
        `;
      }).join('');
    };

    const nhRowsHtml = buildPhysioRows('nonhostile');
    const hRowsHtml = buildPhysioRows('hostile');

    containerEl.innerHTML = `
      <div class="an-stats-section-header">Session Comparison Summary</div>
      <div class="an-pe-card" style="padding: 0; overflow: hidden; margin-bottom: 16px;">
        <table class="an-compare-table">
          <thead>
            <tr>
              <th>Performance Metric</th>
              <th>Session A</th>
              <th>Session B</th>
              <th>Improvement</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      </div>

      <div class="an-stats-section-header">Physiological Response Change (Δ)</div>
      <div class="an-stats-split" style="margin-top: 8px;">
        <!-- Non-Hostile Card -->
        <div class="an-stats-group-card an-stats-group-card--nonhostile" style="padding: 12px 14px;">
          <div class="an-stats-group-title" style="margin-bottom: 12px;">
            <strong>NON-HOSTILE (Withholds)</strong>
          </div>
          <table class="an-compare-table" style="width: 100%;">
            <thead>
              <tr>
                <th>Signal</th>
                <th>Session A</th>
                <th>Session B</th>
                <th>Improvement</th>
              </tr>
            </thead>
            <tbody>
              ${nhRowsHtml}
            </tbody>
          </table>
        </div>

        <!-- Hostile Card -->
        <div class="an-stats-group-card an-stats-group-card--hostile" style="padding: 12px 14px;">
          <div class="an-stats-group-title" style="margin-bottom: 12px;">
            <strong>HOSTILE (Engagements)</strong>
          </div>
          <table class="an-compare-table" style="width: 100%;">
            <thead>
              <tr>
                <th>Signal</th>
                <th>Session A</th>
                <th>Session B</th>
                <th>Improvement</th>
              </tr>
            </thead>
            <tbody>
              ${hRowsHtml}
            </tbody>
          </table>
        </div>
      </div>

      ${footnoteHelper()}
    `;
  }

  return { renderComparison };
})();
