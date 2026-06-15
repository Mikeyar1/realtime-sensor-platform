# LabReplay — Dissociation Detection Architecture (Revised)
## Replay Mode · 4 Dissociation Types · PLI + ML Classifier

---

## Dissociation Type Taxonomy (Final)

| Type | Name | Pattern | Mechanism |
|:---|:---|:---|:---|
| **A** | Silent Competence | High PLI + Correct decision | Operator succeeds despite physiological overload — resilience or expertise signature |
| **B** | Silent Failure | Low/normal PLI + Error | Error occurs without physiological load — upstream failure: bias, priming, decision-policy |
| **C** | Attentional Tunnel | Any PLI + gaze entropy collapsed + Error | Eyes on target, processing quality collapsed — looking but not seeing |
| **D** | Fatigue-Resilience | Fatigue markers rising across session + Performance maintained | Operator sustains accuracy through accumulated cognitive fatigue |

> **Routine Error** (High PLI + Error) is the *expected* cell — it is detected but NOT classified as a dissociation. It is logged as baseline for comparison.

**Note:** Classification is computed **after the scenario ends**, using the full session's data. The system surfaces these events for the post-session debrief, not during the training itself.

---

## ML Classifier — Grounding in the Three Papers

### What the Three Papers Are and What They Contribute

#### ml_physio.pdf — Boudry, Durand, Meric & Mouakher (2024)
*Mini-review of ML methods in physiological explorations of endurance athletes. Front. Sports Act. Living.*

**What it is:** A survey of ML applications in exercise physiology — VO2max prediction, HRV modeling, cardiopulmonary response estimation. Endurance sports context.

**What it contributes to LabReplay:**
- Identifies **Random Forest and Gradient Boosted Trees** as the most consistently reliable algorithms when working with physiological features and small-to-medium N
- Explicitly notes that ANNs often overfit with small samples — a critical caution for our N (small at first)
- Argues for **feature selection grounded in domain knowledge** before model fitting — directly supports our PLI component selection being paper-based, not purely data-driven
- Highlights **RMSSD and HRV features** as among the most successfully ML-modeled physiological signals

**What it does NOT contribute:**
- ⚠️ No military or simulation context whatsoever
- ⚠️ No behavioral outcome (shoot/no-shoot) in any model — it predicts physiology from physiology, not behavior from physiology
- ⚠️ Cannot be cited as evidence that ML works for dissociation detection in VirTra specifically

---

#### ml_physio2.pdf — Jianjun, Isleem, Almoghayer & Khishe (2025)
*Predictive athlete performance modeling with ML and biometric data integration. Scientific Reports.*

**What it is:** A hybrid gradient boosting + neural network model predicting athletic performance from HRV, VO2, muscle activation, AND psychological variables (mental toughness, cohesion). N=480 athletes, R²=0.90.

**What it contributes to LabReplay:**
- Provides the strongest methodological precedent for **combining physiological signals with behavioral/psychological outcomes in a single ML model** — the same structure we need (PLI features + VirTra outcome → dissociation class)
- Shows **gradient boosting outperforms neural networks** when features include mixed physiological and psychological variables at moderate N
- Feature importance analysis (SHAP-equivalent): FMS score (13.7%), athlete dedication (11.5%), max acceleration (10.2%) — demonstrates that **domain-meaningful feature selection and importance analysis is standard practice** in this type of model
- The "hybrid model" framing (physio + behavior + context) is directly citable as a methodological precedent

**What it does NOT contribute:**
- ⚠️ Target population is competitive athletes, not law enforcement trainees under lethal force stress
- ⚠️ No time-series classification — it predicts aggregate outcome, not event-level dissociation
- ⚠️ Psychological variables (mental toughness, cohesion) used as inputs — we do not have equivalent constructs measured

---

#### ml_physio3.pdf — Hwang & Shin (2026)
*Toward Personalized Adaptive Learning Using AI and Physiological Signals. IEEE Access.*

**What it is:** A PAL (Personalized Adaptive Learning) system that uses HRV, HR, and Respiration Rate to model cognitive load, fatigue accumulation, and adapt instructional difficulty in real time. Uses both rule-based (Yerkes-Dodson-Based Policy) and AI-based (look-ahead optimization) policies.

**What it contributes to LabReplay — this is the most structurally relevant paper:**
- **Physiological deviation metric** δ = ‖w ⊙ (ŝ_t - ŝ_normal)‖₁ — a weighted L1 distance from a personal physiological baseline. This is *exactly* what the PLI does, and it is now formally cited.
- **Dual policy approach**: rule-based heuristics first, AI-based refinement second — directly validates our Phase 1 (rule-based) → Phase 2 (ML) build sequence
- **Fatigue accumulation model**: fatigue tracked as a session-level trajectory, not a point estimate — supports Type D (Fatigue-Resilience) being defined at the session quartile level, not per trial
- **Individual baseline normalization** (ŝ_normal) per user — directly validates within-person Z-scoring
- **HRV, HR, RR** are the three signals used in their implementation (practical, wearable-compatible) — consistent with our PLI components
- The Yerkes-Dodson framework they use for policy selection is the same conceptual grounding as our 2×2 quadrant logic

**What it does NOT contribute:**
- ⚠️ System adapts difficulty; LabReplay detects events after the fact — different direction of inference
- ⚠️ No shoot/no-shoot decisions, no VirTra events, no behavioral error classification
- ⚠️ N is small (PSI dataset, synthetic simulation) — no empirical validation in a real operational environment

---

### Honest Assessment: What the Papers Support vs. What Remains Unverified

| Claim | Status | Citation |
|:---|:---|:---|
| HRV (RMSSD, LF/HF) is a reliable ML-predictable cognitive load feature | ✅ Supported | Boudry et al. (2024); Hwang & Shin (2026) |
| Within-person physiological deviation from baseline is the correct normalization | ✅ Supported | Hwang & Shin (2026, Eq. 2); Berger et al. (2023) |
| Gradient Boosted Trees (XGBoost/LightGBM) are appropriate for mixed physio+outcome features at small N | ✅ Supported | Boudry et al. (2024); Jianjun et al. (2025) |
| Dual rule-based + ML approach is methodologically sound | ✅ Supported | Hwang & Shin (2026); Lee et al. (2021) |
| Fatigue should be tracked as session trajectory, not single-point | ✅ Supported | Hwang & Shin (2026, Eq. 3); Lee et al. (2021) |
| This specific ML model will work for VirTra dissociation classification | ❌ NOT proven — no paper applies ML to shoot/no-shoot dissociation classification | — |
| The 4 dissociation types are empirically separable as ML classes | ❌ NOT proven — requires labeled data from this system | — |
| PLI predicts VirTra behavioral outcome | ❌ NOT proven — requires convergent validation (PERCLOS, EAR, PVT) | — |

**Bottom line for your PI:** The ML approach is methodologically grounded in design and feature selection. Whether it works in *this* context is an empirical question — and that is, precisely, your research contribution.

---

## Architecture: Replay Mode Only

### Data Flow (Post-Session)

```
sessions/*.db  (Total Recall)
      │
      ▼
Analysis/drills/dissociation/
      ├── algorithm.py         Trial segmentation — VirTra "Lane Targets Changed" → windows
      ├── baseline.py          First 60s → per-signal individual baseline
      ├── feature_extractor.py Groups A–D features per trial
      ├── pli.py               PLI = mean(Z(HR_Δ), Z(-RMSSD_ratio), Z(LF_HF_Δ), Z(pupil_Δ))
      ├── classifier.py        Rule-based 2×2 + special cases → Type A/B/C/D
      └── ml/
          ├── feature_matrix.py  Build trial × feature matrix for ML input
          ├── predictor.py       Load trained model, run inference
          └── trainer.py         Train XGBoost on labeled sessions

Analysis/signals/ (new extractors)
      ├── hrv.py               RMSSD, LF/HF, pNN50 from HRV_Live stream
      ├── gaze_entropy.py      Shannon entropy of gaze XY per trial window
      └── ear.py               EAR_avg, PERCLOS from WebcamFatigue stream

Analysis/routers/
      └── dissociation.py      New REST endpoints
```

---

## Feature Matrix (per trial)

### Group A — Cardiac (from HRV_Live + Polar PPI)
| Feature | Formula | Grounded In |
|:---|:---|:---|
| `HR_delta` | mean(HR_trial) − HR_baseline | Standard autonomic physiology |
| `RMSSD_ratio` | RMSSD_trial / RMSSD_baseline | Lee et al. (2021); Hwang & Shin (2026) |
| `LF_HF_delta` | LF_HF_trial − LF_HF_baseline | Lee et al. (2021) |
| `pNN50_trial` | % successive PPI diffs > 50ms | Lee et al. (2021) |

### Group B — Pupillometry (from Neon Gaze)
| Feature | Formula | Grounded In |
|:---|:---|:---|
| `pupil_delta` | mean_pupil_trial − pupil_baseline | Salmon et al. (2025); AlSabah et al. (2026) |
| `eyelid_aperture_delta` | mean_aperture_trial − aperture_baseline | WebcamFatigue fatigue indicator |

> ⚠️ Luminance correction required before using pupil_delta as cognitive load proxy (Lighting paper, P6). Mark as caveat in Phase 1; implement correction in Phase 2.

### Group C — Gaze & Attention (from Neon Gaze + Eye Events)
| Feature | Formula | Grounded In |
|:---|:---|:---|
| `gaze_entropy` | Shannon entropy of gaze_x, gaze_y distribution | AlSabah et al. (2026) — attentional narrowing |
| `fixation_mean_duration` | mean(fixation event durations) | AlSabah et al. (2026) |
| `saccade_amplitude_mean` | mean(amplitude_angle_deg) | AlSabah et al. (2026) |
| `EAR_delta` | mean_EAR_trial − EAR_baseline | WebcamFatigue |
| `PERCLOS` | mean(perclose_score) per trial | FAA standard; Ciccarelli et al. (2021) |

### Group D — Performance Outcomes (from VirTra Events)
| Feature | Definition | Grounded In |
|:---|:---|:---|
| `trial_outcome` | HIT / MISS / FALSE_POSITIVE / FALSE_NEGATIVE / CORRECT_NO_SHOOT | VirTra event log |
| `reaction_time_s` | Stimulus onset → first shot event | Biggs et al. (2023) — RT under speed pressure |
| `shots_fired` | Count within trial window | VirTra event log |
| `session_quartile` | Q1 / Q2 / Q3 / Q4 based on elapsed time | Hwang & Shin (2026) — fatigue trajectory context |

### On Session Time / Elapsed Time

The user asked: *"should we take into account time elapsed?"*

**Answer:** Yes, but carefully — as **session_quartile** (Q1/Q2/Q3/Q4), not raw elapsed time.

**Why quartile and not raw time:**
- Raw elapsed time is collinear with fatigue accumulation (LF/HF trajectory), which is already in the feature set
- Quartile is the appropriate temporal context variable because it captures *where in the session* the event occurred — which determines whether the fatigue trajectory is rising or not
- Hwang & Shin (2026) model fatigue accumulation explicitly as a session-level trajectory, not a per-moment timestamp

**What quartile enables:**
- Type D (Fatigue-Resilience) requires that fatigue is rising (LF/HF trend) AND performance holds — this is only meaningful in Q3 and Q4
- A Type B event in Q1 (early session) has a different mechanistic interpretation than a Type B in Q4 (late session, could be fatigue-induced decision-policy shift)

---

## PLI Formula (unchanged, citations added)

```
PLI = mean(Z(HR_delta), Z(-RMSSD_ratio), Z(LF_HF_delta), Z(pupil_delta))
```

*Physiological deviation metric grounded in Hwang & Shin (2026, Eq. 2): δ = ‖w ⊙ (ŝ − ŝ_normal)‖; and Lee et al. (2021): LF/HF and RMSSD as primary cognitive fatigue features. Z-scores computed within-person, within-session (Berger et al., 2023).*

---

## Rule-Based Classifier (Phase 1)

```python
def classify_trial(pli, outcome, gaze_entropy, gaze_entropy_baseline,
                   lf_hf_trend, session_quartile, fatigue_rising):

    # Type A — Silent Competence
    if pli > 1.0 and outcome in ("HIT", "CORRECT_NO_SHOOT"):
        return "type_a"

    # Type B — Silent Failure
    if pli <= 0.5 and outcome in ("MISS", "FALSE_POSITIVE", "FALSE_NEGATIVE"):
        return "type_b"

    # Type C — Attentional Tunnel
    gaze_entropy_suppressed = gaze_entropy < 0.6 * gaze_entropy_baseline
    if gaze_entropy_suppressed and outcome in ("MISS", "FALSE_POSITIVE", "FALSE_NEGATIVE"):
        return "type_c"

    # Type D — Fatigue-Resilience (session-level, Q3/Q4 only)
    if fatigue_rising and session_quartile in ("Q3", "Q4"):
        if outcome in ("HIT", "CORRECT_NO_SHOOT"):
            return "type_d"

    # Routine Error — expected cell, logged but not a dissociation
    if pli > 1.0 and outcome in ("MISS", "FALSE_POSITIVE", "FALSE_NEGATIVE"):
        return "routine_error"

    return "unclassified"
```

**Dissociation Score (for reel ranking):**
```
dissoc_score = |PLI_observed − PLI_expected_for_outcome|
```
Where `PLI_expected_for_outcome` = median PLI across correct trials (baseline expectation).
Higher score = more surprising = higher in the reel.

---

## ML Classifier (Phase 2 — after labeled data exists)

### Model: XGBoost (Gradient Boosted Trees)

**Justification:**
- Boudry et al. (2024): RF and GBT consistently outperform ANN at small N with physiological features
- Jianjun et al. (2025): GBT achieved R²=0.90 with mixed physio+psychological features in performance prediction
- Hwang & Shin (2026): AI-based policies outperformed rule-based but required training coverage
- XGBoost handles mixed feature types (continuous PLI, categorical outcome, ordinal quartile) natively
- SHAP values provide per-trial feature importance — mechanistically interpretable for debrief enrichment

**Input features (20 features):**
All Group A–D features above: HR_delta, RMSSD_ratio, LF_HF_delta, pNN50, pupil_delta, eyelid_aperture_delta, gaze_entropy, fixation_mean_duration, saccade_amplitude_mean, EAR_delta, PERCLOS, reaction_time_s, shots_fired, session_quartile_encoded, trial_outcome_encoded, fatigue_trend_slope

**Target:**
5-class: {type_a, type_b, type_c, type_d, routine_error}

**Validation:**
- Leave-One-Operator-Out Cross-Validation (LOOCV) — because operators are the unit of replication
- Convergent validity: does PLI-based classification agree with PERCLOS/EAR-based independent assessment?
- Confusion matrix weighted by dissoc_score (high-score events matter more)

**Minimum viable training set:** ~5–10 operators, ~20–30 labeled trials per operator.

---

## New REST Endpoints

```
POST /api/dissociation/classify
     Body: { session: "file.db", drill: "behdisc" }
     Returns: { trials: [ { id, timestamp, type, pli, dissoc_score, outcome, quartile } ] }

GET  /api/dissociation/reel?session=X
     Returns: trials sorted by dissoc_score descending

GET  /api/dissociation/trial/{id}/features?session=X
     Returns: full feature vector for one trial (for biosignal panel rendering)

POST /api/ml/label
     Body: { session, trial_id, label: "type_a" }
     Stores ground truth

POST /api/ml/train
     Triggers re-training on all labeled sessions

GET  /api/ml/status
     Returns: { model_version, n_labeled, accuracy_loocv, features_used }
```

---

## Debrief Prompt Design (Post-Scenario, per Dissociation Type)

Classification is computed after the scenario ends. The Frontend displays a paragraph prompt per detected event. Prompts follow the AlSabah et al. (2026) sequential investigative structure: Recall → Interpretation → Decision → Reflection.

```
TYPE A — Silent Competence
"At [timestamp], your physiological data showed significant stress —
elevated heart rate and suppressed HRV — yet your decision was accurate.
Walk me through that moment: what did you notice first? What information
did you use to make that call? Do you remember feeling the pressure, or
were you focused on something specific? Understanding what worked here
can help you reproduce it under future high-stress conditions."

TYPE B — Silent Failure
"At [timestamp], your physiological indicators were within normal range —
there was no strong signal of overload or stress — yet you made an error
here. Since the physiology doesn't explain this one, the error likely
originated elsewhere: what were you focused on in the moment before this?
What did you think you saw? Is there anything about the scenario setup or
what came just before that might have influenced your expectation?"

TYPE C — Attentional Tunnel
"At [timestamp], your gaze data shows you were looking in the right area —
but your attentional pattern shifted: fixations became longer and less
varied. You missed this decision. What do you remember seeing during this
window? Can you describe what was happening in the scene? Sometimes the
eyes are present but the brain has already narrowed what it's processing.
Does this moment feel familiar?"

TYPE D — Fatigue-Resilience
"By [timestamp] — late in this session — your HRV data shows clear signs
of accumulated cognitive fatigue: your system had been working hard. And
yet your decision accuracy held. What were you doing differently at this
point in the session? What did it feel like internally? If you can identify
the strategy or the state that allowed you to maintain performance through
fatigue, that becomes a resource you can deliberately access in extended
operations."
```

---

## Build Sequence (Replay Mode Only)

### Phase 1 — Rule-Based Classifier
| Component | Location | Status |
|:---|:---|:---|
| Trial segmentation | `Analysis/drills/dissociation/algorithm.py` | Build now |
| 60s baseline extraction | `Analysis/drills/dissociation/baseline.py` | Build now |
| PLI computation (4-component) | `Analysis/drills/dissociation/pli.py` | Build now |
| HRV extractor (RMSSD, LF/HF) | `Analysis/signals/hrv.py` | Build now |
| Gaze entropy extractor | `Analysis/signals/gaze_entropy.py` | Build now |
| EAR + PERCLOS extractor | `Analysis/signals/ear.py` | Build now |
| Rule-based classifier | `Analysis/drills/dissociation/classifier.py` | Build now |
| REST endpoints (classify + reel) | `Analysis/routers/dissociation.py` | Build now |
| Teachable Moments page | `Frontend/js/pages/teachable-moments.js` | Build now |
| Biosignal panel (multi-stream) | `Frontend/js/charts/chart-dissociation.js` | Build now |
| Debrief panel + prompts | `Frontend/js/components/debrief-panel.js` | Build now |

### Phase 2 — ML Classifier
| Component | Location | Prerequisite |
|:---|:---|:---|
| Ground truth labeling tool | `Analysis/ml/labeler.py` | Phase 1 deployed + operators run sessions |
| XGBoost trainer | `Analysis/ml/trainer.py` | ≥5 labeled operators |
| LOOCV evaluator + SHAP | `Analysis/ml/evaluator.py` | Trained model |
| ML vs. rule-based toggle | Frontend | Phase 2 model ready |
| Convergent validity report | `Analysis/ml/evaluator.py` | Labeled data |

---

## What This System Is Not (Scientific Boundary)

- **Not a clinical assessment.** Dissociation classification is hypothesis-generating for the debrief, not a diagnostic finding about the operator's cognitive state.
- **Not validated for live mode.** LF/HF computation requires ~2–5 minutes of PPI data for frequency-domain estimation. In live mode this is approximated; in replay it is exact.
- **Not generalizable without cross-operator validation.** The rule-based PLI thresholds (+1.0, 0.5) are theoretically motivated starting points — not empirically derived cutoffs. They require calibration once labeled data exists.
- **The ML model requires labeled ground truth.** Until operators produce labeled sessions, the Phase 2 classifier cannot be trained. Phase 1 (rule-based) is the system for all initial use.
