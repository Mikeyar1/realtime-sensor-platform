# LabReview

A real-time human performance monitoring dashboard built for VirTra-based simulation sessions. LabReview ingests physiological and behavioral data during live simulation sessions and displays running metrics on a local web.

---

## What it does

During a training scenario, LabReview connects to hardware streams over [Lab Streaming Layer (LSL)](https://labstreaminglayer.org/) — including physiological sensors (e.g., Polar H10, Pupil Labs Neon eye tracking) and VirTra simulator events, and routes them through a Python backend to a browser-based dashboard. When a VirTra scenario starts, the backend identifies which cognitive assessment is running, instantiates the appropriate trial tracker, and streams live trial-by-trial metrics to the frontend over WebSocket.

After the scenario stops, summary metrics are computed locally and presented in an after-action review (AAR) panel. The operator can then fill in participant info and save a permanent session record to disk.

---

## Requirements

- Python ≥ 3.14
- [`uv`](https://github.com/astral-sh/uv) for backend dependency management
- A static file server for the frontend (or any browser opened directly on `frontend/index.html`)
- LSL-compatible hardware or a simulated LSL stream for testing

## Getting started

### 1. Backend

```bash
cd backend
uv run python main.py
```

The server starts on `ws://0.0.0.0:8500` by default. Edit `backend/config.toml` to change the host, port, LSL discovery interval, or session data directory.

### 2. Frontend

Serve the `frontend/` directory with any static file server:

```bash
cd frontend
python -m http.server 3000
```

Then open `http://localhost:3000` in a browser. The frontend connects to the backend WebSocket automatically on load.

---

## Configuration

### `backend/config.toml`

Controls backend runtime behavior — server host/port, LSL polling intervals, and where session records are written. All paths are relative to `backend/`.

```toml
[server]
host = "0.0.0.0"
port = 8500

[lsl]
discovery_interval_seconds = 0.5
pull_timeout_seconds = 1.0

[session_data]
session_data_directory = "../data/SessionData"
```

### `config/drill_registry.toml`

Maps VirTra scenario name strings to cognitive attributes. This is the main file to touch when adding scenarios or adjusting trial parameters — no code changes needed.

Each entry specifies:
- Which tracker to use
- Which VirTra scenario names trigger it (matched against `Scenario Started` events)
- Target classification for  drills
- Timing parameters (e.g. `late_shot_window_ms`, `min_rt_ms`)

To add a new scenario to an existing drill type, just append its name to the `scenarios` list. To add a new cognitive attribute entirely, see the section below.

---

## How a session works

1. Operator opens the dashboard and clicks **Start** (or a VirTra scenario fires and triggers auto-activation)
2. The backend detects `Scenario Started` from the VirTra LSL stream, looks up the drill in the registry, and begins trial tracking
3. VirTra events (Target Activated, Shot Hit, Target Deactivated, etc.) arrive and are processed trial-by-trial; each completed trial broadcasts an update to the frontend in real time
4. Physiological data (HR, HRV, pupil diameter) is monitored in a rolling window and broadcast periodically alongside trial data
5. When `Scenario Stopped` fires, summary metrics are computed and the AAR panel fills with final results
6. The operator enters participant info and clicks **Save** — a `session_record.json` is written to `data/SessionData/`

---

## WebSocket protocol

All messages are JSON with a `type` field. The frontend sends:

| Message | Purpose |
|---|---|
| `live.activate` | Enter live monitoring mode |
| `live.deactivate` | Exit live mode |
| `streams.get_catalog` | Request list of active LSL streams |
| `system.get_state` | Request current session state |

The backend sends:

| Message | When |
|---|---|
| `session.state` | On connect, mode change |
| `streams.catalog` | After catalog request or when streams change |
| `stream.sample` | Each LSL sample for subscribed streams |
| `session.scenario_started` | VirTra scenario begins |
| `session.trial_update` | Each completed PVT or GNG trial |
| `session.biosignal_update` | Rolling HR/HRV/pupil stats |
| `session.virtra_event` | Raw VirTra event for the event log |
| `session.scenario_stopped` | VirTra scenario ends |
| `session.metrics_ready` | Post-scenario summary metrics |

Message type constants are defined in `backend/api/message_types.py` (Python) and mirrored in `backend/api/message_types.js` (JavaScript). Keep them in sync when adding new message types.

---

## Adding a new cognitive attribute

1. Create `backend/cognitive_attributes/<name>/tracker.py` — implement `reset(params, targets)`, `on_virtra_event(event_type, timestamp, metadata)`, `get_completed_trials()`, and `get_session_record(...)`
2. Create `backend/cognitive_attributes/<name>/metrics.py` — implement `compute_live_metrics(trials) -> dict`
3. Add an entry to `ATTRIBUTE_REGISTRY` in `backend/cognitive_attributes/__init__.py`
4. Add a `[cognitive_attributes.<name>]` section to `config/drill_registry.toml` with the scenario name(s) and any parameters
5. Optionally add a matching AAR display module under `frontend/js/features/aar/task-types/` and register it in `aar-dashboard.js`

Steps 1–4 are sufficient for backend trial tracking. Step 5 is only needed if you want a custom frontend display beyond the general fallback.

---

## LSL streams

LabReview auto-discovers and subscribes to any LSL stream on the network. Streams known to the system:

| Type | Source | Notes |
|---|---|---|
| `HR`, `ECG`, `PPI`, `PPG`, `ACC`, `GYRO` | Polar H10 | HR/PPI used for HRV computation |
| `Gaze`, `eye_events`, `IMU` | Pupil Labs Neon | Gaze batched at 50 ms intervals |
| `VirTraEvents` | VirTra simulator | Drives scenario lifecycle and trial tracking |
| `speech_transcription` | Microphone / Whisper | Displayed in the live event ticker |

Missing hardware produces no errors — streams that aren't present are simply absent from the catalog.
