/**
 * chart-scatter2d.js — 2D Gaze Scatter Canvas
 *
 * Renders a real-time gaze trail on a proportional scene canvas.
 * X = scene_x_pixels (0–1920), Y = scene_y_pixels (0–1080).
 * Shows a fading trail of the last N gaze points.
 *
 * Used for: Neon Gaze X/Y position.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartScatter2D = (function () {
  const TRAIL_LEN   = 200;   // ~1 second of gaze at 200 Hz
  const SCENE_W     = 1920;
  const SCENE_H     = 1080;
  const POINT_R     = 4;
  const DOT_COLOR   = '#26c6da';
  const TRAIL_COLOR = '#26c6da';

  function create(container, descriptor) {
    const canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block;background:#0d1117;border-radius:4px;';
    container.appendChild(canvas);

    const trail = [];   // { x, y } in scene pixels
    let animId = null;
    let dirty  = false;

    // ── Size canvas to fill body ─────────────────────────────────────────────
    function _resize() {
      canvas.width  = container.offsetWidth  || 380;
      canvas.height = container.offsetHeight || 190;
      _draw();
    }

    const ro = new ResizeObserver(_resize);
    ro.observe(container);
    requestAnimationFrame(_resize);

    // ── Draw ─────────────────────────────────────────────────────────────────
    function _draw() {
      const ctx = canvas.getContext('2d');
      const cw  = canvas.width;
      const ch  = canvas.height;

      // Compute letterbox margins to preserve 16:9 scene aspect ratio
      const sceneAspect = SCENE_W / SCENE_H;
      const canvasAspect = cw / ch;
      let drawW, drawH, offX, offY;
      if (canvasAspect > sceneAspect) {
        drawH = ch;
        drawW = ch * sceneAspect;
        offX  = (cw - drawW) / 2;
        offY  = 0;
      } else {
        drawW = cw;
        drawH = cw / sceneAspect;
        offX  = 0;
        offY  = (ch - drawH) / 2;
      }

      // Background
      ctx.clearRect(0, 0, cw, ch);
      ctx.fillStyle = '#0d1117';
      ctx.fillRect(0, 0, cw, ch);

      // Scene border
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth   = 1;
      ctx.strokeRect(offX, offY, drawW, drawH);

      // Grid lines (thirds — rule of thirds overlay)
      ctx.setLineDash([3, 6]);
      ctx.strokeStyle = 'rgba(255,255,255,0.04)';
      for (let i = 1; i < 3; i++) {
        const gx = offX + drawW * i / 3;
        const gy = offY + drawH * i / 3;
        ctx.beginPath(); ctx.moveTo(gx, offY);      ctx.lineTo(gx, offY + drawH); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(offX, gy);      ctx.lineTo(offX + drawW, gy); ctx.stroke();
      }
      ctx.setLineDash([]);

      if (trail.length === 0) {
        // No-data label
        ctx.fillStyle = 'rgba(255,255,255,0.15)';
        ctx.font      = '11px "Source Sans 3", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for gaze data…', cw / 2, ch / 2);
        return;
      }

      // Helper: scene → canvas
      function toCanvas(sx, sy) {
        return {
          cx: offX + (sx / SCENE_W) * drawW,
          cy: offY + (sy / SCENE_H) * drawH,
        };
      }

      // Draw fading trail
      for (let i = 0; i < trail.length - 1; i++) {
        const t     = i / trail.length;          // 0 = oldest, 1 = newest
        const alpha = t * t * 0.6;               // quadratic fade
        const p0 = toCanvas(trail[i].x,   trail[i].y);
        const p1 = toCanvas(trail[i+1].x, trail[i+1].y);
        ctx.strokeStyle = `rgba(38,198,218,${alpha})`;
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.moveTo(p0.cx, p0.cy);
        ctx.lineTo(p1.cx, p1.cy);
        ctx.stroke();
      }

      // Draw current gaze point (bright dot)
      const last = trail[trail.length - 1];
      const { cx, cy } = toCanvas(last.x, last.y);
      ctx.shadowBlur  = 10;
      ctx.shadowColor = DOT_COLOR;
      ctx.fillStyle   = DOT_COLOR;
      ctx.beginPath();
      ctx.arc(cx, cy, POINT_R, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      dirty = false;
    }

    // ── Data ingestion ───────────────────────────────────────────────────────
    // data is a raw array: [x_pixels, y_pixels, pupil_left, ...]

    function pushSample(timestamp, data) {
      let x, y;

      if (Array.isArray(data)) {
        if (data[0] !== null && typeof data[0] === 'object' && 'value' in data[0]) {
          // JSON-wrapped
          const xObj = data.find(d => d.label && d.label.includes('x'));
          const yObj = data.find(d => d.label && d.label.includes('y'));
          x = xObj?.value;
          y = yObj?.value;
        } else {
          // Raw array: index 0 = x, index 1 = y
          x = data[0];
          y = data[1];
        }
      }

      if (!Number.isFinite(x) || !Number.isFinite(y)) return;

      trail.push({ x, y });
      while (trail.length > TRAIL_LEN) trail.shift();

      if (!dirty) {
        dirty = true;
        cancelAnimationFrame(animId);
        animId = requestAnimationFrame(_draw);
      }
    }

    function resize() { _resize(); }

    function destroy() {
      ro.disconnect();
      cancelAnimationFrame(animId);
    }

    return { pushSample, resize, destroy };
  }

  return { create };
})();
