/**
 * chart-hrv.js — HRV RMSSD Line Chart
 *
 * Source: lsl_unmapped_samples table (HRV_Live stream, type=HRV, ~0.2 Hz)
 * Unmapped sample values (float array, extracted from JSON by ReplaySample):
 *   data[0]  = mean_hr_bpm
 *   data[1]  = sdnn_ms
 *   data[2]  = rmssd_ms       ← THIS CHART
 *   data[3]  = pnn50_pct
 *   data[4]  = sd1_ms
 *   data[5]  = sd2_ms
 *   data[6]  = sd1_sd2_ratio
 *   data[7]  = poincare_area_ms2
 *   data[8]  = sampen
 *   data[9]  = lf_hf_ratio
 *   data[10] = valid_rr_fraction
 *   data[11] = n_rr_valid
 *
 * Y-axis: 40–100 ms, X-axis: MM:SS (same adaptive ticks as ChartHR)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartHRV = (function () {
  const BATCH_MS = 1000;       // HRV_Live fires ~every 5 s — no urgency
  const COLOR    = '#2ECC71';  // emerald green — parasympathetic/recovery

  // Channel index for rmssd_ms in the unmapped value array
  const CH_RMSSD = 2;

  // ── MM:SS tick generator (same pattern as ChartHR) ─────────────────────────
  function _makeTicks(maxElapsed) {
    let step;
    if      (maxElapsed <= 120)  step = 5;
    else if (maxElapsed <= 300)  step = 15;
    else if (maxElapsed <= 600)  step = 30;
    else if (maxElapsed <= 1200) step = 60;
    else if (maxElapsed <= 3600) step = 120;
    else                         step = 300;

    const vals = [];
    const text = [];
    const limit = Math.max(Math.ceil(maxElapsed / step) * step, 30);
    for (let s = 0; s <= limit; s += step) {
      vals.push(s);
      const m   = Math.floor(s / 60);
      const sec = s % 60;
      text.push(`${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`);
    }
    return { vals, text };
  }

  // ── Plotly layout ────────────────────────────────────────────────────────────
  function _layout(maxElapsed) {
    const ticks = _makeTicks(Math.max(maxElapsed, 30));
    return {
      paper_bgcolor: 'transparent',
      plot_bgcolor:  'transparent',
      font: {
        family: 'Source Sans 3, system-ui, sans-serif',
        size:   11,
        color:  '#4A5468',
      },
      margin:     { t: 8, r: 16, b: 36, l: 52, pad: 0 },
      showlegend: false,
      hovermode:  'x',
      xaxis: {
        type:        'linear',
        autorange:   false,
        range:       [0, Math.max(maxElapsed + 5, 30)],
        tickvals:    ticks.vals,
        ticktext:    ticks.text,
        tickfont:    { size: 10, color: '#7B8499' },
        showgrid:    true,
        gridcolor:   'rgba(0,0,0,0.06)',
        gridwidth:   1,
        zeroline:    false,
        linecolor:   'rgba(0,0,0,0.1)',
        linewidth:   1,
        showline:    true,
        title:       { text: '', standoff: 4 },
      },
      yaxis: {
        autorange:  false,
        range:      [40, 100],
        tickfont:   { size: 10, color: '#7B8499' },
        showgrid:   true,
        gridcolor:  'rgba(0,0,0,0.06)',
        gridwidth:  1,
        zeroline:   false,
        linecolor:  'rgba(0,0,0,0.1)',
        linewidth:  1,
        showline:   true,
        fixedrange: true,
        title: {
          text:     'ms',
          standoff: 8,
          font:     { size: 10, color: '#7B8499' },
        },
      },
    };
  }

  const _config = {
    displayModeBar: false,
    responsive:     true,
    scrollZoom:     false,
    doubleClick:    false,
  };

  // ── Factory ──────────────────────────────────────────────────────────────────

  /**
   * @param {HTMLElement} container  - the chart-body div
   * @param {Object}      card       - ChartCard result ({ el, header, body, updateLiveValue })
   * @param {Object}      descriptor - from StreamRegistry
   */
  function create(container, card, descriptor) {
    let lastElapsed = 0;
    let ready       = false;

    let batchX    = [];
    let batchY    = [];
    let batchTimer = null;

    // ── Initial trace ────────────────────────────────────────────────────────
    const initTrace = {
      x:    [],
      y:    [],
      type: 'scatter',
      mode: 'lines+markers',     // markers help — samples are sparse (~5 s apart)
      line: {
        color:    COLOR,
        width:    2,
        shape:    'linear',
        simplify: false,
      },
      marker: {
        color: COLOR,
        size:  5,
      },
      hovertemplate: '<b>%{y:.1f} ms</b><extra></extra>',
    };

    Plotly.newPlot(container, [initTrace], _layout(0), _config).then(() => {
      ready = true;
      Plotly.Plots.resize(container);
      if (batchX.length > 0) _flush();
    });

    // ── Batch flush ──────────────────────────────────────────────────────────
    function _flush() {
      if (!ready || batchX.length === 0) return;

      const x = batchX.slice();
      const y = batchY.slice();
      batchX = [];
      batchY = [];

      Plotly.extendTraces(container, { x: [x], y: [y] }, [0]);

      const ticks = _makeTicks(Math.max(lastElapsed, 30));
      Plotly.relayout(container, {
        'xaxis.range':    [0, Math.max(lastElapsed + 10, 30)],
        'xaxis.tickvals': ticks.vals,
        'xaxis.ticktext': ticks.text,
      });
    }

    function _scheduleBatch() {
      if (batchTimer) return;
      batchTimer = setTimeout(() => {
        batchTimer = null;
        _flush();
      }, BATCH_MS);
    }

    // ── Data ingestion ───────────────────────────────────────────────────────
    function pushSample(timestamp, data, elapsedS) {
      if (!Array.isArray(data)) return;

      // Extract RMSSD — prefer labeled search for robustness,
      // fall back to fixed index.
      let rmssd = null;

      // Format A: array of {value, label} objects (raw unmapped JSON forwarded)
      if (data[0] !== null && typeof data[0] === 'object' && 'label' in data[0]) {
        const entry = data.find(d => d.label === 'rmssd_ms');
        rmssd = entry ? Number(entry.value) : null;
      } else {
        // Format B: plain float array [mean_hr, sdnn, rmssd, ...]
        rmssd = Number(data[CH_RMSSD]);
      }

      if (rmssd === null || !Number.isFinite(rmssd)) return;
      // Sanity clamp — RMSSD rarely exceeds 150 ms or falls below 5 ms
      if (rmssd < 5 || rmssd > 200) return;

      const elapsed = elapsedS != null ? elapsedS : Math.max(0, timestamp);
      lastElapsed   = elapsed;

      batchX.push(elapsed);
      batchY.push(parseFloat(rmssd.toFixed(1)));

      if (card && card.updateLiveValue) {
        card.updateLiveValue(`${rmssd.toFixed(1)}`);
      }

      _scheduleBatch();
    }

    // ── Resize / destroy ─────────────────────────────────────────────────────
    function resize() {
      if (ready) Plotly.Plots.resize(container);
    }

    function destroy() {
      if (batchTimer) clearTimeout(batchTimer);
      if (ready) Plotly.purge(container);
    }

    return { pushSample, resize, destroy };
  }

  return { create };
})();
