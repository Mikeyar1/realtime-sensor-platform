/**
 * gauge.js — Gauge Plugin
 *
 * Big number display + mini sparkline for slow-updating metrics.
 * Used for: Heart Rate, HRV, SpO2, Skin Temperature, etc.
 */

(function () {
  const SPARKLINE_POINTS = 60; // last 60 values
  const TRACE_COLOR = '#F87171';

  class GaugeChart {
    constructor(container, streamMeta) {
      this.streamMeta = streamMeta;
      this.values = [];
      this.times = [];
      this.unit = (streamMeta.channels && streamMeta.channels[0]?.unit) || 'bpm';

      // Build gauge DOM
      this.el = document.createElement('div');
      this.el.className = 'gauge-container';

      // Big number
      const numCol = document.createElement('div');
      numCol.style.textAlign = 'center';

      this.valueEl = document.createElement('div');
      this.valueEl.className = 'gauge-value';
      this.valueEl.style.color = TRACE_COLOR;
      this.valueEl.textContent = '--';
      numCol.appendChild(this.valueEl);

      const unitLabel = document.createElement('div');
      unitLabel.className = 'gauge-unit-label';
      unitLabel.textContent = this.unit;
      numCol.appendChild(unitLabel);

      this.el.appendChild(numCol);

      // Sparkline canvas
      this.canvas = document.createElement('canvas');
      this.canvas.className = 'gauge-sparkline';
      this.el.appendChild(this.canvas);

      container.appendChild(this.el);

      // Create mini chart
      this.chart = new Chart(this.canvas, {
        type: 'line',
        data: {
          datasets: [{
            data: [],
            borderColor: TRACE_COLOR,
            backgroundColor: TRACE_COLOR + '20',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.3,
            fill: true,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: {
            x: { display: false, type: 'linear' },
            y: { display: false },
          },
        },
      });
    }

    pushSample(timestamp, data) {
      const value = data[0];
      this.values.push(value);
      this.times.push(timestamp);

      // Trim
      while (this.values.length > SPARKLINE_POINTS) {
        this.values.shift();
        this.times.shift();
      }

      // Update big number
      this.valueEl.textContent = Number.isFinite(value) ? value.toFixed(1) : '--';

      // Update sparkline
      this.chart.data.datasets[0].data = this.values.map((v, i) => ({ x: i, y: v }));
      this.chart.update('none');
    }

    resize() { this.chart.resize(); }
    destroy() { this.chart.destroy(); }
    getElement() { return this.el; }
  }

  LabReplay.registerPlugin({
    id: 'gauge',
    name: 'Gauge',
    streamTypes: ['slow_numeric', 'HR', 'HRV', 'SpO2', 'SkinTemp', 'HeartRate'],
    create(container, streamMeta) {
      return new GaugeChart(container, streamMeta);
    }
  });
})();
