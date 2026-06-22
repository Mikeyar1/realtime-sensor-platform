# LabReplay — API Specification

> The contract between every layer of the system. If a function call or message isn't defined here, it doesn't exist.

---

## Architecture Overview: The 4 Layers

```
  ┌──────────────┐     WebSocket (JSON)     ┌──────────────┐
  │   FRONTEND   │ ◄─────────────────────► │   BACKEND    │
  │  (Browser)   │                          │  (WsBridge)  │
  └──────────────┘                          └──────┬───────┘
                                                   │ Python method calls
                                                   │
                                            ┌──────┴───────┐
                                            │ REPLAY ENGINE│
                                            │  (Wrapper)   │
                                            └──────┬───────┘
                                                   │ Python imports
                                                   │
                                            ┌──────┴───────┐
                                            │ TOTAL RECALL │
                                            │  (Library)   │
                                            └──────────────┘
```

### Key Principle: Each Layer Talks Only to Its Neighbor

- Frontend ↔ Backend: **WebSocket JSON messages** (defined in Section 1)
- Backend → ReplayEngine: **Python method calls** (defined in Section 2)
- ReplayEngine → Total Recall: **Python imports + threading** (defined in Section 3)
- Data flows UP: Total Recall → ReplayEngine → Backend → Frontend (via LSL → InletManager → WsBridge → WebSocket)
- Commands flow DOWN: Frontend → Backend → ReplayEngine → Total Recall

The frontend never knows about ReplayEngine or Total Recall. The backend never imports `pylsl` directly (InletManager handles that). ReplayEngine is the only module that touches Total Recall code.

---

## Section 1: Frontend ↔ Backend WebSocket Protocol

### 1.1 Connection Lifecycle

```
Browser connects to ws://localhost:8500
  ← Server sends: session_state     (current state — so reconnecting clients sync immediately)
  ← Server sends: stream_catalog    (all currently known streams)
  ← Server sends: db_list           (available session files)

Browser refreshes / reconnects:
  Same sequence. The server always sends full state on connect.
  The frontend never needs to "remember" anything across page loads.
```

### 1.2 Messages: Client → Server

Every message is a JSON object with a `type` field.

#### `scan_db` — Discover available session files

```json
{ "type": "scan_db" }
```
**Response:** Server sends back a `db_list` message.
**When to send:** On first connect, or when user clicks "Refresh".

---

#### `load_db` — Load and start replaying a session

```json
{ "type": "load_db", "path": "C:/path/to/session.db" }
```
**Response:** Server sends `session_state` with `state: "loading"`, then `state: "playing"` when ready, or `error` on failure.
**Side effects:** Stops any currently running session first. Starts Total Recall publishers. InletManager will discover the new LSL outlets and send `stream_catalog` updates.

---

#### `transport` — Playback control

```json
{ "type": "transport", "action": "play" }
{ "type": "transport", "action": "pause" }
{ "type": "transport", "action": "stop" }
{ "type": "transport", "action": "seek", "value": 120.5 }
```

| Action | What happens | Current support |
|--------|-------------|-----------------|
| `play` | Resume playback from current position | ✅ Supported |
| `pause` | Pause playback (frontend stops accepting samples; backend pauses publishers if supported) | ⚠️ Frontend-only for now |
| `stop` | Stop playback, teardown publishers, reset to idle | ✅ Supported |
| `seek` | Jump to position (seconds from session start) | ❌ Not yet — requires Total Recall modification |

**Response:** Server broadcasts updated `session_state` to all clients after processing.

---

#### `subscribe` — Start receiving sample data for specific streams

```json
{ "type": "subscribe", "streams": ["Polar H10 0696D53C_ECG", "Neon Companion_Neon Gaze"] }
```
**Response:** None. Samples for these streams will start arriving as `sample` messages.
**Note:** On first connect, the client is subscribed to nothing. It must explicitly subscribe after receiving the catalog.

---

#### `unsubscribe` — Stop receiving sample data

```json
{ "type": "unsubscribe", "streams": ["Polar H10 0696D53C_ECG"] }
```

---

#### `get_state` — Request current session state

```json
{ "type": "get_state" }
```
**Response:** Server sends `session_state`. Useful after reconnection if the automatic on-connect state was missed.

---

#### `get_catalog` — Request current stream catalog

```json
{ "type": "get_catalog" }
```
**Response:** Server sends `stream_catalog`.

---

### 1.3 Messages: Server → Client

#### `session_state` — The single source of truth for the entire UI

```json
{
  "type": "session_state",
  "mode": "replay",
  "state": "playing",
  "elapsed_seconds": 47.3,
  "duration_seconds": 2847.0,
  "db_path": "C:/.../dnlc_session04_03Dec2025.db",
  "session_name": "dnlc_session04_03Dec2025",
  "stream_count": 26
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `mode` | string | `"live"` \| `"replay"` | Operating mode |
| `state` | string | `"idle"` \| `"loading"` \| `"playing"` \| `"paused"` \| `"stopped"` \| `"finished"` | Playback state within the mode |
| `elapsed_seconds` | float | ≥ 0 | Seconds since playback started (0 when idle/loading/stopped) |
| `duration_seconds` | float | ≥ 0 | Total session length in seconds (0 in live mode) |
| `db_path` | string | file path or `""` | Loaded database path (empty in live mode or idle) |
| `session_name` | string | filename or `""` | Human-readable session name derived from filename |
| `stream_count` | int | ≥ 0 | Number of streams in the loaded session |

**When sent:**
- On client connect (immediate)
- On every state transition (idle → loading → playing → paused → etc.)
- Every 1 second during `playing` state (to update `elapsed_seconds`)

**Frontend rule:** The frontend NEVER computes elapsed time. It displays whatever `elapsed_seconds` the backend sends. This makes the system refresh-safe.

---

#### `stream_catalog` — All currently discovered LSL streams

```json
{
  "type": "stream_catalog",
  "streams": [
    {
      "name": "Polar H10 0696D53C_ECG",
      "stream_type": "ECG",
      "channel_count": 1,
      "sample_rate": 130.0,
      "channel_format": 6,
      "source_id": "H10_0696D53C_ECG",
      "channels": [
        { "label": "ecg", "unit": "mV", "type": "ECG" }
      ]
    }
  ]
}
```

**When sent:**
- On client connect
- Whenever InletManager discovers a new stream or loses one

---

#### `sample` — A single data sample from a stream

```json
{
  "type": "sample",
  "stream": "Polar H10 0696D53C_ECG",
  "timestamp": 1698012345.123,
  "data": [0.342]
}
```

**When sent:** Continuously during playback for every subscribed stream.

**`data`:** Array of floats. Length equals the stream's `channel_count`. For ECG (1 channel) → `[0.342]`. For ACC (3 channels) → `[0.12, -0.98, 0.03]`. For Gaze (16 channels) → `[512.3, 384.7, ...]`.

---

#### `db_list` — Available session files

```json
{
  "type": "db_list",
  "files": [
    { "name": "dnlc_session04_03Dec2025.db", "path": "C:/.../dnlc_session04_03Dec2025.db", "size_mb": 16.7, "valid": true },
    { "name": "dnlc_session06_18Feb2026.db", "path": "C:/.../dnlc_session06_18Feb2026.db", "size_mb": 8.2, "valid": false }
  ]
}
```

**New field `valid`:** `true` if the file has an `lsl_metadata` table (i.e., it's a Total Recall database). `false` otherwise. The frontend should grey out invalid entries in the dropdown.

---

#### `error` — Error response

```json
{
  "type": "error",
  "message": "No LSL metadata found in: dnlc_session06_18Feb2026.db",
  "context": "load_db"
}
```

**New field `context`:** Which operation caused the error. Helps the frontend show the error in the right place (e.g., a toast near the dropdown vs a page-level error).

---

## Section 2: Backend → ReplayEngine Python API

The `WsBridge` calls these methods on `ReplayEngine`. All methods are synchronous (blocking). The bridge wraps them in `run_in_executor()` when calling from async context.

### `scan() → list[dict]`

```python
files = engine.scan()
# Returns: [{ "name": str, "path": str, "size_mb": float, "valid": bool }]
```

**Current:** Returns name, path, size_mb.
**Needed:** Add `valid` field. Check if the file has an `lsl_metadata` table before adding to results. This prevents the frontend from offering unloadable files.

---

### `load(db_path: str) → dict`

```python
session = engine.load("C:/path/to/session.db")
# Returns: {
#   "db_path": str,
#   "session_name": str,
#   "stream_count": int,
#   "streams": [{ "name": str, "type": str, "channels": int, "sample_rate": float }],
#   "start_iso": str,        # ISO 8601
#   "end_iso": str,          # ISO 8601
#   "duration_seconds": float,
# }
```

**Current:** Returns streams as flat list of names (`["ECG", "HR"]`).
**Needed:** Return list of dicts with per-stream metadata (name, type, channels, sample_rate). This lets the frontend know what to expect BEFORE InletManager discovers the LSL outlets — enabling the landing page to pre-build panels with "Loading..." placeholders.

**Side effects:** Calls `stop()` first. Creates and starts all `LslReplayPublisher` threads.

**Raises:** `FileNotFoundError` if path doesn't exist. `RuntimeError` if no metadata found.

---

### `stop()`

```python
engine.stop()
```

Stops all publisher threads. Joins with timeout. Clears the publisher list. Resets loaded path.

**Current:** ✅ Works correctly.

---

### `is_running() → bool`

```python
if engine.is_running(): ...
```

Returns `True` if any publisher thread is still alive.

**Current:** ✅ Works correctly.
**Needed for:** The 1-second state broadcast timer can check this to detect when all publishers have finished and transition from `playing` → `finished`.

---

### `get_session_info() → dict`

```python
info = engine.get_session_info()
```

Returns a copy of the session info dict from the last `load()` call.

**Current:** ✅ Works correctly.

---

### `get_elapsed() → float` (NEW — needed for sync)

```python
elapsed = engine.get_elapsed()
# Returns: seconds since load() was called, or 0 if not running
```

**Why needed:** The backend needs to report elapsed time to the frontend. Since Total Recall replays at wall-clock speed (1x), elapsed = `now - load_start_time`. This is the authoritative clock.

**Implementation:** Store `self._load_start_time = time.monotonic()` in `load()`. Return `time.monotonic() - self._load_start_time` in `get_elapsed()`. Return `0.0` if not running.

---

### `get_state() → str` (NEW — needed for sync)

```python
state = engine.get_state()
# Returns: "idle" | "loading" | "playing" | "finished"
```

**Why needed:** The bridge needs to know the engine's state to build `session_state` messages. Derives from:
- `idle`: no session loaded (`_loaded_db == ""`)
- `loading`: between `load()` start and publishers starting (could be tracked with a flag)
- `playing`: `is_running() == True`
- `finished`: `is_running() == False` and `_loaded_db != ""`

---

## Section 3: ReplayEngine → Total Recall Interface

ReplayEngine interacts with Total Recall through three classes:

### `LslReplayPublisher` (threading.Thread)

**What we use today:**

| Method | Purpose |
|--------|---------|
| `__init__(lsl_metadata, sqlite_file, start_datetime, end_datetime, time_delta)` | Configure the publisher |
| `fetch_sql_records()` | Load samples from SQLite (blocking — runs in a separate thread internally) |
| `start()` | Begin publishing LSL samples (inherited from Thread) |
| `stop()` | Set the stop event, clear the outlet |
| `is_alive()` | Check if the thread is still running (inherited from Thread) |
| `stopped()` | Check if stop was requested |
| `reset()` | Reset sample index and published flags for replay restart |
| `create_restart()` | Create a fresh publisher with same config and data (for re-play without re-fetching SQL) |

**What we DON'T have but will need:**

| Missing Capability | Why Needed | Approach |
|-------------------|-----------|----------|
| **Pause/Resume** | User clicks Pause in the UI | Add a `threading.Event` called `_pause_event`. In `publish_samples()`, check `_pause_event.wait()` before each sample. Set/clear from `pause()`/`resume()` methods. |
| **Seek** | User scrubs the timeline | Call `reset()`, then set `current_replay_sample_index` to the sample nearest the target timestamp. Adjust `time_delta` so that the next `publish_samples()` call starts from the right position. |
| **Progress reporting** | Backend needs to know how far along each publisher is | Expose `current_replay_sample_index / len(replay_samples)` as a property or method. Already partially available. |

### `LslMetadata`

Data class holding stream metadata from the SQLite `lsl_metadata` table. Fields: `name`, `type`, `channels`, `sample_rate_hz`, `channel_format`, `source_id`, `desc` (XML string), `lsl_metadata_id`.

**No changes needed.** Read-only data class.

### `SqlRecordFetcher` (threading.Thread)

Reads sample rows from SQLite, converts them into `ReplaySample` objects.

**No changes needed.** Used internally by `LslReplayPublisher.fetch_sql_records()`.

---

## Section 4: Data Flow Diagrams

### 4.1 Session Load Flow

```
User selects session from dropdown
  │
  ▼
Frontend sends: { type: "load_db", path: "..." }
  │
  ▼
Backend (WsBridge._handle_load_db):
  1. Broadcasts session_state { state: "loading" } to all clients
  2. Calls engine.load(path) in executor thread
     └─ ReplayEngine.load():
        a. Calls self.stop() (kills previous publishers)
        b. Reads lsl_metadata table from SQLite
        c. Creates LslReplayPublisher per stream
        d. Each publisher: fetch_sql_records() (reads samples from DB)
        e. Starts all publisher threads
        f. Records _load_start_time
        g. Returns session info dict
  3. Stores session info
  4. Broadcasts session_state { state: "playing", duration_seconds: X, ... }
  │
  ▼
Meanwhile, Total Recall publishers are now emitting LSL samples
  │
  ▼
InletManager discovery thread finds new LSL outlets
  │
  ▼
InletManager creates InletThread per stream, starts receiving samples
  │
  ▼
InletManager fires _on_catalog_changed callback
  │
  ▼
Backend broadcasts stream_catalog to all clients
  │
  ▼
Frontend receives catalog, builds chart panels, subscribes to streams
  │
  ▼
InletThread receives LSL sample, fires _on_sample callback
  │
  ▼
Backend sends { type: "sample", stream: "ECG", data: [...] } to subscribed clients
  │
  ▼
StreamRouter routes sample to registered chart plugin instances
```

### 4.2 Elapsed Time Flow

```
Backend: 1-second asyncio timer fires
  │
  ▼
Checks engine.get_state():
  - If "playing": elapsed = engine.get_elapsed()
  - If "paused":  elapsed = stored_paused_elapsed
  - If "finished": elapsed = duration_seconds
  │
  ▼
Broadcasts: { type: "session_state", state: "playing", elapsed_seconds: 47.3, ... }
  │
  ▼
Frontend receives session_state:
  - Updates elapsed display (00:00:47)
  - Updates timeline playhead position (47.3 / 2847.0 = 1.66%)
  - Updates play/pause button icon based on state
```

### 4.3 Play/Pause Flow

```
User clicks Play/Pause button
  │
  ▼
Frontend sends: { type: "transport", action: "pause" }
  │
  ▼
Backend:
  1. Calls engine.pause() (sets _pause_event on all publishers)
  2. Stores paused_elapsed = engine.get_elapsed()
  3. Broadcasts session_state { state: "paused", elapsed_seconds: X }
  │
  ▼
Frontend receives session_state { state: "paused" }:
  - Button icon → ▶ (play)
  - Timeline stops moving
  - Charts stop updating (optional — could keep showing last values)

User clicks Play/Pause again:
  │
  ▼
Frontend sends: { type: "transport", action: "play" }
  │
  ▼
Backend:
  1. Calls engine.resume() (clears _pause_event, adjusts time_delta)
  2. Broadcasts session_state { state: "playing" }
```

---

## Section 5: State Machine — Complete

```
                    scan_db
          ┌──────────────────────┐
          │                      │
          ▼                      │
       ┌──────┐   load_db    ┌──────────┐
       │ IDLE │ ───────────► │ LOADING  │
       └──────┘              └────┬─────┘
          ▲                       │ success
          │ stop                  ▼
       ┌──────────┐          ┌──────────┐
       │ STOPPED  │ ◄─────── │ PLAYING  │
       └──────────┘   stop   └────┬─────┘
          ▲                       │ pause     ▲
          │                       ▼           │ resume
          │                  ┌──────────┐     │
          │                  │ PAUSED   │ ────┘
          │                  └──────────┘
          │                       │ stop
          │◄──────────────────────┘
          │
          │                  ┌──────────┐
          │                  │ FINISHED │ ── all publishers done
          │◄──── stop ───────┤          │
          │                  └──────────┘
          │                       │ load_db
          │                       ▼
          │                  ┌──────────┐
          └──── stop ────────┤ LOADING  │ (can load new session from any state)
                             └──────────┘
```

**State transitions:**

| From | Trigger | To | Notes |
|------|---------|----|-------|
| `idle` | `load_db` | `loading` | |
| `loading` | success | `playing` | Broadcasts session_state + starts 1s timer |
| `loading` | failure | `idle` | Sends error message |
| `playing` | `pause` | `paused` | Pauses publishers, stops 1s timer |
| `playing` | `stop` | `stopped` | Stops publishers, resets everything |
| `playing` | all publishers done | `finished` | Detected by 1s timer checking `is_running()` |
| `paused` | `play` | `playing` | Resumes publishers, restarts 1s timer |
| `paused` | `stop` | `stopped` | |
| `finished` | `stop` | `idle` | Cleans up |
| `finished` | `load_db` | `loading` | Load new session |
| any | `load_db` | `loading` | Always allowed — stops current first |

---

## Section 6: What Exists vs What's Missing

### Layer 1: Frontend ↔ Backend (WebSocket)

| Message | Direction | Exists? | Status |
|---------|-----------|---------|--------|
| `scan_db` | C→S | ✅ | Works |
| `load_db` | C→S | ✅ | Works |
| `transport` (play/pause/stop/seek) | C→S | ⚠️ | Only `stop` does anything; play/pause/seek are no-ops |
| `subscribe` / `unsubscribe` | C→S | ✅ | Works |
| `get_state` | C→S | ❌ | Not implemented |
| `get_catalog` | C→S | ✅ | Works |
| `session_state` | S→C | ❌ | **Not implemented** — currently sends `mode` instead, with no state/elapsed fields |
| `stream_catalog` | S→C | ✅ | Works |
| `sample` | S→C | ✅ | Works |
| `db_list` | S→C | ✅ | Works (missing `valid` field) |
| `error` | S→C | ✅ | Works (missing `context` field) |

### Layer 2: Backend → ReplayEngine

| Method | Exists? | Status |
|--------|---------|--------|
| `scan()` | ✅ | Missing `valid` field |
| `load(path)` | ✅ | Returns flat stream name list instead of metadata dicts |
| `stop()` | ✅ | Works |
| `is_running()` | ✅ | Works |
| `get_session_info()` | ✅ | Works |
| `get_elapsed()` | ❌ | **Not implemented** |
| `get_state()` | ❌ | **Not implemented** |
| `pause()` | ❌ | **Not implemented** |
| `resume()` | ❌ | **Not implemented** |

### Layer 3: ReplayEngine → Total Recall

| Capability | Exists? | Status |
|-----------|---------|--------|
| Create & start publishers | ✅ | Works |
| Stop publishers | ✅ | Works |
| Pause publishers | ❌ | Need to add `_pause_event` to `LslReplayPublisher` |
| Seek / jump to position | ❌ | Need index-based seek + `time_delta` adjustment |
| Reset / restart | ✅ | `reset()` and `create_restart()` exist but aren't wired |

---

## Section 7: Implementation Priority

### Step 1: Add `get_elapsed()` and `get_state()` to ReplayEngine

These two methods enable the backend to report accurate state to the frontend. Zero risk — purely additive.

### Step 2: Replace `mode` message with `session_state` message

The backend currently sends `{ type: "mode", mode: "replay", ... }`. Replace this with `{ type: "session_state", state: "playing", elapsed_seconds: X, ... }`. Update the frontend's `stream-router.js` and `mode-manager.js` to consume the new message format.

### Step 3: Add 1-second state broadcast timer

During `playing` state, broadcast `session_state` every 1 second. This drives the elapsed timer and timeline playhead on the frontend. Frontend becomes fully display-only.

### Step 4: Add `get_state` request handler

When frontend sends `{ type: "get_state" }`, respond with current `session_state`. This makes reconnection work correctly.

### Step 5: Wire `pause` / `play` transport commands

Add `_pause_event` to `LslReplayPublisher`. Wire through ReplayEngine.pause()/resume() and WsBridge transport handler. This gives real pause/resume.

### Step 6: Add `valid` field to `scan()` results

Check for `lsl_metadata` table existence during scan. Frontend greys out invalid files.

### Step 7: Wire `seek` transport command (Future)

Requires `LslReplayPublisher` to support index-based seeking with time_delta adjustment. More complex — save for later.
