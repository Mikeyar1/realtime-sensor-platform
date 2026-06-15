/**
 * chart-card.js — Base Chart Card
 *
 * Creates the standardized card wrapper that every chart type lives inside.
 *
 * Header layout:
 *   LEFT:  icon · title · subtitle
 *   RIGHT: live value display · close button
 *
 * The concrete chart calls card.updateLiveValue(text) to push the
 * current reading into the header without touching Plotly.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.ChartCard = (function () {

  /**
   * @param {Object} descriptor - from StreamRegistry.resolve()
   * @param {Object} opts
   * @param {Function} opts.onClose - called when user closes the card
   * @returns {{ el, header, body, updateLiveValue }}
   */
  function create(descriptor, opts = {}) {
    const el = document.createElement('div');
    el.className = 'chart-panel chart-card';
    el.dataset.streamKey = descriptor._key || '';

    // ── Header ──────────────────────────────────────────────────────────────
    const header = document.createElement('div');
    header.className = 'chart-header';

    // Left: icon + title + subtitle
    const titleGroup = document.createElement('div');
    titleGroup.className = 'chart-card-title-group';
    titleGroup.innerHTML = `
      <span class="chart-title">${descriptor.title}</span>
      <span class="chart-card-subtitle">${descriptor.subtitle || ''}</span>
    `;

    // Right: live value + unit + close
    const actions = document.createElement('div');
    actions.className = 'chart-actions';

    // Live value display — updated by the chart on each sample
    const liveEl = document.createElement('span');
    liveEl.className = 'chart-card-live-value';
    liveEl.title = 'Current value';
    liveEl.style.cssText = [
      'font-family:var(--font-mono)',
      'font-size:var(--font-size-lg)',
      'font-weight:700',
      `color:${descriptor.color}`,
      'min-width:52px',
      'text-align:right',
      'line-height:1',
      'letter-spacing:-0.01em',
    ].join(';');

    const liveUnit = document.createElement('span');
    liveUnit.style.cssText = [
      'font-size:var(--font-size-xs)',
      'font-weight:600',
      'color:var(--text-muted)',
      'margin-left:3px',
      'text-transform:uppercase',
      'letter-spacing:0.06em',
    ].join(';');
    liveUnit.textContent = descriptor.channels?.[0]?.unit || '';

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'chart-action-btn close-btn';
    closeBtn.title = 'Close panel';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', () => {
      el.style.display = 'none';
      if (opts.onClose) opts.onClose();
    });

    actions.appendChild(liveEl);
    actions.appendChild(liveUnit);
    actions.appendChild(closeBtn);

    header.appendChild(titleGroup);
    header.appendChild(actions);

    // ── Body ────────────────────────────────────────────────────────────────
    const body = document.createElement('div');
    body.className = 'chart-body';

    el.appendChild(header);
    el.appendChild(body);

    // ── Public API ──────────────────────────────────────────────────────────
    return {
      el,
      header,
      body,
      /**
       * Update the live value display in the card header.
       * Called by the chart instance on each incoming sample.
       * @param {number|string} value
       */
      updateLiveValue(value) {
        liveEl.textContent = value ?? '—';
      },
    };
  }

  return { create };
})();
