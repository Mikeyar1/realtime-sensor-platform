/**
 * chart-numeric.js — Big Number Gauge
 * Uses a styled DOM display + Canvas2D sparkline.
 * Simple and reliable — no external library needed.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartNumeric = (function () {
  const MAX_SPARK = 80;

  function create(container, descriptor) {
    const nd    = descriptor.numericDisplay || {};
    const zones = nd.zones || [];
    const unit  = nd.unit || descriptor.channels?.[0]?.unit || '';
    const color = descriptor.color || '#ff4444';

    // Absolute-fill wrapper
    const wrap = document.createElement('div');
    wrap.style.cssText = [
      'position:absolute;inset:0',
      'display:flex',
      'flex-direction:column',
      'align-items:center',
      'justify-content:center',
      'gap:2px',
      'padding:6px 12px',
      'box-sizing:border-box',
      'user-select:none',
    ].join(';');

    // Big number
    const valueEl = document.createElement('div');
    valueEl.style.cssText = [
      `font-family:'Source Sans 3',system-ui,sans-serif`,
      'font-size:4.5rem',
      'font-weight:800',
      'line-height:1',
      'letter-spacing:-0.03em',
      `color:${color}`,
      'transition:color 0.5s ease',
      'text-align:center',
    ].join(';');
    valueEl.textContent = '--';
    wrap.appendChild(valueEl);

    // Unit + zone row
    const metaRow = document.createElement('div');
    metaRow.style.cssText = 'display:flex;align-items:center;gap:8px;';

    const unitEl = document.createElement('span');
    unitEl.style.cssText = [
      'font-size:0.7rem',
      'font-weight:700',
      'letter-spacing:0.14em',
      'text-transform:uppercase',
      'color:rgba(255,255,255,0.3)',
    ].join(';');
    unitEl.textContent = unit;

    const zoneEl = document.createElement('span');
    zoneEl.style.cssText = [
      'font-size:0.65rem',
      'font-weight:600',
      'letter-spacing:0.1em',
      'text-transform:uppercase',
      'min-height:12px',
      'padding:1px 5px',
      'border-radius:3px',
      'background:rgba(255,255,255,0.06)',
    ].join(';');

    metaRow.appendChild(unitEl);
    metaRow.appendChild(zoneEl);
    wrap.appendChild(metaRow);

    // Sparkline canvas
    const spark = document.createElement('canvas');
    spark.width  = 180;
    spark.height = 38;
    spark.style.cssText = 'width:180px;height:38px;flex-shrink:0;margin-top:4px;opacity:0.75;';
    wrap.appendChild(spark);

    container.style.position = 'relative';
    container.appendChild(wrap);

    const vals = [];

    function _drawSpark() {
      const ctx = spark.getContext('2d');
      const W = spark.width, H = spark.height;
      ctx.clearRect(0, 0, W, H);
      if (vals.length < 2) return;

      let lo = Infinity, hi = -Infinity;
      for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
      if (lo === hi) { lo -= 1; hi += 1; }

      // Fill gradient under line
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, color + '40');
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad;
      ctx.beginPath();
      vals.forEach((v, i) => {
        const x = (i / (vals.length - 1)) * W;
        const y = H - ((v - lo) / (hi - lo)) * (H - 4) - 2;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
      ctx.fill();

      // Line
      ctx.strokeStyle = color;
      ctx.lineWidth   = 1.8;
      ctx.lineJoin    = 'round';
      ctx.beginPath();
      vals.forEach((v, i) => {
        const x = (i / (vals.length - 1)) * W;
        const y = H - ((v - lo) / (hi - lo)) * (H - 4) - 2;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function pushSample(_ts, data) {
      let val = Array.isArray(data) ? data[0] : data;
      if (val !== null && typeof val === 'object' && 'value' in val) val = val.value;
      val = Number(val);
      if (!Number.isFinite(val)) return;

      vals.push(val);
      while (vals.length > MAX_SPARK) vals.shift();

      valueEl.textContent = Math.round(val);

      for (const z of zones) {
        if (val <= z.max) {
          valueEl.style.color = z.color;
          zoneEl.textContent  = z.label || '';
          zoneEl.style.color  = z.color;
          zoneEl.style.borderColor = z.color + '40';
          break;
        }
      }

      _drawSpark();
    }

    function resize()  {}
    function destroy() {}
    return { pushSample, resize, destroy };
  }

  return { create };
})();
