/**
 * waveform.js — Waveform Plugin
 *
 * Scrolling line chart for continuous data (ECG, Pupil, Accel, Resp, etc.)
 * Handles single-channel and multi-channel streams.
 * Uses Chart.js with a time-based X-axis.
 */

(function () {
  const BUFFER_SECONDS = 30;  // show last 30 seconds of data
  const TRACE_COLORS = [
    '#2B8E7E', '#2D8E54', '#C47A2A', '#3D6ECC',
    '#A89026', '#8B5DB8', '#C94444', '#2B9EB3'
  ];

  class WaveformChart {
    constructor(container, streamMeta) {
      this.streamMeta = streamMeta;
      this.channelCount = streamMeta.channel_count || 1;
      this.channels = streamMeta.channels || [];

      // Create canvas
      this.canvas = document.createElement('canvas');
      container.appendChild(this.canvas);

      // Ring buffers per channel
      this.buffers = [];
      for (let i = 0; i < this.channelCount; i++) {
        this.buffers.push({ times: [], values: [] });
      }

      // Determine labels and units
      const unit = this.channels[0]?.unit || '';
      const labels = this.channels.map((ch, i) => ch.label || `ch_${i}`);

      // Create Chart.js datasets
      const datasets = [];
      for (let i = 0; i < this.channelCount; i++) {
        datasets.push({
          label: labels[i],
          data: [],
          borderColor: TRACE_COLORS[i % TRACE_COLORS.length],
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          fill: false,
        });
      }

      this.chart = new Chart(this.canvas, {
        type: 'line',
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              display: this.channelCount > 1,
              position: 'top',
              labels: {
                color: '#4A5468',
                font: { family: 'Source Sans 3', size: 12 },
                boxWidth: 10,
                boxHeight: 10,
                padding: 8,
              },
            },
            tooltip: {
              backgroundColor: 'rgba(255, 255, 255, 0.96)',
              titleColor: '#1A2138',
              bodyColor: '#4A5468',
              borderColor: '#E2E5EA',
              borderWidth: 1,
              padding: 8,
              titleFont: { family: 'Source Sans 3', size: 13 },
              bodyFont: { family: 'Source Code Pro, Consolas, monospace', size: 12 },
              callbacks: {
                title(items) {
                  if (!items.length) return '';
                  const d = new Date(items[0].parsed.x);
                  return d.toLocaleTimeString('en-US', { hour12: false }) + '.' +
                    String(d.getMilliseconds()).padStart(3, '0');
                },
                label(item) {
                  return ` ${item.dataset.label}: ${item.parsed.y.toFixed(3)} ${unit}`;
                }
              }
            }
          },
          scales: {
            x: {
              type: 'time',
              time: {
                unit: 'second',
                displayFormats: { second: 'HH:mm:ss' },
              },
              ticks: {
                color: '#7B8499',
                font: { family: 'Source Sans 3', size: 11 },
                maxTicksLimit: 8,
              },
              grid: {
                color: 'rgba(0,0,0,0.05)',
                drawBorder: false,
              },
            },
            y: {
              ticks: {
                color: '#7B8499',
                font: { family: 'Source Sans 3', size: 11 },
                maxTicksLimit: 6,
              },
              grid: {
                color: 'rgba(0,0,0,0.05)',
                drawBorder: false,
              },
              title: {
                display: !!unit,
                text: unit,
                color: '#7B8499',
                font: { family: 'Source Sans 3', size: 12 },
              },
            },
          },
        },
      });

      // Throttle updates to ~30fps
      this._pendingUpdate = false;
      this._rafId = null;
    }

    pushSample(timestamp, data) {
      const timeMs = timestamp * 1000; // Chart.js time scale expects milliseconds
      const cutoff = timeMs - (BUFFER_SECONDS * 1000);

      for (let i = 0; i < this.channelCount && i < data.length; i++) {
        const buf = this.buffers[i];
        buf.times.push(timeMs);
        buf.values.push(data[i]);

        // Trim old data beyond buffer window
        while (buf.times.length > 0 && buf.times[0] < cutoff) {
          buf.times.shift();
          buf.values.shift();
        }

        // Update chart dataset
        this.chart.data.datasets[i].data = buf.times.map((t, idx) => ({
          x: t,
          y: buf.values[idx]
        }));
      }

      this._scheduleUpdate();
    }

    _scheduleUpdate() {
      if (this._pendingUpdate) return;
      this._pendingUpdate = true;
      this._rafId = requestAnimationFrame(() => {
        this.chart.update('none'); // 'none' = no animation
        this._pendingUpdate = false;
      });
    }

    resize() {
      this.chart.resize();
    }

    destroy() {
      if (this._rafId) cancelAnimationFrame(this._rafId);
      this.chart.destroy();
    }

    getElement() {
      return this.canvas;
    }
  }

  // Register this plugin
  LabReplay.registerPlugin({
    id: 'waveform',
    name: 'Waveform',
    streamTypes: ['continuous', 'ECG', 'EEG', 'Respiration', 'GSR', 'EDA', 'Pupil', 'Accel', 'Gyro'],
    create(container, streamMeta) {
      return new WaveformChart(container, streamMeta);
    }
  });
})();
