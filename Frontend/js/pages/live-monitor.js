/**
 * live-monitor.js — LiveMonitorPage
 *
 * Passive real-time observer. Listens for incoming LSL streams and renders
 * scrolling chart cards when streams are detected.
 *
 * Responsibilities:
 *  - Render the Live Monitor top bar (connection status, elapsed timer, Stream Inspector toggle)
 *  - Manage the 3-state connection status: Connecting → Streams Detected → Live
 *  - Render the Stream Inspector panel (collapsible, top-right)
 *  - Render the chart grid on receiving a catalog
 *  - Idle state placeholder when no streams are active
 *
 * Dependencies:
 *  - LabReplay.TopBarManager (register top bar)
 *  - LabReplay.StreamRegistry (stream → chart descriptor)
 *  - LabReplay.ChartFactory (create chart cards)
 *  - LabReplay.StreamRouter (subscribe, catalog, sample routing)
 *  - LabReplay.EventBus (catalog-updated, stream-lost, ws-connected, ws-disconnected)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.LiveMonitorPage = (function () {

  // ── State ────────────────────────────────────────────────────────────────────

  let _status         = 'connecting';   // 'connecting' | 'detected' | 'live'
  let _elapsedStart   = null;           // performance.now() anchor for elapsed timer
  let _elapsedTimer   = null;           // setInterval handle for elapsed display (1 Hz)
  let _lastSampleTime = null;           // Date.now() of last sample — used for idle detection
  let _idleCheckTimer = null;           // single recurring interval for idle detection
  let _inspectorOpen  = false;

  // Chart instance tracking
  let _activeInstances  = [];
  let _panelsByStream   = {};
  let _fusionInstances  = {};

  // Per-stream sample rate tracker { streamId: { count, lastReset, rate } }
  const _rateTracker = {};

  // Catalog snapshot
  let _catalog = [];

  // Idle timeout: if no sample in this many ms, revert to 'connecting'
  const IDLE_TIMEOUT_MS = 8000;

  // DOM refs (created fresh each time the topbar/page renders)
  let _statusDotEl    = null;
  let _statusLabelEl  = null;
  let _elapsedValueEl = null;
  let _inspectorEl    = null;
  let _chartGridEl    = null;
  let _idleOverlayEl  = null;



  // ── Top bar renderer ─────────────────────────────────────────────────────────

  function _renderTopBar(slot) {
    slot.innerHTML = `
      <div class="topbar" id="live-monitor-topbar">

        <!-- Left: status indicator -->
        <div class="topbar-section" id="lm-status-section">
          <div class="status-dot connecting" id="lm-status-dot"></div>
          <span class="topbar-status-label connecting" id="lm-status-label">Connecting</span>
        </div>

        <div class="topbar-sep"></div>
        <span class="topbar-page-title">Live Monitor</span>
        <div class="topbar-sep"></div>

        <!-- Center: elapsed -->
        <div class="topbar-elapsed" id="lm-elapsed">
          <span class="topbar-elapsed-label">ELAPSED</span>
          <span class="topbar-elapsed-value" id="lm-elapsed-value">00:00:00</span>
        </div>

        <!-- Right: stream inspector toggle -->
        <div class="topbar-section topbar-section--right">
          <button class="topbar-icon-btn" id="lm-inspector-btn" title="Stream Inspector">
            ⚙ Streams
          </button>
        </div>

      </div>
    `;

    // Grab refs
    _statusDotEl    = slot.querySelector('#lm-status-dot');
    _statusLabelEl  = slot.querySelector('#lm-status-label');
    _elapsedValueEl = slot.querySelector('#lm-elapsed-value');

    // Wire Stream Inspector toggle
    const inspectorBtn = slot.querySelector('#lm-inspector-btn');
    if (inspectorBtn) {
      inspectorBtn.addEventListener('click', () => _toggleInspector());
      if (_inspectorOpen) inspectorBtn.classList.add('active');
    }

    // Apply current status
    _applyStatus(_status);

    // Re-start elapsed timer if we're live
    if (_status === 'live' && _elapsedStart !== null) {
      _startElapsedTimer();
    }
  }

  // ── Page content renderer ─────────────────────────────────────────────────────

  function _renderPageContent() {
    const pageEl = document.getElementById('page-live-monitor');
    if (!pageEl) return;

    pageEl.innerHTML = `
      <div class="live-monitor-content" id="lm-content">

        <!-- Idle overlay — shown when no streams -->
        <div class="live-monitor-idle" id="lm-idle-overlay">
          <div class="live-monitor-idle-icon">〰</div>
          <div class="live-monitor-idle-title">Waiting for streams…</div>
          <div class="live-monitor-idle-sub">Connect devices and start streaming over LSL.</div>
        </div>

        <!-- Chart area -->
        <div class="live-monitor-charts" id="lm-chart-area" style="display:none">
          <div class="chart-grid" id="lm-chart-grid"></div>
        </div>

        <!-- Stream Inspector panel (absolutely positioned) -->
        <div class="stream-inspector" id="lm-stream-inspector">
          <div class="stream-inspector-header">
            <span class="stream-inspector-title">Stream Inspector</span>
            <button class="stream-inspector-close" id="lm-inspector-close" title="Close">✕</button>
          </div>
          <div class="stream-inspector-body">
            <div class="stream-inspector-empty" id="lm-inspector-empty">
              No streams detected.
            </div>
            <table class="stream-inspector-table" id="lm-inspector-table" style="display:none">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Hz</th>
                  <th>●</th>
                  <th>☑</th>
                </tr>
              </thead>
              <tbody id="lm-inspector-tbody"></tbody>
            </table>
          </div>
        </div>

      </div>
    `;

    _chartGridEl    = pageEl.querySelector('#lm-chart-grid');
    _idleOverlayEl  = pageEl.querySelector('#lm-idle-overlay');
    _inspectorEl    = pageEl.querySelector('#lm-stream-inspector');

    // Wire inspector close button
    const closeBtn = pageEl.querySelector('#lm-inspector-close');
    if (closeBtn) closeBtn.addEventListener('click', () => _closeInspector());

    // Apply current inspector state
    if (_inspectorOpen && _inspectorEl) _inspectorEl.classList.add('open');

    // If we already have a catalog, rebuild charts
    if (_catalog.length > 0) {
      _buildChartGrid(_catalog);
      _updateInspectorTable(_catalog);
    }
  }

  // ── Connection state machine ─────────────────────────────────────────────────

  function _setStatus(newStatus) {
    if (_status === newStatus) return;
    _status = newStatus;
    _applyStatus(newStatus);

    if (newStatus === 'live' && _elapsedStart === null) {
      _elapsedStart = performance.now();
      _startElapsedTimer();
    }

    if (newStatus !== 'live') {
      _stopElapsedTimer();
      if (newStatus === 'connecting') {
        _elapsedStart = null;
        _updateElapsedDisplay(0);
      }
    }
  }

  function _applyStatus(status) {
    if (!_statusDotEl || !_statusLabelEl) return;

    const LABELS = {
      connecting: 'Connecting',
      detected:   'Streams Detected',
      live:       'Live',
    };

    _statusDotEl.className   = `status-dot ${status}`;
    _statusLabelEl.className  = `topbar-status-label ${status}`;
    _statusLabelEl.textContent = LABELS[status] || status;
  }

  // ── Elapsed timer (1 Hz — HH:MM:SS display needs no more) ────────────────────────────

  function _startElapsedTimer() {
    if (_elapsedTimer) return;
    _elapsedTimer = setInterval(() => {
      if (_elapsedStart !== null && _elapsedValueEl) {
        _updateElapsedDisplay(performance.now() - _elapsedStart);
      }
    }, 1000);   // 1 Hz is indistinguishable from 60 Hz for HH:MM:SS
  }

  function _stopElapsedTimer() {
    if (_elapsedTimer) {
      clearInterval(_elapsedTimer);
      _elapsedTimer = null;
    }
  }

  function _updateElapsedDisplay(ms) {
    if (!_elapsedValueEl) return;
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    _elapsedValueEl.textContent =
      `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }

  // ── EventBus handlers ────────────────────────────────────────────────────────

  function _onWsConnected() {
    if (_status === 'connecting') return;  // already there
    _setStatus('connecting');
  }

  function _onWsDisconnected() {
    _setStatus('connecting');
  }

  function _onCatalogUpdated(catalog) {
    _catalog = catalog || [];

    if (_catalog.length > 0 && _status === 'connecting') {
      _setStatus('detected');
    } else if (_catalog.length === 0) {
      _setStatus('connecting');
    }

    // Only act on catalog if this page is active
    if (LabReplay.Sidebar.getActivePage() !== 'live-monitor') return;

    if (_catalog.length > 0) {
      _showChartArea();
      _buildChartGrid(_catalog);
      _updateInspectorTable(_catalog);
      // Subscribe to all streams
      LabReplay.StreamRouter.subscribeAll();
    } else {
      _showIdleOverlay();
      _updateInspectorTable([]);
    }
  }

  function _onSampleReceived({ streamId }) {
    // Transition to Live on first sample
    if (_status !== 'live') _setStatus('live');

    // Mark stream active in Inspector
    if (streamId) _markStreamActive(streamId);

    // Update timestamp only — idle check runs on its own interval (no per-sample timers)
    _lastSampleTime = Date.now();
  }

  function _onStreamLost({ id }) {
    // Update inspector status dot for the lost stream
    const dot = document.querySelector(`[data-inspector-stream="${id}"] .stream-inspector-status-dot`);
    if (dot) dot.className = 'stream-inspector-status-dot lost';

    // If all streams lost, transition back
    if (_catalog.filter(s => s.name !== id).length === 0) {
      _setStatus('connecting');
    }
  }


  function _checkIdle() {
    if (_lastSampleTime && Date.now() - _lastSampleTime >= IDLE_TIMEOUT_MS) {
      if (_status === 'live') _setStatus('detected');
    }
  }

  // ── Chart grid ───────────────────────────────────────────────────────────────

  function _buildChartGrid(catalog) {
    if (!_chartGridEl) return;

    const shownKeys = new Set(_activeInstances.map(ai => ai.descriptorKey).filter(Boolean));

    for (const stream of catalog) {
      if (_panelsByStream[stream.name]) continue;

      const resolved = LabReplay.StreamRegistry.resolve(stream);
      if (!resolved) continue;

      const descriptors = Array.isArray(resolved) ? resolved : [resolved];

      try {
        let firstPanelEl = null;

        for (const descriptor of descriptors) {
          const key = descriptor._key;

          if (descriptor.fusion) {
            let fusionEntry = _fusionInstances[key];
            if (!fusionEntry) {
              const { el, instance } = LabReplay.ChartFactory.create(descriptor);
              _chartGridEl.appendChild(el);
              fusionEntry = { instance, el };
              _fusionInstances[key] = fusionEntry;
            }
            if (descriptor._fusionTag && fusionEntry.instance) {
              const tag     = descriptor._fusionTag;
              const fusInst = fusionEntry.instance;
              const wrapper = {
                pushSample: (ts, data, elapsed) => fusInst.pushTagged(tag, ts, data, elapsed),
                resize:     () => fusInst.resize(),
                destroy:    () => {},
              };
              LabReplay.StreamRouter.registerInstance(stream.name, wrapper);
              _activeInstances.push({ streamName: stream.name, instance: wrapper, panel: { el: fusionEntry.el } });
            }
            firstPanelEl = firstPanelEl || fusionEntry?.el;
            continue;
          }

          if (shownKeys.has(key)) {
            const existing = _activeInstances.find(ai => ai.descriptorKey === key);
            if (existing?.instance) {
              LabReplay.StreamRouter.registerInstance(stream.name, existing.instance);
            }
            firstPanelEl = firstPanelEl || existing?.panel?.el;
          } else {
            shownKeys.add(key);
            const { el, instance } = LabReplay.ChartFactory.create(descriptor);
            _chartGridEl.appendChild(el);
            if (instance) {
              LabReplay.StreamRouter.registerInstance(stream.name, instance);
              _activeInstances.push({ streamName: stream.name, descriptorKey: key, instance, panel: { el } });
            }
            firstPanelEl = firstPanelEl || el;
          }
        }

        _panelsByStream[stream.name] = firstPanelEl || '__handled__';

      } catch (err) {
        console.error(`[LiveMonitor] Chart error for "${stream.name}":`, err);
      }
    }
  }

  function _destroyCharts() {
    for (const { streamName, instance } of _activeInstances) {
      LabReplay.StreamRouter.unregisterInstance(streamName, instance);
      if (instance.destroy) instance.destroy();
    }
    for (const { instance } of Object.values(_fusionInstances)) {
      if (instance?.destroy) instance.destroy();
    }
    if (_chartGridEl) _chartGridEl.innerHTML = '';
    _activeInstances  = [];
    _panelsByStream   = {};
    _fusionInstances  = {};
  }

  // ── Stream Inspector ─────────────────────────────────────────────────────────

  function _toggleInspector() {
    _inspectorOpen = !_inspectorOpen;
    const btn = document.getElementById('lm-inspector-btn');
    if (btn) btn.classList.toggle('active', _inspectorOpen);
    if (_inspectorEl) _inspectorEl.classList.toggle('open', _inspectorOpen);
  }

  function _closeInspector() {
    _inspectorOpen = false;
    const btn = document.getElementById('lm-inspector-btn');
    if (btn) btn.classList.remove('active');
    if (_inspectorEl) _inspectorEl.classList.remove('open');
  }

  function _updateInspectorTable(catalog) {
    const emptyEl  = document.getElementById('lm-inspector-empty');
    const tableEl  = document.getElementById('lm-inspector-table');
    const tbodyEl  = document.getElementById('lm-inspector-tbody');
    if (!tbodyEl) return;

    if (!catalog || catalog.length === 0) {
      if (emptyEl)  emptyEl.style.display = '';
      if (tableEl)  tableEl.style.display = 'none';
      return;
    }

    if (emptyEl)  emptyEl.style.display = 'none';
    if (tableEl)  tableEl.style.display = '';
    tbodyEl.innerHTML = '';

    for (const stream of catalog) {
      const subscribed = LabReplay.StreamRouter.getSubscribed().has(stream.name);
      const hz = stream.sample_rate != null
        ? (stream.sample_rate === 0 ? 'IRR' : stream.sample_rate.toFixed(0))
        : '—';

      const tr = document.createElement('tr');
      tr.dataset.inspectorStream = stream.name;
      tr.innerHTML = `
        <td class="stream-inspector-name" title="${stream.name}">${_shortName(stream.name)}</td>
        <td class="stream-inspector-type">${stream.stream_type || '—'}</td>
        <td class="stream-inspector-hz">${hz}</td>
        <td><span class="stream-inspector-status-dot silent" data-stream-dot="${stream.name}"></span></td>
        <td class="stream-inspector-toggle">
          <input type="checkbox" ${subscribed ? 'checked' : ''}
                 data-stream-toggle="${stream.name}"
                 title="Toggle stream">
        </td>
      `;

      // Wire toggle checkbox
      tr.querySelector('input[type="checkbox"]').addEventListener('change', (e) => {
        if (e.target.checked) {
          LabReplay.StreamRouter.subscribe(stream.name);
        } else {
          LabReplay.StreamRouter.unsubscribe(stream.name);
          // Remove chart card for this stream
          const entry = _activeInstances.find(ai => ai.streamName === stream.name);
          if (entry?.panel?.el) entry.panel.el.style.display = 'none';
        }
      });

      tbodyEl.appendChild(tr);
    }
  }

  /** Shorten long stream names for display in the inspector. */
  function _shortName(name) {
    // Polar H10 D9D5342F_ECG → H10 ECG
    // Neon_middleware_eye_events_NeonCom007b_7 → Eye Events
    return name.replace(/[A-Z0-9]{8,}_/g, '').replace(/_/g, ' ').trim();
  }

  /** Update the status dot color for a specific stream (called on sample receipt). */
  function _markStreamActive(streamId) {
    const dot = document.querySelector(`[data-stream-dot="${streamId}"]`);
    if (dot && !dot.classList.contains('active')) {
      dot.className = 'stream-inspector-status-dot active';
    }
  }

  // ── Idle / chart-area visibility ─────────────────────────────────────────────

  function _showIdleOverlay() {
    if (_idleOverlayEl) _idleOverlayEl.style.display = '';
    const chartArea = document.getElementById('lm-chart-area');
    if (chartArea) chartArea.style.display = 'none';
  }

  function _showChartArea() {
    if (_idleOverlayEl) _idleOverlayEl.style.display = 'none';
    const chartArea = document.getElementById('lm-chart-area');
    if (chartArea) chartArea.style.display = '';
  }

  // ── Teardown (called by TopBarManager when leaving this page) ────────────────

  function _teardown() {
    _stopElapsedTimer();
    // Don't destroy charts — they continue receiving data in background
    // (passive observer — data flows even when viewing another page)
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Called by app.js once on boot.
   * Registers the top bar renderer with TopBarManager and wires EventBus listeners.
   * Page content is rendered on first 'page-changed' → 'live-monitor' event.
   */
  function init() {
    // Register top bar renderer + teardown with TopBarManager
    LabReplay.TopBarManager.register('live-monitor', _renderTopBar, _teardown);

    // Single recurring interval for idle detection (replaces per-sample setTimeout churn)
    _idleCheckTimer = setInterval(() => {
      if (_lastSampleTime && Date.now() - _lastSampleTime >= IDLE_TIMEOUT_MS) {
        if (_status === 'live') _setStatus('detected');
      }
    }, 2000);   // check every 2 s — more than precise enough for 8 s idle threshold

    // Register EventBus listeners
    LabReplay.EventBus.on('ws-connected',            _onWsConnected);
    LabReplay.EventBus.on('ws-disconnected',         _onWsDisconnected);
    LabReplay.EventBus.on('catalog-updated',         _onCatalogUpdated);
    LabReplay.EventBus.on('stream-lost',             _onStreamLost);
    LabReplay.EventBus.on('stream-sample-received',  _onSampleReceived);

    LabReplay.EventBus.on('page-changed', (page) => {
      if (page === 'live-monitor') {
        _renderPageContent();
      }
    });

    // If this is the default active page, render content immediately
    // (page-changed fires from Sidebar.init() after TopBarManager.register)
    // No explicit call needed — Sidebar.init() → switchPage('live-monitor')
    // → EventBus.emit('page-changed', 'live-monitor') → _renderPageContent()
  }

  return { init };

})();

