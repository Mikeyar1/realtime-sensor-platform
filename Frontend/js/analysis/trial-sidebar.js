/**
 * trial-sidebar.js — Left sidebar: engagement list for BehDisc Per-Engagement view.
 *
 * Design (simplified):
 *   Header: "Engagements (27)"
 *   Filter: [ All ]  [ H ]  [ NH ]
 *   Each row: #  ActorShort   H/NH   RT   shots×
 *
 * No colored HOSTILE/NON-H badges. No outcome icons.
 * Shots shown for both hostile and non-hostile (CE) when > 0.
 *
 * Emits custom 'trialselect' event on sidebar element:
 *   detail: { trialIds: int[] }
 */

window.LabReplay = window.LabReplay || {};

LabReplay.TrialSidebar = (function () {

  let _el      = null;
  let _trials  = [];
  let _term    = {};
  let _selected = new Set();
  let _filter  = 'all';

  function init(containerEl) {
    _el = containerEl;
  }

  function load(trials, terminology) {
    _trials  = trials;
    _term    = terminology;
    _selected = new Set();
    _filter  = 'all';
    _render();
  }

  function clear() {
    _trials  = [];
    _term    = {};
    _selected = new Set();
    if (_el) _el.innerHTML = '';
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  function _render() {
    if (!_el) return;

    const n         = _trials.length;
    const nH        = _trials.filter(t => t.actor_type === 'HOSTILE').length;
    const nNH       = _trials.filter(t => t.actor_type === 'NON_HOSTILE').length;
    const isActive  = f => _filter === f ? 'an-filter-btn--active' : '';

    _el.innerHTML = `
      <div class="an-sidebar-header">
        <div class="an-sidebar-title">${_term.trial_label || 'Engagement'}s (${n})</div>
        <div class="an-sidebar-counts-plain">${nH} H &nbsp;·&nbsp; ${nNH} NH</div>
      </div>

      <div class="an-sidebar-filters">
        <button class="an-filter-btn ${isActive('all')}"        data-filter="all">All</button>
        <button class="an-filter-btn ${isActive('HOSTILE')}"    data-filter="HOSTILE">H</button>
        <button class="an-filter-btn ${isActive('NON_HOSTILE')}" data-filter="NON_HOSTILE">NH</button>
      </div>

      <div class="an-trial-list" id="an-trial-list">
        ${_trials.map(_renderRow).join('')}
      </div>
    `;

    // Filter buttons
    _el.querySelectorAll('.an-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        _filter = btn.dataset.filter;
        _el.querySelectorAll('.an-filter-btn').forEach(b => b.classList.remove('an-filter-btn--active'));
        btn.classList.add('an-filter-btn--active');
        _applyFilter();
      });
    });

    // Row clicks — strictly single select in per-engagement mode
    _el.querySelectorAll('.an-trial-row').forEach(row => {
      row.addEventListener('click', () => {
        const idx = parseInt(row.dataset.index, 10);
        _selected = new Set([idx]);
        _updateRowSelection();
        _emit();
      });
    });

    _applyFilter();
  }

  // ── Row template ─────────────────────────────────────────────────────────────

  function _renderRow(trial) {
    const isHostile  = trial.actor_type === 'HOSTILE';
    const typeLabel  = isHostile ? 'H' : 'NH';
    const typeClass  = isHostile ? 'an-trial-type--h' : 'an-trial-type--nh';
    const actorLabel = trial.actor_short || trial.actor_name || `#${trial.index}`;

    return `
      <div class="an-trial-row" data-index="${trial.index}" data-actor-type="${trial.actor_type || ''}">
        <span class="an-trial-num">${trial.index}</span>
        <span class="an-trial-actor">${actorLabel}</span>
        <span class="an-trial-type ${typeClass}">${typeLabel}</span>
      </div>`;
  }

  // ── Filter ───────────────────────────────────────────────────────────────────

  function _applyFilter() {
    const rows = _el.querySelectorAll('.an-trial-row');
    rows.forEach(row => {
      const at = row.dataset.actorType;
      const show = _filter === 'all'
        || (_filter === 'HOSTILE'     && at === 'HOSTILE')
        || (_filter === 'NON_HOSTILE' && at === 'NON_HOSTILE');
      row.style.display = show ? '' : 'none';
    });

    // Auto-select the first visible engagement only
    const visible = Array.from(rows)
      .filter(r => r.style.display !== 'none')
      .map(r => parseInt(r.dataset.index, 10));
    _selected = new Set(visible.length ? [visible[0]] : []);
    _updateRowSelection();
    _emit();
  }

  function _updateRowSelection() {
    if (!_el) return;
    _el.querySelectorAll('.an-trial-row').forEach(row => {
      const idx = parseInt(row.dataset.index, 10);
      row.classList.toggle('an-trial-row--selected', _selected.has(idx));
    });
  }

  function _emit() {
    if (!_el) return;
    const ids = Array.from(_selected).sort((a, b) => a - b);
    _el.dispatchEvent(new CustomEvent('trialselect', {
      bubbles: true,
      detail: { trialIds: ids },
    }));
  }

  function selectAll() {
    _selected = new Set(_trials.map(t => t.index));
    _updateRowSelection();
    _emit();
  }

  // lock/unlock kept as no-ops for compatibility with analysis-app.js calls
  function lock()   { /* no-op — sidebar is hidden in aggregate mode */ }
  function unlock() { /* no-op */ }

  return { init, load, clear, selectAll, lock, unlock };
})();
