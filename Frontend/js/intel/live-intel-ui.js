/**
 * live-intel-ui.js — Real-Time Human Performance Dashboard
 *
 * Layout (CAPTURING state):
 *   LEFT  (40%): Active Engagement card + live physio
 *   RIGHT (60%): Stats Summary table — METRIC / VALUE / FORMULA
 *   BOTTOM:      Engagement Log — # | Type | RT | Result | ΔHR | ΔPupil | ✓
 *
 * Post-session (renderSessionSummary):
 *   Full Breakdown (HOSTILE | NON-HOSTILE two columns) + RT bar chart
 *
 * Key decisions:
 *   - RT measured from First Movement (tsAnchor) to first shot
 *   - H = blue pill, NH = red pill
 *   - ΔHR, ΔPupil = physioAtAnchor – physioAtFA (stress delta)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.LiveIntelUI = (function () {

  const ROOT_ID = 'li-chart-grid';

  // ─────────────────────────────────────────────────────────────────────────
  // Mount
  // ─────────────────────────────────────────────────────────────────────────
  function mount() {
    const root = _root();
    if (!root) return;
    root.innerHTML = `
      <div class="bd-dashboard" id="bd-dashboard">

        <!-- ══ WAITING STATE ══ -->
        <div class="bd-waiting" id="bd-waiting">
          <div class="bd-spinner"></div>
          <div class="bd-waiting-title">Awaiting Scenario</div>
          <div class="bd-waiting-sub" id="bd-waiting-sub">
            Press ▶ Start, then launch the scenario in VirTra.
          </div>
        </div>

        <!-- ══ LIVE STATE ══ -->
        <div class="bd-live" id="bd-live" style="display:none">

          <!-- ── Row 1: Engagement card (left) + Stats Summary (right) ── -->
          <div class="bd-top-grid">

            <!-- Active Engagement -->
            <div class="bd-card bd-current-card" id="bd-current-card">
              <div class="bd-card-header">
                Active Engagement
                <span class="bd-scenario-name" id="bd-scenario-name"></span>
              </div>
              <div class="bd-current-body" id="bd-current-body">
                <div class="bd-current-idle">Between engagements</div>
              </div>
              <div class="bd-physio-row">
                <div class="bd-physio-item">
                  <span class="bd-physio-dot bd-dot-hr"></span>
                  <span class="bd-physio-lbl">Heart Rate</span>
                  <span class="bd-physio-val" id="bd-live-hr">—</span>
                </div>
                <div class="bd-physio-item">
                  <span class="bd-physio-dot bd-dot-pupil"></span>
                  <span class="bd-physio-lbl">Pupil</span>
                  <span class="bd-physio-val" id="bd-live-pupil">—</span>
                </div>
              </div>
            </div>

            <!-- Stats Summary — mirrors Performance Workspace -->
            <div class="bd-card bd-stats-card">
              <div class="bd-card-header">Stats Summary</div>
              <div class="bd-stats-wrap">
                <table class="bd-stats-tbl">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th class="bd-col-val">Value</th>
                      <th class="bd-col-fml">Formula</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Decision Accuracy</td>
                      <td class="bd-stats-val" id="bd-m-accuracy">—</td>
                      <td class="bd-stats-fml">(hits + withheld) / total</td>
                    </tr>
                    <tr>
                      <td>Hit Rate</td>
                      <td class="bd-stats-val" id="bd-m-hitrate">—</td>
                      <td class="bd-stats-fml">hits / hostile</td>
                    </tr>
                    <tr>
                      <td>Miss Rate</td>
                      <td class="bd-stats-val" id="bd-m-missrate">—</td>
                      <td class="bd-stats-fml">misses / hostile</td>
                    </tr>
                    <tr>
                      <td>Correct Restraint Rate</td>
                      <td class="bd-stats-val" id="bd-m-csr">—</td>
                      <td class="bd-stats-fml">withheld / non-hostile</td>
                    </tr>
                    <tr>
                      <td>False Positive Rate</td>
                      <td class="bd-stats-val" id="bd-m-fpr">—</td>
                      <td class="bd-stats-fml">errors / non-hostile</td>
                    </tr>
                    <tr>
                      <td>Mean Reaction Time</td>
                      <td class="bd-stats-val" id="bd-m-avgtime">—</td>
                      <td class="bd-stats-fml">mean(tsShot − tsFM)</td>
                    </tr>
                    <tr>
                      <td>Mean Shots / Hostile</td>
                      <td class="bd-stats-val" id="bd-m-avgshots">—</td>
                      <td class="bd-stats-fml">total shots / hostile</td>
                    </tr>
                    <tr>
                      <td>HR at Anchor (mean)</td>
                      <td class="bd-stats-val" id="bd-m-hr">—</td>
                      <td class="bd-stats-fml">mean HR at first movement</td>
                    </tr>
                    <tr>
                      <td>Pupil at Anchor (mean)</td>
                      <td class="bd-stats-val" id="bd-m-pupil">—</td>
                      <td class="bd-stats-fml">mean pupil at first movement</td>
                    </tr>
                  </tbody>
                </table>
                <div class="bd-stats-footer" id="bd-stats-footer">
                  N = 0 decisions
                </div>
              </div>
            </div>

          </div>

          <!-- ── Row 2: Engagement Log ── -->
          <div class="bd-card bd-history-card">
            <div class="bd-card-header">Engagement Log</div>
            <div class="bd-history-wrap">
              <div class="bd-history-head">
                <span class="bd-hc-idx">#</span>
                <span class="bd-hc-type">Type</span>
                <span class="bd-hc-rt">RT (ms)</span>
                <span class="bd-hc-result">Result</span>
                <span class="bd-hc-shots">Shots</span>
                <span class="bd-hc-dhr">Δ HR</span>
                <span class="bd-hc-dpup">Δ Pupil</span>
                <span class="bd-hc-ok">Correct</span>
              </div>
              <div class="bd-history-body" id="bd-history-body">
                <div class="bd-history-empty" id="bd-history-empty">No engagements recorded.</div>
              </div>
            </div>
          </div>

        </div>

      </div>`;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────

  function showWaiting(msg) {
    const sub = _el('bd-waiting-sub');
    if (sub) sub.textContent = msg || '';
    _show('bd-waiting');
    _hide('bd-live');
  }

  function setScenarioName(name) {
    const el = _el('bd-scenario-name');
    if (el && name) el.textContent = name;
    _hide('bd-waiting');
    _show('bd-live');
  }

  function updateCurrentEngagement(eng) {
    if (!eng) return;
    const body = _el('bd-current-body');
    if (!body) return;

    // RT = from anchor (first movement) to first shot, or elapsed
    const rt = (eng.tsAnchor != null && eng.tsFirstShot != null)
      ? `${((eng.tsFirstShot - eng.tsAnchor) * 1000).toFixed(0)} ms`
      : (eng.tsAnchor != null ? '…' : '—');

    body.innerHTML = `
      <div class="bd-cur-row">
        <span class="bd-cur-lbl">Screen</span>
        <span class="bd-cur-val">${eng.screen}</span>
        ${_typePill(eng.threatType)}
      </div>
      <div class="bd-cur-row">
        <span class="bd-cur-lbl">Actor</span>
        <span class="bd-cur-val bd-actor-name" title="${eng.primaryActor || '—'}">${_shortActor(eng.primaryActor)}</span>
      </div>
      <div class="bd-cur-row">
        <span class="bd-cur-lbl">Shots</span>
        <span class="bd-cur-val">${eng.shotsFired} fired · ${eng.hits} hit · ${eng.misses} miss</span>
      </div>
      <div class="bd-cur-timer-row">
        <span class="bd-cur-lbl">RT</span>
        <span class="bd-cur-timer">${rt}</span>
      </div>`;
  }

  function clearCurrentEngagement() {
    const body = _el('bd-current-body');
    if (body) body.innerHTML = `<div class="bd-current-idle">Between engagements</div>`;
  }

  function addEngagementRow(eng) {
    const body = _el('bd-history-body');
    if (!body) return;
    const empty = _el('bd-history-empty');
    if (empty) empty.remove();

    // RT: tsAnchor → tsFirstShot (shots), or tsAnchor → tsCompleted (withheld)
    const rtMs = (eng.tsAnchor != null && eng.tsFirstShot != null)
      ? ((eng.tsFirstShot - eng.tsAnchor) * 1000).toFixed(0)
      : (eng.tsAnchor != null && eng.tsCompleted != null)
        ? ((eng.tsCompleted - eng.tsAnchor) * 1000).toFixed(0)
        : null;
    const rtStr = rtMs != null ? `${rtMs}` : '—';

    // Physio deltas: anchor vs first appearance
    const dhr   = _delta(eng.physioAtAnchor?.hr,    eng.physioAtFA?.hr,    0);
    const dpup  = _delta(eng.physioAtAnchor?.pupil,  eng.physioAtFA?.pupil,  2);

    const okStr = eng.correct === true
      ? '<span class="bd-ok-yes">✓</span>'
      : eng.correct === false
        ? '<span class="bd-ok-no">✗</span>'
        : '<span class="bd-ok-dim">—</span>';

    const row = document.createElement('div');
    row.className = `bd-history-row ${eng.correct === true ? 'bd-row-ok' : eng.correct === false ? 'bd-row-err' : 'bd-row-dim'}`;
    row.innerHTML = `
      <span class="bd-hc-idx">${eng.index}</span>
      <span class="bd-hc-type">${_typePill(eng.threatType)}</span>
      <span class="bd-hc-rt bd-mono">${rtStr}</span>
      <span class="bd-hc-result">${_resultLabel(eng)}</span>
      <span class="bd-hc-shots bd-mono">${eng.shotsFired}</span>
      <span class="bd-hc-dhr">${dhr.html}</span>
      <span class="bd-hc-dpup">${dpup.html}</span>
      <span class="bd-hc-ok">${okStr}</span>`;

    body.insertBefore(row, body.firstChild);
    row.style.opacity = '0';
    requestAnimationFrame(() => {
      row.style.transition = 'opacity 0.3s';
      row.style.opacity    = '1';
    });
  }

  function updateMetrics(stats) {
    if (!stats) return;

    const fmtPct  = v  => v  != null ? `${v.toFixed(1)}%`              : '—';
    const fmtMs   = v  => v  != null ? `${(v * 1000).toFixed(0)} ms`   : '—';
    const fmtVal  = v  => v  != null ? v.toFixed(1)                     : '—';

    const total  = stats.totalEngagements || 0;
    const hHits  = stats.hostile.hits      || 0;
    const nhCorr = stats.nonhostile.correct || 0;
    const accPct  = total > 0
      ? ((hHits + nhCorr) / total) * 100 : null;
    const missPct = stats.hostile.total > 0
      ? (stats.hostile.misses / stats.hostile.total) * 100 : null;
    const fprPct  = stats.nonhostile.total > 0
      ? ((stats.nonhostile.incorrect || 0) / stats.nonhostile.total) * 100 : null;

    _setText('bd-m-accuracy', fmtPct(accPct));
    _setText('bd-m-hitrate',  fmtPct(stats.hostile.hitRate));
    _setText('bd-m-missrate', fmtPct(missPct));
    _setText('bd-m-csr',      fmtPct(stats.nonhostile.correctRate));
    _setText('bd-m-fpr',      fmtPct(fprPct));
    _setText('bd-m-avgtime',  fmtMs(stats.overall.avgTimeToDecision));
    _setText('bd-m-avgshots', fmtVal(stats.hostile.avgShots));
    _setText('bd-m-hr',
      stats.physio.avgHrAtAnchor    != null ? `${stats.physio.avgHrAtAnchor.toFixed(0)} bpm`   : '—');
    _setText('bd-m-pupil',
      stats.physio.avgPupilAtAnchor != null ? `${stats.physio.avgPupilAtAnchor.toFixed(2)} mm`  : '—');

    _setText('bd-stats-footer',
      `N\u202f=\u202f${total}\u2002·\u2002` +
      `${stats.hostile.total || 0}\u202fH\u2002·\u2002` +
      `${stats.nonhostile.total || 0}\u202fNH`);

    // Flash value cells
    document.querySelectorAll('.bd-stats-val').forEach(el => {
      el.classList.add('bd-val-flash');
      setTimeout(() => el.classList.remove('bd-val-flash'), 500);
    });
  }

  function updateLivePhysio(hr, pupil) {
    const hrEl = _el('bd-live-hr');
    const puEl = _el('bd-live-pupil');
    if (hrEl) hrEl.textContent = hr    != null ? `${hr.toFixed(0)} bpm`   : '—';
    if (puEl) puEl.textContent = pupil != null ? `${pupil.toFixed(2)} mm` : '—';
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Post-session: per-engagement cards + aggregate breakdown
  // Writes into li-results-content so it shows during SUMMARIZING + RESULTS
  // ─────────────────────────────────────────────────────────────────────────
  function renderSessionSummary(session) {
    if (!session) return;

    // Final metrics flush into the live stats table
    if (session.stats) updateMetrics(session.stats);

    const engs  = session.engagements;
    const stats = session.stats;
    if (!engs || engs.length === 0 || !stats) return;

    const container = document.getElementById('li-results-content');
    if (!container) return;

    // Remove old session cards if re-run
    document.getElementById('li-session-cards')?.remove();

    const _mean   = arr => arr.length ? arr.reduce((a,b) => a+b, 0) / arr.length : null;
    const _fmtMs  = v  => v  != null ? `${(v * 1000).toFixed(0)} ms` : '—';
    const _fmtPct = v  => v  != null ? `${v.toFixed(1)}%`            : '—';
    const _fmtN   = (v, u, d=1) => v != null ? `${v.toFixed(d)} ${u}` : '—';

    // ── Per-engagement cards ─────────────────────────────────────────────
    const engCards = engs.map(eng => {
      const rt = (eng.tsAnchor != null && eng.tsFirstShot != null)
        ? `${((eng.tsFirstShot - eng.tsAnchor) * 1000).toFixed(0)} ms` : '—';
      const shots = eng.shotsFired > 0 ? `${eng.shotsFired}×` : '0';
      const typeLabel = eng.threatType === 'hostile' ? 'Hostile' : eng.threatType === 'nonhostile' ? 'Non-Hostile' : '—';
      const resultLabel = _resultLabel(eng);

      const correctBadge = eng.correct === true
        ? `<span class="bd-eng-correct-yes">✓ Correct</span>`
        : eng.correct === false
          ? `<span class="bd-eng-correct-no">✗ Incorrect</span>`
          : `<span class="bd-eng-correct-dim">—</span>`;

      // Physio table rows
      const hrBase   = eng.physioAtFA?.hr    != null ? `${eng.physioAtFA.hr.toFixed(1)} bpm`    : '—';
      const hrAnch   = eng.physioAtAnchor?.hr != null ? `${eng.physioAtAnchor.hr.toFixed(1)} bpm` : '—';
      const hrDelta  = _physioDelta(eng.physioAtAnchor?.hr,   eng.physioAtFA?.hr,   1, 'bpm');

      const puBase   = eng.physioAtFA?.pupil    != null ? `${eng.physioAtFA.pupil.toFixed(2)} mm`    : '—';
      const puAnch   = eng.physioAtAnchor?.pupil != null ? `${eng.physioAtAnchor.pupil.toFixed(2)} mm` : '—';
      const puDelta  = _physioDelta(eng.physioAtAnchor?.pupil, eng.physioAtFA?.pupil, 3, 'mm');

      const hasPhysio = (eng.physioAtFA?.hr != null || eng.physioAtFA?.pupil != null);
      const physioSection = hasPhysio ? `
        <div class="bd-eng-physio-title">Physio Response</div>
        <table class="bd-eng-physio-tbl">
          <thead><tr>
            <th>Signal</th>
            <th>Baseline</th>
            <th>Analysis</th>
            <th>Δ</th>
          </tr></thead>
          <tbody>
            ${eng.physioAtFA?.hr != null ? `
            <tr>
              <td><div class="bd-sig-row"><span class="bd-sig-dot bd-sig-hr"></span>Heart Rate</div></td>
              <td>${hrBase}</td><td>${hrAnch}</td><td>${hrDelta}</td>
            </tr>` : ''}
            ${eng.physioAtFA?.pupil != null ? `
            <tr>
              <td><div class="bd-sig-row"><span class="bd-sig-dot bd-sig-pupil"></span>Pupil Diameter</div></td>
              <td>${puBase}</td><td>${puAnch}</td><td>${puDelta}</td>
            </tr>` : ''}
          </tbody>
        </table>
        <div class="bd-eng-physio-note">
          Baseline = First Appearance &nbsp;·&nbsp; Analysis = First Movement (anchor event) &nbsp;·&nbsp; Δ = change from baseline
        </div>` : '';

      return `
        <div class="bd-eng-card">
          <div class="bd-eng-card-header">
            <span class="bd-eng-card-title">Engagement #${eng.index}</span>
            ${correctBadge}
          </div>
          <table class="bd-eng-meta-tbl">
            <tr><td>Actor</td><td title="${eng.primaryActor || '—'}">${eng.primaryActor || '—'}</td></tr>
            <tr><td>Type</td><td>${_typePill(eng.threatType)} ${typeLabel}</td></tr>
            <tr><td>Reaction Time (1st shot)</td><td>${rt}</td></tr>
            <tr><td>Shots</td><td>${shots}</td></tr>
            <tr><td>Result</td><td>${resultLabel}</td></tr>
          </table>
          ${physioSection}
        </div>`;
    }).join('');

    // ── Aggregate breakdown ──────────────────────────────────────────────
    const hostile    = engs.filter(e => e.threatType === 'hostile');
    const nonhostile = engs.filter(e => e.threatType === 'nonhostile');

    const hRTs  = hostile.filter(e => e.tsAnchor != null && e.tsFirstShot != null)
                         .map(e => e.tsFirstShot - e.tsAnchor);
    const nhRTs = nonhostile.filter(e => !e.correct && e.tsAnchor != null && e.tsFirstShot != null)
                            .map(e => e.tsFirstShot - e.tsAnchor);
    const hDhrArr  = hostile.map(e => e.physioAtAnchor?.hr    != null && e.physioAtFA?.hr    != null
      ? e.physioAtAnchor.hr    - e.physioAtFA.hr    : null).filter(v => v != null);
    const hDpupArr = hostile.map(e => e.physioAtAnchor?.pupil != null && e.physioAtFA?.pupil != null
      ? e.physioAtAnchor.pupil - e.physioAtFA.pupil : null).filter(v => v != null);
    const nhDhrArr  = nonhostile.map(e => e.physioAtAnchor?.hr    != null && e.physioAtFA?.hr    != null
      ? e.physioAtAnchor.hr    - e.physioAtFA.hr    : null).filter(v => v != null);
    const nhDpupArr = nonhostile.map(e => e.physioAtAnchor?.pupil != null && e.physioAtFA?.pupil != null
      ? e.physioAtAnchor.pupil - e.physioAtFA.pupil : null).filter(v => v != null);

    const errRate = stats.nonhostile.total > 0
      ? ((stats.nonhostile.incorrect || 0) / stats.nonhostile.total * 100) : null;

    const aggregate = `
      <div class="bd-session-breakdown">
        <div class="bd-session-breakdown-title">Aggregate Summary</div>
        <div class="bd-breakdown-grid">

          <div class="bd-breakdown-col bd-breakdown-h">
            <div class="bd-breakdown-header">
              <span class="bd-pill bd-pill-h">H</span>
              Hostile &nbsp;— N\u202f=\u202f${hostile.length}
            </div>
            <table class="bd-breakdown-tbl">
              <tr><td>Hit Rate</td>        <td>${_fmtPct(stats.hostile.hitRate)}</td></tr>
              <tr><td>Hits</td>            <td>${stats.hostile.hits}</td></tr>
              <tr><td>Misses</td>          <td>${stats.hostile.misses}</td></tr>
              <tr><td>Mean RT</td>         <td>${_fmtMs(_mean(hRTs))}</td></tr>
              <tr><td>Mean Shots</td>      <td>${stats.hostile.avgShots != null ? stats.hostile.avgShots.toFixed(1) : '—'}</td></tr>
              <tr><td>Mean ΔHR</td>        <td>${_mean(hDhrArr) != null ? `${_mean(hDhrArr).toFixed(1)} bpm` : '—'}</td></tr>
              <tr><td>Mean ΔPupil</td>     <td>${_mean(hDpupArr) != null ? `${_mean(hDpupArr).toFixed(2)} mm` : '—'}</td></tr>
            </table>
          </div>

          <div class="bd-breakdown-col bd-breakdown-nh">
            <div class="bd-breakdown-header">
              <span class="bd-pill bd-pill-nh">NH</span>
              Non-Hostile &nbsp;— N\u202f=\u202f${nonhostile.length}
            </div>
            <table class="bd-breakdown-tbl">
              <tr><td>Error Rate</td>       <td>${_fmtPct(errRate)}</td></tr>
              <tr><td>Withheld (✓)</td>     <td>${stats.nonhostile.correct}</td></tr>
              <tr><td>Commission Errors</td><td>${stats.nonhostile.incorrect || 0}</td></tr>
              <tr><td>Mean RT (errors)</td> <td>${_fmtMs(_mean(nhRTs))}</td></tr>
              <tr><td>Mean Shots</td>        <td>—</td></tr>
              <tr><td>Mean ΔHR</td>          <td>${_mean(nhDhrArr) != null ? `${_mean(nhDhrArr).toFixed(1)} bpm` : '—'}</td></tr>
              <tr><td>Mean ΔPupil</td>       <td>${_mean(nhDpupArr) != null ? `${_mean(nhDpupArr).toFixed(2)} mm` : '—'}</td></tr>
            </table>
          </div>

        </div>
      </div>`;

    // ── Assemble and prepend to li-results-content ───────────────────────
    const wrapper = document.createElement('div');
    wrapper.id        = 'li-session-cards';
    wrapper.innerHTML = `
      <div class="bd-session-cards-header">
        <span class="bd-session-cards-title">Session Review</span>
        <span class="bd-session-cards-meta">
          ${engs.length} engagement${engs.length !== 1 ? 's' : ''} &nbsp;·&nbsp;
          ${hostile.length}H &nbsp;·&nbsp; ${nonhostile.length}NH
        </span>
      </div>
      <div class="bd-eng-cards-wrap">${engCards}</div>
      ${aggregate}`;

    container.prepend(wrapper);
  }



  // ─────────────────────────────────────────────────────────────────────────
  // Private helpers
  // ─────────────────────────────────────────────────────────────────────────


  /** Format a physio delta with +/- sign and coloring */
  function _delta(anchor, fa, decimals) {
    if (anchor == null || fa == null) return { html: '<span class="bd-dim">—</span>', val: null };
    const d = anchor - fa;
    const s = (d > 0 ? '+' : '') + d.toFixed(decimals);
    const cls = d > 0 ? 'bd-delta-up' : d < 0 ? 'bd-delta-down' : 'bd-dim';
    return { html: `<span class="${cls}">${s}</span>`, val: d };
  }

  /**
   * Returns HTML string for physio table Δ column:
   * e.g. "+33.0 bpm ↑" or "-1.602 mm ↓"
   */
  function _physioDelta(anchor, fa, decimals, unit) {
    if (anchor == null || fa == null) return '<span class="bd-physio-delta-dim">—</span>';
    const d   = anchor - fa;
    const sign = d >= 0 ? '+' : '';
    const arrow = d > 0 ? '↑' : d < 0 ? '↓' : '';
    const cls  = d > 0 ? 'bd-physio-delta-up' : d < 0 ? 'bd-physio-delta-down' : 'bd-physio-delta-dim';
    return `<span class="${cls}">${sign}${d.toFixed(decimals)} ${unit} ${arrow}</span>`;
  }


  function _typePill(type) {
    if (type === 'hostile')    return `<span class="bd-pill bd-pill-h">H</span>`;
    if (type === 'nonhostile') return `<span class="bd-pill bd-pill-nh">NH</span>`;
    return `<span class="bd-pill bd-pill-dim">?</span>`;
  }

  function _resultLabel(eng) {
    const { result, threatType, correct } = eng;
    if (threatType === 'hostile') {
      if (result === 'hit')    return 'Hit';
      if (result === 'miss')   return 'Miss';
      return 'No Shot';
    }
    if (threatType === 'nonhostile') {
      return correct ? 'Withheld' : 'Fired';
    }
    return '—';
  }

  // "A1_ADM_MEMC_M_1_1_S1" → "A1·S1"
  function _shortActor(name) {
    if (!name || name === '—') return '—';
    const m = name.match(/^([AB]\d+).*_S(\d+)$/i);
    if (m) return `${m[1]}·S${m[2]}`;
    return name.length > 12 ? name.slice(0, 12) + '…' : name;
  }

  function _root()         { return document.getElementById(ROOT_ID); }
  function _el(id)         { return document.getElementById(id); }
  function _show(id)       { const e = _el(id); if (e) e.style.display = ''; }
  function _hide(id)       { const e = _el(id); if (e) e.style.display = 'none'; }
  function _setText(id, v) { const e = _el(id); if (e) e.textContent = v; }

  // ─────────────────────────────────────────────────────────────────────────
  // reset — clear UI for a new session
  // ─────────────────────────────────────────────────────────────────────────
  function reset() {
    const body = _el('bd-history-body');
    if (body) body.innerHTML =
      `<div class="bd-history-empty" id="bd-history-empty">No engagements recorded.</div>`;
    clearCurrentEngagement();
    ['bd-m-accuracy','bd-m-hitrate','bd-m-missrate','bd-m-csr',
     'bd-m-fpr','bd-m-avgtime','bd-m-avgshots','bd-m-hr','bd-m-pupil']
      .forEach(id => _setText(id, '—'));
    _setText('bd-stats-footer', 'N\u202f=\u202f0');
    _setText('bd-scenario-name', '');
    document.getElementById('li-session-cards')?.remove();
    document.getElementById('li-analysis-output')?.remove();
  }

  // ─────────────────────────────────────────────────────────────────────────
  return {
    mount,
    showWaiting,
    setScenarioName,
    updateCurrentEngagement,
    clearCurrentEngagement,
    addEngagementRow,
    updateMetrics,
    updateLivePhysio,
    renderSessionSummary,
    reset,
  };
})();
