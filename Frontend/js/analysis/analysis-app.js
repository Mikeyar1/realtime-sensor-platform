/**
 * analysis-app.js — Top-level controller for the Analysis tab.
 *
 * Manages:
 *  - Drill dropdown (BehDisc / PVT / L2GoNoGo)
 *  - Session dropdown (populated per drill)
 *  - Baseline Window / Analysis Window spinners
 *  - View mode toggle: Aggregate | Per-Engagement
 *  - Run button → fetch trials → fetch epochs → render chart + stats
 *  - Trial sidebar selection → re-render chart (no re-fetch)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.AnalysisApp = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _drill       = 'behdisc';
  let _session     = null;
  let _sessionB    = null;
  let _baselineS   = 2.0;
  let _analysisS   = 2.0;
  let _mode        = 'aggregate';  // 'aggregate' | 'per-engagement'
  let _aggFilter   = 'all';        // 'all' | 'hostile' | 'nonhostile'
  let _trialsData  = null;
  let _epochData   = null;
  let _trialsDataB = null;
  let _epochDataB  = null;
  let _selectedIds = [];

  const DRILLS = [
    { key: 'behdisc', label: 'BehDisc', longLabel: 'Behavioral Discrimination' },
    { key: 'pvt', label: 'PVT', longLabel: 'Psychomotor Vigilance Task' },
    { key: 'l2gonogo', label: 'L2GoNoGo', longLabel: 'L2 Go/No-Go' },
  ];

  const SIGNALS = ['hr', 'pupil', 'motion'];

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    _buildShell();
    _bindControls();
    _loadSessions(_drill);

    LabReplay.AnalysisAPI.health()
      .catch(() => _setStatus(
        '⚠ Analysis API not reachable — start backend: <code>cd Analysis &amp;&amp; uv run python main.py</code>',
        'error'
      ));
  }

  // ── Shell HTML ─────────────────────────────────────────────────────────────

  function _buildShell() {
    const page = document.getElementById('page-analysis')
               || document.getElementById('page-workspace');
    if (!page) return;

    page.innerHTML = `
      <!-- Analysis control bar -->
      <div class="an-control-bar">
        <div class="an-controls-row">
          <div class="an-control-group">
            <label class="an-label">Drills</label>
            <select class="an-select" id="an-drill-select">
              ${DRILLS.map(d => `
                <option value="${d.key}" ${d.key === _drill ? 'selected' : ''}>${d.label} — ${d.longLabel}</option>
              `).join('')}
            </select>
          </div>

          <span class="an-divider"></span>

          <div class="an-control-group">
            <label class="an-label">Session</label>
            <select class="an-select" id="an-session-select">
              <option value="">— loading —</option>
            </select>
          </div>

          <span class="an-divider"></span>

          <div class="an-control-group">
            <label class="an-label">Baseline Window</label>
            <div class="an-spinner-wrap">
              <button class="an-spin-btn" id="an-bl-dec">−</button>
              <span class="an-spin-val" id="an-bl-val">2.0 s</span>
              <button class="an-spin-btn" id="an-bl-inc">+</button>
            </div>
          </div>

          <div class="an-control-group">
            <label class="an-label">Analysis Window</label>
            <div class="an-spinner-wrap">
              <button class="an-spin-btn" id="an-an-dec">−</button>
              <span class="an-spin-val" id="an-an-val">2.0 s</span>
              <button class="an-spin-btn" id="an-an-inc">+</button>
            </div>
          </div>

          <span class="an-divider"></span>

          <div class="an-control-group">
            <!-- View mode toggle -->
            <label class="an-label">View</label>
            <div class="an-mode-toggle" id="an-mode-toggle">
              <button class="an-mode-btn an-mode-btn--active" data-mode="aggregate" title="Grand average across all selected engagements">Aggregate</button>
              <button class="an-mode-btn" data-mode="per-engagement" title="Each engagement shown individually">Per-Engagement</button>
              <button class="an-mode-btn" data-mode="comparison" title="Side-by-side session comparison">Comparison</button>
            </div>
          </div>

          <div class="an-control-group" id="an-session-b-group" style="display:none">
            <span class="an-divider"></span>
            <label class="an-label">Session B</label>
            <select class="an-select" id="an-session-select-b">
              <option value="">— select session —</option>
            </select>
          </div>

          <button class="an-run-btn" id="an-run-btn" disabled>▶ Analyze</button>
        </div>

        <div class="an-status" id="an-status"></div>
      </div>

      <!-- Main layout: sidebar + chart area -->
      <div class="an-layout">
        <!-- Left sidebar: engagement list -->
        <div class="an-sidebar" id="an-sidebar">
          <div class="an-sidebar-placeholder">
            Select a drill and session,<br>then click <strong>Analyze</strong>.
          </div>
        </div>

        <!-- Right: chart + stats -->
        <div class="an-main">
          <div class="an-chart-card" id="an-chart-card">
            <div class="an-chart-title" id="an-chart-title" style="display:none">
              <span id="an-chart-title-text"></span>
              <span class="an-chart-subtitle" id="an-chart-subtitle"></span>
            </div>
            <div class="an-chart-placeholder" id="an-chart-placeholder">
              <span></span>
              <p>Physiological response charts appear here after analysis runs.</p>
            </div>
            <div id="an-epoch-chart" style="display:none"></div>
            
            <!-- Comparison Mode Side-by-Side Charts -->
            <div class="an-comparison-charts" id="an-comparison-charts" style="display:none">
              <div class="an-chart-card-compare">
                <div class="an-compare-header">Session A</div>
                <div id="an-epoch-chart-compare-a"></div>
              </div>
              <div class="an-chart-card-compare">
                <div class="an-compare-header">Session B</div>
                <div id="an-epoch-chart-compare-b"></div>
              </div>
            </div>

            <!-- Aggregate filter bar: visible only in aggregate mode with type-split data -->
            <div class="an-agg-filter-bar" id="an-agg-filter-bar" style="display:none">
              <span class="an-agg-filter-label">Show:</span>
              <button class="an-agg-filter-btn an-agg-filter-btn--active" data-agg="all">Both</button>
              <button class="an-agg-filter-btn" data-agg="hostile">Hostile only</button>
              <button class="an-agg-filter-btn" data-agg="nonhostile">Non-Hostile only</button>
            </div>
          </div>

          <div class="an-stats-card" id="an-stats-panel"></div>
        </div>
      </div>
    `;
  }

  // ── Control binding ────────────────────────────────────────────────────────

  function _bindControls() {
    const page = document.getElementById('page-analysis')
               || document.getElementById('page-workspace');
    if (!page) return;


    // Drill select
    page.addEventListener('change', e => {
      if (e.target.id === 'an-drill-select') {
        _drill = e.target.value;
        _resetResults();
        _loadSessions(_drill);
      }
    });

    // Session A and Session B select listeners
    page.addEventListener('change', e => {
      if (e.target.id === 'an-session-select') {
        _session = e.target.value || null;
        _updateRunButtonState();
      } else if (e.target.id === 'an-session-select-b') {
        _sessionB = e.target.value || null;
        _updateRunButtonState();
      }
    });

    function _updateRunButtonState() {
      const btn = document.getElementById('an-run-btn');
      if (!btn) return;
      if (_mode === 'comparison') {
        btn.disabled = !_session || !_sessionB;
      } else {
        btn.disabled = !_session;
      }
    }

    // Baseline Window spinner
    _bindSpinner('an-bl-dec', 'an-bl-inc', 'an-bl-val',
      v => { _baselineS = v; },
      () => _baselineS, 0.1, 10.0, 0.1);

    // Analysis Window spinner
    _bindSpinner('an-an-dec', 'an-an-inc', 'an-an-val',
      v => { _analysisS = v; },
      () => _analysisS, 0.1, 10.0, 0.1);

    // View mode toggle — reset selection and sidebar state cleanly between modes
    page.querySelectorAll('.an-mode-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        _mode = btn.dataset.mode;
        page.querySelectorAll('.an-mode-btn').forEach(b => b.classList.remove('an-mode-btn--active'));
        btn.classList.add('an-mode-btn--active');

        // Hide/show sidebar based on mode
        const sidebar = document.getElementById('an-sidebar');
        if (sidebar) sidebar.style.display = _mode === 'per-engagement' ? '' : 'none';

        // Hide/show Session B group in controls
        const bGroup = document.getElementById('an-session-b-group');
        if (bGroup) bGroup.style.display = _mode === 'comparison' ? 'flex' : 'none';

        // Show/hide aggregate filter bar
        const filterBar = document.getElementById('an-agg-filter-bar');
        if (filterBar) filterBar.style.display =
          (_mode === 'aggregate' && _epochData?.has_type_split) ? 'flex' : 'none';

        // Show/hide standard vs comparison charts
        const chartStd = document.getElementById('an-epoch-chart');
        const chartComp = document.getElementById('an-comparison-charts');
        const hasCompareData = _epochData && _epochDataB;
        if (chartStd) chartStd.style.display = _mode === 'comparison' ? 'none' : (_epochData ? 'block' : 'none');
        if (chartComp) chartComp.style.display = (_mode === 'comparison' && hasCompareData) ? 'flex' : 'none';

        const placeholder = document.getElementById('an-chart-placeholder');
        if (placeholder) {
          placeholder.style.display = (_mode === 'comparison' ? !hasCompareData : !_epochData) ? 'block' : 'none';
        }

        const titleBlock = document.getElementById('an-chart-title');
        if (titleBlock) {
          titleBlock.style.display = (_mode === 'comparison' ? hasCompareData : _epochData) ? 'flex' : 'none';
        }

        if (_epochData && _trialsData) {
          if (_mode === 'per-engagement') {
            // Freshly reload sidebar to trigger single select of the first trial
            LabReplay.TrialSidebar.load(_trialsData.trials, _trialsData.terminology);
          } else if (_mode === 'aggregate') {
            // Aggregate mode: always render grand averages (pass null) and reset stats summary
            LabReplay.EpochChart.setMode('aggregate');
            LabReplay.EpochChart.render(_epochData, null);
            _updateChartTitle();
            LabReplay.StatsPanel.render(_epochData, _trialsData.summary);
          }
        }
      });
    });

    // Aggregate filter buttons
    page.addEventListener('click', e => {
      const btn = e.target.closest('.an-agg-filter-btn');
      if (!btn) return;
      _aggFilter = btn.dataset.agg;
      page.querySelectorAll('.an-agg-filter-btn').forEach(b => b.classList.remove('an-agg-filter-btn--active'));
      btn.classList.add('an-agg-filter-btn--active');
      if (_epochData) {
        LabReplay.EpochChart.setAggFilter(_aggFilter);
        LabReplay.EpochChart.render(_epochData, null);
      }
    });

    // Run button
    page.addEventListener('click', e => {
      if (e.target.id === 'an-run-btn') _runAnalysis();
    });

    // Trial selection from sidebar
    page.addEventListener('trialselect', e => {
      _selectedIds = e.detail.trialIds;
      if (!_epochData) return;

      LabReplay.EpochChart.setMode(_mode);
      LabReplay.EpochChart.render(_epochData, _selectedIds.length > 0 ? _selectedIds : null);
      _updateChartTitle();

      // Per-engagement mode: update stats panel for selected trial(s)
      if (_mode === 'per-engagement' && _trialsData) {
        if (_selectedIds.length === 1) {
          const trial = _getTrialByIndex(_selectedIds[0]);
          if (trial) LabReplay.StatsPanel.renderPerEngagement(_epochData, trial);
        } else if (_selectedIds.length > 1) {
          const trials = _selectedIds.map(id => _getTrialByIndex(id)).filter(Boolean);
          LabReplay.StatsPanel.renderPerEngagementMulti(_epochData, trials);
        }
      }
    });
  }

  function _getTrialByIndex(idx) {
    return (_trialsData?.trials || []).find(t => t.index === idx) || null;
  }

  function _bindSpinner(decId, incId, valId, setter, getter, min, max, step) {
    document.getElementById(decId)?.addEventListener('click', () => {
      const nv = Math.max(min, Math.round((getter() - step) * 10) / 10);
      setter(nv);
      document.getElementById(valId).textContent = nv.toFixed(1) + ' s';
    });
    document.getElementById(incId)?.addEventListener('click', () => {
      const nv = Math.min(max, Math.round((getter() + step) * 10) / 10);
      setter(nv);
      document.getElementById(valId).textContent = nv.toFixed(1) + ' s';
    });
  }

  // ── Load sessions ──────────────────────────────────────────────────────────

  async function _loadSessions(drill) {
    const selA = document.getElementById('an-session-select');
    const selB = document.getElementById('an-session-select-b');
    if (!selA || !selB) return;

    selA.innerHTML = '<option value="">— loading —</option>';
    selB.innerHTML = '<option value="">— loading —</option>';
    document.getElementById('an-run-btn').disabled = true;
    _session = null;
    _sessionB = null;

    try {
      const sessions = await LabReplay.AnalysisAPI.sessions(drill);
      const matching = sessions.filter(s => s.drill === drill);

      if (!matching.length) {
        selA.innerHTML = `<option value="">No ${drill} sessions found</option>`;
        selB.innerHTML = `<option value="">No ${drill} sessions found</option>`;
        return;
      }

      const optionsHtml = `<option value="">— select session —</option>` +
        matching.map(s => {
          const dur = s.duration_s ? ` (${Math.round(s.duration_s / 60)}m)` : '';
          return `<option value="${s.filename}">${s.filename.replace('.db', '')}${dur}</option>`;
        }).join('');

      selA.innerHTML = optionsHtml;
      selB.innerHTML = optionsHtml;
    } catch (e) {
      selA.innerHTML = '<option value="">API unavailable</option>';
      selB.innerHTML = '<option value="">API unavailable</option>';
    }
  }

  // ── Run analysis ───────────────────────────────────────────────────────────

  async function _runAnalysis() {
    if (!_session || !_drill) return;

    if (_mode === 'comparison') {
      if (!_sessionB) return;
      _setStatus('Comparing sessions…', 'loading');
      
      const sA = _session;
      const sB = _sessionB;
      
      _resetResults();
      _session = sA;
      _sessionB = sB;

      try {
        // Fetch concurrently
        const [resTrialsA, resTrialsB] = await Promise.all([
          LabReplay.AnalysisAPI.trials(sA, _drill),
          LabReplay.AnalysisAPI.trials(sB, _drill)
        ]);

        const trialsA = resTrialsA.trials;
        const trialsB = resTrialsB.trials;

        if (!trialsA.length || !trialsB.length) {
          _setStatus('One or both sessions contain no engagements.', 'warn');
          return;
        }

        const idsA = trialsA.map(t => t.index);
        const idsB = trialsB.map(t => t.index);

        _setStatus('Computing physiological epochs for both sessions…', 'loading');

        const [resEpochsA, resEpochsB] = await Promise.all([
          LabReplay.AnalysisAPI.epoch({
            session: sA,
            drill: _drill,
            trial_ids: idsA,
            signals: SIGNALS,
            baseline_s: _baselineS,
            analysis_s: _analysisS,
            bin_s: 0.1,
            do_zscore: false,
          }),
          LabReplay.AnalysisAPI.epoch({
            session: sB,
            drill: _drill,
            trial_ids: idsB,
            signals: SIGNALS,
            baseline_s: _baselineS,
            analysis_s: _analysisS,
            bin_s: 0.1,
            do_zscore: false,
          })
        ]);

        _trialsData = resTrialsA;
        _epochData = resEpochsA;
        _trialsDataB = resTrialsB;
        _epochDataB = resEpochsB;

        _setStatus('Analysis completed.', 'ok');

        // Hide standard chart, show comparison container
        document.getElementById('an-chart-placeholder').style.display = 'none';
        document.getElementById('an-epoch-chart').style.display = 'none';
        document.getElementById('an-comparison-charts').style.display = 'flex';
        document.getElementById('an-chart-title').style.display = 'flex';

        _updateChartTitle();

        // Render both charts side-by-side
        _renderComparisonGraphs();

        // Render Comparison Stats Table
        LabReplay.StatsPanel.init(document.getElementById('an-stats-panel'));
        LabReplay.StatsPanel.setTerminology(resTrialsA.terminology);
        LabReplay.StatsPanel.renderComparison(_epochData, _trialsData, _epochDataB, _trialsDataB, _drill);

      } catch (e) {
        _setStatus('Session comparison failed: ' + e.message, 'error');
      }
      return;
    }

    _setStatus('Loading engagements…', 'loading');
    _resetResults();

    try {
      // 1. Fetch trials
      _trialsData = await LabReplay.AnalysisAPI.trials(_session, _drill);
      const { trials, summary, terminology } = _trialsData;

      if (!trials || trials.length === 0) {
        _setStatus('No engagements found in this session.', 'warn');
        return;
      }

      const termLabel = terminology.trial_label || 'engagement';
      _setStatus(`Found ${trials.length} ${termLabel}s — computing physio epochs…`, 'loading');

      // 3. Fetch epochs (all trials)
      const allIds = trials.map(t => t.index);
      _epochData = await LabReplay.AnalysisAPI.epoch({
        session: _session,
        drill: _drill,
        trial_ids: allIds,
        signals: SIGNALS,
        baseline_s: _baselineS,
        analysis_s: _analysisS,
        bin_s: 0.1,
        do_zscore: false,
      });

      // 4. Render chart
      document.getElementById('an-chart-placeholder').style.display = 'none';
      document.getElementById('an-epoch-chart').style.display = 'block';
      document.getElementById('an-chart-title').style.display = 'flex';

      LabReplay.EpochChart.init('an-epoch-chart');
      LabReplay.EpochChart.setWindows(_baselineS, _analysisS);
      LabReplay.EpochChart.setTerminology(terminology);
      LabReplay.EpochChart.setMode(_mode);
      LabReplay.EpochChart.render(_epochData, null);

      // 5. Stats panel + sidebar/filter bar visibility
      LabReplay.StatsPanel.init(document.getElementById('an-stats-panel'));
      LabReplay.StatsPanel.setTerminology(terminology);

      const sidebar   = document.getElementById('an-sidebar');
      const filterBar = document.getElementById('an-agg-filter-bar');

      if (_mode === 'aggregate') {
        // Hide sidebar immediately (before load) to prevent flash
        if (sidebar)    sidebar.style.display = 'none';
        if (filterBar && _epochData.has_type_split) filterBar.style.display = 'flex';
        else if (filterBar) filterBar.style.display = 'none';

        // Load sidebar quietly (it stays hidden)
        LabReplay.TrialSidebar.init(document.getElementById('an-sidebar'));
        LabReplay.TrialSidebar.load(trials, terminology);

        _selectedIds = allIds;
        LabReplay.StatsPanel.render(_epochData, summary);

      } else {
        // Per-engagement mode: show sidebar, default to first trial
        if (sidebar)    sidebar.style.display = '';
        if (filterBar)  filterBar.style.display = 'none';

        LabReplay.TrialSidebar.init(document.getElementById('an-sidebar'));
        LabReplay.TrialSidebar.load(trials, terminology);

        // Auto-select first trial — re-render chart with just that one
        const firstTrial = trials[0];
        _selectedIds = firstTrial ? [firstTrial.index] : allIds;
        LabReplay.EpochChart.render(_epochData, _selectedIds);

        if (firstTrial) {
          LabReplay.StatsPanel.renderPerEngagement(_epochData, firstTrial);
          // Highlight row 1 in sidebar
          setTimeout(() => {
            const row = document.querySelector(`.an-trial-row[data-index="${firstTrial.index}"]`);
            if (row) { row.click(); }
          }, 50);
        }
      }

      _updateChartTitle();
      _setStatus(`Analysis complete`, 'ok');

    } catch (e) {
      _setStatus(`Error: ${e.message}`, 'error');
      console.error('[AnalysisApp]', e);
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _updateChartTitle() {
    const titleEl = document.getElementById('an-chart-title-text');
    const subtitleEl = document.getElementById('an-chart-subtitle');
    if (!titleEl || !_trialsData) return;

    const { trials, summary } = _trialsData;
    const n = _selectedIds.length > 0 ? _selectedIds.length : trials.length;

    if (_mode === 'aggregate') {
      titleEl.textContent = `Aggregate Physio Response`;
      const hostile = summary.n_hostile != null ? ` · ${summary.n_hostile} hostile` : '';
      const nonhostile = summary.n_nonhostile != null ? `, ${summary.n_nonhostile} non-hostile` : '';
      subtitleEl.textContent = `N = ${n} engagements${hostile}${nonhostile} · Baseline: ${_baselineS}s | Analysis: ${_analysisS}s`;
    } else if (_mode === 'comparison') {
      titleEl.textContent = `Session Comparison`;
      const sA = _session ? _session.replace('.db', '') : '—';
      const sB = _sessionB ? _sessionB.replace('.db', '') : '—';
      subtitleEl.textContent = `Comparing Session A: ${sA} vs Session B: ${sB} · Baseline: ${_baselineS}s | Analysis: ${_analysisS}s`;
    } else {
      titleEl.textContent = `Per-Engagement Physio Traces`;
      subtitleEl.textContent = `${n} engagements selected · click sidebar rows to filter`;
    }
  }

  function _resetResults() {
    _trialsData = null;
    _epochData = null;
    _trialsDataB = null;
    _epochDataB = null;
    _selectedIds = [];
    LabReplay.TrialSidebar.clear?.();
    LabReplay.EpochChart.clear?.();
    LabReplay.StatsPanel.clear?.();
    const ph = document.getElementById('an-chart-placeholder');
    const ch = document.getElementById('an-epoch-chart');
    const cc = document.getElementById('an-comparison-charts');
    const tt = document.getElementById('an-chart-title');
    if (ph) ph.style.display = '';
    if (ch) ch.style.display = 'none';
    if (cc) cc.style.display = 'none';
    if (tt) tt.style.display = 'none';
  }

  function _renderComparisonGraphs() {
    if (!_epochData || !_epochDataB) return;

    // Left Chart: Session
    LabReplay.EpochChart.init('an-epoch-chart-compare-a');
    LabReplay.EpochChart.setWindows(_baselineS, _analysisS);
    LabReplay.EpochChart.setTerminology(_trialsData.terminology);
    LabReplay.EpochChart.setMode('aggregate');
    LabReplay.EpochChart.render(_epochData, null);

    // Right Chart: Session B
    LabReplay.EpochChart.init('an-epoch-chart-compare-b');
    LabReplay.EpochChart.setWindows(_baselineS, _analysisS);
    LabReplay.EpochChart.setTerminology(_trialsDataB.terminology);
    LabReplay.EpochChart.setMode('aggregate');
    LabReplay.EpochChart.render(_epochDataB, null);
  }

  function _setStatus(html, type) {
    const el = document.getElementById('an-status');
    if (!el) return;
    el.innerHTML = html;
    el.className = `an-status an-status--${type || ''}`;
  }

  return { init };
})();
