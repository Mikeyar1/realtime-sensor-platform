/**
 * chart-line.js — Plotly Real-time Scrolling Line Chart
 *
 * Uses Plotly.extendTraces() for efficient real-time streaming.
 * Batches incoming samples every BATCH_MS to avoid per-sample redraws.
 * X-axis: elapsed time in seconds (displayed as MM:SS via ticktext).
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartLine = (function () {
  const BATCH_MS  = 100;  // flush to Plotly every 100 ms
  const MAX_PTS   = 2000; // max points kept in Plotly trace

  // Shared dark layout base — applied to every chart
  function _baseLayout(descriptor) {
    const yAxis = descriptor.yAxis || {};
    const yRange = (yAxis.min != null && yAxis.max != null)
      ? [yAxis.min, yAxis.max]
      : undefined;

    return {
      paper_bgcolor: 'transparent',
      plot_bgcolor:  'rgba(13,17,23,0.6)',
      font:  { family: 'Source Sans 3, sans-serif', size: 10, color: '#6b7280' },
      margin: { t: 6, r: 10, b: 34, l: 54, pad: 0 },
      showlegend: false,
      hovermode:  'x unified',
      xaxis: {
        type:        'linear',
        title:       { text: '', standoff: 4 },
        showgrid:    true,
        gridcolor:   'rgba(255,255,255,0.05)',
        gridwidth:   1,
        zeroline:    false,
        tickcolor:   'rgba(255,255,255,0.08)',
        tickfont:    { size: 9, color: '#6b7280' },
        ticksuffix:  ' s',
        autorange:   false,         // we drive the range manually
        range:       [0, descriptor.windowSeconds || 30],
      },
      yaxis: {
        title:      { text: yAxis.label || '', standoff: 6, font: { size: 9 } },
        showgrid:   true,
        gridcolor:  'rgba(255,255,255,0.05)',
        gridwidth:  1,
        zeroline:   false,
        tickcolor:  'rgba(255,255,255,0.08)',
        tickfont:   { size: 9, color: '#6b7280' },
        autorange:  !yRange,
        range:      yRange,
        fixedrange: true,           // prevent user zooming y
      },
    };
  }

  const _config = {
    displayModeBar:   false,
    responsive:       true,
    scrollZoom:       false,
    doubleClick:      false,
  };

  // ── Factory ──────────────────────────────────────────────────────────────

  function create(container, descriptor) {
    const windowSec   = descriptor.windowSeconds || 30;
    const channels    = descriptor.channels || [{ label: 'Value', unit: '' }];
    const color       = descriptor.color || '#00e5ff';

    let activeChannel = descriptor.defaultChannel ?? 0;
    if (descriptor.deriveMagnitude && descriptor.defaultChannel === null) {
      activeChannel = 'magnitude';
    }

    // Ring buffers for all channels (kept separate for channel switching)
    const buffers = channels.map(() => ({ times: [], values: [] }));
    let firstTs   = null;
    let lastElapsed = 0;

    // Pending batch for the active channel
    let batchX   = [];
    let batchY   = [];
    let batchTimer = null;
    let ready    = false;

    // ── Init Plotly ──────────────────────────────────────────────────────────

    const initTrace = {
      x:    [],
      y:    [],
      type: 'scattergl',   // WebGL-accelerated scatter — fast for high-Hz data
      mode: 'lines',
      line: { color, width: 1.5, simplify: false },
      hovertemplate: '%{y:.1f}<extra></extra>',
    };

    const layout = _baseLayout(descriptor);

    Plotly.newPlot(container, [initTrace], layout, _config).then(() => {
      ready = true;
      // Flush anything that accumulated before init
      if (batchX.length > 0) _flush();
    });

    // ── Batch flush ──────────────────────────────────────────────────────────

    let _lastXMin = -999;
    let _lastXMax = -999;

    function _flush() {
      if (!ready || batchX.length === 0) return;

      const x = batchX.slice();
      const y = batchY.slice();
      batchX = [];
      batchY = [];

      // Extend trace — Plotly removes old points beyond MAX_PTS automatically
      Plotly.extendTraces(container, { x: [x], y: [y] }, [0], MAX_PTS);

      // Only relayout when the visible window shifts by ≥5 s — avoids constant layout recalcs
      const xMax = lastElapsed;
      const xMin = windowSec ? Math.max(0, xMax - windowSec) : 0;
      if (Math.abs(xMax - _lastXMax) >= 5 || Math.abs(xMin - _lastXMin) >= 5) {
        _lastXMin = xMin;
        _lastXMax = xMax;
        Plotly.relayout(container, { 'xaxis.range': [xMin, xMax] });
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

    function _extractValues(data, chans) {
      if (!Array.isArray(data)) return [data];
      if (data.length > 0 && data[0] !== null && typeof data[0] === 'object' && 'value' in data[0]) {
        return chans.map(ch => {
          const m = data.find(d => d.label === ch.key);
          return m ? m.value : null;
        });
      }
      return data;
    }

    function pushSample(timestamp, data) {
      if (firstTs === null) firstTs = timestamp;
      const elapsed = Math.max(0, timestamp - firstTs);
      lastElapsed   = elapsed;

      const vals = _extractValues(data, channels);

      // Store ALL channels in buffers
      for (let i = 0; i < channels.length && i < vals.length; i++) {
        const v = Number(vals[i]);
        if (!Number.isFinite(v)) continue;
        buffers[i].times.push(elapsed);
        buffers[i].values.push(v);
        // Trim to MAX_PTS
        if (buffers[i].times.length > MAX_PTS * 1.2) {
          buffers[i].times.splice(0, Math.floor(MAX_PTS * 0.2));
          buffers[i].values.splice(0, Math.floor(MAX_PTS * 0.2));
        }
      }

      // Only batch the active channel
      let activeVal;
      if (activeChannel === 'magnitude') {
        let sumSq = 0;
        for (let i = 0; i < channels.length && i < vals.length; i++) {
          const v = Number(vals[i]);
          if (Number.isFinite(v)) sumSq += v * v;
        }
        activeVal = Math.sqrt(sumSq);
      } else {
        activeVal = Number(vals[activeChannel]);
      }

      if (Number.isFinite(activeVal)) {
        batchX.push(elapsed);
        batchY.push(activeVal);
        _scheduleBatch();
      }
    }

    // ── Channel switch ───────────────────────────────────────────────────────

    function setChannel(idx) {
      activeChannel = idx;
      if (!ready) return;

      // Rebuild trace from buffer for the new channel
      let xs, ys;
      if (idx === 'magnitude') {
        const len = buffers[0].times.length;
        xs = buffers[0].times.slice();
        ys = new Array(len);
        for (let j = 0; j < len; j++) {
          let sumSq = 0;
          for (const b of buffers) sumSq += (b.values[j] || 0) ** 2;
          ys[j] = Math.sqrt(sumSq);
        }
      } else {
        const buf = buffers[idx] || buffers[0];
        xs = buf.times.slice();
        ys = buf.values.slice();
      }

      // Replace trace entirely
      Plotly.react(container, [{ ...initTrace, x: xs, y: ys }], _baseLayout(descriptor), _config);
      batchX = []; batchY = [];
    }

    // ── Cleanup ──────────────────────────────────────────────────────────────

    function resize() {
      if (ready) Plotly.Plots.resize(container);
    }

    function destroy() {
      if (batchTimer) clearTimeout(batchTimer);
      if (ready) Plotly.purge(container);
    }

    return { pushSample, resize, destroy, setChannel };
  }

  return { create };
})();
