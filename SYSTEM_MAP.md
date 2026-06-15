# LabReplay — Integrated System Map

> **LabReplay** is a physiological data capture, replay, and analysis
> platform for training research. It captures multi-modal sensor data
> during VirTra training scenarios and provides real-time and post-session
> human performance intelligence to operators and researchers.

---

## What the System Does

LabReplay records physiological and behavioral data (heart rate, gaze,
motion, VirTra events, speech) from a training scenario in real time and
lets operators replay that session later with full timeline control.
It computes human performance metrics both live (during the session) and
on-demand (after it), visualized through a web dashboard with four
purpose-built views.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PHYSICAL SENSORS                                │
└────────────────────────────┬────────────────────────────────────────────┘
                             │  Lab Streaming Layer (LSL)
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                      TOTAL RECALL                              │
│  Records all LSL streams → SQLite .db file  (capture mode)    │
│  Replays .db file → LSL outlets            (replay mode)       │
└──────────┬───────────────────────────────────┬─────────────────┘
           │  LSL outlets (replay)             │  .db files (direct SQL)
           ▼                                   ▼
┌──────────────────────┐           ┌──────────────────────────────┐
│       BACKEND        │           │          ANALYSIS            │
│                      │           │                              │
│  InletManager        │           │  FastAPI REST server         │
│  SessionService      │           │  Drill algorithms            │
│  ReplayService       │           │  Signal extractors           │
│  LiveService         │           │  Epoch builder               │
│  BatchRouter         │           │  Port 8081                   │
│  WebSocket :8500     │           └──────────────┬───────────────┘
└──────────┬───────────┘                          │ HTTP fetch
           │  WebSocket JSON                      │
           ▼                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                │
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐ │
│  │   LIVE pages     │  │        POST-SESSION pages            │ │
│  │                  │  │                                      │ │
│  │ Real-Time        │  │ Replay Sessions                      │ │
│  │ Monitoring       │  │ (transport controls + event log)     │ │
│  │                  │  │                                      │ │
│  │ Real-Time Human  │  │ Human Performance Workspace          │ │
│  │ Performance      │  │ (epoch charts, stats, comparison)    │ │
│  │ (BehDisc live)   │  │                                      │ │
│  └──────────────────┘  └──────────────────────────────────────┘ │
│                                                                 │
│  http://host:8080  (served by python3 -m http.server)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Summary

| Component | Language | Port | Role |
|-----------|----------|------|------|
| **Total Recall** | Python | — | Capture + LSL replay |
| **Backend** | Python | 8500 (WS) | Hub: LSL → WebSocket |
| **Analysis** | Python | 8081 (HTTP) | Post-session epoch API |
| **Frontend** | HTML/JS | 8080 (HTTP) | Dashboard UI |

---

## Data Flow: Two Operating Modes

### Mode 1 — Live Session

```
Sensors
  └─ LSL streams
       └─ Backend/InletManager
            ├─ LiveService → LiveSessionWriter → sessions/*.db
            └─ BatchRouter → WebSocket
                   └─ Frontend: Real-Time Monitoring
                                Real-Time Human Performance
                                  └─ intel/behdisc-engine.js
                                       (live BehDisc math, no server call)
```

**Who computes live math:** `Backend/services/live_service.py` tracks Intel
state; `Frontend/js/intel/behdisc-engine.js` runs the BehDisc scoring
algorithm in the browser using streamed engagement events.

### Mode 2 — Replay Session

```
sessions/*.db
  └─ Total Recall (LSL replay publisher)
       └─ LSL outlets
            └─ Backend/InletManager (identical to live path)
                 └─ ReplayService → WebSocket
                        └─ Frontend: Replay Sessions page
                                     (timeline, transport controls)

sessions/*.db  (also read directly by Analysis)
  └─ Analysis FastAPI
       └─ HTTP fetch from Frontend
            └─ Human Performance Workspace
                 ├─ Aggregate view   (grand avg epoch chart)
                 ├─ Per-Engagement   (individual trial traces)
                 └─ Comparison view  (Session A vs Session B)
```

**Who computes post-session math:** `Analysis/` FastAPI — drill algorithms
identify trials, signal extractors slice epochs, the epoch builder
computes Δ from baseline. Frontend renders the result with Plotly.js.

---

## Communication Contracts

### Backend ↔ Frontend (WebSocket)
- **Protocol:** JSON over WebSocket (`ws://host:8500`)
- **Contract document:** [`Backend/api/contract.md`](Backend/api/contract.md)
- **Message types:** mirrored in `Backend/api/message_types.py` and
  `Backend/api/message_types.js`

All messages have a `"type"` field. Key families:

| Family | Direction | Description |
|--------|-----------|-------------|
| `session.*` | both | Load/unload/list sessions, state broadcast |
| `playback.*` | F→B | Play, pause, stop, seek |
| `streams.*` | both | Catalog, subscribe/unsubscribe |
| `stream.sample(s)` | B→F | Sample data (single or batch) |
| `live.*` | both | Live mode activate/deactivate |
| `live.intel.*` | both | Intel capture state machine |
| `analysis.data` | B→F | Pre-computed analysis from DB |

### Frontend ↔ Analysis (HTTP REST)
- **Base URL:** `http://127.0.0.1:8081`
- **Wrapper:** `Frontend/js/analysis/api-client.js`

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | Liveness check |
| `GET /api/sessions?drill=X` | List sessions for a drill |
| `GET /api/trials?session=F&drill=X` | List trials + summary |
| `POST /api/epoch` | Compute epoch data (main analysis) |

### Backend ↔ Total Recall (LSL)
- **Protocol:** Lab Streaming Layer (pub/sub, auto-discovery)
- Total Recall publishes LSL outlets; Backend subscribes via `InletManager`
- The Backend also reads `.db` files directly via `ReplayEngine` to extract
  session metadata (duration, stream list) without starting a replay.

### Analysis ↔ Total Recall (SQL)
- Analysis reads `.db` files **directly** from the `sessions/` directory
- No network protocol — shared filesystem path configured in `Analysis/config.py`

---

## Session Lifecycle

```
1. Operator loads a session in Total Recall GUI
      │
      ▼
2. Total Recall publishes LSL outlets
      │
      ▼
3. Backend/InletManager detects streams → broadcasts catalog to Frontend
      │
      ▼
4. Operator clicks Play in Frontend Replay Sessions page
      │
      ▼
5. Frontend sends playback.play → Backend/ReplayService
      │
      ▼
6. Backend broadcasts session.state: playing → all clients
      │
      ▼
7. Samples flow: LSL → InletManager → BatchRouter → WebSocket → Frontend charts
      │
      ▼
8. Operator navigates to Human Performance Workspace
      │
      ▼
9. Frontend calls Analysis API (fetch):
      GET /api/sessions + /api/trials + POST /api/epoch
      │
      ▼
10. Analysis reads .db directly, computes epochs, returns JSON
      │
      ▼
11. Frontend renders epoch charts + stats table (Plotly.js)
```

---

## How to Start Everything

```bash
# 1. Total Recall (captures or replays sessions)
cd total-recall
uv run python main.py           # or open the Tkinter GUI: python guiapp.py

# 2. Backend (WebSocket server)
cd Backend
uv run python websocket_bridge.py

# 3. Analysis API (post-session analysis)
cd Analysis
uv run python main.py

# 4. Frontend (web dashboard)
cd Frontend
python3 -m http.server 8080
# Open: http://localhost:8080
```

All four can run simultaneously. The Frontend auto-detects the Backend
host from `window.location.hostname` — serving on a LAN IP works without
any config change.

---

## Directory Layout

```
LabReplay/
│
├── total-recall/               # Session recorder + LSL replayer
│   └── OVERVIEW.md
│
├── Backend/                    # WebSocket hub + session coordinator
│   └── OVERVIEW.md
│
├── Analysis/                   # Post-session REST analysis API
│   └── OVERVIEW.md
│
├── Frontend/                   # Browser dashboard
│   └── OVERVIEW.md
│
├── Data/                       # Reference data, lookup tables
├── Research Papers/            # Background literature
├── Testing/                    # Integration and manual test scripts
├── markdowns/                  # Design documents and specs
│
├── SYSTEM_MAP.md               # ← this file
└── README.md                   # Quick-start guide
```

---

## Design Principles

The codebase is organized around five guiding principles:

| Principle | How it manifests |
|-----------|-----------------|
| **DRY** | `shared/constants.js`, `Backend/config.py`, `api/message_types.*` are single sources of truth |
| **Separation of concerns** | Transport (websocket_bridge) ≠ Logic (session_service) ≠ Storage (total-recall) |
| **Fix broken windows** | No `.bak` files, no hardcoded paths, no commented-out dead code in production |
| **Orthogonality** | Replay and Live services share no mutable state; CSS pages are scoped files |
| **Easier to change** | Drill registry pattern, plugin registry, coordinator injection — swapping a drill or chart type is one file |
