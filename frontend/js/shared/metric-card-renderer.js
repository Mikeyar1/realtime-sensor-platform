/**
 * metric-card-renderer.js — Shared utility for rendering and updating metric cards.
 *
 * Reads from LabReview.DrillMetrics (drill_metrics.js) for shared
 * physiological cards. Behavioral cards are rendered by AAR.Standardized.
 *
 * Usage:
 *   MetricCardRenderer.renderCards(behavioralEl, physioEl, drillKey)
 *   MetricCardRenderer.updateCard(id, rawValue)
 *   MetricCardRenderer.updateAllCards(payloadObj, source)  // source: 'trial' | 'biosignal'
 *   MetricCardRenderer.freezeCards()     // RESULTS state — stop flash, mark as final
 *   MetricCardRenderer.unfreezeCards()   // back to live
 *   MetricCardRenderer.clearCards()      // full teardown
 */

window.LabReview = window.LabReview || {};

LabReview.MetricCardRenderer = (function () {

  // ── Internal state ────────────────────────────────────────────────────────

  /** @type {Map<string, {def: object, valueEl: HTMLElement}>} */
  let _cards = new Map();
  let _frozen = false;

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Build the card grids for a given drill.
   * @param {HTMLElement} behavioralContainer  — where behavioral metric cards go
   * @param {HTMLElement} physioContainer      — where physiological cards go
   * @param {string}      drillKey             — key into LabReview.DrillMetrics
   */
  function renderCards(behavioralContainer, physioContainer, drillKey) {
    clearCards();
    _frozen = false;

    const registry = LabReview.DrillMetrics;
    if (!registry) {
      console.error('[MetricCardRenderer] LabReview.DrillMetrics not loaded.');
      return;
    }

    const behavioralDefs = registry[drillKey] || [];
    const physioDefs     = registry.physiological || [];

    // Build behavioral section
    if (behavioralContainer) {
      behavioralContainer.innerHTML = '';
      behavioralDefs.forEach(def => {
        const card = _buildCard(def, 'behavioral');
        behavioralContainer.appendChild(card.el);
        _cards.set(def.id, { def, valueEl: card.valueEl });
      });
    }

    // Build physiological section
    if (physioContainer) {
      physioContainer.innerHTML = '';
      physioDefs.forEach(def => {
        const card = _buildCard(def, 'physiological');
        physioContainer.appendChild(card.el);
        _cards.set(def.id, { def, valueEl: card.valueEl });
      });
    }
  }

  /**
   * Update a single metric card by its id.
   * @param {string} id        — card id from DrillMetrics
   * @param {*}      rawValue  — raw number to display
   */
  function updateCard(id, rawValue) {
    const entry = _cards.get(id);
    if (!entry) return;

    const { def, valueEl } = entry;

    // For showWhen:'nonzero' — hide/show the card's parent
    if (def.showWhen === 'nonzero') {
      const cardEl = valueEl.closest('.metric-card');
      if (cardEl) cardEl.style.display = (rawValue === 0 || rawValue == null) ? 'none' : '';
    }

    if (rawValue == null) return;
    const nextValue = _formatValue(rawValue, def.format);
    if (valueEl.textContent === nextValue) return;
    valueEl.textContent = nextValue;

    // Highlight the card surface only while live; keep its content visible.
    if (!_frozen) {
      const cardEl = valueEl.closest('.metric-card');
      if (cardEl) {
        cardEl.classList.remove('metric-card--flash');
        void cardEl.offsetWidth;
        cardEl.classList.add('metric-card--flash');
      }
    }
  }

  /**
   * Batch-update cards from a trial-update or biosignal-update payload.
   * @param {object} payload  — the full message payload
   * @param {string} source   — 'trial' | 'biosignal' — filters which cards to update
   */
  function updateAllCards(payload, source) {
    _cards.forEach(({ def }, id) => {
      if (source && def.source !== source) return;
      const value = _getNestedValue(payload, def.field);
      if (value !== undefined) updateCard(id, value);
    });
  }

  /**
   * Freeze cards — called when scenario stops.
   * Stops flash animation and adds the "FINAL" visual state to each card.
   */
  function freezeCards() {
    _frozen = true;
    document.querySelectorAll('.metric-card').forEach(el => {
      el.classList.add('metric-card--frozen');
    });
  }

  /**
   * Unfreeze cards — called on new session start.
   */
  function unfreezeCards() {
    _frozen = false;
    document.querySelectorAll('.metric-card').forEach(el => {
      el.classList.remove('metric-card--frozen');
    });
  }

  /**
   * Tear down all cards and internal state.
   */
  function clearCards() {
    _cards.clear();
    _frozen = false;
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /**
   * Build a single metric card DOM element.
   * @param {object} def     — card definition from DrillMetrics
   * @param {string} section — 'behavioral' | 'physiological'
   * @returns {{ el: HTMLElement, valueEl: HTMLElement }}
   */
  function _buildCard(def, section) {
    const el = document.createElement('div');
    el.className = [
      'metric-card',
      `metric-card--${def.size || 'md'}`,
      section === 'physiological' ? 'metric-card--physio' : '',
    ].filter(Boolean).join(' ');
    el.id = `mc-${def.id}`;

    // Hide nonzero-only cards initially
    if (def.showWhen === 'nonzero') el.style.display = 'none';

    const labelEl = document.createElement('div');
    labelEl.className = 'metric-card__label';
    labelEl.textContent = def.label;

    const valueEl = document.createElement('div');
    valueEl.className = 'metric-card__value';
    valueEl.textContent = '—';

    const unitEl = document.createElement('div');
    unitEl.className = 'metric-card__unit';
    unitEl.textContent = def.unit || '';

    const finalBadge = document.createElement('div');
    finalBadge.className = 'metric-card__final-badge';
    finalBadge.textContent = 'FINAL';

    el.appendChild(labelEl);
    el.appendChild(valueEl);
    if (def.unit) el.appendChild(unitEl);
    el.appendChild(finalBadge);

    return { el, valueEl };
  }

  /**
   * Format a raw numeric value according to the card's format spec.
   * @param {number} value
   * @param {string} format
   * @returns {string}
   */
  function _formatValue(value, format) {
    if (value == null || isNaN(value)) return '—';
    switch (format) {
      case 'percent':
        return `${Math.round(value * 100)}`;  // expects 0–1 from backend
      case 'ms':
        return `${Math.round(value)}`;
      case 'decimal':
        return value.toFixed(2);
      case 'time': {
        const s = Math.floor(value);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s % 60).padStart(2,'0')}`;
      }
      case 'number':
      default:
        return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }
  }

  /**
   * Safely get a value from an object using a dot-path or simple key.
   * Supports flat keys ('hit_rate') and nested paths ('metrics.hit_rate').
   */
  function _getNestedValue(obj, field) {
    if (!obj || !field) return undefined;
    if (field.includes('.')) {
      return field.split('.').reduce((acc, key) => acc?.[key], obj);
    }
    // Try direct key first, then look inside obj.metrics or obj.running_metrics if present
    if (obj[field] !== undefined) return obj[field];
    if (obj.metrics?.[field] !== undefined) return obj.metrics[field];
    if (obj.running_metrics?.[field] !== undefined) return obj.running_metrics[field];
    return undefined;
  }

  // ── Export ─────────────────────────────────────────────────────────────────

  return {
    renderCards,
    updateCard,
    updateAllCards,
    freezeCards,
    unfreezeCards,
    clearCards,
  };

})();
