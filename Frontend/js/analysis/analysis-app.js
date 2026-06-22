/**
 * analysis-app.js — Top-level controller for the Analysis tab.
 *
 * Unified view (no Aggregate/Per-Engagement toggle):
 *   1. Aggregate stats card (top, always visible after analysis)
 *   2. Epoch chart (shows all-trial average; updates to single trial on row click)
 *   3. Per-trial table (full-width, scrollable; clicking a row updates the chart)
 *
 * Comparison mode (separate ⇄ Compare button):
 *   Side-by-side epoch charts for two sessions.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.AnalysisApp = (function () {

  // ── State ──────────────────────────────────────────────────────────────────
  let _drill       = 'behdisc';
  let _session     = null;
  let _sessionB    = null;
  let _baselineS   = 30.0;
  let _analysisS   = 2.0;
  let _isCompare   = false;   // toggles comparison mode
  let _aggFilter   = 'all';   // 'all' | 'hostile' | 'nonhostile'
  let _trialsData  = null;
  let _epochData   = null;
  let _trialsDataB = null;
  let _epochDataB  = null;
  let _selectedIds = [];      // trial indices currently highlighted in table

  const DRILLS = [
    { key: 'behdisc',  label: 'BehDisc',  longLabel: 'Behavioral Discrimination' },
    { key: 'pvt',      label: 'PVT',       longLabel: 'Psychomotor Vigilance Task' },
    { key: 'l2gonogo', label: 'L2GoNoGo',  longLabel: 'L2 Go/No-Go' },
  ];

  const SIGNALS = ['hr', 'pupil', 'rmssd'];

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
      <!-- Control bar -->
      <div class="an-control-bar">
        <div class="an-controls-row">
          <div class="an-control-group">
            <label class="an-label">Drill</label>
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
            <label class="an-label">Baseline Window
              <span class="an-label-hint" title="First T seconds of session used as physiological reference">ⓘ</span>
            </label>
            <div class="an-spinner-wrap">
              <button class="an-spin-btn" id="an-bl-dec">−</button>
              <span class="an-spin-val" id="an-bl-val">30 s</span>
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

          <!-- Compare toggle -->
          <button class="an-compare-btn" id="an-compare-btn" title="Compare two sessions side-by-side">⇄ Compare</button>

          <!-- Session B (shown only in compare mode) -->
          <div class="an-control-group" id="an-session-b-group" style="display:none">
            <label class="an-label">Session B</label>
            <select class="an-select" id="an-session-select-b">
              <option value="">— select session —</option>
            </select>
          </div>

          <button class="an-run-btn" id="an-run-btn" disabled>▶ Analyze</button>
        </div>

        <div class="an-status" id="an-status"></div>
      </div>

      <!-- Baseline stats panel (session-opening baseline) -->
      <div class="an-baseline-panel" id="an-baseline-panel" style="display:none"></div>

      <!-- Single scrollable column -->
      <div class="an-main">

        <!-- 1. Aggregate stats -->
        <div class="an-stats-card" id="an-stats-panel"></div>

        <!-- 2. Per-trial accordion — immediately below aggregate stats -->
        <div id="an-accordion" style="display:none">
          <div class="an-accordion-header">
            <span class="an-accordion-title">Per-Trial Breakdown</span>
            <span class="an-accordion-hint">▸ Click any trial to expand</span>
          </div>
          <div class="an-accordion-list" id="an-accordion-list"></div>
        </div>

        <!-- 3. Epoch chart (below accordion) -->
        <div class="an-chart-card" id="an-chart-card">
          <div class="an-chart-title" id="an-chart-title" style="display:none">
            <span id="an-chart-title-text"></span>
            <span class="an-chart-subtitle" id="an-chart-subtitle"></span>
          </div>
          <div class="an-chart-placeholder" id="an-chart-placeholder">
            <span></span>
            <p>Select a drill and session, then click <strong>Analyze</strong>.</p>
          </div>
          <div id="an-epoch-chart" style="display:none"></div>
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
          <div class="an-agg-filter-bar" id="an-agg-filter-bar" style="display:none">
            <span class="an-agg-filter-label">Show:</span>
            <button class="an-agg-filter-btn an-agg-filter-btn--active" data-agg="all">Both</button>
            <button class="an-agg-filter-btn" data-agg="hostile">Hostile only</button>
            <button class="an-agg-filter-btn" data-agg="nonhostile">Non-Hostile only</button>
          </div>
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

    // Session selects
    page.addEventListener('change', e => {
      if (e.target.id === 'an-session-select') {
        _session = e.target.value || null;
        _updateRunBtn();
      } else if (e.target.id === 'an-session-select-b') {
        _sessionB = e.target.value || null;
        _updateRunBtn();
      }
    });

    // Baseline Window spinner
    _bindSpinner('an-bl-dec', 'an-bl-inc', 'an-bl-val',
      v => { _baselineS = v; },
      () => _baselineS, 5, 60, 5,
      v => `${v} s`);

    // Analysis Window spinner
    _bindSpinner('an-an-dec', 'an-an-inc', 'an-an-val',
      v => { _analysisS = v; },
      () => _analysisS, 0.5, 15.0, 0.5,
      v => `${v.toFixed(1)} s`);

    // Compare toggle button
    page.addEventListener('click', e => {
      if (e.target.id !== 'an-compare-btn') return;
      _isCompare = !_isCompare;
      const btn = document.getElementById('an-compare-btn');
      if (btn) btn.classList.toggle('an-compare-btn--active', _isCompare);
      const bGroup = document.getElementById('an-session-b-group');
      if (bGroup) bGroup.style.display = _isCompare ? 'flex' : 'none';
      _updateRunBtn();
      _resetResults();
    });

    // Aggregate filter buttons (BehDisc)
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

    // Trial row selection from table → update stats panel + toggle chart
    document.addEventListener('trialselect', e => {
      _selectedIds = e.detail.trialIds;
      if (!_epochData) return;

      const chart    = document.getElementById('an-epoch-chart');
      const chartTit = document.getElementById('an-chart-title');
      const filterBr = document.getElementById('an-agg-filter-bar');

      if (_selectedIds.length === 0) {
        // Deselected → restore aggregate view (chart + aggregate stats)
        if (chart)    chart.style.display    = 'block';
        if (chartTit) chartTit.style.display = 'flex';
        if (filterBr) filterBr.style.display = _epochData.has_type_split ? 'flex' : 'none';
        LabReplay.EpochChart.setMode('aggregate');
        LabReplay.EpochChart.render(_epochData, null);
        _updateChartTitle();
        LabReplay.StatsPanel.render(_epochData, _trialsData.summary);
      } else {
        // Trial selected → hide chart, show per-trial stats panel
        if (chart)    chart.style.display    = 'none';
        if (chartTit) chartTit.style.display = 'none';
        if (filterBr) filterBr.style.display = 'none';

        const trialIdx = _selectedIds[0];
        const trial = (_epochData.trials_used || []).find(t => t.index === trialIdx);
        if (trial) {
          LabReplay.StatsPanel.renderPerEngagement(_epochData, trial);
        }
      }
    });
  }

  function _updateRunBtn() {
    const btn = document.getElementById('an-run-btn');
    if (!btn) return;
    btn.disabled = _isCompare ? (!_session || !_sessionB) : !_session;
  }

  function _bindSpinner(decId, incId, valId, setter, getter, min, max, step, fmt) {
    const format = fmt || (v => v.toFixed(1) + ' s');
    document.getElementById(decId)?.addEventListener('click', () => {
      const nv = Math.max(min, Math.round((getter() - step) * 100) / 100);
      setter(nv);
      document.getElementById(valId).textContent = format(nv);
    });
    document.getElementById(incId)?.addEventListener('click', () => {
      const nv = Math.min(max, Math.round((getter() + step) * 100) / 100);
      setter(nv);
      document.getElementById(valId).textContent = format(nv);
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
    _session = null; _sessionB = null;

    try {
      const sessions = await LabReplay.AnalysisAPI.sessions(drill);
      const matching = sessions.filter(s => s.drill === drill);

      if (!matching.length) {
        selA.innerHTML = `<option value="">No ${drill} sessions found</option>`;
        selB.innerHTML = `<option value="">No ${drill} sessions found</option>`;
        return;
      }

      const optHtml = `<option value="">— select session —</option>` +
        matching.map(s => {
          const dur = s.duration_s ? ` (${Math.round(s.duration_s / 60)}m)` : '';
          return `<option value="${s.filename}">${s.filename.replace('.db', '')}${dur}</option>`;
        }).join('');

      selA.innerHTML = optHtml;
      selB.innerHTML = optHtml;
    } catch {
      selA.innerHTML = '<option value="">API unavailable</option>';
      selB.innerHTML = '<option value="">API unavailable</option>';
    }
  }

  // ── Run analysis ───────────────────────────────────────────────────────────

  async function _runAnalysis() {
    if (!_session || !_drill) return;
    if (_isCompare) { await _runComparison(); return; }

    _setStatus('Loading trials…', 'loading');
    _resetResults();

    try {
      // 1. Fetch trials
      _trialsData = await LabReplay.AnalysisAPI.trials(_session, _drill);
      const { trials, summary, terminology } = _trialsData;

      if (!trials || trials.length === 0) {
        _setStatus('No trials found in this session.', 'warn');
        return;
      }

      const termLabel = terminology.trial_label || 'trial';
      _setStatus(`Found ${trials.length} ${termLabel}s — computing epochs…`, 'loading');

      // 2. Fetch epochs (all trials)
      _epochData = await LabReplay.AnalysisAPI.epoch({
        session:       _session,
        drill:         _drill,
        trial_ids:     trials.map(t => t.index),
        signals:       SIGNALS,
        baseline_s:    _baselineS,
        analysis_s:    _analysisS,
        bin_s:         0.1,
        do_zscore:     false,
        baseline_mode: 'session_opening',
      });

      // 3. Aggregate stats card (top)
      LabReplay.StatsPanel.init(document.getElementById('an-stats-panel'));
      LabReplay.StatsPanel.setTerminology(terminology);
      LabReplay.StatsPanel.render(_epochData, summary);

      // 4. Epoch chart — aggregate (all trials)
      document.getElementById('an-chart-placeholder').style.display = 'none';
      document.getElementById('an-epoch-chart').style.display = 'block';
      document.getElementById('an-chart-title').style.display = 'flex';
      document.getElementById('an-comparison-charts').style.display = 'none';

      LabReplay.EpochChart.init('an-epoch-chart');
      LabReplay.EpochChart.setWindows(0, _analysisS);
      LabReplay.EpochChart.setTerminology(terminology);
      LabReplay.EpochChart.setMode('aggregate');
      LabReplay.EpochChart.render(_epochData, null);

      // 4b. Baseline panel
      _renderBaselinePanel(_epochData.session_baseline);

      // 4c. BehDisc aggregate filter bar
      const filterBar = document.getElementById('an-agg-filter-bar');
      if (filterBar) filterBar.style.display = _epochData.has_type_split ? 'flex' : 'none';

      // 5. Per-trial accordion cards
      const accordionTrials = _epochData.trials_used && _epochData.trials_used.length > 0
        ? _epochData.trials_used
        : trials;
      _renderAccordion(accordionTrials, terminology, _epochData);

      _selectedIds = [];
      _updateChartTitle();
      _setStatus('Analysis complete.', 'ok');

    } catch (e) {
      _setStatus(`Error: ${e.message}`, 'error');
      console.error('[AnalysisApp]', e);
    }
  }

  async function _runComparison() {
    if (!_sessionB) return;
    _setStatus('Comparing sessions…', 'loading');

    const sA = _session;
    const sB = _sessionB;
    _resetResults();
    _session = sA; _sessionB = sB;

    try {
      const [resA, resB] = await Promise.all([
        LabReplay.AnalysisAPI.trials(sA, _drill),
        LabReplay.AnalysisAPI.trials(sB, _drill),
      ]);

      if (!resA.trials.length || !resB.trials.length) {
        _setStatus('One or both sessions contain no trials.', 'warn');
        return;
      }

      _setStatus('Computing epochs…', 'loading');

      const [epA, epB] = await Promise.all([
        LabReplay.AnalysisAPI.epoch({
          session: sA, drill: _drill,
          trial_ids: resA.trials.map(t => t.index), signals: SIGNALS,
          baseline_s: _baselineS, analysis_s: _analysisS, bin_s: 0.1, do_zscore: false,
        }),
        LabReplay.AnalysisAPI.epoch({
          session: sB, drill: _drill,
          trial_ids: resB.trials.map(t => t.index), signals: SIGNALS,
          baseline_s: _baselineS, analysis_s: _analysisS, bin_s: 0.1, do_zscore: false,
        }),
      ]);

      _trialsData = resA; _epochData = epA;
      _trialsDataB = resB; _epochDataB = epB;

      _setStatus('Analysis complete.', 'ok');

      document.getElementById('an-chart-placeholder').style.display = 'none';
      document.getElementById('an-epoch-chart').style.display = 'none';
      document.getElementById('an-comparison-charts').style.display = 'flex';
      document.getElementById('an-chart-title').style.display = 'flex';
      document.getElementById('an-agg-filter-bar').style.display = 'none';

      _updateChartTitle();
      _renderComparisonGraphs();

      LabReplay.StatsPanel.init(document.getElementById('an-stats-panel'));
      LabReplay.StatsPanel.setTerminology(resA.terminology);
      LabReplay.StatsPanel.renderComparison(_epochData, _trialsData, _epochDataB, _trialsDataB, _drill);

    } catch (e) {
      _setStatus('Comparison failed: ' + e.message, 'error');
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function _updateChartTitle() {
    const titleEl    = document.getElementById('an-chart-title-text');
    const subtitleEl = document.getElementById('an-chart-subtitle');
    if (!titleEl) return;

    if (_isCompare) {
      titleEl.textContent = 'Session Comparison';
      const sA = _session    ? _session.replace('.db', '')  : '—';
      const sB = _sessionB   ? _sessionB.replace('.db', '') : '—';
      subtitleEl.textContent = `${sA} vs ${sB} · Baseline: first ${_baselineS}s · Analysis: ${_analysisS}s`;
      return;
    }

    if (!_trialsData) return;
    const { trials } = _trialsData;

    if (_selectedIds.length === 1) {
      const term = _trialsData.terminology;
      titleEl.textContent = `${term.trial_label || 'Trial'} #${_selectedIds[0]} — Physio Trace`;
      subtitleEl.textContent = `Click row again to return to aggregate · Analysis: ${_analysisS}s post-onset`;
    } else {
      titleEl.textContent = 'Aggregate Physio Response';
      subtitleEl.textContent = `N = ${trials.length} trials · Baseline: first ${_baselineS}s · Analysis: ${_analysisS}s post-onset`;
    }
  }

  // ── Per-trial accordion ────────────────────────────────────────────────────

  function _renderAccordion(trials, terminology, epochData) {
    const wrap = document.getElementById('an-accordion');
    const list = document.getElementById('an-accordion-list');

    if (!wrap || !list) {
      console.error('[Accordion] MISSING ELEMENTS — an-accordion or an-accordion-list not found in DOM');
      return;
    }
    if (!trials || trials.length === 0) {
      console.warn('[Accordion] No trials — hiding accordion');
      wrap.style.display = 'none';
      return;
    }


    const term = terminology || {};
    const signals = epochData
      ? Object.keys(epochData.signals).filter(k => !epochData.signals[k].error)
      : [];

    // Pre-compute per-trial physio
    const physioByTrial = {};
    signals.forEach(sig => {
      const sigData = epochData.signals[sig];
      const times   = sigData.times || [];
      const blMean  = epochData.session_baseline?.[sig]?.mean ?? null;
      (sigData.epochs || []).forEach(ep => {
        const idx  = ep.trial_index;
        if (!physioByTrial[idx]) physioByTrial[idx] = {};
        const anVals = (ep.values || []).filter((v, i) => v != null && !isNaN(v) && (times[i] ?? -1) >= 0);
        const anMean = anVals.length > 0 ? anVals.reduce((a, b) => a + b, 0) / anVals.length : null;
        const delta  = (anMean != null && blMean != null) ? anMean - blMean : null;
        const pct    = (delta  != null && blMean)         ? (delta / Math.abs(blMean)) * 100 : null;
        physioByTrial[idx][sig] = {
          anMean, delta, pct,
          unit:  sigData.unit  || SIGNAL_UNITS[sig]  || '',
          label: sigData.label || SIGNAL_LABELS[sig] || sig,
          color: sigData.color || SIGNAL_COLORS[sig] || '#888',
        };
      });
    });

    const outcomeLabel = (oc) => term[
      oc === 'HIT'              ? 'hit_label'   :
      oc === 'MISS'             ? 'miss_label'  :
      oc === 'LAPSE'||oc==='OMISSION' ? 'lapse_label' :
      oc === 'COMMISSION_ERROR' ? 'fa_label'    :
      null
    ] || oc;

    const outcomeClass = (oc) =>
      (oc === 'HIT' || oc === 'CORRECT_WITHHOLD') ? 'an-ac-badge--good' :
      (oc === 'MISS'||oc === 'LAPSE'||oc === 'OMISSION') ? 'an-ac-badge--bad' : '';

    list.innerHTML = trials.map(t => {
      const ocLabel = outcomeLabel(t.outcome || '');
      const ocCls   = outcomeClass(t.outcome || '');
      const rt      = t.rt_s != null ? `${Math.round(t.rt_s * 1000)} ms` : '—';
      const ttff    = t.ttff_ms != null ? `${t.ttff_ms} ms` : '—';
      const gaze    = t.gaze_dispersion_px != null ? `${t.gaze_dispersion_px.toFixed(0)} px` : '—';
      // Drill-aware field aliases (PVT uses direction_deg/screen; L2GoNoGo uses direction/distance_m)
      const screen     = t.screen != null ? `Screen ${t.screen}` : '';
      const dirDeg     = t.direction_deg ?? t.direction;   // both drills
      const dir        = dirDeg != null ? ` · ${Math.round(dirDeg)}°` : '';
      const distStr    = t.distance_m  != null ? `${t.distance_m.toFixed(1)} m`  : null;
      const nShotsStr  = t.n_shots     > 0     ? String(t.n_shots)               : null;
      const subScen    = t.sub_scenario || null;
      const targetType = t.target_type  || null;

      // Physio summary for the collapsed row
      const physio = physioByTrial[t.index] || {};
      const physioSummary = signals.map(sig => {
        const d = physio[sig];
        if (!d || d.delta == null) return '';
        const sign = d.delta > 0 ? '+' : '';
        const arr  = d.delta > 0.5 ? '↑' : d.delta < -0.5 ? '↓' : '→';
        const cls  = d.delta > 0.5 ? 'an-ac-delta--up' : d.delta < -0.5 ? 'an-ac-delta--dn' : '';
        return `<span class="an-ac-delta ${cls}">${d.label}: ${sign}${d.delta.toFixed(1)} ${d.unit} ${arr}</span>`;
      }).filter(Boolean).join('');

      // Physio detail table for expanded section
      const physioRows = signals.map(sig => {
        const d = physio[sig];
        if (!d) return '';
        const anStr = d.anMean != null ? `${d.anMean.toFixed(2)} ${d.unit}` : '—';
        const dStr  = d.delta  != null ? `${d.delta > 0 ? '+' : ''}${d.delta.toFixed(2)} ${d.unit}` : '—';
        const pStr  = d.pct    != null ? `${d.pct > 0 ? '+' : ''}${d.pct.toFixed(1)}%` : '—';
        const arr   = d.delta  != null ? (d.delta > 0 ? '↑' : d.delta < 0 ? '↓' : '→') : '';
        const cls   = d.delta  != null ? (d.delta > 0.5 ? 'an-ac-delta--up' : d.delta < -0.5 ? 'an-ac-delta--dn' : '') : '';
        return `<tr>
          <td><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${d.color};margin-right:5px;vertical-align:middle"></span>${d.label} <span class="an-ac-unit">${d.unit}</span></td>
          <td>${anStr}</td>
          <td class="${cls}">${dStr}</td>
          <td class="${cls}">${pStr} ${arr}</td>
        </tr>`;
      }).join('');

      const physioSection = physioRows ? `
        <div class="an-ac-section">Physio Response (Analysis Window vs Session Baseline)</div>
        <table class="an-ac-physio">
          <thead><tr><th>Signal</th><th>Analysis Mean</th><th>Δ (units)</th><th>Δ %</th></tr></thead>
          <tbody>${physioRows}</tbody>
        </table>` : '';

      return `
        <details class="an-ac-card">
          <summary class="an-ac-summary">
            <span class="an-ac-num">#${t.index}</span>
            ${targetType ? `<span class="an-ac-badge ${targetType === 'GO' ? 'an-ac-badge--go' : 'an-ac-badge--nogo'}">${targetType}</span>` : ''}
            <span class="an-ac-badge ${ocCls}">${ocLabel}</span>
            <span class="an-ac-metric"><span class="an-ac-metric-label">RT</span> ${rt}</span>
            <span class="an-ac-metric"><span class="an-ac-metric-label">TTFF</span> ${ttff}</span>
            <span class="an-ac-metric"><span class="an-ac-metric-label">Gaze Disp</span> ${gaze}</span>
            ${screen ? `<span class="an-ac-metric"><span class="an-ac-metric-label">${screen}${dir}</span></span>` : ''}
            ${!screen && dirDeg != null ? `<span class="an-ac-metric"><span class="an-ac-metric-label">${Math.round(dirDeg)}°</span></span>` : ''}
            ${distStr ? `<span class="an-ac-metric"><span class="an-ac-metric-label">Dist</span> ${distStr}</span>` : ''}
            <span class="an-ac-physio-summary">${physioSummary}</span>
            <span class="an-ac-arrow">▸</span>
          </summary>
          <div class="an-ac-body">
            <div class="an-ac-kv-grid">
              <div class="an-ac-kv"><span class="an-ac-kv-label">Outcome</span><span class="an-ac-kv-val">${ocLabel}</span></div>
              <div class="an-ac-kv"><span class="an-ac-kv-label">${term.rt_label || 'Reaction Time'}</span><span class="an-ac-kv-val">${rt}</span></div>
              ${nShotsStr ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Shots Fired</span><span class="an-ac-kv-val">${nShotsStr}</span></div>` : ''}
              ${t.screen != null ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Screen</span><span class="an-ac-kv-val">${t.screen}</span></div>` : ''}
              ${dirDeg != null ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Direction</span><span class="an-ac-kv-val">${Math.round(dirDeg)}°</span></div>` : ''}
              ${distStr ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Distance</span><span class="an-ac-kv-val">${distStr}</span></div>` : ''}
              ${t.ttff_ms != null ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Time to First Fixation</span><span class="an-ac-kv-val">${ttff}</span></div>` : ''}
              ${t.gaze_dispersion_px != null ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Gaze Dispersion</span><span class="an-ac-kv-val">${gaze}</span></div>` : ''}
              ${subScen ? `<div class="an-ac-kv"><span class="an-ac-kv-label">Sub-scenario</span><span class="an-ac-kv-val" style="font-size:10px">${subScen}</span></div>` : ''}
            </div>
            ${physioSection}
          </div>
        </details>`;
    }).join('');

    wrap.style.display = '';
  }

  function _resetResults() {
    _trialsData = null; _epochData = null;
    _trialsDataB = null; _epochDataB = null;
    _selectedIds = [];
    LabReplay.TrialSidebar.clear?.();
    LabReplay.EpochChart.clear?.();
    LabReplay.StatsPanel.clear?.();

    const ph  = document.getElementById('an-chart-placeholder');
    const ch  = document.getElementById('an-epoch-chart');
    const cc  = document.getElementById('an-comparison-charts');
    const tt  = document.getElementById('an-chart-title');
    const bp  = document.getElementById('an-baseline-panel');
    const fb  = document.getElementById('an-agg-filter-bar');
    const sp  = document.getElementById('an-stats-panel');
    const pe  = document.getElementById('an-pe-panel');
    const cc2 = document.getElementById('an-chart-card');
    const acc = document.getElementById('an-accordion');
    const aList = document.getElementById('an-accordion-list');

    if (ph)    ph.style.display  = '';
    if (ch)    ch.style.display  = 'none';
    if (cc)    cc.style.display  = 'none';
    if (tt)    tt.style.display  = 'none';
    if (fb)    fb.style.display  = 'none';
    if (bp)    { bp.style.display = 'none'; bp.innerHTML = ''; }
    if (sp)    sp.innerHTML = '';
    if (pe)    { pe.style.display = 'none'; pe.innerHTML = ''; }
    if (cc2)   cc2.style.display = '';
    if (acc)   acc.style.display = 'none';
    if (aList) aList.innerHTML = '';
  }

  // ── Baseline panel ─────────────────────────────────────────────────────────

  const SIGNAL_LABELS = { hr: 'Heart Rate', pupil: 'Pupil', rmssd: 'RMSSD' };
  const SIGNAL_UNITS  = { hr: 'bpm', pupil: 'mm', rmssd: 'ms' };
  const SIGNAL_COLORS = { hr: '#C94444', pupil: '#7C6AE8', rmssd: '#22C55E' };

  function _renderBaselinePanel(sessionBaseline) {
    const el = document.getElementById('an-baseline-panel');
    if (!el) return;
    if (!sessionBaseline || Object.keys(sessionBaseline).length === 0) {
      el.style.display = 'none'; return;
    }

    const cards = Object.entries(sessionBaseline)
      .filter(([, stats]) => stats && !stats.error && stats.mean !== null)
      .map(([sig, stats]) => {
        const label = SIGNAL_LABELS[sig] || sig;
        const unit  = SIGNAL_UNITS[sig]  || stats.unit || '';
        const color = SIGNAL_COLORS[sig] || '#888';
        const mean  = stats.mean.toFixed(1);
        const sd    = stats.sd !== null ? stats.sd.toFixed(1) : '—';
        return `
          <div class="an-bl-card">
            <div class="an-bl-card-dot" style="background:${color}"></div>
            <div class="an-bl-card-body">
              <span class="an-bl-card-label">${label}</span>
              <span class="an-bl-card-value">${mean}<span class="an-bl-card-unit"> ${unit}</span></span>
              <span class="an-bl-card-sd">± ${sd} &nbsp;<span class="an-bl-card-n">n=${stats.n}</span></span>
            </div>
          </div>`;
      }).join('');

    el.innerHTML = `
      <div class="an-bl-header">
        <span class="an-bl-title">SESSION BASELINE</span>
        <span class="an-bl-duration">First ${_baselineS}s</span>
      </div>
      <div class="an-bl-cards">${cards}</div>
    `;
    el.style.display = 'block';
  }

  function _renderComparisonGraphs() {
    if (!_epochData || !_epochDataB) return;

    LabReplay.EpochChart.init('an-epoch-chart-compare-a');
    LabReplay.EpochChart.setWindows(_baselineS, _analysisS);
    LabReplay.EpochChart.setTerminology(_trialsData.terminology);
    LabReplay.EpochChart.setMode('aggregate');
    LabReplay.EpochChart.render(_epochData, null);

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
