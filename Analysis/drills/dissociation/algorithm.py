"""
drills/dissociation/algorithm.py — Dissociation detection drill.

Pipeline (all computed from a single .db file, replay mode only):

  Step 1 — Session Baseline
    Extract the first `baseline_s` seconds of recording (before any
    VirTra event). Compute mean and SD per signal (HR, pupil, RMSSD).
    These are the within-person normalization constants.

  Step 2 — Per-trial PLI (Types A, B, C)
    For each BehDisc trial (reusing behdisc trial detection):
      - Extract signal in [t0, t0 + analysis_s]
      - Compute trial mean per signal
      - Normalize: z = (trial_mean - rest_mean) / rest_sd
      - PLI = mean(z_HR, -z_RMSSD, z_pupil)   (-RMSSD: suppression = load)
      - Classify: type_a / type_b / type_c / routine_error / unclassified

  Step 3 — Session-level Type D
    After all trials are classified:
      - Extract LF/HF across full session from HRV_Live_HRV
      - Split into Q1/Q2/Q3/Q4 by session elapsed time
      - Compute linear slope of mean LF/HF per quartile
      - Compare accuracy in Q3+Q4 vs Q1+Q2
      - Tag Q3/Q4 correct trials as type_d if fatigue rising + performance held

Dissociation score (for ranking):
    dissoc_score = |PLI - PLI_expected_for_outcome|
    Where PLI_expected = median PLI of correct trials in session.
    Higher = more surprising.
"""

import math
import sqlite3
import numpy as np
from typing import Optional

# Signals
from signals.hr    import extract as _hr
from signals.pupil import extract as _pupil
from signals.hrv   import extract_rmssd, extract_lfhf


DRILL_KEY = "dissociation"

TERMINOLOGY = {
    "drill_name":   "Performance Debrief",
    "drill_short":  "Dissociation",
    "anchor_label": "Actor Movement",
    "trial_label":  "Engagement",
}

# ── Dissociation type colors (matched to Frontend CSS) ─────────────────────────
TYPE_META = {
    "type_a": {
        "label":       "Type A — Silent Competence",
        "short":       "Type A",
        "color":       "#2D8E54",   # green
        "description": "High physiological load with correct decision — resilience signature.",
    },
    "type_b": {
        "label":       "Type B — Silent Failure",
        "short":       "Type B",
        "color":       "#C94444",   # red
        "description": "Low load, yet error occurred — upstream failure: bias, priming, or decision policy.",
    },
    "type_c": {
        "label":       "Type C — Attentional Tunnel",
        "short":       "Type C",
        "color":       "#C47A2A",   # orange
        "description": "Normal or high load with error despite gaze on target — looking but not seeing.",
    },
    "type_d": {
        "label":       "Type D — Fatigue Resilience",
        "short":       "Type D",
        "color":       "#6B5DB8",   # purple
        "description": "Accumulated fatigue (rising LF/HF) with performance maintained — late-session resilience.",
    },
    "routine_error": {
        "label":       "Routine Error",
        "short":       "Routine",
        "color":       "#7B8499",   # grey
        "description": "High load with expected degraded performance — not a dissociation.",
    },
    "unclassified": {
        "label":       "Unclassified",
        "short":       "—",
        "color":       "#A0A8B8",
        "description": "Insufficient signal data to classify.",
    },
}

# ── Debrief prompts per type ─────────────────────────────────────────────────

DEBRIEF_PROMPTS = {
    "type_a": (
        "At this moment your physiological data showed significant stress — elevated heart rate "
        "and suppressed HRV — yet your decision was accurate. Walk me through that moment: what "
        "did you notice first? What information did you use to make that call? Do you remember "
        "feeling the pressure, or were you focused on something specific? Understanding what worked "
        "here can help you reproduce it under future high-stress conditions."
    ),
    "type_b": (
        "At this moment your physiological indicators were within normal range — no strong signal "
        "of overload or stress — yet you made an error. Since the physiology does not explain this "
        "one, the error likely originated elsewhere: what were you focused on in the moment before "
        "this? What did you think you saw? Is there anything about the scenario or what came just "
        "before that might have influenced your expectation?"
    ),
    "type_c": (
        "At this moment your physiological state was elevated but your error occurred despite "
        "being engaged. Sometimes under load the quality of attention narrows — the eyes move "
        "but processing depth degrades. What do you remember seeing during this window? Can you "
        "describe what was happening in the scene? Does this moment feel familiar from training "
        "or operational experience?"
    ),
    "type_d": (
        "By this point in the session your HRV data shows clear signs of accumulated cognitive "
        "fatigue — your system had been working hard. And yet your decision accuracy held. What "
        "were you doing differently at this stage? What did it feel like internally? If you can "
        "identify the strategy or the state that allowed you to maintain performance through "
        "fatigue, that becomes a resource you can deliberately access in extended operations."
    ),
    "routine_error": (
        "This was a high-load moment that produced the expected outcome: physiological overload "
        "coincided with degraded performance. What were you experiencing in the moment? What, "
        "if anything, could have reduced the load or helped your processing?"
    ),
    "unclassified": (
        "Insufficient physiological data was available to classify this moment. Review the "
        "biosignal quality for this trial window."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1 — Session Baseline
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_session_baseline(
    db_path: str,
    t_session_start: float,
    t_first_event: float,
    baseline_s: float,
) -> dict:
    """
    Extract signal means and SDs from the quiet standing period before
    the first VirTra event.

    Returns dict with keys: hr_mean, hr_sd, pupil_mean, pupil_sd,
    rmssd_mean, rmssd_sd, baseline_duration_s, n_samples.
    Returns None values for signals with insufficient data (<3 samples).
    """
    # Clamp window to available recording
    t_end   = t_first_event
    t_start = max(t_session_start, t_first_event - baseline_s)
    actual_duration = t_end - t_start

    result = {
        "t_start":            t_start,
        "t_end":              t_end,
        "baseline_duration_s": round(actual_duration, 1),
    }

    def _stats(times, values, name):
        vals = values[~np.isnan(values)] if len(values) > 0 else np.array([])
        if len(vals) < 3:
            result[f"{name}_mean"] = None
            result[f"{name}_sd"]   = None
            result[f"{name}_n"]    = len(vals)
        else:
            result[f"{name}_mean"] = float(np.mean(vals))
            result[f"{name}_sd"]   = float(np.std(vals)) if np.std(vals) > 1e-6 else 1.0
            result[f"{name}_n"]    = len(vals)

    t, v = _hr(db_path, t_start, t_end)
    _stats(t, v, "hr")

    t, v = _pupil(db_path, t_start, t_end)
    _stats(t, v, "pupil")

    t, v = extract_rmssd(db_path, t_start, t_end)
    _stats(t, v, "rmssd")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2 — Per-trial signal extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _trial_signal_mean(
    db_path: str,
    t0: float,
    analysis_s: float,
    extractor_fn,
) -> Optional[float]:
    """
    Mean of signal in [t0, t0 + analysis_s]. Returns None if <2 samples.
    """
    t, v = extractor_fn(db_path, t0, t0 + analysis_s)
    v = v[~np.isnan(v)] if len(v) > 0 else np.array([])
    if len(v) < 2:
        return None
    return float(np.mean(v))


def _z(value: Optional[float], mean: Optional[float], sd: Optional[float]) -> Optional[float]:
    """Z-score a value relative to baseline mean/sd. Returns None if data missing."""
    if value is None or mean is None or sd is None or sd < 1e-6:
        return None
    return (value - mean) / sd


def _compute_pli(
    hr_z:    Optional[float],
    rmssd_z: Optional[float],  # will be negated (suppression = load)
    pupil_z: Optional[float],
) -> Optional[float]:
    """
    PLI = mean of available normalized components.
    RMSSD is negated: parasympathetic withdrawal (RMSSD drop) = higher load.
    Requires at least 2 valid components.
    """
    components = []
    if hr_z    is not None: components.append(hr_z)
    if rmssd_z is not None: components.append(-rmssd_z)
    if pupil_z is not None: components.append(pupil_z)

    if len(components) < 2:
        return None
    return round(float(np.mean(components)), 3)


# ═══════════════════════════════════════════════════════════════════════════════
# 3 — Classification
# ═══════════════════════════════════════════════════════════════════════════════

CORRECT_OUTCOMES = {"HIT", "CORRECT_WITHHOLD"}
ERROR_OUTCOMES   = {"MISS", "COMMISSION_ERROR"}

# PLI thresholds
PLI_HIGH = 1.0   # ≥ this → high load
PLI_LOW  = 0.5   # ≤ this → low/normal load


def _classify_abc(
    pli:     Optional[float],
    outcome: str,
) -> str:
    """
    Rule-based classification for Types A, B, C.
    Type C (gaze entropy) is flagged separately post-hoc if gaze data available.
    """
    if pli is None:
        return "unclassified"

    correct = outcome in CORRECT_OUTCOMES
    error   = outcome in ERROR_OUTCOMES

    if pli >= PLI_HIGH and correct:
        return "type_a"       # Silent Competence

    if pli <= PLI_LOW and error:
        return "type_b"       # Silent Failure

    if pli >= PLI_HIGH and error:
        return "routine_error"

    return "unclassified"


def _dissoc_score(pli: Optional[float], pli_expected: float) -> Optional[float]:
    """
    Dissociation score = |PLI - expected PLI for outcome type|.
    Higher = more surprising. Used to rank the event reel.
    """
    if pli is None:
        return None
    return round(abs(pli - pli_expected), 3)


# ═══════════════════════════════════════════════════════════════════════════════
# 4 — Type D: session-level fatigue resilience
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_type_d(
    db_path: str,
    trials:  list[dict],
    t_session_start: float,
    t_session_end:   float,
    lfhf_slope_threshold: float = 0.3,
) -> list[dict]:
    """
    Post-pass: detect Type D (Fatigue-Resilience) events.

    Algorithm:
      1. Extract LF/HF across full session
      2. Divide into Q1-Q4 by elapsed time
      3. Compute mean LF/HF per quartile
      4. Linear slope across quartile means
      5. If slope > threshold AND Q3+Q4 accuracy >= Q1+Q2 accuracy:
         → label Q3/Q4 correct trials as type_d

    Returns modified trials list.
    """
    session_duration = t_session_end - t_session_start
    if session_duration < 120:   # Need at least 2 minutes for meaningful quartiles
        return trials

    # Extract LF/HF across entire session
    t_lfhf, v_lfhf = extract_lfhf(db_path, t_session_start, t_session_end)
    if len(v_lfhf) < 8:
        return trials   # Not enough HRV samples

    # Quartile boundaries
    q_boundaries = [
        t_session_start + session_duration * 0.25,
        t_session_start + session_duration * 0.50,
        t_session_start + session_duration * 0.75,
        t_session_end,
    ]

    def _q_mean(q_start, q_end):
        mask = (t_lfhf >= q_start) & (t_lfhf < q_end)
        vals = v_lfhf[mask]
        vals = vals[~np.isnan(vals)]
        return float(np.mean(vals)) if len(vals) >= 2 else None

    q_means = [
        _q_mean(t_session_start,    q_boundaries[0]),
        _q_mean(q_boundaries[0],    q_boundaries[1]),
        _q_mean(q_boundaries[1],    q_boundaries[2]),
        _q_mean(q_boundaries[2],    q_boundaries[3]),
    ]

    valid_qs = [(i + 1, m) for i, m in enumerate(q_means) if m is not None]
    if len(valid_qs) < 3:
        return trials

    xs = [v[0] for v in valid_qs]
    ys = [v[1] for v in valid_qs]
    slope = float(np.polyfit(xs, ys, 1)[0])

    # Compare accuracy early (Q1+Q2) vs late (Q3+Q4)
    q2_boundary = q_boundaries[1]
    early = [t for t in trials if t["t0"] < q2_boundary]
    late  = [t for t in trials if t["t0"] >= q2_boundary]

    def _acc(group):
        if not group:
            return 0.0
        return sum(1 for t in group if t["outcome"] in CORRECT_OUTCOMES) / len(group)

    acc_early = _acc(early)
    acc_late  = _acc(late)
    fatigue_rising    = slope > lfhf_slope_threshold
    performance_held  = acc_late >= acc_early * 0.85   # 15% tolerance

    # Metadata to attach to each trial
    type_d_meta = {
        "lf_hf_slope":   round(slope, 4),
        "lf_hf_q_means": [round(m, 3) if m is not None else None for m in q_means],
        "acc_early_pct": round(acc_early * 100, 1),
        "acc_late_pct":  round(acc_late  * 100, 1),
        "fatigue_rising":    fatigue_rising,
        "performance_held":  performance_held,
        "type_d_triggered":  fatigue_rising and performance_held,
    }

    for trial in trials:
        trial["type_d_session"] = type_d_meta

        if (
            fatigue_rising
            and performance_held
            and trial["t0"] >= q2_boundary        # Q3 or Q4
            and trial["outcome"] in CORRECT_OUTCOMES
        ):
            trial["dissociation_type"] = "type_d"
            trial["dissociation_type_meta"] = TYPE_META["type_d"]
            trial["debrief_prompt"] = DEBRIEF_PROMPTS["type_d"]

    return trials


# ═══════════════════════════════════════════════════════════════════════════════
# 5 — Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def _session_time_range(db_path: str) -> tuple[float, float]:
    """Return (t_min, t_max) of the entire recording from any table."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    candidates = []
    for table in ("neon_gaze", "polar_verity_sense_hr", "polar_h10_heart_rate", "hrv_live_hrv"):
        try:
            row = cur.execute(
                f"SELECT MIN(unix_timestamp_seconds), MAX(unix_timestamp_seconds) FROM {table}"
            ).fetchone()
            if row and row[0] is not None:
                candidates.append((float(row[0]), float(row[1])))
        except Exception:
            pass
    con.close()
    if not candidates:
        return (0.0, 0.0)
    t_min = min(c[0] for c in candidates)
    t_max = max(c[1] for c in candidates)
    return t_min, t_max


def analyze_session(
    db_path: str,
    drill_module,
    baseline_s:           float = 60.0,
    analysis_s:           float = 2.0,
    lfhf_slope_threshold: float = 0.3,
) -> dict:
    """
    Full dissociation analysis for a session.

    Args:
        db_path:              Path to .db file.
        drill_module:         Registered drill module (from DRILL_MAP).
                              Must expose extract_trials(db_path) -> list[dict].
        baseline_s:           Duration of pre-session standing baseline (default 60s).
        analysis_s:           Duration of post-event analysis window (default 2s).
        lfhf_slope_threshold: LF/HF slope threshold for Type D detection (default 0.3).

    Returns dict with:
        baseline      : session baseline stats
        trials        : list of classified trial dicts
        summary       : aggregate session stats
        session_meta  : timing, parameters used
        drill         : drill key and terminology
    """
    # ── Get trials using the drill's own extractor ───────────────────────────
    try:
        raw_trials = drill_module.extract_trials(db_path)
    except Exception as e:
        raise RuntimeError(f"Trial extraction failed for drill '{getattr(drill_module, 'DRILL_KEY', '?')}': {e}")
    if not raw_trials:
        return {
            "baseline": None,
            "trials":   [],
            "summary":  {"error": f"No trials detected by drill '{getattr(drill_module, 'DRILL_KEY', '?')}' in session"},
            "session_meta": {"baseline_s": baseline_s, "analysis_s": analysis_s},
            "drill":    {"key": getattr(drill_module, 'DRILL_KEY', '?'), "terminology": getattr(drill_module, 'TERMINOLOGY', {})},
        }

    t_session_start, t_session_end = _session_time_range(db_path)
    t_first_event = min(t["t0"] for t in raw_trials)

    # ── Step 1: Session baseline ─────────────────────────────────────────────
    baseline = _compute_session_baseline(
        db_path, t_session_start, t_first_event, baseline_s
    )

    # Warn if baseline window is very short
    baseline["warning"] = (
        "Baseline window < 30s — consider longer quiet standing period."
        if baseline["baseline_duration_s"] < 30 else None
    )

    # ── Step 2: Per-trial PLI + A/B/C classification ─────────────────────────
    trials = []
    for raw in raw_trials:
        t0      = raw["t0"]
        outcome = raw["outcome"]

        # Extract trial-window means
        hr_mean    = _trial_signal_mean(db_path, t0, analysis_s, _hr)
        pupil_mean = _trial_signal_mean(db_path, t0, analysis_s, _pupil)
        rmssd_mean = _trial_signal_mean(
            db_path, t0, analysis_s,
            lambda p, a, b: extract_rmssd(p, a, b)
        )

        # Z-score each component against session baseline
        hr_z    = _z(hr_mean,    baseline.get("hr_mean"),    baseline.get("hr_sd"))
        pupil_z = _z(pupil_mean, baseline.get("pupil_mean"), baseline.get("pupil_sd"))
        rmssd_z = _z(rmssd_mean, baseline.get("rmssd_mean"), baseline.get("rmssd_sd"))

        pli = _compute_pli(hr_z, rmssd_z, pupil_z)

        # Classify A, B, C
        dtype = _classify_abc(pli, outcome)

        trial = {
            **raw,
            # PLI components
            "hr_trial_mean":    round(hr_mean,    2) if hr_mean    is not None else None,
            "pupil_trial_mean": round(pupil_mean, 3) if pupil_mean is not None else None,
            "rmssd_trial_mean": round(rmssd_mean, 2) if rmssd_mean is not None else None,
            "hr_z":    round(hr_z,    3) if hr_z    is not None else None,
            "pupil_z": round(pupil_z, 3) if pupil_z is not None else None,
            "rmssd_z": round(rmssd_z, 3) if rmssd_z is not None else None,
            "pli":     pli,
            # Session quartile (for context)
            "session_quartile": _quartile(t0, t_first_event, t_session_end),
            # Classification
            "dissociation_type":      dtype,
            "dissociation_type_meta": TYPE_META[dtype],
            "debrief_prompt":         DEBRIEF_PROMPTS[dtype],
            # Analysis params used
            "analysis_s": analysis_s,
        }
        trials.append(trial)

    # ── Step 3: Compute expected PLI and dissociation scores ─────────────────
    correct_plis = [t["pli"] for t in trials if t["pli"] is not None and t["outcome"] in CORRECT_OUTCOMES]
    pli_expected = float(np.median(correct_plis)) if correct_plis else 0.0

    for trial in trials:
        trial["dissoc_score"] = _dissoc_score(trial["pli"], pli_expected)
        trial["pli_expected"] = round(pli_expected, 3)

    # ── Step 4: Type D session-level pass ────────────────────────────────────
    trials = _detect_type_d(
        db_path, trials, t_session_start, t_session_end, lfhf_slope_threshold
    )

    # ── Sort reel by dissoc_score (highest first) ────────────────────────────
    trials_sorted = sorted(
        [t for t in trials if t["dissociation_type"] not in ("routine_error", "unclassified")],
        key=lambda t: t.get("dissoc_score") or 0,
        reverse=True,
    )
    routine_and_unclassified = [
        t for t in trials if t["dissociation_type"] in ("routine_error", "unclassified")
    ]

    # ── Summary ──────────────────────────────────────────────────────────────
    type_counts = {}
    for t in trials:
        k = t["dissociation_type"]
        type_counts[k] = type_counts.get(k, 0) + 1

    pli_values = [t["pli"] for t in trials if t["pli"] is not None]

    summary = {
        "n_trials":      len(trials),
        "type_counts":   type_counts,
        "n_dissociation": sum(
            v for k, v in type_counts.items()
            if k not in ("routine_error", "unclassified")
        ),
        "pli_mean":  round(float(np.mean(pli_values)),   3) if pli_values else None,
        "pli_sd":    round(float(np.std(pli_values)),    3) if len(pli_values) > 1 else None,
        "pli_max":   round(float(np.max(pli_values)),    3) if pli_values else None,
        "pli_min":   round(float(np.min(pli_values)),    3) if pli_values else None,
        "baseline_warning": baseline.get("warning"),
        "signals_available": {
            "hr":    baseline.get("hr_n", 0) >= 3,
            "pupil": baseline.get("pupil_n", 0) >= 3,
            "rmssd": baseline.get("rmssd_n", 0) >= 3,
        },
    }

    return {
        "baseline":     baseline,
        "trials":       trials_sorted + routine_and_unclassified,
        "summary":      summary,
        "session_meta": {
            "t_session_start": t_session_start,
            "t_session_end":   t_session_end,
            "t_first_event":   t_first_event,
            "baseline_s":      baseline_s,
            "analysis_s":      analysis_s,
            "lfhf_slope_threshold": lfhf_slope_threshold,
            "pli_expected":    round(pli_expected, 3),
        },
        "drill": {
            "key":         getattr(drill_module, 'DRILL_KEY', '?'),
            "terminology": getattr(drill_module, 'TERMINOLOGY', {}),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _quartile(t0: float, t_start: float, t_end: float) -> str:
    if t_end <= t_start:
        return "Q1"
    frac = (t0 - t_start) / (t_end - t_start)
    if frac < 0.25: return "Q1"
    if frac < 0.50: return "Q2"
    if frac < 0.75: return "Q3"
    return "Q4"


# Stub for DRILL_MAP compatibility (not used — dissociation has its own endpoint)
def extract_trials(db_path: str) -> list[dict]:
    """Stub: dissociation drill uses analyze_session() via its own endpoint."""
    return []
