/**
 * epoch-chart.js — Plotly stacked epoch chart.
 *
 * Renders one subplot per physiological signal (HR, Pupil, Motion)
 * stacked vertically with a shared X axis.
 *
 * Modes:
 *   'aggregate'      — grand average ± 1 SD + light individual traces
 *   'per-engagement' — selected trial(s) shown individually, no averaging
 *
 * Y-axis: z-score (baseline-normalized). Each subplot clearly labeled.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.EpochChart = (function () {

  const SIGNAL_ORDER = ['hr', 'pupil', 'motion'];

  const SIGNAL_META = {
    hr: { color: '#C94444', label: 'Heart Rate', yLabel: 'Heart Rate (BPM)', unit: 'bpm', yRange: [40, 160] },
    pupil: { color: '#7C6AE8', label: 'Pupil Diameter', yLabel: 'Pupil Diameter (mm)', unit: 'mm', yRange: [0, 8] },
    motion: { color: '#F39C12', label: 'Motion Intensity', yLabel: 'Motion Intensity [0–1]', unit: '', yRange: [0, 1] },
  };

  const OUTCOME_COLORS = {
    HIT: '#2D8E54',
    MISS: '#C94444',
    COMMISSION_ERROR: '#D97706',
    CORRECT_WITHHOLD: '#3D6ECC',
  };

  const ACTOR_TYPE_COLORS = {
    HOSTILE: '#C94444',
    NON_HOSTILE: '#2D8E54',
  };

  let _chartId = null;
  let _baseline = 2.0;
  let _analysis = 2.0;
  let _term = {};
  let _mode = 'aggregate';    // 'aggregate' | 'per-engagement'
  let _aggFilter = 'all';     // 'all' | 'hostile' | 'nonhostile'

  function init(containerId) {
    _chartId = containerId;
  }

  function setWindows(baselineS, analysisS) {
    _baseline = baselineS;
    _analysis = analysisS;
  }

  function setTerminology(term) {
    _term = term;
  }

  function setMode(mode) {
    _mode = mode;
  }

  function setAggFilter(filter) {
    _aggFilter = filter;   // 'all' | 'hostile' | 'nonhostile'
  }

  // ── Main render ─────────────────────────────────────────────────────────────

  /**
   * @param {Object} epochData  — POST /api/epoch response
   * @param {int[]}  selectedIds — trial indices to include (null = all)
   */
  function render(epochData, selectedIds) {
    if (!_chartId) return;

    const el = document.getElementById(_chartId);
    if (!el) return;

    // Determine which signals have valid data
    const validSigs = SIGNAL_ORDER.filter(
      k => epochData.signals[k] && !epochData.signals[k].error
    );

    if (validSigs.length === 0) {
      el.innerHTML = '<div style="padding:24px;color:#9CA3AF;font-size:13px;">No signal data available.</div>';
      return;
    }

    const N = validSigs.length;
    const traces = [];
    const layout = _buildLayout(N, validSigs, epochData);

    validSigs.forEach((sig, rowIdx) => {
      const data = epochData.signals[sig];
      const meta = SIGNAL_META[sig] || { color: '#888', yLabel: sig };
      const color = meta.color;
      const times = data.times;
      const yAxis = rowIdx === 0 ? 'y' : `y${rowIdx + 1}`;
      const xAxis = rowIdx === N - 1 ? 'x' : `x${rowIdx + 1}`;

      if (_mode === 'per-engagement') {
        // ── Per-engagement: show individual traces for selected trials ──────
        _renderPerEngagement(traces, data, times, color, sig, yAxis, xAxis, selectedIds, epochData.trials_used, rowIdx === 0);
      } else if (epochData.has_type_split) {
        // ── Aggregate (BehDisc): hostile vs non-hostile split lines ─────────
        _renderTypeSplit(traces, data, times, sig, yAxis, xAxis, rowIdx === 0);
      } else {
        // ── Aggregate: grand average + ±1SD + light individual traces ───────
        _renderAggregate(traces, data, times, color, sig, yAxis, xAxis, selectedIds);
      }
    });

    const config = {
      displayModeBar: false,
      responsive: true,
    };

    if (el._plotlyInitialized) {
      Plotly.react(_chartId, traces, layout, config);
    } else {
      Plotly.newPlot(_chartId, traces, layout, config);
      el._plotlyInitialized = true;
    }
  }

  // ── Type-split aggregate rendering (BehDisc: hostile vs non-hostile) ────────

  function _renderTypeSplit(traces, data, times, sig, yAxis, xAxis, showLegend = true) {
    const sigMeta    = SIGNAL_META[sig] || { label: sig, unit: '' };
    const unitSuffix = sigMeta.unit ? ` ${sigMeta.unit}` : '';

    const allGroups = [
      {
        key:   'hostile',
        avg:   data.hostile_avg,
        upper: data.hostile_ci_upper,
        lower: data.hostile_ci_lower,
        n:     data.n_hostile || 0,
        color: '#C94444',
        label: 'Hostile',
      },
      {
        key:   'nonhostile',
        avg:   data.nonhostile_avg,
        upper: data.nonhostile_ci_upper,
        lower: data.nonhostile_ci_lower,
        n:     data.n_nonhostile || 0,
        color: '#3D6ECC',
        label: 'Non-Hostile',
      },
    ];

    // Apply aggregate filter
    const groups = _aggFilter === 'all'
      ? allGroups
      : allGroups.filter(g => g.key === _aggFilter);

    groups.forEach(g => {
      if (!g.avg || !g.avg.length) return;

      // ±1 SD band
      if (g.upper && g.lower && g.upper.length) {
        traces.push({
          x: [...times, ...times.slice().reverse()],
          y: [...g.upper, ...g.lower.slice().reverse()],
          type: 'scatter', mode: 'none',
          fill: 'toself',
          fillcolor: _rgba(g.color, 0.12),
          line: { width: 0 },
          showlegend: false, hoverinfo: 'none',
          xaxis: xAxis, yaxis: yAxis,
          name: `${g.key}_band`,
        });
      }

      // Average line — show in legend on first subplot only
      traces.push({
        x: times, y: g.avg,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color: g.color, width: 2.5 },
        name: `${g.label} (N=${g.n})`,
        showlegend: showLegend,
        legendgroup: g.key,
        hovertemplate:
          `<b>${g.label} — ${sigMeta.label}</b><br>` +
          `t = %{x:.2f} s<br><b>%{y:.2f}${unitSuffix}</b><extra></extra>`,
        xaxis: xAxis, yaxis: yAxis,
      });
    });
  }

  // ── Aggregate rendering ─────────────────────────────────────────────────────

  function _renderAggregate(traces, data, times, color, sig, yAxis, xAxis, selectedIds) {
    const epochs = (data.epochs || []).filter(ep => {
      return !selectedIds || selectedIds.includes(ep.trial_index);
    });

    // Individual epoch traces (ghost)
    epochs.forEach(ep => {
      const values = ep.values || ep;
      traces.push({
        x: times, y: values,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color, width: 0.8 },
        opacity: 0.13,
        showlegend: false,
        hoverinfo: 'none',
        xaxis: xAxis, yaxis: yAxis,
        name: `${sig}_t${ep.trial_index}`,
      });
    });

    // ±1 SD band (from grand_avg of selected)
    if (epochs.length > 1) {
      const selValues = epochs.map(ep => ep.values || ep);
      const { avg, upper, lower } = _computeAvg(selValues);
      traces.push({
        x: [...times, ...times.slice().reverse()],
        y: [...upper, ...lower.slice().reverse()],
        type: 'scatter', mode: 'none',
        fill: 'toself',
        fillcolor: _rgba(color, 0.10),
        line: { width: 0 },
        showlegend: false, hoverinfo: 'none',
        xaxis: xAxis, yaxis: yAxis,
        name: `${sig}_band`,
      });

      // Grand average
      const sigUnit = (SIGNAL_META[sig] || {}).unit || '';
      const unitSuffix = sigUnit ? ` ${sigUnit}` : '';
      traces.push({
        x: times, y: avg,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color, width: 2.5 },
        name: (SIGNAL_META[sig] || {}).label || sig,
        showlegend: true,
        legendgroup: sig,
        hovertemplate: `<b>${(SIGNAL_META[sig] || {}).label || sig}</b><br>t = %{x:.2f} s<br><b>%{y:.2f}${unitSuffix}</b><extra></extra>`,
        xaxis: xAxis, yaxis: yAxis,
      });
    } else if (epochs.length === 1) {
      // Single trial — just show it clearly
      const ep = epochs[0];
      const values = ep.values || ep;
      const sigUnit1 = (SIGNAL_META[sig] || {}).unit || '';
      const unitSuffix1 = sigUnit1 ? ` ${sigUnit1}` : '';
      traces.push({
        x: times, y: values,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color, width: 2.2 },
        name: (SIGNAL_META[sig] || {}).label || sig,
        showlegend: true,
        hovertemplate: `<b>${(SIGNAL_META[sig] || {}).label || sig}</b><br>t = %{x:.2f} s<br><b>%{y:.2f}${unitSuffix1}</b><extra></extra>`,
        xaxis: xAxis, yaxis: yAxis,
      });
    } else if (data.grand_avg && data.grand_avg.length) {
      // No filter — use pre-computed grand average from API
      if (data.ci_upper && data.ci_lower) {
        traces.push({
          x: [...times, ...times.slice().reverse()],
          y: [...data.ci_upper, ...data.ci_lower.slice().reverse()],
          type: 'scatter', mode: 'none',
          fill: 'toself', fillcolor: _rgba(color, 0.10),
          line: { width: 0 },
          showlegend: false, hoverinfo: 'none',
          xaxis: xAxis, yaxis: yAxis, name: `${sig}_band`,
        });
      }
      const sigUnitG = (SIGNAL_META[sig] || {}).unit || '';
      const unitSuffixG = sigUnitG ? ` ${sigUnitG}` : '';
      traces.push({
        x: times, y: data.grand_avg,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color, width: 2.5 },
        name: (SIGNAL_META[sig] || {}).label || sig,
        showlegend: true, legendgroup: sig,
        hovertemplate: `<b>${(SIGNAL_META[sig] || {}).label || sig}</b><br>t = %{x:.2f} s<br><b>%{y:.2f}${unitSuffixG}</b><extra></extra>`,
        xaxis: xAxis, yaxis: yAxis,
      });
    }
  }

  // ── Per-engagement rendering ─────────────────────────────────────────────────

  function _renderPerEngagement(traces, data, times, color, sig, yAxis, xAxis, selectedIds, trialsUsed, showLegend = true) {
    const epochs = (data.epochs || []).filter(ep => {
      return !selectedIds || selectedIds.includes(ep.trial_index);
    });

    // Build a lookup from trial index → metadata
    const trialMeta = {};
    (trialsUsed || []).forEach(t => { trialMeta[t.index] = t; });

    epochs.forEach(ep => {
      const values     = ep.values || ep;
      const meta       = trialMeta[ep.trial_index] || {};
      const actorType  = meta.actor_type || '';
      const outcome    = meta.outcome || ep.outcome || '';
      const rtMs       = meta.rt_s != null ? `${(meta.rt_s * 1000).toFixed(0)} ms` : '—';
      const actorShort = meta.actor_short || meta.actor_name || `T${ep.trial_index}`;

      const traceColor = actorType === 'HOSTILE' ? '#C94444' : '#3D6ECC';
      const sigLabel   = (SIGNAL_META[sig] || {}).label || sig;

      const engLabel = actorType === 'HOSTILE'
        ? `<b>#${ep.trial_index} ${actorShort}</b> HOSTILE<br>Outcome: ${outcome}<br>RT: ${rtMs}`
        : `<b>#${ep.trial_index} ${actorShort}</b> NON-HOSTILE<br>Outcome: ${outcome}`;

      const sigUnitP   = (SIGNAL_META[sig] || {}).unit || '';
      const unitSuffixP = sigUnitP ? ` ${sigUnitP}` : '';
      traces.push({
        x: times, y: values,
        type: 'scatter', mode: 'lines',
        connectgaps: true,
        line: { color: traceColor, width: 2.2 },
        name: `#${ep.trial_index} ${actorShort}`,
        showlegend: showLegend,          // only first subplot shows legend
        legendgroup: `trial_${ep.trial_index}`,
        hovertemplate: `${engLabel}<br><br>${sigLabel}: <b>%{y:.2f}${unitSuffixP}</b> at t=%{x:.2f}s<extra></extra>`,
        xaxis: xAxis, yaxis: yAxis,
      });
    });
  }

  // ── Layout builder ──────────────────────────────────────────────────────────

  function _buildLayout(N, validSigs, epochData) {
    const ROW_HEIGHT = 180;   // px per subplot
    const TOP_MARGIN = 40;
    const BOT_MARGIN = 50;
    const GAP_FRACTION = 0.06;
    const totalH = ROW_HEIGHT * N + TOP_MARGIN + BOT_MARGIN;

    // Compute subplot y-domains from bottom (index N-1) to top (index 0)
    // Plotly domains go 0 (bottom) to 1 (top)
    const domains = _subplotDomains(N, GAP_FRACTION);

    const eventLabel = _term.anchor_label || 'Event';
    const layout = {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: '#ffffff',
      font: { family: "'Source Sans 3', 'Inter', system-ui", size: 11, color: '#374151' },
      margin: { t: TOP_MARGIN, r: 24, b: BOT_MARGIN, l: 70 },
      height: totalH,
      showlegend: true,
      legend: {
        orientation: 'h',
        x: 0, y: -BOT_MARGIN / totalH - 0.02,
        font: { size: 10 },
        bgcolor: 'rgba(0,0,0,0)',
      },
      shapes: [],
      annotations: [],
    };

    // Build per-subplot axes
    validSigs.forEach((sig, i) => {
      const [y0, y1] = domains[i];
      const isBottom = (i === N - 1);
      const axSuffix = i === 0 ? '' : String(i + 1);
      const xAxisKey = `xaxis${axSuffix}`;
      const yAxisKey = `yaxis${axSuffix}`;
      const sigMeta = SIGNAL_META[sig] || {};

      layout[xAxisKey] = {
        range: [-_baseline - 0.1, _analysis + 0.1],
        domain: [0, 1],
        anchor: `y${axSuffix}`,
        matches: i === 0 ? undefined : 'x',   // share x with subplot 1
        zeroline: false,
        gridcolor: 'rgba(0,0,0,0.05)',
        tickfont: { size: 10 },
        showline: true, linecolor: 'rgba(0,0,0,0.1)',
        showticklabels: isBottom,
        title: isBottom
          ? { text: `Time from ${eventLabel} (s)`, font: { size: 10 } }
          : { text: '' },
      };

      layout[yAxisKey] = {
        domain: [y0, y1],
        anchor: `x${axSuffix}`,
        title: { text: sigMeta.yLabel || sig, font: { size: 10 }, standoff: 8 },
        zeroline: false,
        gridcolor: 'rgba(0,0,0,0.05)',
        tickfont: { size: 10 },
        showline: true, linecolor: 'rgba(0,0,0,0.1)',
        automargin: true,
        // Use preset range matching the replay charts; fall back to autorange
        ...(sigMeta.yRange
          ? { autorange: false, range: sigMeta.yRange }
          : { autorange: true }),
      };

      // Add a clean badge annotation in the top-right of each subplot
      layout.annotations.push({
        xref: 'paper',
        yref: 'paper',
        x: 0.99,
        y: y1 - 0.015,
        xanchor: 'right',
        yanchor: 'top',
        text: ` <b>${sigMeta.label || sig.toUpperCase()}</b> `,
        showarrow: false,
        font: {
          size: 9.5,
          color: sigMeta.color || '#374151'
        },
        bgcolor: 'rgba(255, 255, 255, 0.85)',
        bordercolor: 'rgba(0, 0, 0, 0.08)',
        borderwidth: 1,
        borderpad: 4
      });
    });

    // Shapes: baseline region, analysis region, t=0 line, and per-subplot borders
    const subplotBorders = domains.map(([y0, y1]) => ({
      type: 'rect',
      xref: 'paper', yref: 'paper',
      x0: 0, x1: 1,
      y0, y1,
      fillcolor: 'rgba(0,0,0,0)',
      line: { color: 'rgba(0,0,0,0.10)', width: 1 },
      layer: 'above',
    }));

    layout.shapes = [
      // Baseline region fill
      {
        type: 'rect',
        xref: 'x', yref: 'paper',
        x0: -_baseline, x1: 0,
        y0: 0, y1: 1,
        fillcolor: 'rgba(107,114,128,0.05)',
        line: { width: 0 }, layer: 'below',
      },
      // Analysis region fill
      {
        type: 'rect',
        xref: 'x', yref: 'paper',
        x0: 0, x1: _analysis,
        y0: 0, y1: 1,
        fillcolor: 'rgba(245,158,11,0.06)',
        line: { width: 0 }, layer: 'below',
      },
      // t=0 event line
      {
        type: 'line',
        xref: 'x', yref: 'paper',
        x0: 0, x1: 0,
        y0: 0, y1: 1,
        line: { color: '#C94444', width: 1.5, dash: 'dot' },
      },
      // Per-subplot border outlines
      ...subplotBorders,
    ];

    // Region label annotations at top of the figure
    const topDomain = domains[0][1];  // top of first subplot
    layout.annotations = [
      {
        x: -_baseline / 2, xref: 'x', yref: 'paper', y: topDomain + 0.015,
        text: 'Baseline Window', showarrow: false,
        font: { size: 9, color: '#9CA3AF' }, xanchor: 'center',
      },
      {
        x: _analysis / 2, xref: 'x', yref: 'paper', y: topDomain + 0.015,
        text: 'Analysis Window', showarrow: false,
        font: { size: 9, color: '#D97706' }, xanchor: 'center',
      },
      {
        x: 0.02, xref: 'x', yref: 'paper', y: topDomain - 0.02,
        text: eventLabel, showarrow: false,
        font: { size: 8, color: '#C94444' }, xanchor: 'left',
      },
      // t=0 marker below the bottom x-axis
      {
        x: 0, xref: 'x', yref: 'paper', y: -0.04,
        text: '<b>t=0</b>', showarrow: false,
        font: { size: 9, color: '#C94444' }, xanchor: 'center',
        yanchor: 'top',
      },
    ];

    return layout;
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

  /**
   * Compute subplot y-domains.
   * Returns [[y0,y1], ...] for each row, top-to-bottom order.
   * Plotly paper coords: 0=bottom, 1=top.
   */
  function _subplotDomains(n, gap) {
    const rowH = (1.0 - gap * (n - 1)) / n;
    const domains = [];
    for (let i = 0; i < n; i++) {
      // Row 0 → topmost subplot (domain near 1.0)
      const y1 = 1.0 - i * (rowH + gap);
      const y0 = y1 - rowH;
      domains.push([Math.max(0, y0), y1]);
    }
    return domains;
  }

  function _computeAvg(epochArrays) {
    // epochArrays: array of number arrays (same length, may have nulls)
    const n = epochArrays[0].length;
    const avg = new Array(n).fill(null);
    const upper = new Array(n).fill(null);
    const lower = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const vals = epochArrays.map(e => e[i]).filter(v => v != null);
      if (vals.length === 0) continue;
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      const sd = vals.length > 1
        ? Math.sqrt(vals.reduce((s, v) => s + (v - mean) ** 2, 0) / (vals.length - 1))
        : 0;
      avg[i] = mean;
      upper[i] = mean + sd;
      lower[i] = mean - sd;
    }
    return { avg, upper, lower };
  }

  function _rgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function clear() {
    const el = document.getElementById(_chartId);
    if (el) {
      if (el._plotlyInitialized) {
        Plotly.purge(_chartId);
        el._plotlyInitialized = false;
      }
    }
  }

  return { init, setWindows, setTerminology, setMode, setAggFilter, render, clear };

})();
