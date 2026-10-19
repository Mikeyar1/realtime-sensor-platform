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
| Fitts-PVT_Clay | `fitts_pvt` / `fitts_pvt_clay` custom VirTra LSL | Existing PVT metrics per round, with recorded preset and difficulty settings |
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

Defines the implemented cognitive trackers, target classification, and trial timing parameters. Scenario aliases listed in `config/virtra_streams.toml` take precedence; unlisted legacy aliases here still work.

Each entry specifies:
- Which tracker to use
- Which VirTra scenario names trigger it (matched against `Scenario Started` events)
- Target classification for drills
- Timing parameters (e.g. `late_shot_window_ms`, `min_rt_ms`)

To add a new scenario alias, use `config/virtra_streams.toml`. To add a new cognitive attribute, see the section below.

### `config/virtra_streams.toml`

Defines native and custom VirTra stream identity, drill aliases, each drill's `metrics_source`, lifecycle event names, round policy, payload paths, and component names. `Fitts-PVT_Clay` selects `custom` and recognizes the observed `fitts_pvt` drill type as well as `fitts_pvt_clay`; its rounds use the existing PVT metric names and formulas. Change `[drills.fitts_pvt_clay].metrics_source` to `"native"` only for native PVT comparison testing, then restart LabReview. The other two PVT/Fitts aliases retain native PVT scoring.

For a new custom drill, fill in `custom_drill_types`, component `phase_values`, event names, and payload paths. Select `metrics_source = "custom"` only after a matching Python tracker and metric contract are implemented; startup rejects unfinished component definitions. In a source checkout, edit `config/virtra_streams.toml`; an installed build reads the operator copy at `%APPDATA%\LabReview\config\virtra_streams.toml` when present. Older version 1 operator copies are converted in memory, with the timed Auto policy removed; update the file to version 2 before editing `metrics_source`. Stream identity changes need an app restart. Drill aliases reload at scenario start.

The Live view starts a supported drill as soon as its configured scoring stream emits a start event. Native recognition accepts stream name `V_300` or type `VirTraEvents`; custom recognition accepts type `VirTra Events` (the usual name is `virtra_lsl`). LabReview continues reading `V_300` for scenario, simulator, and weapon context while custom events supply metrics. The session title shows the scenario name, with a separate **Metrics: Native/Custom** label. A missing selected scoring stream does not trigger a timed fallback.

For `Fitts-PVT_Clay`, the emitted preset label is shown on each round and saved with that round. Completed rounds require a participant name at confirmation. Longitudinal review queries completed rounds by participant and scenario, with an optional preset-label filter. An unreported preset remains unreported; LabReview does not assign a preset from numeric settings.

A round appears when the custom `Round Started` event follows the fixation target hit. `Round Completed` collapses it into previous rounds. Only after a completed round does the live view show **Waiting for next round**; a new active round appears on the next `Round Started`. Active, expanded completed, and final results share the same PVT cards. At scenario end, **Grand Total / Average** pools trials from completed rounds, sums their active trial windows for Session Duration, and excludes inter-round waiting time. For scoring, a fired shot with exactly one visible target is assigned to that presentation. A timeout after an assigned shot is a PVT miss, while a timeout without a shot is an omission. When concurrent targets make shot attribution uncertain, affected PVT rates remain unavailable rather than treating the timeout as an omission. Those partial rounds remain in detail but are excluded from trends. Native and custom records retain their source tags for longitudinal comparison.

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
4. Add a `[cognitive_attributes.<name>]` section to `config/drill_registry.toml` with tracker parameters, and add its scenario aliases and components to `config/virtra_streams.toml`
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

For reproducible VirTra sessions, use the [raw LSL capture and replay tool](VIRTRA_LSL_CAPTURE.md). It records the original channels from both native and custom event streams, then republishes them for home testing. Console logs and scored session records do not contain the complete wire samples.
