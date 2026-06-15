/**
 * behdisc-engine.js — BehDisc Live Event Parser & State Machine
 *
 * Parses VirTra LSL event samples in real time and builds structured
 * engagement objects.  Fires callbacks whenever state changes so the UI
 * can refresh without polling.
 *
 * VirTra samples arrive in two formats (both handled transparently):
 *
 *  A) Packed 4-channel sample  (Shots, some compound events)
 *     data[0] = event type    "Shot Fired"
 *     data[1] = description   "Trainee 1 fired LID3_P1"
 *     data[2] = code          "TFW - Trainee Fires Weapon"
 *     data[3] = JSON meta     "[[\"ShotID\",\"123\"],...]"
 *
 *  B) Single-channel samples  (Scenario / Actor / Event Triggered groups)
 *     4 consecutive samples each with only data[0] populated:
 *       sample 1 → type marker    "Actor Event"
 *       sample 2 → description    "A8_ADM_MEMC_R_3_2_S1 First Appearance event"
 *       sample 3 → code           "FA - First Appearance"
 *       sample 4 → meta / empty
 *
 * Scoring rules (confirmed):
 *  Hostile:     correct = hits > 0  (must hit the threat)
 *  Non-hostile: correct = shotsFired === 0  (must NOT fire, even a miss is wrong)
 *  Instructor shots (IsInstructorShot = "True") are excluded from all counts.
 */

window.LabReplay = window.LabReplay || {};

LabReplay.BehDiscEngine = (function () {

  // ── Regex patterns for single-channel event descriptions ─────────────────
  const RE_FA      = /^(\S+) First Appearance event$/;
  const RE_FMH     = /^(\S+) First Movement Hostile event$/;
  const RE_FMNH    = /^(\S+) First Movement Non-Hostile event$/;
  const RE_SCREEN  = /^Screen_(\d+) was triggered$/;
  const TRAINING_COMPLETE = 'Training_Event_Completed was triggered';

  // ── Module state ──────────────────────────────────────────────────────────
  let _active          = false;  // true after Scenario Started
  let _session         = null;
  let _engagement      = null;   // current in-progress engagement
  let _engIdx          = 0;

  // Accumulator for single-channel sample groups
  let _pendingScenario = false;  // saw "Scenario Started" waiting for meta
  let _pendingShot     = null;   // { type: 'fired'|'hit'|'miss', isTFW: bool }

  // Current rolling physio (updated externally via ingestPhysio)
  let _physio = { hr: null, pupil: null };

  // ── Callbacks (set by consumer) ───────────────────────────────────────────
  let _cbScenarioStart      = null;
  let _cbScenarioStop       = null;
  let _cbEngagementStart    = null;
  let _cbEngagementUpdate   = null;
  let _cbEngagementComplete = null;
  let _cbStatsUpdated       = null;

  // ─────────────────────────────────────────────────────────────────────────
  // Public: reset all state (called automatically on Scenario Started)
  // ─────────────────────────────────────────────────────────────────────────
  function reset() {
    _active          = false;
    _session         = _newSession();
    _engagement      = null;
    _engIdx          = 0;
    _pendingScenario = false;
    _pendingShot     = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Public: feed one virtra-event from the EventBus
  // evt = { data: [...], elapsedS: number, timestamp: number }
  // ─────────────────────────────────────────────────────────────────────────
  function ingest(evt) {
    const data    = evt.data;
    if (!Array.isArray(data) || data.length === 0) return;

    const elapsed = evt.elapsedS ?? 0;
    const ch0     = _val(data[0]);
    const ch1     = _val(data[1]);
    const ch2     = _val(data[2]);
    const ch3     = _val(data[3]);

    // Format A: packed 4-channel (ch1 or ch2 populated)
    if (ch1 || ch2) {
      _ingestPacked(ch0, ch1, ch2, ch3, elapsed);
    } else {
      // Format B: single-channel accumulation
      _ingestSingle(ch0, elapsed);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Public: update current rolling physio values
  // ─────────────────────────────────────────────────────────────────────────
  function ingestPhysio(signal, value) {
    if (signal === 'hr')    _physio.hr    = value;
    if (signal === 'pupil') _physio.pupil = value;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Internal: packed 4-channel sample
  // ─────────────────────────────────────────────────────────────────────────
  function _ingestPacked(type, desc, code, meta, elapsed) {
    switch (type) {
      case 'Shot Fired':
        if (code === 'TFW - Trainee Fires Weapon') {
          const m = _parseMeta(meta);
          if (m.IsInstructorShot !== 'True') _onShotFired(m, elapsed);
        }
        break;

      case 'Shot Hit': {
        const m = _parseMeta(meta);
        if (m.IsInstructorShot !== 'True') _onShotHit(m, elapsed);
        break;
      }

      case 'Shot Miss': {
        const m = _parseMeta(meta);
        if (m.IsInstructorShot !== 'True') _onShotMiss(m, elapsed);
        break;
      }

      case 'Actor Event':
        _matchActorDesc(desc, elapsed);
        break;

      case 'Event Triggered':
        _matchEventTrig(desc, elapsed);
        break;

      case 'Scenario Started':
        _onScenarioStart(_parseMeta(meta), elapsed);
        break;

      case 'Scenario Stopped':
        _onScenarioStop(elapsed);
        break;

      default: break;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Internal: single-channel sample — detect from ch0 alone
  // ─────────────────────────────────────────────────────────────────────────
  function _ingestSingle(val, elapsed) {
    if (!val) return;

    // ── Scenario lifecycle ──────────────────────────────────────────────────
    if (val === 'Scenario Started') {
      _pendingScenario = true;
      if (!_active) _onScenarioStart({}, elapsed); // start immediately, update name later
      return;
    }
    if (val === 'Scenario Stopped') { _onScenarioStop(elapsed); return; }

    // SS code triggers scenario start if not already active
    if (val === 'SS - Scenario Started') {
      if (!_active) _onScenarioStart({}, elapsed);
      return;
    }

    // Scenario metadata arrives after Scenario Started marker
    if (_pendingScenario && val.startsWith('[[')) {
      const m = _parseMeta(val);
      if (_session) {
        if (m.Scenario)  _session.scenarioName = m.Scenario;
        if (m.SessionID) _session.sessionId    = m.SessionID;
      }
      _pendingScenario = false;
      _cbScenarioStart?.(_session);  // re-fire with name populated
      return;
    }

    // ── Actor event descriptions ────────────────────────────────────────────
    if (_matchActorDesc(val, elapsed)) return;

    // ── Event trigger descriptions ──────────────────────────────────────────
    if (_matchEventTrig(val, elapsed)) return;

    // ── Shot accumulation (single-channel path) ─────────────────────────────
    if (val === 'Shot Fired') { _pendingShot = { type: 'fired', isTFW: false }; return; }
    if (val === 'Shot Hit')   { _pendingShot = { type: 'hit',   isTFW: false }; return; }
    if (val === 'Shot Miss')  { _pendingShot = { type: 'miss',  isTFW: false }; return; }
    if (val === 'TFW - Trainee Fires Weapon') {
      if (_pendingShot) _pendingShot.isTFW = true;
      return;
    }
    if (val.startsWith('[[') && _pendingShot) {
      const m = _parseMeta(val);
      if (m.IsInstructorShot !== 'True') {
        if (_pendingShot.type === 'fired' && _pendingShot.isTFW) _onShotFired(m, elapsed);
        else if (_pendingShot.type === 'hit')  _onShotHit(m, elapsed);
        else if (_pendingShot.type === 'miss') _onShotMiss(m, elapsed);
      }
      _pendingShot = null;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Pattern matchers
  // ─────────────────────────────────────────────────────────────────────────
  function _matchActorDesc(desc, elapsed) {
    let m;
    if ((m = RE_FA.exec(desc)))   { _onActorFA(m[1], elapsed);   return true; }
    if ((m = RE_FMH.exec(desc)))  { _onActorFMH(m[1], elapsed);  return true; }
    if ((m = RE_FMNH.exec(desc))) { _onActorFMNH(m[1], elapsed); return true; }
    return false;
  }

  function _matchEventTrig(desc, elapsed) {
    const sm = RE_SCREEN.exec(desc);
    if (sm) { _onScreenTrigger(parseInt(sm[1], 10), elapsed); return true; }
    if (desc === TRAINING_COMPLETE) { _onEngagementComplete(elapsed); return true; }
    return false;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Event handlers
  // ─────────────────────────────────────────────────────────────────────────
  function _onScenarioStart(meta, elapsed) {
    reset();
    _active = true;
    _session.startTs      = elapsed;
    _session.scenarioName = meta.Scenario  || '';
    _session.sessionId    = meta.SessionID || '';
    _cbScenarioStart?.(_session);
  }

  function _onScenarioStop(elapsed) {
    _active = false;
    // Close any open engagement
    if (_engagement && _engagement.result === 'pending') {
      _engagement.tsCompleted = elapsed;
      _finalizeEngagement();
    }
    _cbScenarioStop?.(_session);
  }

  function _onScreenTrigger(screenNum, elapsed) {
    if (!_active) return;
    // Close previous if still open
    if (_engagement && _engagement.result === 'pending') {
      _engagement.tsCompleted = elapsed;
      _finalizeEngagement();
    }
    _engagement = _newEngagement(screenNum);
    _cbEngagementStart?.(_engagement);
  }

  function _onActorFA(actorName, elapsed) {
    if (!_active || !_engagement) return;
    if (!_engagement.primaryActor) _engagement.primaryActor = actorName;
    _engagement.actors[actorName] = { name: actorName, threatType: 'unknown' };
    _engagement.tsFA        = elapsed;
    _engagement.physioAtFA  = { ..._physio };
    _cbEngagementUpdate?.(_engagement);
  }

  function _onActorFMH(actorName, elapsed) {
    if (!_active || !_engagement) return;
    if (_engagement.actors[actorName]) _engagement.actors[actorName].threatType = 'hostile';
    _engagement.tsFMH      = elapsed;
    _engagement.threatType = 'hostile';
    if (!_engagement.tsAnchor) {
      _engagement.tsAnchor       = elapsed;
      _engagement.physioAtAnchor = { ..._physio };
    }
    _cbEngagementUpdate?.(_engagement);
  }

  function _onActorFMNH(actorName, elapsed) {
    if (!_active || !_engagement) return;
    if (_engagement.actors[actorName]) _engagement.actors[actorName].threatType = 'nonhostile';
    _engagement.tsFMNH = elapsed;
    if (_engagement.threatType === 'unknown') _engagement.threatType = 'nonhostile';
    if (!_engagement.tsAnchor) {
      _engagement.tsAnchor       = elapsed;
      _engagement.physioAtAnchor = { ..._physio };
    }
    _cbEngagementUpdate?.(_engagement);
  }

  function _onShotFired(meta, elapsed) {
    if (!_active || !_engagement) return;
    _engagement.shotsFired++;
    if (_engagement.tsFirstShot == null) _engagement.tsFirstShot = elapsed;
    _cbEngagementUpdate?.(_engagement);
  }

  function _onShotHit(meta, elapsed) {
    if (!_active || !_engagement) return;
    _engagement.hits++;
    _cbEngagementUpdate?.(_engagement);
  }

  function _onShotMiss(meta, elapsed) {
    if (!_active || !_engagement) return;
    _engagement.misses++;
    _cbEngagementUpdate?.(_engagement);
  }

  function _onEngagementComplete(elapsed) {
    if (!_active || !_engagement) return;
    _engagement.tsCompleted = elapsed;
    _finalizeEngagement();
    _engagement = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Engagement finalization & scoring
  // ─────────────────────────────────────────────────────────────────────────
  function _finalizeEngagement() {
    const eng = _engagement;
    const { threatType, hits, misses, shotsFired } = eng;

    if (threatType === 'hostile') {
      eng.result  = hits > 0 ? 'hit' : (shotsFired > 0 ? 'miss' : 'noshoot');
      eng.correct = hits > 0;                // must hit the threat
    } else if (threatType === 'nonhostile') {
      eng.result  = shotsFired > 0 ? (hits > 0 ? 'hit' : 'miss') : 'noshoot';
      eng.correct = shotsFired === 0;        // must NOT fire (even a miss = wrong)
    } else {
      eng.result  = shotsFired > 0 ? (hits > 0 ? 'hit' : 'miss') : 'noshoot';
      eng.correct = null;
    }

    _session.engagements.push({ ...eng });
    _recomputeStats();
    _cbEngagementComplete?.(eng, _session.stats);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Aggregate statistics
  // ─────────────────────────────────────────────────────────────────────────
  function _recomputeStats() {
    const engs  = _session.engagements;
    const stats = _emptyStats();
    stats.totalEngagements = engs.length;

    let sumDecision = 0, nDecision = 0;
    let sumFirstShot = 0, nFirstShot = 0;
    let sumHrAnchor = 0, nHr = 0;
    let sumPupilAnchor = 0, nPupil = 0;

    for (const e of engs) {
      stats.overall.shots += e.shotsFired;

      // RT measured from First Movement (Anchor) — not First Appearance
      if (e.tsAnchor != null && e.tsCompleted != null) {
        sumDecision += e.tsCompleted - e.tsAnchor; nDecision++;
      }
      if (e.tsAnchor != null && e.tsFirstShot != null) {
        sumFirstShot += e.tsFirstShot - e.tsAnchor; nFirstShot++;
      }
      if (e.physioAtAnchor?.hr != null)    { sumHrAnchor    += e.physioAtAnchor.hr;    nHr++; }
      if (e.physioAtAnchor?.pupil != null) { sumPupilAnchor += e.physioAtAnchor.pupil; nPupil++; }

      if (e.threatType === 'hostile') {
        stats.hostile.total++;
        if (e.result === 'hit') stats.hostile.hits++;
        else stats.hostile.misses++;
      } else if (e.threatType === 'nonhostile') {
        stats.nonhostile.total++;
        if (e.correct) stats.nonhostile.correct++;
        else stats.nonhostile.incorrect++;
      }
    }

    if (stats.hostile.total > 0) {
      stats.hostile.hitRate  = (stats.hostile.hits / stats.hostile.total) * 100;
      stats.hostile.avgShots = stats.overall.shots / stats.hostile.total;
    }
    if (stats.nonhostile.total > 0) {
      stats.nonhostile.correctRate = (stats.nonhostile.correct / stats.nonhostile.total) * 100;
    }
    if (nDecision > 0)  stats.overall.avgTimeToDecision  = sumDecision / nDecision;
    if (nFirstShot > 0) stats.overall.avgTimeToFirstShot = sumFirstShot / nFirstShot;
    if (nHr > 0)        stats.physio.avgHrAtAnchor        = sumHrAnchor    / nHr;
    if (nPupil > 0)     stats.physio.avgPupilAtAnchor     = sumPupilAnchor / nPupil;

    _session.stats = stats;
    _cbStatsUpdated?.(stats);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Factories
  // ─────────────────────────────────────────────────────────────────────────
  function _newSession() {
    return {
      scenarioName: '',
      sessionId:    '',
      startTs:      null,
      engagements:  [],
      stats:        _emptyStats(),
    };
  }

  function _newEngagement(screenNum) {
    return {
      index:        ++_engIdx,
      screen:       `Screen_${screenNum}`,
      screenNum,
      actors:       {},
      primaryActor: null,
      threatType:   'unknown',

      tsFA:         null,
      tsFMH:        null,
      tsFMNH:       null,
      tsAnchor:     null,
      tsFirstShot:  null,
      tsCompleted:  null,

      shotsFired:   0,
      hits:         0,
      misses:       0,
      result:       'pending',
      correct:      null,

      physioAtFA:     { hr: null, pupil: null },
      physioAtAnchor: { hr: null, pupil: null },
    };
  }

  function _emptyStats() {
    return {
      totalEngagements: 0,
      hostile:    { total: 0, hits: 0, misses: 0, hitRate: null, avgShots: null },
      nonhostile: { total: 0, correct: 0, incorrect: 0, correctRate: null },
      overall:    { shots: 0, avgTimeToDecision: null, avgTimeToFirstShot: null },
      physio:     { avgHrAtAnchor: null, avgPupilAtAnchor: null },
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────────
  function _val(channel) {
    if (channel == null) return '';
    if (typeof channel === 'string') return channel.trim();
    if (typeof channel === 'object' && 'value' in channel) return String(channel.value ?? '').trim();
    return String(channel).trim();
  }

  function _parseMeta(str) {
    if (!str) return {};
    try {
      const arr = JSON.parse(str);
      return Array.isArray(arr) ? Object.fromEntries(arr) : {};
    } catch { return {}; }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────
  return {
    reset,
    ingest,
    ingestPhysio,

    getSession()    { return _session; },
    getStats()      { return _session?.stats ?? _emptyStats(); },
    getEngagement() { return _engagement; },
    isActive()      { return _active; },

    // Callback setters
    set onScenarioStart(fn)      { _cbScenarioStart      = fn; },
    set onScenarioStop(fn)       { _cbScenarioStop       = fn; },
    set onEngagementStart(fn)    { _cbEngagementStart    = fn; },
    set onEngagementUpdate(fn)   { _cbEngagementUpdate   = fn; },
    set onEngagementComplete(fn) { _cbEngagementComplete = fn; },
    set onStatsUpdated(fn)       { _cbStatsUpdated       = fn; },
  };
})();
