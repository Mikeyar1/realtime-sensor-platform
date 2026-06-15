# LabReplay — API Contract
# Version 1.0 | Planning Document

## Overview

Two separate namespaces. Two distinct operating modes.

```
REPLAY mode: Load a recorded .db session → play, pause, stop, scrub
LIVE mode:   Receive whatever LSL streams are active on the network → display only
```

The WebSocket API uses JSON messages with a `domain.action` type field.

---

## Part 1: Known Stream Catalogue

### What we actually collect (from real CSV data)

| Stream Name | LSL Type | Channels | Rate | Real Column Labels | Unit |
|-------------|----------|----------|------|--------------------|------|
| Polar H10 *_ECG | ECG | 1 | 130 Hz | `sample value` | µV (microvolts) |
| Polar H10 *_HR | HR | 1 | 1 Hz | `HR` | BPM |
| Polar H10 *_ACC | ACC | 3 | 200 Hz | `X acceleration`, `Y acceleration`, `Z acceleration` | mG |
| Polar Sense *_HR | HR | 1 | 1 Hz | `HR` | BPM |
| Polar Sense *_ACC | ACC | 3 | 52 Hz | `X acceleration`, `Y acceleration`, `Z acceleration` | mG |
| Polar Sense *_GYRO | GYRO | 3 | 52 Hz | `X-axis`, `Y-axis`, `Z-axis` | degree/s |
| Polar Sense *_PPG | PPG | 4 | 55 Hz | `PPG0`, `PPG1`, `PPG2`, `Ambient` | counts |
| Polar Sense *_PPI | PPI | 4 | irregular | `blocker`, `ppi_ms`, `hr_from_ppi`, `error_estimate` | ms/BPM |
| Polar Sense *_MAG_3D | MAG_3D | 3 | 100 Hz | `X-axis`, `Y-axis`, `Z-axis` | MAGNETOMETER_3d |
| Polar Sense *_MAG_COMPASS | MAG_COMPASS | 4 | 100 Hz | `X-axis`, `Y-axis`, `Z-axis`, `Calibration status` | milligauss |
| Neon Companion_Neon Gaze | Gaze | 16 | 200 Hz | `x`, `y`, `worn`, `pupil_diameter_left`, `eyeball_center_left_x/y/z`, `optical_axis_left_x/y/z`, `pupil_diameter_right`, `eyeball_center_right_x/y/z`, `optical_axis_right_x/y/z` | px / mm / unit |
| Neon Companion_Neon Events | Event | 1 | irregular | `Event` | text |
| Neon_middleware_eye_events_* | eye_events | 3 | irregular | `Event Name`, `Event Info`, `UTC datetime microseconds` | text |
| Neon_middleware_imu_* | IMU | 11 | 30 Hz | `accel_x/y/z` (G), `gyro_x/y/z` (deg/s), `quaternion_x/y/z/w` | G / deg/s / unit |
| Neon_middleware_scene_video_frame_* | Indicator | 2 | 1 Hz | `video_frames_written`, `time_since_start_seconds` | integer / s |
| Neon_middleware_speech_transcription_* | speech_transcription | 3 | irregular | `Transcribed Text`, `Utterance Start Timestamp`, `Utterance End Timestamp` | text / ms |
| WebcamFatigue | WebcamFatigue | 8 | 200 Hz | `perclose_score`, `ear_avg`, `ear_left`, `ear_right`, `gaze_score`, `head_yaw_degrees`, `head_pitch_degrees`, `head_roll_degrees` | % / ratio / degrees |
| Pose_Middleware | Pose | 133 | 30 Hz | 133 joint channels (skeleton) | mm |
| pose_video_frame | Indicator | 2 | 10 Hz | `video_frames_written`, `time_since_start_seconds` | integer / s |
| V_300_VirTraEvents | VirTra | 4 | irregular | `Event Name`, `Event Description`, `Event Code`, `Event Params JSON` | text |

### Streams excluded from the dashboard UI

| Stream | Reason |
|--------|--------|
| MAG_3D, MAG_COMPASS | Raw magnetometer — no research-relevant visualization |
| Indicator (video frames) | Internal sync signal |
| Pose_Middleware (133ch) | Requires 3D skeleton viewer — not a chart |

---

## Part 2: Message Format

Every message is a JSON object:

```json
{ "type": "domain.action", ...payload }
```

Requests from client → server have an optional `request_id` for correlation:

```json
{ "type": "session.load", "request_id": "abc123", "path": "..." }
```

Responses from server → client echo `request_id` when present:

```json
{ "type": "session.loaded", "request_id": "abc123", ...data }
```

---

## Part 3: REPLAY Mode API

### 3.1 Session Discovery

#### `session.list` (Client → Server)
List all available session files.

```json
{ "type": "session.list" }
```

**Response: `session.list.result`**
```json
{
  "type": "session.list.result",
  "sessions": [
    {
      "id": "dnlc_session04_03Dec2025",
      "name": "dnlc_session04_03Dec2025.db",
      "path": "C:/.../dnlc_session04_03Dec2025.db",
      "size_mb": 16.7,
      "valid": true,
      "stream_count": 26,
      "duration_seconds": 2847.0,
      "recorded_at": "2025-12-03T14:54:00Z"
    },
    {
      "id": "dnlc_session06_18Feb2026",
      "name": "dnlc_session06_18Feb2026.db",
      "path": "C:/.../dnlc_session06_18Feb2026.db",
      "size_mb": 8.2,
      "valid": false,
      "invalid_reason": "Missing lsl_metadata table"
    }
  ]
}
```

`valid: false` sessions are shown greyed out in the UI — not selectable.

---

### 3.2 Session Lifecycle

#### `session.load` (Client → Server)
Load a session and begin replay.

```json
{
  "type": "session.load",
  "path": "C:/.../dnlc_session04_03Dec2025.db"
}
```

**Immediate response: `session.state`** with `state: "loading"`

**On success: `session.state`** with `state: "playing"` + full stream catalog

**On failure: `api.error`** with `context: "session.load"`

---

#### `session.unload` (Client → Server)
Stop replay and return to idle.

```json
{ "type": "session.unload" }
```

**Response: `session.state`** with `state: "idle"`

---

### 3.3 Playback Control

#### `playback.play` (Client → Server)

```json
{ "type": "playback.play" }
```

Start or resume playback from current position.

**Response: `session.state`** with `state: "playing"`

**Errors:** `state: "idle"` → no session loaded

---

#### `playback.pause` (Client → Server)

```json
{ "type": "playback.pause" }
```

Pause playback. Elapsed counter freezes. Charts stop updating.

**Response: `session.state`** with `state: "paused"`

**Implementation note:** Currently frontend-only. Total Recall publishers continue running in the background. When native pause is implemented in Total Recall, the publishers will be suspended via `_pause_event`.

---

#### `playback.stop` (Client → Server)

```json
{ "type": "playback.stop" }
```

Stop playback and reset position to 0. Session remains loaded.

**Response: `session.state`** with `state: "stopped"`, `elapsed_seconds: 0`

---

#### `playback.seek` (Client → Server)

```json
{
  "type": "playback.seek",
  "position_seconds": 120.5
}
```

Jump to a specific position in the session (0 to duration).

**Response: `session.state`** with updated `elapsed_seconds`

**Implementation note:** PLANNED. Requires Total Recall publisher to support index-based seek + time_delta recalculation. Not yet implemented. Server returns `api.error` with `code: "NOT_IMPLEMENTED"` until supported.

---

### 3.4 State Broadcasts

#### `session.state` (Server → All Clients)

The single source of truth for the entire UI. Sent:
- On every client connect (immediately)
- On every state transition
- Every 1 second during `playing` state

```json
{
  "type": "session.state",
  "mode": "replay",
  "state": "playing",
  "elapsed_seconds": 47.3,
  "duration_seconds": 2847.0,
  "session_id": "dnlc_session04_03Dec2025",
  "session_name": "dnlc_session04 — 03 Dec 2025",
  "stream_count": 26,
  "recorded_at": "2025-12-03T14:54:00Z"
}
```

| `state` | Meaning | `elapsed_seconds` |
|---------|---------|-------------------|
| `idle` | No session loaded | 0 |
| `loading` | Loading publishers (15-30s) | 0 |
| `playing` | Actively streaming | current position |
| `paused` | Paused at a position | frozen position |
| `stopped` | Stopped, session still loaded | 0 |
| `finished` | All samples published | = duration |

**Frontend rule:** Never compute elapsed time locally. Always display `elapsed_seconds` from this message.

---

## Part 4: LIVE Mode API

Live mode is read-only. No sessions, no scrubbing, no loading. The server listens to whatever LSL streams appear on the network.

### 4.1 Mode Switch

#### `live.activate` (Client → Server)

```json
{ "type": "live.activate" }
```

Switch the backend to Live mode. Stops any active replay session.

**Response: `session.state`** with `mode: "live"`, `state: "listening"`

#### `live.deactivate` (Client → Server)

```json
{ "type": "live.deactivate" }
```

Return to idle/replay mode.

**Response: `session.state`** with `mode: "replay"`, `state: "idle"`

---

### 4.2 Live State

#### `session.state` in live mode

```json
{
  "type": "session.state",
  "mode": "live",
  "state": "listening",
  "elapsed_seconds": 142.7,
  "duration_seconds": 0,
  "session_id": null,
  "session_name": "Live Session",
  "stream_count": 12
}
```

`elapsed_seconds` counts up from when `live.activate` was sent.
`duration_seconds` is always 0 (unknown/infinite).

**Constraints in live mode:**
- `playback.play`, `playback.pause`, `playback.stop`, `playback.seek` → `api.error`: "Not available in live mode"
- `session.load`, `session.list`, `session.unload` → `api.error`: "Not available in live mode"

---

## Part 5: Stream Data API (Both Modes)

### 5.1 Stream Catalog

#### `streams.get_catalog` (Client → Server)

```json
{ "type": "streams.get_catalog" }
```

**Response: `streams.catalog`**

```json
{
  "type": "streams.catalog",
  "streams": [
    {
      "id": "Polar_H10_0696D53C_ECG",
      "name": "Polar H10 0696D53C_ECG",
      "lsl_type": "ECG",
      "channel_count": 1,
      "sample_rate_hz": 130.0,
      "channels": [
        { "index": 0, "label": "sample value", "unit": "µV", "type": "ECG" }
      ],
      "device_id": "0696D53C",
      "device_family": "Polar H10",
      "status": "active"
    },
    {
      "id": "NeonCom007b_Neon_Gaze",
      "name": "NeonCom007b_Neon Gaze",
      "lsl_type": "Gaze",
      "channel_count": 16,
      "sample_rate_hz": 200.0,
      "channels": [
        { "index": 0,  "label": "x",                      "unit": "px",   "type": "Gaze" },
        { "index": 1,  "label": "y",                      "unit": "px",   "type": "Gaze" },
        { "index": 2,  "label": "worn",                   "unit": "bool", "type": "Status" },
        { "index": 3,  "label": "pupil_diameter_left",    "unit": "mm",   "type": "Pupil" },
        { "index": 4,  "label": "eyeball_center_left_x",  "unit": "mm",   "type": "Position" },
        { "index": 5,  "label": "eyeball_center_left_y",  "unit": "mm",   "type": "Position" },
        { "index": 6,  "label": "eyeball_center_left_z",  "unit": "mm",   "type": "Position" },
        { "index": 7,  "label": "optical_axis_left_x",    "unit": "unit", "type": "Direction" },
        { "index": 8,  "label": "optical_axis_left_y",    "unit": "unit", "type": "Direction" },
        { "index": 9,  "label": "optical_axis_left_z",    "unit": "unit", "type": "Direction" },
        { "index": 10, "label": "pupil_diameter_right",   "unit": "mm",   "type": "Pupil" },
        { "index": 11, "label": "eyeball_center_right_x", "unit": "mm",   "type": "Position" },
        { "index": 12, "label": "eyeball_center_right_y", "unit": "mm",   "type": "Position" },
        { "index": 13, "label": "eyeball_center_right_z", "unit": "mm",   "type": "Position" },
        { "index": 14, "label": "optical_axis_right_x",   "unit": "unit", "type": "Direction" },
        { "index": 15, "label": "optical_axis_right_y",   "unit": "unit", "type": "Direction" }
      ],
      "device_id": "007b",
      "device_family": "Neon Companion",
      "status": "active"
    }
  ]
}
```

`status` values: `"active"` (streaming), `"inactive"` (known but no samples recently), `"lost"` (was active, now gone).

#### `streams.catalog` (Server → All Clients)

Broadcast automatically whenever a stream appears or disappears:

```json
{ "type": "streams.catalog", "streams": [...] }
```

---

### 5.2 Subscriptions

#### `streams.subscribe` (Client → Server)

```json
{
  "type": "streams.subscribe",
  "stream_ids": ["Polar_H10_0696D53C_ECG", "NeonCom007b_Neon_Gaze"]
}
```

Subscribe to receive `stream.sample` messages for these streams.

**Response: `streams.subscribed`**

```json
{
  "type": "streams.subscribed",
  "stream_ids": ["Polar_H10_0696D53C_ECG", "NeonCom007b_Neon_Gaze"],
  "active": ["Polar_H10_0696D53C_ECG"],
  "pending": ["NeonCom007b_Neon_Gaze"]
}
```

`pending` = known from catalog but outlet not yet discovered by InletManager.

#### `streams.unsubscribe` (Client → Server)

```json
{
  "type": "streams.unsubscribe",
  "stream_ids": ["Polar_H10_0696D53C_ECG"]
}
```

---

### 5.3 Sample Data

#### `stream.sample` (Server → Subscribed Clients)

```json
{
  "type": "stream.sample",
  "stream_id": "Polar_H10_0696D53C_ECG",
  "timestamp": 1764791712.863,
  "data": [-0.042]
}
```

`data`: array of floats. Length = `channel_count`. Index corresponds to `channels[].index` in the catalog.

**Examples:**

ECG (1ch): `"data": [-0.042]`  → ch[0] = -0.042 µV

ACC (3ch): `"data": [12.0, -980.0, 30.0]`  → ch[0]=X, ch[1]=Y, ch[2]=Z in mG

Gaze (16ch): `"data": [768.7, 512.1, 1.0, 3.74, ...]`  → ch[0]=x px, ch[1]=y px, ch[2]=worn, ch[3]=pupil_left mm, ...

WebcamFatigue (8ch): `"data": [0.12, 0.38, 0.37, 0.39, 0.05, -3.2, 1.1, 0.5]`
→ ch[0]=PERCLOS %, ch[1]=EAR avg, ch[2]=EAR left, ch[3]=EAR right, ch[4]=gaze_score, ch[5]=head_yaw°, ch[6]=head_pitch°, ch[7]=head_roll°

VirTra events (4ch): `"data": ["Threat Engaged", "Actor 02 fired at trainee", "ThreatEngaged", "[...]"]`
→ ch[0]=Event Name, ch[1]=Description, ch[2]=Code, ch[3]=Params JSON string

#### `stream.lost` (Server → All Clients)

```json
{
  "type": "stream.lost",
  "stream_id": "Polar_H10_0696D53C_ECG",
  "reason": "publisher_finished"
}
```

`reason`: `"publisher_finished"` (replay complete), `"lsl_disconnect"` (device disconnected in live mode)

---

## Part 6: System Messages

#### `system.ping` (Client → Server)

```json
{ "type": "system.ping", "ts": 1764791712.863 }
```

**Response: `system.pong`**

```json
{ "type": "system.pong", "client_ts": 1764791712.863, "server_ts": 1764791712.901 }
```

#### `system.get_state` (Client → Server)

```json
{ "type": "system.get_state" }
```

Request full current state. Used after reconnection.

**Response: `session.state`** (current state) + `streams.catalog` (current catalog)

---

## Part 7: Error Messages

#### `api.error` (Server → Client)

```json
{
  "type": "api.error",
  "code": "SESSION_LOAD_FAILED",
  "message": "No lsl_metadata table found in: dnlc_session06_18Feb2026.db",
  "context": "session.load",
  "request_id": "abc123"
}
```

**Error codes:**

| Code | Meaning |
|------|---------|
| `SESSION_NOT_FOUND` | File path does not exist |
| `SESSION_INVALID` | Not a Total Recall database |
| `SESSION_LOAD_FAILED` | DB is valid but load failed (see message) |
| `SEEK_NOT_IMPLEMENTED` | playback.seek called but not yet supported |
| `WRONG_MODE` | Operation not valid in current mode (e.g., seek in live mode) |
| `NO_SESSION` | Playback command sent with no session loaded |
| `INTERNAL_ERROR` | Unexpected backend error |

---

## Part 8: Connection Lifecycle

```
Client connects to ws://localhost:8500
  ← Server: session.state      (current state — replay idle or live listening)
  ← Server: streams.catalog    (currently known streams)
  ← Server: session.list.result  (available .db files, if in replay mode)

Client reconnects (browser refresh mid-session):
  Same sequence. Backend sends current state — client resumes at correct position.
  No state is lost. Backend continues streaming regardless of UI connections.

Multiple clients:
  All connected clients receive all broadcasts (session.state, streams.catalog, stream.lost).
  Each client has its own subscription set for stream.sample messages.
```

---

## Part 9: Implementation Status

| Message | Direction | Status | Priority |
|---------|-----------|--------|----------|
| `session.list` / `session.list.result` | C↔S | Exists as `scan_db`/`db_list` — rename + add `valid`, `stream_count`, `duration_seconds`, `recorded_at` | P1 |
| `session.load` / `session.state` | C↔S | Exists as `load_db`/`mode` — rename + add `state`, `elapsed_seconds` | P1 |
| `session.unload` | C→S | Exists as `transport.stop` partially — formalise | P1 |
| `playback.play` / `playback.pause` | C→S | Exists as `transport` — rename | P1 |
| `playback.stop` | C→S | Exists as `transport.stop` | P1 |
| `playback.seek` | C→S | Defined. Returns NOT_IMPLEMENTED | P2 |
| `session.state` (1s broadcast) | S→C | Missing — needs asyncio timer in backend | P1 |
| `streams.get_catalog` / `streams.catalog` | C↔S | Exists as `get_catalog`/`stream_catalog` — rename | P1 |
| `streams.subscribe` / `streams.unsubscribe` | C→S | Exists — rename + add `pending` field | P1 |
| `stream.sample` | S→C | Exists as `sample` — rename | P1 |
| `stream.lost` | S→C | Exists as `stream_lost` — rename + add `reason` | P1 |
| `live.activate` / `live.deactivate` | C→S | Missing | P2 |
| `system.get_state` | C→S | Missing | P1 |
| `system.ping` / `system.pong` | C↔S | Missing | P3 |
| `api.error` | S→C | Exists as `error` — add `code`, `context` | P1 |

---

## Part 10: File Locations

```
LabReplay/
  api/
    contract.md         ← This document
    message_types.py    ← Python constants: MSG_SESSION_LOAD = "session.load"
    message_types.js    ← JS mirror: const MSG = { SESSION_LOAD: "session.load", ... }

  Backend/
    websocket_bridge.py ← THIN: WebSocket transport only
    session_service.py  ← NEW: State machine, session load/unload, 1s timer
    replay_engine.py    ← Unchanged (add get_elapsed, get_state, pause, resume)
    inlet_manager.py    ← Unchanged
    stream_meta_parser.py ← Unchanged

  Frontend/
    js/core/
      stream-router.js  ← Imports message_types.js, handles all incoming messages
      mode-manager.js   ← Display only: driven by session.state messages
```
