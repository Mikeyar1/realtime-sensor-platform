/**
 * chart-hr.js — Heart Rate Line Chart
 *
 * Purpose-built for the polar_h10_heart_rate stream.
 * - Light theme (matches LabReplay design system)
 * - Fixed Y-axis: 60–120 BPM
 * - X-axis: elapsed time displayed as MM:SS
 * - Full session history from t=0 (no sliding window)
 * - Live BPM number updated in card header each sample
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartHR = (function () {
  const BATCH_MS = 250;   // flush to Plotly every 250ms (HR is 1 Hz — no rush)
  const COLOR    = '#C94444';   // --trace-hr

  // ── MM:SS tick generator ────────────────────────────────────────────────────
  function _makeTicks(maxElapsed) {
    // Adaptive tick step based on visible duration
    let step;
    if      (maxElapsed <= 120)  step = 5;    // ≤ 2 min  → every 5s
    else if (maxElapsed <= 300)  step = 15;   // ≤ 5 min  → every 15s
    else if (maxElapsed <= 600)  step = 30;   // ≤ 10 min → every 30s
    else if (maxElapsed <= 1200) step = 60;   // ≤ 20 min → every 1 min
    else if (maxElapsed <= 3600) step = 120;  // ≤ 60 min → every 2 min
    else                         step = 300;  // > 60 min → every 5 min

    const vals = [];
    const text = [];
    // Always cover at least the visible window (30s minimum)
    const limit = Math.max(Math.ceil(maxElapsed / step) * step, 30);
    for (let s = 0; s <= limit; s += step) {
      vals.push(s);
      const m   = Math.floor(s / 60);
      const sec = s % 60;
      text.push(`${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`);
    }
    return { vals, text };
  }


  // ── Base Plotly layout (light theme) ───────────────────────────────────────
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
      margin: { t: 8, r: 16, b: 36, l: 48, pad: 0 },
      showlegend: false,
      hovermode: 'x',
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
        range:      [60, 140],
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
          text:     'BPM',
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
    let firstTs              = null;
    let lastElapsed          = 0;
    let _lastRelayoutElapsed = -999;  // throttle axis relayout — only every 10 s
    let ready                = false;

    // Pending batch
    let batchX    = [];
    let batchY    = [];
    let batchTimer = null;

    // ── Initial trace ───────────────────────────────────────────────────────
    const initTrace = {
      x:    [],
      y:    [],
      type: 'scatter',
      mode: 'lines',
      line: {
        color:    COLOR,
        width:    2,
        shape:    'linear',
        simplify: false,
      },
      hovertemplate: '<b>%{y} BPM</b><extra></extra>',
    };

    Plotly.newPlot(container, [initTrace], _layout(0), _config).then(() => {
      ready = true;
      Plotly.Plots.resize(container);   // measure true container size after DOM settles
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

      // Relayout is expensive — only expand the X-axis when elapsed grows by ≥10 s.
      if (lastElapsed - _lastRelayoutElapsed >= 10) {
        _lastRelayoutElapsed = lastElapsed;
        const ticks = _makeTicks(Math.max(lastElapsed, 30));
        Plotly.relayout(container, {
          'xaxis.range':    [0, Math.max(lastElapsed + 10, 30)],
          'xaxis.tickvals': ticks.vals,
          'xaxis.ticktext': ticks.text,
        });
      }
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
      // Extract BPM — data is [bpm] (single-channel float array)
      let bpm = Array.isArray(data) ? data[0] : data;
      // Handle object format: { label, value }
      if (bpm !== null && typeof bpm === 'object' && 'value' in bpm) bpm = bpm.value;
      bpm = Number(bpm);
      if (!Number.isFinite(bpm)) return;

      // Use the backend's authoritative elapsed_s (shared across all streams).
      // Fall back to per-chart firstTs only when elapsed_s is unavailable.
      let elapsed;
      if (elapsedS != null) {
        elapsed = elapsedS;
      } else {
        if (firstTs === null) firstTs = timestamp;
        elapsed = Math.max(0, timestamp - firstTs);
      }
      lastElapsed = elapsed;

      batchX.push(elapsed);
      batchY.push(bpm);

      // Update live BPM in card header (DOM update — cheap)
      if (card && card.updateLiveValue) {
        card.updateLiveValue(Math.round(bpm));
      }

      _scheduleBatch();
    }

    // ── Resize / destroy ────────────────────────────────────────────────────
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
