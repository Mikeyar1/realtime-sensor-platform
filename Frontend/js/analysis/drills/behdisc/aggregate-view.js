/**
 * js/analysis/drills/behdisc/aggregate-view.js
 *
 * Renders the Aggregate view for the BehDisc drill in the
 * Human Performance Workspace. Owns:
 *   - Grand average epoch chart across all selected engagements
 *   - Aggregate filter bar (All / Hostile only / Non-Hostile only)
 *   - Stats summary panel (aggregate row)
 *
 * Exports a single factory: createAggregateView(deps)
 * where deps = { epochChart, statsPanel, trialSidebar }
 *
 * This module is a plain ES module. The parent controller
 * (analysis-app.js) calls view.activate() / view.deactivate()
 * when the user switches tabs.
 */

export function createAggregateView({ epochChart, statsPanel, trialSidebar }) {
  let _epochData   = null;
  let _trialsData  = null;
  let _aggFilter   = 'all';

  // ── DOM refs ──────────────────────────────────────────────────────────────

  function _getFilterBar() {
    return document.getElementById('an-agg-filter-bar');
  }

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Called when the user switches to Aggregate mode.
   * Renders the chart and stats using the already-fetched data.
   */
  function activate(epochData, trialsData) {
    _epochData  = epochData;
    _trialsData = trialsData;

    // Hide sidebar (not needed in aggregate)
    const sidebar = document.getElementById('an-sidebar');
    if (sidebar) sidebar.style.display = 'none';

    // Show/hide aggregate filter bar
    const filterBar = _getFilterBar();
    if (filterBar) {
      filterBar.style.display = epochData?.has_type_split ? 'flex' : 'none';
    }

    if (!epochData || !trialsData) return;

    epochChart.setMode('aggregate');
    epochChart.render(epochData, null);
    statsPanel.render(epochData, trialsData.summary);
  }

  /** Called when leaving this view. */
  function deactivate() {
    const filterBar = _getFilterBar();
    if (filterBar) filterBar.style.display = 'none';
  }

  /**
   * Update the aggregate filter selection.
   * @param {'all'|'hostile'|'nonhostile'} filter
   */
  function setAggFilter(filter) {
    _aggFilter = filter;
    if (!_epochData) return;
    epochChart.setAggFilter(filter);
    epochChart.render(_epochData, null);
  }

  /** Re-render using new data (called after a fresh Analyze run). */
  function load(epochData, trialsData) {
    _epochData  = epochData;
    _trialsData = trialsData;
    _aggFilter  = 'all';
    activate(epochData, trialsData);
  }

  return { activate, deactivate, load, setAggFilter };
}
