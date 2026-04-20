# LabReplay

**LabReplay** is a modular data replay and visualization platform for human performance research. It replays recorded lab sessions as if they were happening live, and visualizes every sensor stream in a real-time dashboard.

---

## Background

This project merges two existing tools:

### Total Recall
Python code to replay lab streaming layer (LSL) data from a SQLite database.

### Dashboard for visualizing data from a threat/non-threat decision task
A Streamlit-based dashboard originally built to analyze data from a threat/non-threat decision task (the 2 Alternative Forced Choice paradigm). It handles trial extraction from VirTra simulation logs, pupillometry analysis and gaze visualization. It contains a library of Plotly chart builders and metric computation functions.

**LabReplay combines these two projects:** Total Recall becomes the replay engine; the 2AFC visualization layer becomes the live dashboard that reacts to the data as it streams.

---

## What LabReplay Does

1. The user opens LabReplay and selects a data source — a SQLite database recorded during a lab session, or a CSV file.
2. They pick a date/time window and choose which sensor streams to replay.
3. They press **Play**.
4. Every selected stream begins publishing over LSL exactly as it did during the original session.
5. The dashboard visualizes each stream in real time — pupil diameter, heart rate, gaze position, eye events, etc.
6. In later iterations, an analysis framework computes metrics on the live stream (pupil response, decision accuracy, gaze entropy), and SAM3DB body pose data can be overlaid alongside the physiological signals.

---


## Architecture Overview

LabReplay is designed with **loose coupling** and **modularity** as core constraints. This makes it straightforward to add new panels, analysis modules, or data sources without modifying existing code.

```
Data Source (SQLite / CSV)
        |
Replay Engine  ──LSL──  LSL Subscriber Bus  ──►  Dashboard Panels
(Total Recall)                                ──►  Analysis Framework
                                              ──►  SAM3DB Adapter
```

### Core Components

#### Replay Engine (Total Recall — extended)
Reads stored data and re-publishes it over LSL with correct wall-clock timing. Each stream runs in its own thread. A `ReplayEventBus` broadcasts playback state (play, stop, progress) to any listener.

#### LSL Subscriber Bus
The central routing layer. It discovers all active LSL outlets, opens one inlet thread per stream, and dispatches incoming samples to any registered consumer via callbacks. Dashboard panels, analysis plugins, and SAM3DB adapters all attach here. 

#### Dashboard (evolved)
A web-based real-time dashboard built with Plotly Dash. Panels subscribe to the LSL Subscriber Bus and render incoming data on a configurable refresh interval. 

#### Timeline Component
A UI element that shows the full session span, the current playhead position advancing in real time, per-stream data availability bars, and (in later phases) interactive scrubbing and event markers.

#### Analysis Framework (future plug-in)
A plug-in interface that attaches to the LSL Subscriber Bus and receives the same live samples as the dashboard. First modules will be: pupil baseline correction, event-locked alignment, pupil dilation metrics (mean Δ, peak), gaze entropy, and decision accuracy. 

#### SAM3DB Adapter (future plug-in)
A separate process that runs pose estimation on video frames and publishes joint angles and body orientation. Dashboard panels subscribe to it through the same bus.

---

## Iterative Development Plan


### Phase 1 — Core: SQLite replay
**Goal:** end-to-end working pipeline from database to live visualization.

- Add `ReplayEventBus`
- Build the `LslSubscriberBus` (inlet threads, callback dispatch)
- Scaffold the Dash app: sidebar session controls + timeline skeleton
- Wire Play / Stop controls end-to-end
- **Deliverable:** select a session, press Play, watch pupil and ECG animate

### TODO: Next phases