# LabReview

A real-time human performance monitoring dashboard built for VirTra-based simulation sessions. LabReview ingests physiological and behavioral data during live simulation sessions and displays running metrics on a local web.

---

## What it does

During a training scenario, LabReview connects to hardware streams over [Lab Streaming Layer (LSL)](https://labstreaminglayer.org/) — including physiological sensors (e.g., Polar H10, Pupil Labs Neon eye tracking) and VirTra simulator events, and routes them through a Python backend to a browser-based dashboard. When a VirTra scenario starts, the backend identifies which cognitive assessment is running, instantiates the appropriate trial tracker, and streams live trial-by-trial metrics to the frontend over WebSocket.

When the scenario stops, either from VirTra or the dashboard's Stop button, summary metrics are computed locally and shown in the after-action review (AAR) panel. Sessions with valid trials are auto-saved to the local database; the operator then confirms the participant name or discards the session.

## Supported scored drills

| Drill | Input | Primary construct |
| --- | --- | --- |
| PVT | Registered native VirTra scenarios | Sustained attention / vigilance |
| Go/No-Go | `multicolor_gng` custom VirTra LSL stream | Response inhibition / inhibitory control |
| Behavioral Discrimination | Registered native VirTra scenarios | Perceptual / decision discrimination |

Unknown scenarios and former Marksmanship scenarios do not start a scored session. Shared VirTra events may still appear in the raw event feed. The live and final cards use the same definitions for each drill; see [the current metric reference](METRICS.md).

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
- Target classification for drills
- Timing parameters (e.g. `late_shot_window_ms`, `min_rt_ms`)

To add a new scenario to an existing drill type, just append its name to the `scenarios` list. To add a new cognitive attribute entirely, see the section below.

---

## How a session works

1. Operator opens the dashboard and clicks **Start** (or a VirTra scenario fires and triggers auto-activation)
2. The backend detects `Scenario Started` from the VirTra LSL stream, looks up the drill in the registry, and begins trial tracking
3. VirTra events (Target Activated, Shot Hit, Target Deactivated, etc.) arrive and are processed trial-by-trial; each completed trial broadcasts an update to the frontend in real time
4. Physiological data (HR, HRV, pupil diameter) is monitored in a rolling window and broadcast periodically alongside trial data
5. When `Scenario Stopped` fires or the operator clicks **Stop**, the backend finalizes the drill, computes summary metrics, and auto-saves sessions with valid trials to the local database
6. The operator confirms or corrects the participant name, or discards the session

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
| `session.trial_update` | Each completed PVT, Go/No-Go, or Discrimination trial |
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
5. Add an explicit task-type mapping and metric-card specification in the frontend AAR files

The registry and AAR reject unsupported task types. Add a new type only when its tracker, metric contract, and UI are ready together.

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
