/**
 * chart-motion.js — Motion Intensity Strip Chart (Canvas)
 *
 * Fusion chart: consumes two streams simultaneously via pushTagged():
 *   tag='acc'  ← PhoneSensor_Linear Acceleration  [ax, ay, az]  m/s²
 *   tag='gyro' ← PhoneSensor_Gyroscope            [gx, gy, gz]  rad/s
 *
 * Formula (per gyro sample, fused with latest acc):
 *   acc_mag   = √(ax² + ay² + az²)
 *   gyro_mag  = √(gx² + gy² + gz²)
 *   intensity = 0.4 × (acc_mag / ACC_MAX) + 0.6 × (gyro_mag / GYRO_MAX)  → [0..1]
 *
 * Y-axis: numeric [0–1] with 0.25 ticks.
 * X-axis: MM:SS timeline below the canvas strip.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartMotion = (function () {

  const ACC_WEIGHT  = 0.4;
  const GYRO_WEIGHT = 0.6;
  const ACC_MAX     = 9.0;    // m/s²
  const GYRO_MAX    = 3.0;    // rad/s
  const RENDER_MS   = 50;     // one canvas column per 50 ms

  // Layout constants
  const YAXIS_W  = 44;    // px — left column for Y-axis labels + title
  const XAXIS_H  = 26;    // px — bottom row for timeline
  const VPAD     = 8;     // px — vertical inset so labels at 0 and 1.0 stay inside the card

  // Y-axis ticks
  const Y_TICKS = [0, 0.25, 0.5, 0.75, 1.0];

  // ── Colour mapping ─────────────────────────────────────────────────────────────
  function _color(v) {
    v = Math.max(0, Math.min(1, v));
    const h = 128 * (1 - v);
    const s = 80 + 10 * v;
    const l = 42 + (v < 0.5 ? 8 * v : 8 * (1 - v));
    return `hsl(${h.toFixed(0)},${s.toFixed(0)}%,${l.toFixed(0)}%)`;
  }

  function _gradient(ctx, intensity, barH, canvasH) {
    const grad = ctx.createLinearGradient(0, canvasH, 0, canvasH - barH);
    grad.addColorStop(0, 'rgba(255,255,255,0.0)');
    grad.addColorStop(1, _color(intensity));
    return grad;
  }

  // ── X-axis helpers ────────────────────────────────────────────────────────────
  function _fmtTime(s) {
    const m   = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  }

  function _xTickStep(maxElapsed) {
    if      (maxElapsed <= 60)   return 10;
    else if (maxElapsed <= 120)  return 15;
    else if (maxElapsed <= 300)  return 30;
    else if (maxElapsed <= 600)  return 60;
    else if (maxElapsed <= 1200) return 120;
    else                         return 300;
  }

  // ── Factory ───────────────────────────────────────────────────────────────────

  function create(container, card, descriptor) {
    let _latestAccMag  = null;
    let _latestGyroMag = null;
    let _pending = [];
    let _timer   = null;
    let _elapsed = 0;

    let _outerWrap  = null;
    let _chartArea  = null;
    let _canvas     = null;
    let _ctx        = null;
    let _xAxisEl    = null;
    let _w          = 0;
    let _h          = 0;
    let _col        = 0;           // current write column; fills 0→_w-1, then scrolls
    let _dataBuffer = [];          // circular buffer: [{intensity, elapsed}] per pixel column
    let _ready      = false;

    // ── DOM ──────────────────────────────────────────────────────────────────────
    container.style.padding  = '0';
    container.style.position = 'relative';
    // Keep overflow:hidden (card clips at border-radius) — we use VPAD instead

    // Root flex column
    _outerWrap = document.createElement('div');
    _outerWrap.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;';

    // ── Strip row (canvas + Y-axis overlay) ───────────────────────────────────
    _chartArea = document.createElement('div');
    _chartArea.style.cssText = [
      'position:relative;flex:1;min-height:0;',
      `padding:${VPAD}px 0 ${VPAD}px ${YAXIS_W}px;`,  // inset so edge labels stay inside card
    ].join('');

    _canvas = document.createElement('canvas');
    _canvas.style.cssText = 'display:block;width:100%;height:100%;';

    // ── Y-axis (absolutely positioned over the left column) ──────────────────
    const _yAxis = document.createElement('div');
    _yAxis.style.cssText = [
      `position:absolute;top:0;left:0;bottom:0;width:${YAXIS_W}px;`,
      'pointer-events:none;',
    ].join('');

    // The canvas occupies [VPAD, height-VPAD] vertically.
    // Map tick value → CSS top% inside the _yAxis div (which spans the full flex height).
    // We need to account for the top/bottom VPAD so labels align with canvas rows.
    Y_TICKS.forEach(val => {
      // Raw % in the canvas area
      const rawPct = (1 - val) * 100;  // 1.0 → top of canvas, 0 → bottom

      // Translate into % of the full _yAxis height (including top/bottom VPAD)
      // fullH  = chartAreaH (unknown until paint, but we use CSS calc)
      // labelY = VPAD + rawPct/100 * (fullH - 2*VPAD)
      // labelY / fullH  →  not easily expressible in pure CSS % without calc
      // Simpler: use CSS calc with custom properties, OR just clamp edge ticks to pixel values.

      const lbl = document.createElement('span');
      lbl.textContent = val.toFixed(2);

      const baseStyle =
        `position:absolute;right:5px;` +
        `font:600 8px/1 'Source Sans 3',system-ui;color:rgba(0,0,0,0.45);white-space:nowrap;`;

      if (val === 1.0) {
        // Top of canvas = VPAD px from the top of _yAxis
        lbl.style.cssText = baseStyle + `top:${VPAD}px;transform:translateY(-50%);`;
      } else if (val === 0) {
        // Bottom of canvas = VPAD px from the bottom of _yAxis
        lbl.style.cssText = baseStyle + `bottom:${VPAD}px;transform:translateY(50%);`;
      } else {
        // Middle ticks: VPAD + rawPct/100 * (100% - 2*VPAD px)
        lbl.style.cssText =
          baseStyle +
          `top:calc(${VPAD}px + ${rawPct}% - ${rawPct / 100 * VPAD * 2}px);` +
          `transform:translateY(-50%);`;
      }

      _yAxis.appendChild(lbl);

      // Horizontal gridline for mid ticks — position within the canvas inset area
      if (val > 0 && val < 1) {
        const line = document.createElement('div');
        // Same Y as the label but extending right into the canvas column
        const topExpr = `calc(${VPAD}px + ${rawPct}% - ${rawPct / 100 * VPAD * 2}px)`;
        line.style.cssText =
          `position:absolute;left:${YAXIS_W}px;right:0;` +
          `top:${topExpr};height:1px;` +
          `background:rgba(0,0,0,0.07);pointer-events:none;`;
        _chartArea.appendChild(line);
      }
    });

    // Rotated "Intensity" title using writing-mode (reliable centering)
    const _yTitleWrap = document.createElement('div');
    _yTitleWrap.style.cssText =
      `position:absolute;top:0;bottom:0;left:0;width:14px;` +
      `display:flex;align-items:center;justify-content:center;pointer-events:none;`;
    const _yTitle = document.createElement('span');
    _yTitle.textContent = 'Intensity';
    _yTitle.style.cssText =
      `writing-mode:vertical-rl;transform:rotate(180deg);` +
      `font:500 8px/1 'Source Sans 3',system-ui;` +
      `color:rgba(0,0,0,0.28);letter-spacing:.5px;text-transform:uppercase;white-space:nowrap;`;
    _yTitleWrap.appendChild(_yTitle);
    _yAxis.appendChild(_yTitleWrap);

    // Assemble strip row
    _chartArea.appendChild(_canvas);
    _chartArea.appendChild(_yAxis);

    // ── Hover tooltip overlay ─────────────────────────────────────────────────
    const _tooltip = document.createElement('div');
    _tooltip.style.cssText = [
      'position:absolute;pointer-events:none;display:none;',
      'background:rgba(30,30,30,0.82);color:#fff;',
      'font:600 10px/1 \'Source Sans 3\',system-ui;',
      'padding:4px 7px;border-radius:4px;white-space:nowrap;',
      'transform:translate(-50%,-120%);z-index:10;',
    ].join('');
    _chartArea.appendChild(_tooltip);

    _canvas.style.cursor = 'crosshair';
    _canvas.addEventListener('mousemove', e => {
      if (!_ready || !_dataBuffer.length) return;
      const rect  = _canvas.getBoundingClientRect();
      const scaleX = _w / rect.width;

      // Which pixel column is the mouse over?
      const canvasX = Math.round((e.clientX - rect.left) * scaleX);
      const idx     = Math.max(0, Math.min(_dataBuffer.length - 1, canvasX));
      const entry   = _dataBuffer[idx];

      // Build tooltip: intensity value + time
      const label = entry
        ? `${entry.intensity.toFixed(2)}  ·  ${_fmtTime(entry.elapsed)}`
        : `—`;

      // Position relative to _chartArea
      const areaRect     = _chartArea.getBoundingClientRect();
      const xInChartArea = e.clientX - areaRect.left;
      const yInChartArea = e.clientY - areaRect.top;

      _tooltip.textContent   = label;
      _tooltip.style.left    = `${xInChartArea}px`;
      _tooltip.style.top     = `${yInChartArea}px`;
      _tooltip.style.display = 'block';
    });
    _canvas.addEventListener('mouseleave', () => { _tooltip.style.display = 'none'; });

    // ── X-axis row ────────────────────────────────────────────────────────────
    _xAxisEl = document.createElement('div');
    _xAxisEl.style.cssText =
      `height:${XAXIS_H}px;position:relative;flex-shrink:0;` +
      `border-top:1px solid rgba(0,0,0,0.08);`;

    // Inner container offset to align exactly with the canvas left/right edges
    const _xTickContainer = document.createElement('div');
    _xTickContainer.style.cssText =
      `position:absolute;left:${YAXIS_W}px;right:0;top:0;bottom:0;`;
    _xAxisEl.appendChild(_xTickContainer);
    _renderXAxis(0);

    _outerWrap.appendChild(_chartArea);
    _outerWrap.appendChild(_xAxisEl);
    container.appendChild(_outerWrap);

    // ── Canvas init ───────────────────────────────────────────────────────────
    function _initCanvas() {
      const rect = _canvas.getBoundingClientRect();
      _w = Math.max(Math.round(rect.width)  || 460, 50);
      _h = Math.max(Math.round(rect.height) || 80,  20);
      _canvas.width  = _w;
      _canvas.height = _h;
      _ctx = _canvas.getContext('2d');
      _ctx.fillStyle = '#ffffff';
      _ctx.fillRect(0, 0, _w, _h);
      _ready = true;
    }
    setTimeout(_initCanvas, 80);

    // ── X-axis render ─────────────────────────────────────────────────────────
    // Renders into _xTickContainer (aligned with canvas, not the full row width)
    function _renderXAxis(maxElapsed) {
      if (!_xAxisEl) return;
      const container = _xAxisEl.querySelector('div');  // _xTickContainer
      if (!container) return;
      const step  = _xTickStep(maxElapsed || 60);
      const limit = Math.max(Math.ceil((maxElapsed || 60) / step) * step, 60);

      let html = '';
      for (let t = 0; t <= limit; t += step) {
        const pct = limit > 0 ? (t / limit) * 100 : 0;
        // Clamp 0% label so it doesn't overflow left; clamp 100% so it doesn't overflow right
        const align = t === 0 ? 'left:0%;transform:none;'
                    : t === limit ? 'right:0%;transform:none;left:auto;'
                    : `left:${pct}%;transform:translateX(-50%);`;
        html +=
          `<div style="position:absolute;left:${pct}%;top:0;width:1px;height:4px;` +
          `background:rgba(0,0,0,0.18);"></div>` +
          `<span style="position:absolute;${align}top:6px;` +
          `font:400 8px/1 'Source Sans 3',system-ui;color:rgba(0,0,0,0.38);` +
          `white-space:nowrap;">${_fmtTime(t)}</span>`;
      }
      container.innerHTML = html;
    }

    // ── Canvas drawing ────────────────────────────────────────────────────────
    function _drawColumn(intensity) {
      if (!_ready) return;

      if (_col < _w) {
        // ── Fill phase: advance left → right (matches Plotly chart direction) ──
        _ctx.fillStyle = '#ffffff';
        _ctx.fillRect(_col, 0, 1, _h);
        if (intensity > 0.005) {
          const barH = Math.round(Math.min(1, intensity) * _h);
          _ctx.fillStyle = _gradient(_ctx, intensity, barH, _h);
          _ctx.fillRect(_col, _h - barH, 1, barH);
        }
        _col++;
      } else {
        // ── Scroll phase: shift everything left, draw newest at right edge ──
        _ctx.drawImage(_canvas, -1, 0);
        _ctx.fillStyle = '#ffffff';
        _ctx.fillRect(_w - 1, 0, 1, _h);
        if (intensity > 0.005) {
          const barH = Math.round(Math.min(1, intensity) * _h);
          _ctx.fillStyle = _gradient(_ctx, intensity, barH, _h);
          _ctx.fillRect(_w - 1, _h - barH, 1, barH);
        }
      }
    }

    function _flush() {
      if (!_pending.length) return;
      const avg = _pending.reduce((s, v) => s + v, 0) / _pending.length;
      _pending = [];

      // Maintain data buffer so hover can look up intensity + time per pixel column
      const entry = { intensity: avg, elapsed: _elapsed };
      if (_col < _w) {
        _dataBuffer[_col] = entry;          // fill phase
      } else {
        _dataBuffer.shift();                // scroll phase — drop oldest
        _dataBuffer.push(entry);
      }

      _drawColumn(avg);
      if (card && card.updateLiveValue) {
        card.updateLiveValue(`${Math.round(avg * 100)}%`);
      }
    }

    function _scheduleBatch() {
      if (_timer) return;
      _timer = setTimeout(() => { _timer = null; _flush(); }, RENDER_MS);
    }

    // ── Intensity ─────────────────────────────────────────────────────────────
    function _compute() {
      if (_latestAccMag === null || _latestGyroMag === null) return null;
      return ACC_WEIGHT  * Math.min(1, _latestAccMag  / ACC_MAX) +
             GYRO_WEIGHT * Math.min(1, _latestGyroMag / GYRO_MAX);
    }

    // ── Tagged push ───────────────────────────────────────────────────────────
    function pushTagged(tag, timestamp, data, elapsedS) {
      if (!Array.isArray(data) || data.length < 3) return;
      const x = Number(data[0]), y = Number(data[1]), z = Number(data[2]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
      const mag = Math.sqrt(x * x + y * y + z * z);

      if (tag === 'acc') {
        _latestAccMag = mag;
      } else if (tag === 'gyro') {
        _latestGyroMag = mag;
        if (elapsedS != null) {
          _elapsed = elapsedS;
          if (Math.floor(_elapsed) % 5 === 0) _renderXAxis(_elapsed);
        }
        const intensity = _compute();
        if (intensity !== null) { _pending.push(intensity); _scheduleBatch(); }
      }
    }

    function pushSample() {}

    // ── Resize ────────────────────────────────────────────────────────────────
    function resize() {
      if (!_canvas || !_ready) return;
      const rect = _canvas.getBoundingClientRect();
      const nw = Math.max(Math.round(rect.width)  || 460, 50);
      const nh = Math.max(Math.round(rect.height) || 80,  20);
      if (nw === _w && nh === _h) return;
      const tmp = document.createElement('canvas');
      tmp.width = _w; tmp.height = _h;
      tmp.getContext('2d').drawImage(_canvas, 0, 0);
      _w = nw; _h = nh;
      _canvas.width = nw; _canvas.height = nh;
      _ctx.fillStyle = '#ffffff';
      _ctx.fillRect(0, 0, nw, nh);
      _ctx.drawImage(tmp, 0, 0);
      // After resize, existing content covers the left portion; continue scrolling
      _col = _w;
    }

    // ── Destroy ───────────────────────────────────────────────────────────────
    function destroy() {
      if (_timer) clearTimeout(_timer);
      _outerWrap?.remove();
      _canvas = null; _ctx = null; _ready = false;
    }

    return { pushSample, pushTagged, resize, destroy };
  }

  return { create };
})();
