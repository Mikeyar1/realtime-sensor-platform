/**
 * js/analysis/drills/behdisc/per-engagement-view.js
 *
 * Renders the Per-Engagement view for the BehDisc drill.
 * Owns:
 *   - Trial sidebar (visible)
 *   - Individual engagement epoch chart
 *   - Per-engagement stats panel
 *
 * The parent controller wires the 'trialselect' custom event
 * and calls this view's onTrialSelect() method.
 */

export function createPerEngagementView({ epochChart, statsPanel, trialSidebar }) {
  let _epochData  = null;
  let _trialsData = null;

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Activate this view with pre-fetched data.
   * Immediately renders the first trial in the sidebar.
   */
  function activate(epochData, trialsData) {
    _epochData  = epochData;
    _trialsData = trialsData;

    // Show sidebar
    const sidebar = document.getElementById('an-sidebar');
    if (sidebar) sidebar.style.display = '';

    // Hide aggregate filter bar
    const filterBar = document.getElementById('an-agg-filter-bar');
    if (filterBar) filterBar.style.display = 'none';

    if (!epochData || !trialsData) return;

    // Re-load sidebar (re-applies selection state)
    trialSidebar.load(trialsData.trials, trialsData.terminology);

    // Auto-select first trial
    const first = trialsData.trials?.[0];
    if (first) {
      epochChart.setMode('per-engagement');
      epochChart.render(epochData, [first.index]);
      statsPanel.renderPerEngagement(epochData, first);

      // Highlight the first row in the sidebar
      setTimeout(() => {
        const row = document.querySelector(`.an-trial-row[data-index="${first.index}"]`);
        if (row) row.click();
      }, 50);
    }
  }

  function deactivate() {
    // Nothing to hide beyond what activate() shows
  }

  /**
   * Called when the user selects trial(s) in the sidebar.
   * @param {number[]} trialIds
   */
  function onTrialSelect(trialIds) {
    if (!_epochData || !_trialsData) return;

    epochChart.setMode('per-engagement');
    epochChart.render(_epochData, trialIds.length > 0 ? trialIds : null);

    if (trialIds.length === 1) {
      const trial = _getTrialByIndex(trialIds[0]);
      if (trial) statsPanel.renderPerEngagement(_epochData, trial);
    } else if (trialIds.length > 1) {
      const trials = trialIds.map(id => _getTrialByIndex(id)).filter(Boolean);
      statsPanel.renderPerEngagementMulti(_epochData, trials);
    }
  }

  function load(epochData, trialsData) {
    _epochData  = epochData;
    _trialsData = trialsData;
    activate(epochData, trialsData);
  }

  // ── Private ───────────────────────────────────────────────────────────────

  function _getTrialByIndex(idx) {
    return (_trialsData?.trials || []).find(t => t.index === idx) || null;
  }

  return { activate, deactivate, load, onTrialSelect };
}
