# Analysis — Component Overview

> **Role:** Post-session physiological analysis engine with a REST API.
>
> The Analysis component runs as an independent FastAPI server. It
> reads Total Recall `.db` files directly from disk and computes
> epoch-based physiological metrics on demand. It is stateless — every
> request is a fresh computation from SQL data.

---

## Purpose

After a training session is recorded, operators want to understand
*what happened physiologically* during each engagement: Did heart rate
spike? Did pupil diameter expand? Did reaction time degrade? The Analysis
component answers these questions by:

1. Listing available sessions from the `sessions/` directory
2. Identifying discrete trials (engagements) within a session
3. Slicing each trial's physiological signals into **baseline** and
   **analysis** time windows
4. Computing delta metrics (Δ from baseline) and returning them as JSON

The Frontend's **Human Performance Workspace** page calls this API.

---

## Conceptual Model

```
sessions/*.db  (written by Total Recall)
      │
      ▼
 SQL reader (direct SQLite access)
      │
 Drill Router (selects logic for BehDisc / PVT / L2GoNoGo)
      │
 Signal Extractor
      │   ├─ hr.py    (Heart Rate)
      │   ├─ pupil.py (Pupil Diameter)
      │   └─ motion.py (Head Motion)
      │
 Epoch Builder
      │   ├─ baseline window  (N seconds before event)
      │   └─ analysis window (N seconds after event)
      │
 REST JSON response  ──→  Frontend (Human Performance Workspace)
```

---

## Structure

```
Analysis/
│
├── main.py                     # FastAPI app entry point (router mounting only)
├── config.py                   # SESSIONS_DIR and path configuration
├── pyproject.toml              # uv project manifest
│
├── signals/                    # Physiological signal extraction
│   ├── __init__.py
│   ├── registry.py             # SIGNAL_MAP: name → extractor class
│   ├── extractor.py            # Core epoch builder (baseline/analysis windowing)
│   ├── hr.py                   # Heart Rate: BPM from LSL samples
│   ├── pupil.py                # Pupil Diameter: mm from Neon gaze data
│   └── motion.py               # Head Motion: magnitude from IMU / head tracker
│
├── drills/                     # Drill-specific trial identification logic
│   ├── __init__.py             # DRILL_MAP registry: key → DrillBase subclass
│   ├── base.py                 # DrillBase ABC: get_trials(), get_summary()
│   ├── behdisc/
│   │   ├── __init__.py
│   │   └── algorithm.py        # BehDisc: identifies Hostile / Non-Hostile engagements
│   └── pvt/
│       ├── __init__.py
│       └── algorithm.py        # PVT: identifies reaction-time trials
│
└── routers/                    # FastAPI endpoint handlers
    ├── __init__.py
    ├── health.py               # GET /api/health
    ├── sessions.py             # GET /api/sessions?drill=X
    ├── trials.py               # GET /api/trials?session=X&drill=Y
    └── epoch.py                # POST /api/epoch (main computation endpoint)
```

---

## Key Concepts

### Epoch
An **epoch** is a time-locked slice of a physiological signal relative to
a trial event. Each epoch has:
- A **baseline window** (e.g. 2 s before the event) — establishes the
  resting value
- An **analysis window** (e.g. 2 s after the event) — captures the response

The signal is resampled to a fixed bin resolution (default 100 ms bins)
and the delta from baseline mean is computed per bin.

### Trial
A **trial** is a discrete event identified by drill-specific logic:
- **BehDisc**: one engagement (target appears → operator responds)
- **PVT**: one reaction-time probe (tone/flash → button press)

Each drill's `algorithm.py` knows how to identify trial boundaries from
the VirTra event stream stored in the `.db`.

### DRILL_MAP
The `drills/__init__.py` registry maps drill keys to algorithm classes:
```python
DRILL_MAP = {
    'behdisc': BehDiscDrill,
    'pvt':     PVTDrill,
}
```
Adding a new drill = create a new package + register one key.

---

## REST API

All endpoints return JSON. Base URL: `http://127.0.0.1:8081`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness check |
| GET | `/api/sessions?drill=X` | List `.db` files matching drill |
| GET | `/api/trials?session=F&drill=X` | List trials + summary stats |
| POST | `/api/epoch` | Compute physiological epochs |

### POST `/api/epoch` request body
```json
{
  "session":     "filename.db",
  "drill":       "behdisc",
  "trial_ids":   [0, 1, 2, ...],
  "signals":     ["hr", "pupil", "motion"],
  "baseline_s":  2.0,
  "analysis_s":  2.0,
  "bin_s":       0.1,
  "do_zscore":   false
}
```

---

## How to Run

```bash
cd Analysis
uv run python main.py
# Serves on http://127.0.0.1:8081
```

---

## Modes: Live vs. Replay

The Analysis component only operates in **Replay mode** — it always reads
from a `.db` file. The Live modality (real-time math during a session) is
handled by the Backend's `LiveService` + the Frontend's `intel/` JavaScript
layer, which compute simpler rolling metrics without the full epoch pipeline.

| Modality | Who computes | Where |
|----------|-------------|-------|
| **Live** | `Backend/services/live_service.py` + `Frontend/js/intel/` | In-process, streaming |
| **Replay** | `Analysis/` FastAPI | On-demand, batch |
