/**
 * js/analysis/drills/behdisc/comparison-view.js
 *
 * Renders the Comparison (side-by-side) view for the BehDisc drill.
 * Owns:
 *   - Session B selector visibility
 *   - Two side-by-side epoch charts (Session A / Session B)
 *   - Comparison stats table
 *
 * The parent controller fetches both sessions' data and passes them
 * via load(epochA, trialsA, epochB, trialsB).
 */

export function createComparisonView({ epochChart, statsPanel }) {
  let _epochA  = null;
  let _trialsA = null;
  let _epochB  = null;
  let _trialsB = null;

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Activate (show Session B group in the control bar).
   * Call before data is loaded to set up the UI.
   */
  function activate(epochA, trialsA, epochB, trialsB, drill) {
    _epochA  = epochA;
    _trialsA = trialsA;
    _epochB  = epochB;
    _trialsB = trialsB;

    // Show Session B group
    const bGroup = document.getElementById('an-session-b-group');
    if (bGroup) bGroup.style.display = 'flex';

    // Hide sidebar + filter bar
    const sidebar   = document.getElementById('an-sidebar');
    const filterBar = document.getElementById('an-agg-filter-bar');
    if (sidebar)   sidebar.style.display = 'none';
    if (filterBar) filterBar.style.display = 'none';

    if (!epochA || !epochB) return;

    _renderCharts(epochA, trialsA, epochB, trialsB, drill);
  }

  function deactivate() {
    const bGroup = document.getElementById('an-session-b-group');
    if (bGroup) bGroup.style.display = 'none';
  }

  /**
   * Load and render both sessions' data.
   * @param {object} epochA
   * @param {object} trialsA
   * @param {object} epochB
   * @param {object} trialsB
   * @param {string} drill
   */
  function load(epochA, trialsA, epochB, trialsB, drill) {
    activate(epochA, trialsA, epochB, trialsB, drill);
  }

  // ── Private ───────────────────────────────────────────────────────────────

  function _renderCharts(epochA, trialsA, epochB, trialsB, drill) {
    // Show comparison container; hide single chart
    const compContainer = document.getElementById('an-comparison-charts');
    const singleChart   = document.getElementById('an-epoch-chart');
    const placeholder   = document.getElementById('an-chart-placeholder');
    const titleBlock    = document.getElementById('an-chart-title');

    if (compContainer) compContainer.style.display = 'flex';
    if (singleChart)   singleChart.style.display   = 'none';
    if (placeholder)   placeholder.style.display   = 'none';
    if (titleBlock)    titleBlock.style.display     = 'flex';

    // Left: Session A
    epochChart.init('an-epoch-chart-compare-a');
    epochChart.setTerminology(trialsA.terminology);
    epochChart.setMode('aggregate');
    epochChart.render(epochA, null);

    // Right: Session B
    epochChart.init('an-epoch-chart-compare-b');
    epochChart.setTerminology(trialsB.terminology);
    epochChart.setMode('aggregate');
    epochChart.render(epochB, null);

    // Stats comparison table
    statsPanel.init(document.getElementById('an-stats-panel'));
    statsPanel.setTerminology(trialsA.terminology);
    statsPanel.renderComparison(epochA, trialsA, epochB, trialsB, drill);
  }

  return { activate, deactivate, load };
}
