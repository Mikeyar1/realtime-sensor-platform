# Backend — Component Overview

> **Role:** Real-time WebSocket server, LSL inlet manager, and session
> coordinator.
>
> The Backend is the central hub of the system. It speaks LSL inward
> (sensors / Total Recall) and WebSocket outward (Frontend), managing
> all session lifecycle decisions and replay playback.

---

## Purpose

The Backend answers one question for the Frontend: *"What is happening right
now?"* It abstracts away whether data is coming from a live sensor or a
replayed session file. The Frontend only ever sees WebSocket messages —
it never touches LSL, SQLite, or any file path directly.

---

## Conceptual Model

```
Sensors / Total Recall
  │  (Lab Streaming Layer)
  ▼
InletManager  ──── samples ──→  SessionService (coordinator)
                                   │              │
                           ReplayService    LiveService
                           (playback,       (live activation)
                            session DB)
                                   │
                           BatchRouter (high-Hz buffer)
                                   │
                        WebSocket clients (Frontend)
```

**Two modes are strictly separate:**

| Mode | Trigger | Data source |
|------|---------|-------------|
| **Replay** | User loads a session file | Total Recall LSL replay publisher |
| **Live** | Live activate (LSL streams present) | Physical sensors via LSL |

---

## Structure

```
Backend/
│
├── websocket_bridge.py         # Transport layer: WS connections, subscription routing
├── batch_router.py             # High-Hz sample batching (Neon at 200 Hz → 20 frames/s)
├── session_service.py          # Coordinator: routes messages to Replay or Live service
├── config.py                   # Central config loader (reads config.toml)
├── config.toml                 # Runtime configuration (host, port, paths)
│
├── services/
│   ├── replay_service.py       # All replay/session business logic
│   └── live_service.py         # Live mode activation and deactivation
│
├── api/
│   ├── message_types.py        # Python enum of all WS message type strings
│   ├── message_types.js        # Same enum, mirrored for Frontend consumption
│   └── contract.md             # Full WS API contract (request/response spec)
│
├── inlet_manager.py            # LSL inlet watcher: discovers + reads streams
├── replay_engine.py            # Manages the Total Recall replay subprocess lifecycle
└── stream_meta_parser.py       # Parses LSL StreamInfo XML into Python dicts
```

---

## Key Files Explained

### `websocket_bridge.py`
**Transport only.** Owns:
- WebSocket server (websockets library, async)
- Client connection/disconnection lifecycle
- Subscription management (`streams.subscribe` / `streams.unsubscribe`)
- `send()` and `broadcast()` helpers

Does **not** contain any business logic. All decisions delegate to `SessionService`.

### `session_service.py`
**Coordinator.** The single entry point for all incoming WS messages and
all incoming LSL samples. Decides whether a message should go to
`ReplayService` or `LiveService` based on the current session mode.

### `services/replay_service.py`
All replay-path logic:
- Loading / unloading sessions
- Controlling playback (play, pause, stop, seek)
- Reading session metadata from `.db` files
- Reading analysis data (pre-computed results) from `.db` files
- Broadcasting `session.state` to all clients

### `services/live_service.py`
All live-path logic:
- Activating / deactivating live mode

### `batch_router.py`
High-frequency stream optimization. Streams at ≥ 100 Hz (e.g. Neon
Gaze at 200 Hz) are buffered for 50 ms then sent as a single
`stream.samples` batch message, reducing WebSocket frame rate from 200
frames/s to 20 frames/s. Lower-rate streams go through immediately as
`stream.sample` singles.

### `inlet_manager.py`
Continuously resolves LSL streams using `pylsl.resolve_streams()` in a
background thread. Calls `on_sample` and `on_catalog_changed` callbacks
when data arrives or the stream list changes.

### `replay_engine.py`
Manages the Total Recall replay lifecycle as a subprocess. When a session
is loaded, it starts a Total Recall replay process that publishes LSL
outlets; when unloaded or stopped, it terminates that process cleanly.

---

## Ports & Interfaces

| Interface | Direction | Protocol | Port |
|-----------|-----------|----------|------|
| WebSocket server | OUT to Frontend | WebSocket (JSON) | 8500 |
| LSL inlets | IN from sensors / Total Recall | Lab Streaming Layer | (auto) |
| SQLite `.db` reads | IN from filesystem | SQL | local |
| Total Recall subprocess | OUT → IN | subprocess + LSL | local |

---

## How to Run

```bash
cd Backend
uv run python websocket_bridge.py
```

---

## Message API

All Frontend ↔ Backend communication uses JSON WebSocket messages.
The full contract is documented in [`api/contract.md`](api/contract.md).
Message type strings are mirrored in `api/message_types.py` (Python)
and `api/message_types.js` (JavaScript).

Key message families:

| Family | Direction | Purpose |
|--------|-----------|---------|
| `session.*` | both | Load, unload, list sessions; session state |
| `playback.*` | F→B | Play, pause, stop, seek |
| `streams.*` | both | Catalog, subscribe/unsubscribe |
| `stream.sample(s)` | B→F | Sample data (single or batch) |
| `live.*` | both | Live mode activate/deactivate |
| `analysis.*` | B→F | Pre-computed analysis results |
| `system.*` | both | Ping, pong, state query |
