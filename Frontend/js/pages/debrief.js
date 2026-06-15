/**
 * pages/debrief.js — Performance Debrief Page
 *
 * Detects psychophysiological dissociation events from a VirTra/drill session.
 *
 * Dissociation types (rule-based PLI classifier):
 *   Type A — Silent Competence  (PLI ≥ 1.0 + correct outcome)
 *   Type B — Silent Failure     (PLI ≤ 0.5 + error outcome)
 *   Type C — Attentional Tunnel (elevated load + error under gaze engagement)
 *   Type D — Fatigue Resilience (late-session correct + rising LF/HF trend)
 *
 * ML Classifier: reserved — not yet implemented. Each card shows a
 *   separate "ML Classifier" section labeled "Pending" until a model
 *   is available. PLI (rule-based) and ML are always rendered distinctly.
 *
 * Layout: vertical scrollable feed
 *   - Baseline panel: shows physiological resting stats once computed
 *   - Summary bar: type counts, session PLI stats
 *   - Feed: one card per trial, sorted by dissociation score (highest first)
 *     Each card is collapsible. Expandable body shows:
 *       1. PLI block  — rule-based classification
 *       2. ML block   — placeholder (pending)
 *       3. Signal breakdown (HR, Pupil, RMSSD)
 *       4. Type D session data (if applicable)
 *       5. Debrief prompt
 *
 * API: POST /api/dissociation/analyze (Analysis API, port 8081)
 * Accepts: session, drill, baseline_s, analysis_s, lfhf_slope_threshold
 */

window.LabReplay = window.LabReplay || {};

LabReplay.DebriefPage = (function () {

  const ANALYSIS_BASE = 'http://127.0.0.1:8081';

  // ── Type metadata ────────────────────────────────────────────────────────────
  const TYPE_META = {
    type_a:       { label: 'Type A — Silent Competence',  short: 'Type A',   color: '#2D8E54' },
    type_b:       { label: 'Type B — Silent Failure',     short: 'Type B',   color: '#C94444' },
    type_c:       { label: 'Type C — Attentional Tunnel', short: 'Type C',   color: '#C47A2A' },
    type_d:       { label: 'Type D — Fatigue Resilience', short: 'Type D',   color: '#6B5DB8' },
    routine_error:{ label: 'Routine Error',               short: 'Routine',  color: '#7B8499' },
    unclassified: { label: 'Unclassified',                short: '—',        color: '#A0A8B8' },
  };

  const DISSOCIATION_TYPES = ['type_a', 'type_b', 'type_c', 'type_d'];
  const TYPE_ORDER          = ['type_a', 'type_b', 'type_c', 'type_d', 'routine_error', 'unclassified'];

  // ── State ────────────────────────────────────────────────────────────────────
  let _initialized = false;
  let _sessions    = [];
  let _drills      = ['behdisc', 'pvt']; // will be extended as drills are added

  // ── Init ─────────────────────────────────────────────────────────────────────

  function init() {
    LabReplay.TopBarManager.register('debrief', _renderTopBar, _teardown);

    LabReplay.EventBus.on('page-changed', (page) => {
      if (page === 'debrief') _onPageActivate();
    });
  }

  function _onPageActivate() {
    if (!_initialized) {
      _buildPageShell();
      _initialized = true;
    }
    _loadSessions();
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // TOP BAR
  // ══════════════════════════════════════════════════════════════════════════════

  function _renderTopBar(slot) {
    slot.innerHTML = `
      <div class="topbar" id="debrief-topbar">

        <span class="topbar-page-title">Performance Debrief</span>
        <div class="topbar-sep"></div>

        <div class="debrief-topbar-controls">

          <!-- Session file -->
          <select id="db-session-select" class="topbar-select" style="min-width:190px">
            <option value="">— Session —</option>
          </select>

          <!-- Drill selector -->
          <select id="db-drill-select" class="topbar-select">
            <option value="behdisc">BehDisc</option>
            <option value="pvt">PVT</option>
          </select>

          <div class="topbar-sep"></div>

          <!-- Baseline window -->
          <div class="debrief-param-group">
            <span class="debrief-param-label">Baseline</span>
            <input id="db-baseline-s" type="number" class="debrief-param-input"
                   value="60" min="10" max="300" step="5">
            <span class="debrief-param-label">s</span>
          </div>

          <!-- Analysis window -->
          <div class="debrief-param-group">
            <span class="debrief-param-label">Analysis</span>
            <input id="db-analysis-s" type="number" class="debrief-param-input"
                   value="2" min="0.5" max="10" step="0.5">
            <span class="debrief-param-label">s</span>
          </div>

          <!-- Run -->
          <button id="db-run-btn" class="debrief-run-btn" disabled>
            ▶ Run Analysis
          </button>

        </div>

        <div class="topbar-sep"></div>

        <div class="topbar-section topbar-section--right">
          <span id="db-api-status" style="font-size:var(--font-size-xs);color:var(--text-dim)"></span>
        </div>

      </div>
    `;

    _checkApiHealth();
    document.getElementById('db-session-select').addEventListener('change', _onParamChange);
    document.getElementById('db-drill-select').addEventListener('change', _onParamChange);
    document.getElementById('db-run-btn').addEventListener('click', _runAnalysis);
  }

  function _checkApiHealth() {
    fetch(`${ANALYSIS_BASE}/api/health`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(() => {
        const el = document.getElementById('db-api-status');
        if (el) { el.textContent = '● Analysis API'; el.style.color = '#10B981'; }
      })
      .catch(() => {
        const el = document.getElementById('db-api-status');
        if (el) { el.textContent = '⚠ Analysis API offline'; el.style.color = '#F59E0B'; }
      });
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // PAGE SHELL
  // ══════════════════════════════════════════════════════════════════════════════

  function _buildPageShell() {
    const page = document.getElementById('page-debrief');
    if (!page) return;

    page.innerHTML = `

      <!-- BASELINE PANEL — shown once analysis completes -->
      <div id="db-baseline-panel" class="debrief-baseline-panel hidden">
        <div class="baseline-panel-title">
          Physiological Baseline
          <span id="db-baseline-duration-chip" class="baseline-duration-chip"></span>
        </div>
        <div id="db-baseline-signals" class="baseline-signals-row"></div>
        <div id="db-baseline-warning"></div>
      </div>

      <!-- SUMMARY BAR -->
      <div id="db-summary-bar" class="debrief-summary-bar"></div>

      <!-- FEED — scrollable event cards -->
      <div id="db-feed" class="debrief-feed">
        <div class="debrief-feed-empty">
          <div class="debrief-feed-empty-icon">◈</div>
          <div class="debrief-feed-empty-text">
            Select a session and drill, then run analysis<br>
            to see the dissociation event feed.
          </div>
        </div>
      </div>

    `;
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // SESSIONS
  // ══════════════════════════════════════════════════════════════════════════════

  function _loadSessions() {
    const drill = document.getElementById('db-drill-select')?.value || 'behdisc';
    fetch(`${ANALYSIS_BASE}/api/sessions?drill=${drill}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        _sessions = Array.isArray(data) ? data.map(s => s.filename || s) : [];
        const select = document.getElementById('db-session-select');
        if (!select) return;
        const current = select.value;
        select.innerHTML = '<option value="">— Session —</option>';
        _sessions.forEach(s => {
          const filename = typeof s === 'string' ? s : s.filename;
          const opt = document.createElement('option');
          opt.value = filename;
          opt.textContent = filename;
          if (filename === current) opt.selected = true;
          select.appendChild(opt);
        });
        _updateRunBtn();
      })
      .catch(() => console.warn('[DebriefPage] session load failed'));
  }

  function _onParamChange() {
    _updateRunBtn();
    // Reset page when params change
    _resetPage();
  }

  function _updateRunBtn() {
    const session = document.getElementById('db-session-select')?.value;
    const btn     = document.getElementById('db-run-btn');
    if (btn) btn.disabled = !session;
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // RUN ANALYSIS
  // ══════════════════════════════════════════════════════════════════════════════

  async function _runAnalysis() {
    const session   = document.getElementById('db-session-select')?.value;
    const drill     = document.getElementById('db-drill-select')?.value || 'behdisc';
    const baselineS = parseFloat(document.getElementById('db-baseline-s')?.value) || 60;
    const analysisS = parseFloat(document.getElementById('db-analysis-s')?.value) || 2;

    if (!session) {
      _showFeedError('No session selected.');
      return;
    }

    const payload = {
      session,
      drill,
      baseline_s:           baselineS,
      analysis_s:           analysisS,
      lfhf_slope_threshold: 0.3,
    };

    console.log('[DebriefPage] sending payload:', payload);

    const btn = document.getElementById('db-run-btn');
    if (btn) {
      btn.disabled  = true;
      btn.innerHTML = '<span class="debrief-spinner"></span> Analyzing…';
    }

    _resetPage();

    try {
      const res = await fetch(`${ANALYSIS_BASE}/api/dissociation/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        // FastAPI 422 returns {detail: [{msg:..., loc:..., type:...}]}
        let msg = res.statusText;
        if (errBody?.detail) {
          if (Array.isArray(errBody.detail)) {
            msg = errBody.detail.map(e => `${e.loc?.join('.')}: ${e.msg}`).join('\n');
          } else {
            msg = String(errBody.detail);
          }
        }
        console.error('[DebriefPage] API error', res.status, errBody);
        throw new Error(`${res.status}: ${msg}`);
      }

      const result = await res.json();
      _renderResult(result);

    } catch (e) {
      _showFeedError(e.message);
    } finally {
      if (btn) {
        btn.disabled  = false;
        btn.innerHTML = '▶ Run Analysis';
      }
    }
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // RENDER RESULT
  // ══════════════════════════════════════════════════════════════════════════════

  function _renderResult(result) {
    const { trials = [], summary = {}, baseline = {}, session_meta = {}, drill = {} } = result;

    _renderBaselinePanel(baseline, session_meta);
    _renderSummaryBar(summary);
    _renderFeed(trials, session_meta, drill);
  }

  // ── Baseline panel ───────────────────────────────────────────────────────────

  function _renderBaselinePanel(baseline, session_meta) {
    if (!baseline || !baseline.baseline_duration_s) return;

    const panel = document.getElementById('db-baseline-panel');
    if (panel) panel.classList.remove('hidden');

    const chip = document.getElementById('db-baseline-duration-chip');
    if (chip) chip.textContent = `${baseline.baseline_duration_s}s recorded`;

    const signalContainer = document.getElementById('db-baseline-signals');
    if (signalContainer) {
      signalContainer.innerHTML = [
        _baselineSignalBlock('Heart Rate',     baseline.hr_mean,    baseline.hr_sd,    'bpm',  baseline.hr_n),
        _baselineSignalBlock('Pupil Ø',        baseline.pupil_mean, baseline.pupil_sd, 'mm',   baseline.pupil_n),
        _baselineSignalBlock('RMSSD',          baseline.rmssd_mean, baseline.rmssd_sd, 'ms',   baseline.rmssd_n),
      ].join('');
    }

    const warningEl = document.getElementById('db-baseline-warning');
    if (warningEl && baseline.warning) {
      warningEl.innerHTML = `<div class="baseline-warning-banner">⚠ ${baseline.warning}</div>`;
    }
  }

  function _baselineSignalBlock(label, mean, sd, unit, n) {
    if (mean === null || mean === undefined || n < 3) {
      return `
        <div class="baseline-signal-block">
          <div class="baseline-signal-label">${label}</div>
          <div class="baseline-signal-na">No data ${n !== undefined ? `(${n} samples)` : ''}</div>
        </div>
      `;
    }
    return `
      <div class="baseline-signal-block">
        <div class="baseline-signal-label">${label}</div>
        <div class="baseline-signal-value">${mean.toFixed(1)} <span style="font-size:var(--font-size-xs);font-weight:400;color:var(--text-muted)">${unit}</span></div>
        <div class="baseline-signal-sd">σ = ${sd ? sd.toFixed(2) : '—'} &nbsp;·&nbsp; n = ${n}</div>
      </div>
    `;
  }

  // ── Summary bar ──────────────────────────────────────────────────────────────

  function _renderSummaryBar(summary) {
    const bar = document.getElementById('db-summary-bar');
    if (!bar) return;

    if (summary.error) {
      bar.innerHTML = `<span style="font-size:var(--font-size-sm);color:var(--accent-red)">⚠ ${summary.error}</span>`;
      bar.classList.add('visible');
      return;
    }

    const chips = TYPE_ORDER
      .filter(k => (summary.type_counts || {})[k] > 0)
      .map(k => {
        const meta  = TYPE_META[k];
        const count = summary.type_counts[k];
        return `
          <div class="summary-chip">
            <div class="summary-chip-dot" style="background:${meta.color}"></div>
            <span class="summary-chip-count">${count}</span>
            <span class="summary-chip-label">${meta.short}</span>
          </div>
        `;
      }).join('');

    const pliStat = summary.pli_mean !== null && summary.pli_mean !== undefined
      ? `<span class="summary-pli-stat">PLĪ = ${summary.pli_mean} ± ${summary.pli_sd ?? '—'}</span>`
      : '';

    bar.innerHTML = chips + pliStat;
    bar.classList.add('visible');
  }

  // ── Feed ─────────────────────────────────────────────────────────────────────

  function _renderFeed(trials, session_meta, drillMeta) {
    const feed = document.getElementById('db-feed');
    if (!feed) return;
    feed.innerHTML = '';

    if (!trials.length) {
      feed.innerHTML = `<div class="debrief-feed-empty">
        <div class="debrief-feed-empty-icon">◈</div>
        <div class="debrief-feed-empty-text">No trials detected in this session for the selected drill.</div>
      </div>`;
      return;
    }

    const dissocTrials = trials.filter(t => DISSOCIATION_TYPES.includes(t.dissociation_type));
    const otherTrials  = trials.filter(t => !DISSOCIATION_TYPES.includes(t.dissociation_type));

    if (dissocTrials.length > 0) {
      feed.appendChild(_sectionLabel(`Dissociation Events — ${dissocTrials.length} detected`));
      dissocTrials.forEach((trial, idx) => {
        const { card, expand } = _buildEventCard(trial, session_meta);
        if (idx === 0) expand();
        feed.appendChild(card);
      });
    }

    if (otherTrials.length > 0) {
      feed.appendChild(_sectionLabel(`Other Trials — ${otherTrials.length}`));
      otherTrials.forEach(trial => {
        const { card } = _buildEventCard(trial, session_meta);
        feed.appendChild(card);
      });
    }
  }

  function _sectionLabel(text) {
    const el = document.createElement('div');
    el.className = 'feed-section-label';
    el.textContent = text;
    return el;
  }

  // ── Event card ──────────────────────────────────────────────────────────────────────
  // All card content uses INLINE STYLES only — no CSS class dependencies.

  function _buildEventCard(trial, session_meta) {
    const dtype       = trial.dissociation_type || 'unclassified';
    const meta        = TYPE_META[dtype] || TYPE_META.unclassified;
    const pli         = trial.pli;
    const t0          = trial.t0;
    const elapsed     = session_meta?.t_first_event != null ? t0 - session_meta.t_first_event : null;
    const timeLabel   = elapsed !== null ? _formatElapsed(elapsed) : `t=${t0.toFixed(1)}s`;
    const pliStr      = pli !== null ? (pli >= 0 ? '+' : '') + pli.toFixed(2) : '—';
    const accentColor = meta.color;

    // Card wrapper
    const card = document.createElement('div');
    card.style.cssText = [
      'display:block',
      'background:#ffffff',
      'border:1.5px solid #e2e5ea',
      `border-left:4px solid ${accentColor}`,
      'border-radius:10px',
      'overflow:hidden',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
      'font-size:13px',
    ].join(';');

    // Header (always visible, block display)
    const header = document.createElement('div');
    header.style.cssText = [
      'display:block',
      'overflow:hidden',
      'padding:10px 16px',
      'background:#ffffff',
      'cursor:pointer',
      'line-height:1.6',
      'border-bottom:1px solid transparent',
    ].join(';');

    // PLI right-aligned (rendered first so float works)
    const scoreEl = document.createElement('span');
    scoreEl.textContent = `PLI ${pliStr}`;
    scoreEl.style.cssText = 'float:right;color:#1a2138;font-size:13px;font-weight:600;font-family:monospace;';
    header.appendChild(scoreEl);

    const toggleEl = document.createElement('span');
    toggleEl.textContent = '▾';
    toggleEl.style.cssText = 'float:right;color:#7b8499;font-size:14px;margin-right:8px;';
    header.appendChild(toggleEl);

    // Left-side content
    const badge = document.createElement('span');
    badge.textContent = meta.short;
    badge.style.cssText = [
      `color:${accentColor}`,
      'font-size:10px',
      'font-weight:700',
      'text-transform:uppercase',
      'letter-spacing:0.05em',
      'background:#f5f6f8',
      'padding:2px 8px',
      'border-radius:3px',
      'display:inline',
      'margin-right:8px',
    ].join(';');
    header.appendChild(badge);

    const infoText = document.createTextNode(
      `Trial ${trial.index}  ·  ${timeLabel}  ·  ${_outcomeLabel(trial.outcome)}` +
      (trial.actor_type ? `  ·  ${trial.actor_type}` : '') +
      (trial.session_quartile ? `  ·  ${trial.session_quartile}` : '') +
      (trial.rt_s != null ? `  ·  RT ${trial.rt_s.toFixed(2)}s` : '')
    );
    const infoSpan = document.createElement('span');
    infoSpan.style.cssText = 'color:#4a5468;font-size:13px;';
    infoSpan.appendChild(infoText);
    header.appendChild(infoSpan);

    // Body (hidden until toggled)
    const body = document.createElement('div');
    body.style.cssText = [
      'display:none',
      'padding:14px 16px 16px',
      'border-top:1px solid #e2e5ea',
      'background:#fafbfc',
    ].join(';');

    // 1. PLI block
    const pliDesc  = trial.dissociation_type_meta?.description || '';
    const expPli   = trial.pli_expected != null
      ? `<span style="color:#7b8499;font-size:11px;"> (session expected: ${trial.pli_expected.toFixed(2)})</span>` : '';
    const pliBlock = document.createElement('div');
    pliBlock.style.cssText = 'margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #eef0f3;';
    pliBlock.innerHTML = `
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#3d6ecc;margin-bottom:6px;">PLI — Rule-Based</div>
      <div style="font-size:26px;font-weight:700;color:#1a2138;font-family:monospace;margin-bottom:4px;">${pliStr}</div>
      <div style="color:#4a5468;font-size:13px;">→ ${meta.label}${expPli}</div>
      ${pliDesc ? `<div style="color:#4a5468;font-size:12px;margin-top:6px;line-height:1.5;">${pliDesc}</div>` : ''}
    `;
    body.appendChild(pliBlock);

    // 2. ML block
    const mlBlock = document.createElement('div');
    mlBlock.style.cssText = 'margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #eef0f3;';
    mlBlock.innerHTML = `
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#6b5db8;margin-bottom:6px;">ML — Classifier</div>
      <div style="color:#7b8499;font-size:12px;font-style:italic;">◌ Pending — model training requires labeled session data.</div>
    `;
    body.appendChild(mlBlock);

    // 3. Signal breakdown
    const sigBlock = document.createElement('div');
    sigBlock.style.cssText = 'margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #eef0f3;';
    const analysisS = session_meta?.analysis_s ?? 2;
    sigBlock.innerHTML = `
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#2b9eb3;margin-bottom:8px;">
        Signal Breakdown <span style="font-weight:400;text-transform:none;letter-spacing:0;">(${analysisS}s post-event)</span>
      </div>
      <div>
        ${_iSigCell('Heart Rate', trial.hr_trial_mean, 'bpm', trial.hr_z, null)}
        ${_iSigCell('Pupil Ø', trial.pupil_trial_mean, 'mm', trial.pupil_z, null)}
        ${_iSigCell('RMSSD', trial.rmssd_trial_mean, 'ms',
          trial.rmssd_z != null ? -trial.rmssd_z : null, '↓RMSSD = ↑load')}
      </div>
    `;
    body.appendChild(sigBlock);

    // 4. Type D (conditional)
    if (trial.type_d_session) {
      const d = trial.type_d_session;
      const qRow = (d.lf_hf_q_means || [])
        .map((v, i) => `Q${i+1}: <b>${v != null ? v.toFixed(3) : '—'}</b>`)
        .join(' | ');
      const tdBlock = document.createElement('div');
      tdBlock.style.cssText = 'margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #eef0f3;';
      tdBlock.innerHTML = `
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#6b5db8;margin-bottom:6px;">Type D — Session Profile</div>
        <div style="font-size:12px;color:#4a5468;line-height:1.8;">
          LF/HF slope: <b>${d.lf_hf_slope?.toFixed(4) ?? '—'}</b>
          &nbsp;·&nbsp; ${d.fatigue_rising ? '↑ Fatigue rising' : '→ Stable'}<br>
          ${qRow}<br>
          Accuracy early: <b>${d.acc_early_pct ?? '—'}%</b>
          &nbsp;|&nbsp; Late: <b>${d.acc_late_pct ?? '—'}%</b>
          &nbsp;·&nbsp; ${d.performance_held ? '✓ Held' : '✗ Dropped'}
        </div>
      `;
      body.appendChild(tdBlock);
    }

    // 5. Debrief prompt
    if (trial.debrief_prompt) {
      const dbBlock = document.createElement('div');
      dbBlock.innerHTML = `
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#7b8499;margin-bottom:6px;">Debrief Prompt</div>
        <div style="font-size:13px;color:#4a5468;line-height:1.6;font-style:italic;">${trial.debrief_prompt}</div>
      `;
      body.appendChild(dbBlock);
    }

    // Toggle logic
    let expanded = false;
    function _toggle() {
      expanded = !expanded;
      body.style.display          = expanded ? 'block' : 'none';
      toggleEl.textContent        = expanded ? '▾' : '▸';
      header.style.borderBottomColor = expanded ? '#e2e5ea' : 'transparent';
    }
    header.addEventListener('click', _toggle);

    card.appendChild(header);
    card.appendChild(body);
    return { card, expand: _toggle };
  }

  /** Inline signal cell — no CSS classes */
  function _iSigCell(label, value, unit, zScore, note) {
    if (value === null || value === undefined) {
      return `<div style="display:inline-block;min-width:110px;margin-right:16px;margin-bottom:4px;vertical-align:top;">
        <div style="font-size:10px;color:#7b8499;text-transform:uppercase;letter-spacing:0.04em;">${label}</div>
        <div style="font-size:12px;color:#a0a8b8;font-style:italic;">No data</div>
      </div>`;
    }
    const valStr = value.toFixed ? value.toFixed(2) : String(value);
    const zStr   = zScore != null
      ? `z ${zScore >= 0 ? '+' : ''}${zScore.toFixed(2)}${note ? ' · ' + note : ''}` : '';
    return `<div style="display:inline-block;min-width:110px;margin-right:16px;margin-bottom:4px;vertical-align:top;">
      <div style="font-size:10px;color:#7b8499;text-transform:uppercase;letter-spacing:0.04em;">${label}</div>
      <div style="font-size:16px;font-weight:600;color:#1a2138;">${valStr} <span style="font-size:11px;font-weight:400;color:#7b8499;">${unit}</span></div>
      ${zStr ? `<div style="font-size:11px;color:#7b8499;">${zStr}</div>` : ''}
    </div>`;
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // RESET / ERROR
  // ══════════════════════════════════════════════════════════════════════════════

  function _resetPage() {
    const panel = document.getElementById('db-baseline-panel');
    if (panel) panel.classList.add('hidden');

    const bar = document.getElementById('db-summary-bar');
    if (bar) { bar.innerHTML = ''; bar.classList.remove('visible'); }

    const feed = document.getElementById('db-feed');
    if (feed) {
      feed.innerHTML = `<div class="debrief-feed-empty">
        <div class="debrief-feed-empty-icon">◈</div>
        <div class="debrief-feed-empty-text">Run analysis to see events.</div>
      </div>`;
    }
  }

  function _showFeedError(msg) {
    const feed = document.getElementById('db-feed');
    if (feed) {
      feed.innerHTML = `<div class="debrief-feed-empty">
        <div class="debrief-feed-empty-icon" style="color:var(--accent-red)">⚠</div>
        <div class="debrief-feed-empty-text" style="color:var(--accent-red)">${msg}</div>
      </div>`;
    }
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // HELPERS
  // ══════════════════════════════════════════════════════════════════════════════

  function _formatElapsed(secs) {
    if (secs == null) return '—';
    const m = Math.floor(Math.abs(secs) / 60);
    const s = Math.floor(Math.abs(secs) % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function _outcomeLabel(outcome) {
    const MAP = {
      HIT:               'Hit',
      MISS:              'Miss',
      COMMISSION_ERROR:  'Commission Error',
      CORRECT_WITHHOLD:  'Correct Withhold',
      ANTICIPATION_ERROR:'Anticipation Error',
    };
    return MAP[outcome] || outcome || '—';
  }

  function _teardown() {}

  return { init };

})();
