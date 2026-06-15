# LabReplay — Architecture Answers & Development Roadmap

---

## 1. What Is the "LSL Subscriber Bus"?

It's just a name for **the thing that listens to LSL streams and hands the data to your dashboard**. Here's why it exists:

### The Problem

Total Recall publishes data over LSL. LSL is a **network protocol** — it speaks TCP/UDP, not HTTP. Your browser (HTML/CSS/JS) cannot listen to LSL directly. There's no `new LSLInlet()` in JavaScript.

### The Solution: A Python Bridge

```
Total Recall          Python Backend           Browser
(LSL Publisher)  →   (LSL → WebSocket)    →   (JS Dashboard)
    pylsl               asyncio + websockets      Chart.js / canvas
```

The "LSL Subscriber Bus" is that **middle box** — a Python process that:

1. **Discovers** all active LSL streams on the network (using `pylsl.resolve_streams()`)
2. **Opens one inlet per stream** (each in its own thread)
3. **Forwards every sample** to the browser over a **WebSocket** connection

That's it. It's a translator: LSL in, WebSocket out.

### What the browser receives

A JSON message per sample, something like:

```json
{
  "stream": "ECG",
  "timestamp": 1698012345.123,
  "data": [0.342]
}
```

or for multi-channel streams:

```json
{
  "stream": "Gaze",
  "timestamp": 1698012345.123,
  "data": [512.3, 384.7],
  "channels": ["gaze_x", "gaze_y"]
}
```

> [!IMPORTANT]
> You are NOT building this bridge from scratch. It's ~100 lines of Python using `pylsl` + `websockets`. The heavy lifting (replay timing, thread management) is already done by Total Recall. The bridge just listens and forwards.

---

## 2. How Will Charts Work Given Different Stream Types?

Different streams need different visualizations. Here's the mapping:

### Chart Type by Stream Nature

| Stream Type             | Example Streams                    | Chart                               | Library                                                   | Why                                                      |
| ----------------------- | ---------------------------------- | ----------------------------------- | --------------------------------------------------------- | -------------------------------------------------------- |
| **Continuous waveform** | ECG, Respiration, GSR, EDA         | Scrolling line chart                | **Chart.js** with `streaming` plugin or **Canvas 2D API** | Smooth, real-time, GPU-accelerated                       |
| **Slow numeric**        | Heart Rate, SpO2, Skin Temp, HRV   | Gauge + sparkline                   | **Chart.js** (line) + DOM element for big number          | Updates every 1-5 seconds, not a waveform                |
| **Dual trace**          | Pupil L + Pupil R                  | Two overlaid lines, same axes       | **Chart.js** (two datasets on one chart)                  | Compare left vs right eye                                |
| **2D position**         | Gaze X + Gaze Y                    | Scatter plot with fading trail      | **Canvas 2D API** (manual draw)                           | Need to draw a "dot" that moves and leaves a trail       |
| **Discrete events**     | Eye state, shots, actor visibility | Coloured marker ticks on a timeline | **DOM elements** or SVG                                   | Not continuous data — just "fixation started at t=12.3s" |
| **Categorical state**   | Eye state (fixation/saccade/blink) | Colour-coded bar or indicator       | DOM element with colour swaps                             | Shows current state as a coloured badge                  |

### The Key Insight: You Only Need 3-4 Chart "Widgets"

You don't build 30 different charts. You build **reusable widget classes**:

```
1. WaveformWidget    — scrolling line chart (handles ECG, Pupil, GSR, Respiration, etc.)
2. GaugeWidget       — big number + sparkline (handles HR, SpO2, Temp, HRV)
3. ScatterWidget     — 2D dot with trail (handles Gaze)
4. EventWidget       — timeline of discrete markers (handles eye events, shots)
```

Each widget is a self-contained JS class that:
- Receives a `{ timestamp, data }` message from the WebSocket
- Appends it to an internal circular buffer (last N seconds of data)
- Re-renders on each animation frame

### How Hover / Tooltips / Axes Work

**Chart.js** gives you this for free:
- Hover over any point → tooltip shows exact value + timestamp
- X-axis = time (auto-scrolling), Y-axis = value with proper units
- You configure the axis label and units when you create the chart

For the **Canvas 2D** widgets (gaze scatter), you'd implement a simple hit-test:
- On `mousemove`, check if cursor is near a data point
- Show a small tooltip div with the coordinates

**Example of what a WaveformWidget produces:**

```
 ECG (µV)
 0.8 ┤                    ╱╲
 0.4 ┤              ╱╲   ╱  ╲   ╱╲
 0.0 ┤─────────╱╲──╱──╲─╱────╲─╱──╲──
-0.4 ┤        ╱  ╲╱    ╲╱      ╲╱
     └──────────────────────────────── t
      -5s   -4s   -3s   -2s   -1s  now
                                  ↑ hover: -1.2s, 0.34 µV
```

---

## 3. Your Page Structure (Landing + Category Pages)

Your idea is solid. Here's how it maps:

```
┌─────────────────────────────────────────────────┐
│  SIDEBAR (always visible)                       │
│  ┌──────────┐                                   │
│  │ 🏠 Home  │  ← Landing page                  │
│  │ 🫀 Heart │  ← ECG, HRV, HR, SpO2, BP       │
│  │ 👁 Gaze  │  ← Pupil L/R, Gaze X/Y, Events  │
│  │ 🧠 Mind  │  ← EEG, GSR, EDA, Skin Temp     │
│  │ 🌬 Resp  │  ← Respiration rate + waveform   │
│  │ 🏃 Body  │  ← Accel, Gyro (skeleton later)  │
│  │ 📊 Analysis │ ← 2AFC charts (post-hoc)      │
│  ├──────────┤                                   │
│  │ ⚙ Settings │                                 │
│  └──────────┘                                   │
│                   MAIN CONTENT                   │
│  ┌─────────────────────────────────────────┐    │
│  │  (changes based on sidebar selection)   │    │
│  └─────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────┐    │
│  │  TIMELINE BAR (always visible at bottom)│    │
│  │  ◄◄  ▶  ■  ═══════●══════  1x ▼       │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

### Landing Page ("Home")

Shows the **most relevant** streams at a glance — your "executive summary":

| Panel                      | Why it's on the landing page           |
| -------------------------- | -------------------------------------- |
| ECG waveform               | Most recognizable physiological signal |
| Heart Rate gauge           | Instant readout everyone understands   |
| Pupil L + R overlay        | Core metric from your research         |
| Gaze 2D scatter            | Visually striking, shows attention     |
| A few mini sparkline cards | Quick status for other active streams  |

### Category Pages

When you click "🫀 Heart" in the sidebar, the main content swaps to show **all cardiac streams in detail** — full-width waveforms, larger charts, more axis detail, analysis controls specific to that category.

### How Pages Work in Plain HTML/CSS/JS

You're not using a framework, so "pages" are just **div containers that you show/hide**:

```html
<div id="page-home" class="page active">...</div>
<div id="page-heart" class="page hidden">...</div>
<div id="page-gaze" class="page hidden">...</div>
```

Clicking a sidebar link swaps which div has `class="active"`. No routing library needed. The WebSocket stays connected across all "page" switches — you just show/hide the relevant chart canvases.

---

## 4. Development Roadmap — Where to Start

### Phase 0: The WebSocket Bridge (Do This First)

**Why first:** Without data flowing from LSL to the browser, you have nothing to visualize. Everything depends on this.

**What you build:**
- A small Python script (`ws_bridge.py`) that:
  1. Resolves all LSL streams
  2. Opens one inlet per stream
  3. Starts a WebSocket server on `ws://localhost:8765`
  4. Forwards every sample as JSON to connected browser clients
- A test HTML page that connects to the WebSocket and prints messages to `console.log`

**Done when:** You run Total Recall, run the bridge, open the HTML page, and see JSON data flowing in the browser console.

**Estimated effort:** A few hours. ~100-150 lines of Python.

---

### Phase 1: One Chart, End to End

**What you build:**
- The app shell: light gray background, sidebar skeleton, main content area
- ONE `WaveformWidget` class that renders a scrolling Chart.js line chart
- Wire it to the WebSocket: when an ECG sample arrives, push it to the chart
- Add hover tooltips, axis labels, time axis

**Done when:** You see a live ECG waveform scrolling in the browser with proper axes and hover values.

**Estimated effort:** 1-2 days. This is the hardest phase because you're establishing all the patterns.

---

### Phase 2: Widget Library

**What you build:**
- `GaugeWidget` (big number + sparkline)
- `ScatterWidget` (gaze 2D plot)
- `EventWidget` (event markers)
- A `WidgetFactory` function: given a stream type, return the right widget

**Done when:** You can add any of the 4 widget types to a page by just specifying the stream name and type.

---

### Phase 3: Landing Page + Sidebar Navigation

**What you build:**
- The landing page layout with your chosen "hero" panels
- Sidebar navigation (show/hide page divs)
- Category pages: Heart, Gaze, Mind, Resp, Body
- Each category page auto-populates widgets for its streams

**Done when:** You can navigate between pages, each showing the correct streams with the correct chart types.

---

### Phase 4: Timeline + Transport Controls

**What you build:**
- Play/Pause/Stop buttons that send commands back to Total Recall
- Timeline bar showing session duration and playhead position
- Speed control (0.5x, 1x, 2x)

**Done when:** You can pause, resume, and scrub the replay from the browser.

---

### Phase 5: Analysis Integration

**What you build:**
- The "📊 Analysis" page
- Port the 2AFC charts (pupil response, gaze heatmap, reaction time, classifier) from Plotly/Streamlit to Chart.js
- These work on the **full session data** (not real-time), loaded from the SQLite/CSV after the session

---

### Phase 6: Polish + Future

- Skeleton viewer panel (SAM3DB)
- Panel drag/resize
- Session export
- Multi-session comparison

---

## 5. The Very First Thing to Do Right Now

> [!IMPORTANT]
> **Build the WebSocket bridge.** That is step zero. Everything else is just drawing pictures without data.

Here's the exact sequence:

1. **Create the project structure:**
   ```
   LabReplay/
   ├── backend/
   │   └── ws_bridge.py        ← Python: LSL → WebSocket
   ├── frontend/
   │   ├── index.html           ← The dashboard
   │   ├── css/
   │   │   └── style.css
   │   └── js/
   │       ├── app.js           ← WebSocket connection + page routing
   │       └── widgets/
   │           └── waveform.js  ← First chart widget
   ├── total-recall/            ← (already exists)
   └── requirements.md
   ```

2. **Write `ws_bridge.py`** — resolve LSL streams, forward to WebSocket as JSON.

3. **Write a test `index.html`** — connect to `ws://localhost:8765`, log messages to console.

4. **Test end-to-end:** Run Total Recall → run bridge → open browser → see data flowing.

5. **Then** start building the actual dashboard UI.
