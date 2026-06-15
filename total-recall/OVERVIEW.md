# Total Recall — Component Overview

> **Role:** Session database recorder and LSL stream replayer.
>
> Total Recall is the data capture and replay layer. It has two jobs:
> (1) record a live session to a SQLite database in real time, and
> (2) replay that database back over Lab Streaming Layer (LSL) so the
> rest of the system can treat a recorded session exactly like a live one.

---

## Purpose

During a training session, physiological and event data arrives over LSL from
multiple sensors simultaneously (Polar heart rate, Pupil Labs Neon gaze,
VirTra motion/events, speech-to-text, etc.). Total Recall captures all of
this into a single timestamped `.db` file and can later re-publish it
stream-by-stream at the original timing, allowing the system to replay a
session without needing the physical hardware present.

---

## Conceptual Model

```
Live Session
  └─ LSL inlets (one per sensor stream)
       └─ SQL writer  ──→  sessions/<name>.db
                                │
                      ──────────┘
                     Replay request
                          │
                   sql_record_fetcher
                          │
                   lsl_replay_publisher
                          │
                   LSL outlets (mimics live sensors)
                          │
                   Backend InletManager (sees no difference)
```

The key design insight: **the replay path produces the same LSL outlet
format as the real sensors.** The Backend, Analysis, and Frontend
components are completely unaware of whether data is live or replayed.

---

## Structure

```
total-recall/
│
├── main.py                     # CLI / headless entry point
├── guiapp.py                   # Tkinter desktop GUI (primary user-facing tool)
├── config.toml                 # Runtime configuration (paths, inlet names)
├── config_mgr.py               # Loads config.toml; exposes typed config object
│
├── lsl_replay_publisher.py     # Core replay engine: reads DB → publishes LSL outlets
├── sql_record_fetcher.py       # SQL queries: fetches samples by stream + time range
├── replay_sample.py            # Data class: one sample (stream, timestamp, data[])
├── lsl_metadata.py             # Builds LSL StreamInfo from stored stream metadata
├── playback_feed_summary.py    # Summarizes streams available in a DB file
├── util.py                     # Shared helpers (time math, formatting)
│
├── src/total_recall/           # Installable Python package (imported by Backend)
│   └── __init__.py             # Public API surface for external consumers
│
├── sessions/                   # Default output directory for .db session files
│   └── *.db                    # SQLite session databases (one per recording)
│
├── gui/                        # GUI assets and sub-windows
├── tests/                      # Unit tests
└── pyproject.toml              # Poetry / uv project manifest
```

---

## Key Files Explained

### `guiapp.py`
The Tkinter desktop application. Provides controls for:
- Selecting streams to record
- Starting/stopping a recording session
- Browsing and replaying existing `.db` files
- Monitoring inlet health during capture

### `lsl_replay_publisher.py`
The heart of replay. Given a `.db` path and an optional time range:
1. Opens the SQLite database
2. Queries all samples via `sql_record_fetcher`
3. Creates one LSL `StreamOutlet` per stream (using metadata stored in the DB)
4. Publishes samples at their original inter-sample intervals using
   `pylsl.local_clock()` timing — preserving the exact temporal structure

### `sql_record_fetcher.py`
SQL abstraction layer. Fetches samples by:
- Stream name
- Time range (start / end LSL timestamps)
- Optional limit / offset

### `config_mgr.py`
Loads `config.toml` and provides typed attribute access. Consumed by both
the GUI and the external Backend via `from total_recall import config_mgr`.

---

## Ports & Interfaces

| Interface | Direction | Protocol |
|-----------|-----------|----------|
| LSL inlets (recording mode) | IN from sensors | Lab Streaming Layer |
| LSL outlets (replay mode) | OUT to Backend | Lab Streaming Layer |
| SQLite `.db` files | READ/WRITE | Local filesystem |
| Backend API | IN (HTTP) | REST — Backend calls `/sessions` to scan for DB files |

---

## How to Run

```bash
# GUI (recommended for operators)
cd total-recall
python main.py          # or: uv run python main.py

# Headless replay (used by Backend internally via subprocess or API)
# Not exposed directly — Backend's ReplayEngine manages the replay lifecycle
```

---

## Relationship to Other Components

- **Backend** uses Total Recall's `.db` files as the source of truth for replay.
  `ReplayEngine` (`Backend/replay_engine.py`) starts a Total Recall replay
  process and receives the LSL streams via `InletManager`.
- **Analysis** reads Total Recall `.db` files directly (via SQL) for
  post-session epoch computation. The Analysis API scans the `sessions/`
  directory to build its session list.
- **Frontend** never touches Total Recall directly — it receives data from
  Backend over WebSocket.
