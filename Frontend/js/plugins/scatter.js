/**
 * scatter.js — Scatter Plugin
 *
 * 2D gaze position plot with fading trail.
 * Uses raw Canvas 2D API for the fading dot effect.
 * Used for: Gaze X/Y coordinates.
 */

(function () {
  const TRAIL_LENGTH = 200;    // number of dots in the trail
  const DOT_COLOR = [107, 93, 184]; // #6B5DB8 as RGB
  const LATEST_RADIUS = 5;
  const TRAIL_RADIUS = 2;

  class ScatterChart {
    constructor(container, streamMeta) {
      this.streamMeta = streamMeta;
      this.points = []; // { x, y, age }

      // Canvas
      this.canvas = document.createElement('canvas');
      this.canvas.className = 'scatter-canvas';
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      container.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d');

      // Coordinate range (auto-scaling)
      this.xMin = Infinity; this.xMax = -Infinity;
      this.yMin = Infinity; this.yMax = -Infinity;

      // Animation loop
      this._rafId = requestAnimationFrame(() => this._draw());

      // Handle resize
      this._resizeObserver = new ResizeObserver(() => this._fitCanvas());
      this._resizeObserver.observe(container);
      this._fitCanvas();
    }

    _fitCanvas() {
      const rect = this.canvas.parentElement.getBoundingClientRect();
      this.canvas.width = rect.width;
      this.canvas.height = rect.height;
    }

    pushSample(timestamp, data) {
      if (data.length < 2) return;
      const x = data[0];
      const y = data[1];

      // Update coordinate range with padding
      if (x < this.xMin) this.xMin = x;
      if (x > this.xMax) this.xMax = x;
      if (y < this.yMin) this.yMin = y;
      if (y > this.yMax) this.yMax = y;

      this.points.push({ x, y });

      // Trim trail
      while (this.points.length > TRAIL_LENGTH) {
        this.points.shift();
      }
    }

    _draw() {
      const ctx = this.ctx;
      const w = this.canvas.width;
      const h = this.canvas.height;

      ctx.clearRect(0, 0, w, h);

      if (this.points.length === 0) {
        ctx.fillStyle = '#7B8499';
        ctx.font = '13px Source Sans 3, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for gaze data…', w / 2, h / 2);
        this._rafId = requestAnimationFrame(() => this._draw());
        return;
      }

      const xRange = Math.max(this.xMax - this.xMin, 1);
      const yRange = Math.max(this.yMax - this.yMin, 1);
      const pad = 20;

      // Draw axis labels
      ctx.fillStyle = '#7B8499';
      ctx.font = '11px Source Sans 3, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(`X: ${this.xMin.toFixed(0)}`, pad, h - 6);
      ctx.textAlign = 'right';
      ctx.fillText(`${this.xMax.toFixed(0)}`, w - pad, h - 6);
      ctx.textAlign = 'left';
      ctx.fillText(`Y: ${this.yMin.toFixed(0)}`, 4, pad);
      ctx.fillText(`${this.yMax.toFixed(0)}`, 4, h - pad);

      // Draw points with fading trail
      const len = this.points.length;
      for (let i = 0; i < len; i++) {
        const p = this.points[i];
        const screenX = pad + ((p.x - this.xMin) / xRange) * (w - 2 * pad);
        const screenY = pad + ((p.y - this.yMin) / yRange) * (h - 2 * pad);

        const alpha = (i + 1) / len; // 0→1, newest = most opaque
        const isLatest = (i === len - 1);

        ctx.beginPath();
        ctx.arc(screenX, screenY, isLatest ? LATEST_RADIUS : TRAIL_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${DOT_COLOR[0]}, ${DOT_COLOR[1]}, ${DOT_COLOR[2]}, ${alpha * 0.8})`;
        ctx.fill();

        if (isLatest) {
          // Glow effect on latest point
          ctx.beginPath();
          ctx.arc(screenX, screenY, LATEST_RADIUS + 4, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${DOT_COLOR[0]}, ${DOT_COLOR[1]}, ${DOT_COLOR[2]}, 0.15)`;
          ctx.fill();
        }
      }

      this._rafId = requestAnimationFrame(() => this._draw());
    }

    resize() { this._fitCanvas(); }

    destroy() {
      if (this._rafId) cancelAnimationFrame(this._rafId);
      if (this._resizeObserver) this._resizeObserver.disconnect();
    }

    getElement() { return this.canvas; }
  }

  LabReplay.registerPlugin({
    id: 'scatter',
    name: 'Scatter',
    streamTypes: ['position', 'Gaze', 'gaze'],
    create(container, streamMeta) {
      return new ScatterChart(container, streamMeta);
    }
  });
})();
