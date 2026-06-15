/**
 * chart-pupil.js — Pupil Diameter Line Chart (Average)
 *
 * Source: neon_gaze table (NeonCom007b_Neon Gaze stream, type=Gaze, 200 Hz)
 * Channels (after mapped_values_start_index=6 header offset):
 *   data[2]  → pupil_diameter_left_millimeters
 *   data[9]  → pupil_diameter_right_millimeters
 *
 * Displays the mean(L, R) as a single clean trace.
 * Y-axis: 0–8 mm, X-axis: elapsed time MM:SS (same adaptive ticks as ChartHR)
 * Live value: shows current average diameter in card header.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartPupil = (function () {
  const BATCH_MS   = 100;         // flush every 100 ms — 200 Hz stream
  const MAX_POINTS = 2000;       // ~10 s at 200 Hz — SVG DOM stays tiny at this size
  const COLOR = '#7C6AE8';   // violet — pupil avg

  // Channel indices within the LSL data array (after 6-col DB header)
  const CH_LEFT = 2;   // pupil_diameter_left_millimeters
  const CH_RIGHT = 9;   // pupil_diameter_right_millimeters

  // ── MM:SS tick generator (same pattern as ChartHR) ─────────────────────────
  function _makeTicks(maxElapsed) {
    let step;
    if (maxElapsed <= 120) step = 5;
    else if (maxElapsed <= 300) step = 15;
    else if (maxElapsed <= 600) step = 30;
    else if (maxElapsed <= 1200) step = 60;
    else if (maxElapsed <= 3600) step = 120;
    else step = 300;

    const vals = [];
    const text = [];
    const limit = Math.max(Math.ceil(maxElapsed / step) * step, 30);
    for (let s = 0; s <= limit; s += step) {
      vals.push(s);
      const m = Math.floor(s / 60);
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
      plot_bgcolor: 'transparent',
      font: {
        family: 'Source Sans 3, system-ui, sans-serif',
        size: 11,
        color: '#4A5468',
      },
      margin: { t: 8, r: 16, b: 36, l: 48, pad: 0 },
      showlegend: false,
      hovermode: 'x',
      xaxis: {
        type: 'linear',
        autorange: false,
        range: [0, Math.max(maxElapsed + 5, 30)],
        tickvals: ticks.vals,
        ticktext: ticks.text,
        tickfont: { size: 10, color: '#7B8499' },
        showgrid: true,
        gridcolor: 'rgba(0,0,0,0.06)',
        gridwidth: 1,
        zeroline: false,
        linecolor: 'rgba(0,0,0,0.1)',
        linewidth: 1,
        showline: true,
        title: { text: '', standoff: 4 },
      },
      yaxis: {
        autorange: false,
        range: [0, 8],
        tickfont: { size: 10, color: '#7B8499' },
        showgrid: true,
        gridcolor: 'rgba(0,0,0,0.06)',
        gridwidth: 1,
        zeroline: false,
        linecolor: 'rgba(0,0,0,0.1)',
        linewidth: 1,
        showline: true,
        fixedrange: true,
        title: {
          text: 'mm',
          standoff: 8,
          font: { size: 10, color: '#7B8499' },
        },
      },
    };
  }

  const _config = {
    displayModeBar: false,
    responsive: true,
    scrollZoom: false,
    doubleClick: false,
  };

  // ── Factory ──────────────────────────────────────────────────────────────────

  /**
   * @param {HTMLElement} container  - the chart-body div
   * @param {Object}      card       - ChartCard result ({ el, header, body, updateLiveValue })
   * @param {Object}      descriptor - from StreamRegistry
   */
  function create(container, card, descriptor) {
    let lastElapsed         = 0;
    let _lastRelayoutElapsed = -999;  // throttle axis relayout — only every 10 s
    let ready = false;

    // Pending batch
    let batchX = [];
    let batchY = [];
    let batchTimer = null;

    // ── Initial trace ────────────────────────────────────────────────────────
    // scatter (SVG) with a tight 2000-point window keeps the DOM tiny (~10 s of data).
    // scattergl (WebGL) was causing context loss when 3 WebGL charts ran simultaneously.
    const initTrace = {
      x: [],
      y: [],
      type: 'scatter',
      mode: 'lines',
      line: {
        color: COLOR,
        width: 2,
        shape: 'linear',
        simplify: false,
      },
      hovertemplate: '<b>%{y:.2f} mm</b><extra></extra>',
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

      // MAX_POINTS keeps the WebGL buffer and JS heap bounded regardless of session length.
      Plotly.extendTraces(container, { x: [x], y: [y] }, [0], MAX_POINTS);

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
      if (!Array.isArray(data)) return;

      const left = Number(data[CH_LEFT]);
      const right = Number(data[CH_RIGHT]);

      // Require both eyes to be valid and in plausible range (0.5–10 mm)
      if (!Number.isFinite(left) || left < 0.5 || left > 10) return;
      if (!Number.isFinite(right) || right < 0.5 || right > 10) return;

      const avg = (left + right) / 2;
      const elapsed = elapsedS != null ? elapsedS : Math.max(0, timestamp);
      lastElapsed = elapsed;

      batchX.push(elapsed);
      batchY.push(parseFloat(avg.toFixed(3)));

      // Live value in card header
      if (card && card.updateLiveValue) {
        card.updateLiveValue(avg.toFixed(2));
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
