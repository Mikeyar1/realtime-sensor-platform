/**
 * chart-gaze.js — Gaze Position Scatterplot
 *
 * Source: neon_gaze table (NeonCom007b_Neon Gaze stream, type=Gaze, 200 Hz)
 * Channels (after mapped_values_start_index=6 header offset):
 *   data[0]  → scene_x_pixels   (0 – ~1600)
 *   data[1]  → scene_y_pixels   (0 – ~1200, 0 = top of scene)
 *
 * Accumulates ALL gaze points for the session (replay context).
 * Uses scattergl (WebGL) so 20k+ points stay fast.
 * Y-axis is inverted — screen origin (0,0) is top-left.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartGaze = (function () {
  const BATCH_MS   = 150;      // flush every 150 ms
  const MAX_POINTS = 12000;    // ~60 s at 200 Hz — matches pupil chart window
  const SCENE_W = 1600;
  const SCENE_H = 1200;
  const POINT_COLOR = 'rgba(75,156,247,0.25)';   // semi-transparent blue

  // ── Plotly layout ────────────────────────────────────────────────────────────
  function _layout() {
    return {
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'rgba(0,0,0,0.03)',
      font: {
        family: 'Source Sans 3, system-ui, sans-serif',
        size: 11,
        color: '#4A5468',
      },
      margin: { t: 8, r: 16, b: 36, l: 48, pad: 0 },
      showlegend: false,
      hovermode: false,   // hoverinfo off for performance at high density
      xaxis: {
        type: 'linear',
        autorange: false,
        range: [0, SCENE_W],
        tickfont: { size: 10, color: '#7B8499' },
        showgrid: true,
        gridcolor: 'rgba(0,0,0,0.06)',
        zeroline: false,
        linecolor: 'rgba(0,0,0,0.1)',
        linewidth: 1,
        showline: true,
        title: {
          text: 'X (px)',
          standoff: 4,
          font: { size: 10, color: '#7B8499' },
        },
      },
      yaxis: {
        type: 'linear',
        autorange: false,
        range: [SCENE_H, 0],   // inverted: 0 at top (screen coordinates)
        tickfont: { size: 10, color: '#7B8499' },
        showgrid: true,
        gridcolor: 'rgba(0,0,0,0.06)',
        zeroline: false,
        linecolor: 'rgba(0,0,0,0.1)',
        linewidth: 1,
        showline: true,
        fixedrange: true,
        title: {
          text: 'Y (px)',
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
    let ready = false;
    let totalPts = 0;

    // Pending batch
    let batchX = [];
    let batchY = [];
    let batchTimer = null;

    // ── Initial trace ────────────────────────────────────────────────────────
    const initTrace = {
      x: [],
      y: [],
      type: 'scattergl',   // WebGL — handles 20k+ points comfortably
      mode: 'markers',
      marker: {
        color: POINT_COLOR,
        size: 3,
        line: { width: 0 },
      },
      hoverinfo: 'none',
    };

    Plotly.newPlot(container, [initTrace], _layout(), _config).then(() => {
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

      Plotly.extendTraces(container, { x: [x], y: [y] }, [0], MAX_POINTS);
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

      const x = Number(data[0]);   // scene_x_pixels
      const y = Number(data[1]);   // scene_y_pixels

      // Drop invalid / off-screen samples
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      if (x < 0 || x > SCENE_W || y < 0 || y > SCENE_H) return;

      batchX.push(Math.round(x));
      batchY.push(Math.round(y));
      totalPts++;

      // Live counter in card header
      if (card && card.updateLiveValue) {
        card.updateLiveValue(`${totalPts.toLocaleString()} pts`);
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
