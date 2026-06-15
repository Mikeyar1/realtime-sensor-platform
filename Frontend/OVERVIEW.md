# Frontend — Component Overview

> **Role:** Real-time and post-session visualization dashboard.
>
> The Frontend is a pure browser application — HTML, CSS, and JavaScript
> served over HTTP. It connects to the Backend via WebSocket and to the
> Analysis API via HTTP fetch. All rendering and interactivity lives here.

---

## Purpose

The Frontend presents four distinct pages that correspond to the system's
two operating modes (Live and Post-Session):

| Mode | Page | Description |
|------|------|-------------|
| **Live** | Real-Time Monitoring | Stream charts for all active LSL sensors |
| **Live** | Real-Time Human Performance | Live BehDisc engagement scoring + physio |
| **Post-Session** | Replay Sessions | Transport controls + event log replay |
| **Post-Session** | Human Performance Workspace | Epoch analysis charts per drill |

---

## Conceptual Model

```
Backend (ws://host:8500)
  │  WebSocket JSON messages
  ▼
StreamRouter  ──→  EventBus  ──→  Page controllers
  │                              │
  │                    ┌─────────┴──────────┐
  │               LiveMonitorPage    LiveIntelPage
  │               ReplaySessionsPage WorkspacePage
  │
  └──→ chart instances (push samples in real time)

Analysis API (http://127.0.0.1:8081)
  │  fetch() HTTP
  ▼
AnalysisApp  ──→  EpochChart + StatsPanel (post-session workspace)
```

---

## Structure

```
Frontend/
│
├── index.html                  # Single HTML file; all pages live here as div#page-*
│
├── css/
│   ├── variables.css           # Design tokens: colors, spacing, typography
│   ├── layout.css              # App shell: sidebar, topbar slot, main grid
│   ├── charts.css              # Chart card, axis, legend, stream card styles
│   ├── analysis.css            # Analysis workspace control bar + chart styles
│   └── pages.css               # @import index → css/pages/ splits
│       └── pages/
│           ├── topbar.css      # Shared topbar components (all pages)
│           ├── live-monitor.css # Live Monitor layout + stream inspector
│           ├── live-intel.css  # Live Intel layout + BehDisc bd-* dashboard
│           ├── replay.css      # Replay Sessions layout
│           └── workspace.css   # Workspace layout
│
└── js/
    ├── app.js                  # ES module entry point; wires all modules
    │
    ├── shared/                 # Pure ES modules (no DOM, no globals)
    │   ├── constants.js        # WS_URL, ANALYSIS_API_BASE, SIGNALS, DRILLS
    │   ├── format.js           # formatElapsed, formatDelta, formatDuration, clamp
    │   └── api.js              # analysisGet, analysisPost, wsSend
    │
    ├── core/                   # Infrastructure (LabReplay.* namespace)
    │   ├── event-bus.js        # Pub/sub: EventBus.on/emit/off
    │   ├── plugin-registry.js  # Chart plugin registry: register/getPluginFor/create
    │   ├── stream-router.js    # WebSocket transport + message dispatch
    │   ├── mode-manager.js     # Session state machine (playing/paused/stopped)
    │   ├── sidebar.js          # Navigation sidebar: page switching
    │   ├── topbar-manager.js   # Topbar slot: register/activate per page
    │   └── timeline.js         # Playhead timeline widget
    │
    ├── charts/                 # Chart plugin implementations
    │   ├── stream-registry.js  # Catalog registry: stream name → chart type
    │   ├── chart-card.js       # Base card wrapper (header, resize, destroy)
    │   ├── chart-factory.js    # Creates the right chart for a stream type
    │   ├── chart-hr.js         # Heart rate time-series (Plotly)
    │   ├── chart-hrv.js        # HRV line chart
    │   ├── chart-pupil.js      # Pupil diameter dual-eye chart
    │   ├── chart-gaze.js       # Gaze heatmap / scatter
    │   ├── chart-motion.js     # Head motion 3-axis
    │   ├── chart-line.js       # Generic continuous line chart
    │   ├── chart-numeric.js    # Single-value numeric readout
    │   ├── chart-scatter2d.js  # 2D scatter (e.g. gaze fixation)
    │   └── chart-virtra.js     # VirTra engagement event chart
    │
    ├── plugins/                # Stream-type chart plugins (registered at load)
    │   ├── event-ticker.js     # VirTra event scrolling ticker
    │   ├── event-log.js        # Structured event log table
    │   ├── waveform.js         # Generic waveform plugin
    │   ├── gauge.js            # Numeric gauge plugin
    │   └── scatter.js          # Scatter2D plugin
    │
    ├── intel/                  # Live Human Performance Intelligence
    │   ├── behdisc-engine.js   # Real-time BehDisc math: engagement scoring, Δphysio
    │   └── live-intel-ui.js    # UI rendering: stats bar, state banner, results tabs
    │
    ├── analysis/               # Human Performance Workspace (post-session)
    │   ├── api-client.js       # LabReplay.AnalysisAPI — fetch wrapper for port 8081
    │   ├── analysis-app.js     # Top-level workspace controller
    │   ├── epoch-chart.js      # Epoch overlay chart (Plotly, baseline + analysis)
    │   ├── stats-panel.js      # Statistics table (aggregate / per-engagement / comparison)
    │   ├── trial-sidebar.js    # Engagement list sidebar + selection
    │   └── drills/
    │       └── behdisc/
    │           ├── aggregate-view.js      # Aggregate epoch chart + filter bar
    │           ├── per-engagement-view.js # Per-trial sidebar + individual trace
    │           └── comparison-view.js     # Side-by-side session comparison
    │
    └── pages/                  # Page controllers (register topbar + content)
        ├── live-monitor.js     # Real-Time Monitoring page
        ├── live-intel.js       # Real-Time Human Performance page
        ├── replay-sessions.js  # Replay Sessions page
        └── workspace.js        # Human Performance Workspace page
```

---

## Key Architectural Patterns

### LabReplay Namespace
All modules attach to `window.LabReplay` (e.g. `LabReplay.StreamRouter`).
This allows classic `<script>` tags with a guaranteed global namespace —
no module bundler required. The `js/shared/` layer uses native ES module
`import`/`export` and is bridged to the namespace in `app.js`.

### Page Controller Pattern
Each of the four pages follows the same contract:
```javascript
LabReplay.SomePage = (function () {
  function init() {
    // 1. Build HTML content into div#page-*
    // 2. Build topbar HTML
    // 3. Register with TopBarManager
    // 4. Subscribe to EventBus events
  }
  return { init };
})();
```
The `app.js` bootstrap calls `init()` on each page once.

### Plugin Registry Pattern
Chart types are registered as plugins:
```javascript
LabReplay.registerPlugin({
  id: 'waveform',
  streamTypes: ['continuous'],
  create: (container, meta) => new WaveformChart(container, meta),
});
```
`StreamRouter` calls `LabReplay.createChart(pluginId, container, meta)`
when a subscribed stream's first sample arrives.

### Two-Math-Path Design
| Path | Where | When |
|------|-------|------|
| **Live math** | `js/intel/behdisc-engine.js` | During active session, real-time |
| **Replay math** | `Analysis/` FastAPI | After session, on-demand |

These two paths are completely separate and do not share code.

---

## How to Run

```bash
cd Frontend
python3 -m http.server 8080
# Open http://localhost:8080
```

The Frontend auto-detects the WebSocket backend from `window.location.hostname`
so it works on any machine without configuration changes.
