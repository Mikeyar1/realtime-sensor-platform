/**
 * features/aar/aar-dashboard.js
 *
 * After Action Review Dashboard — central router and task-type dispatcher.
 *
 * Responsibilities:
 *   1. Receive the session.scenario_started payload (contains task_type)
 *   2. Activate one of the three supported task displays
 *   3. Forward all events and metric results to the active module
 *
 * Task-type to module mapping (driven by Config/drill_registry.toml):
 *   GNG / PVT / DISCRIMINATION → LabReview.AAR.Standardized
 *
 * New task types require an explicit registry entry, metric contract, and
 * display specification. Unknown task types never fall back to another drill.
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.Dashboard = (function () {

  // ── Task-type → module registry ────────────────────────────────────────────
  // Keys must match the task_type values in drill_registry.toml (uppercase).
  // Values are resolved lazily at runtime so modules can load in any order.
  const TASK_TYPE_MODULES = {
    'GNG':            () => LabReview.AAR.Standardized.create('GNG'),
    'PVT':            () => LabReview.AAR.Standardized.create('PVT'),
    'DISCRIMINATION': () => LabReview.AAR.Standardized.create('DISCRIMINATION'),
    'PVT_FITTS':      () => LabReview.AAR.Standardized.create('PVT_FITTS'),
  };

  // ── State ──────────────────────────────────────────────────────────────────
  let _container   = null;
  let _activeModule = null;   // the task-type display module currently in use

  // ── Mount / Unmount ────────────────────────────────────────────────────────

  function mount(containerEl) {
    _container = containerEl;
    _renderWaiting();
  }

  function unmount() {
    if (_activeModule && _activeModule.unmount) _activeModule.unmount();
    _activeModule = null;
    _container    = null;
  }

  function reset() {
    if (_activeModule && _activeModule.unmount) _activeModule.unmount();
    _activeModule = null;
    _renderWaiting();
  }

  // ── Event forwarding ────────────────────────────────────────────────────────

  /**
   * Called when Backend broadcasts session.scenario_started.
   * payload.task_type determines which AAR module to activate.
   */
  function onScenarioStarted(payload) {
    const taskType = (payload.task_type || '').toUpperCase();
    _activateModule(taskType, payload);
    if (_activeModule && _activeModule.onScenarioStarted) {
      _activeModule.onScenarioStarted(payload);
    }
  }

  /**
   * Called for each incoming VirTra event during a live session.
   * Forwarded to the active module for any live state tracking it may need.
   */
  function onEvent(evt) {
    if (_activeModule && _activeModule.onEvent) {
      _activeModule.onEvent(evt);
    }
  }

  /**
   * Called when the Backend sends computed metrics (session.metrics_ready).
   * The active task-type module uses this to populate its cards.
   */
  function onMetricsReady(metrics) {
    if (_activeModule && _activeModule.onMetricsReady) {
      _activeModule.onMetricsReady(metrics);
    }
  }

  /**
   * Called on biosignal updates (HR, HRV, pupil).
   */
  function onSensorUpdate(payload) {
    if (_activeModule && _activeModule.onSensorUpdate) {
      _activeModule.onSensorUpdate(payload);
    }
  }

  /**
   * Called when Backend broadcasts session.scenario_stopped.
   */
  function onScenarioStopped(payload) {
    if (_activeModule && _activeModule.onScenarioStopped) {
      _activeModule.onScenarioStopped(payload);
    }
  }

  /**
   * Called when Backend broadcasts session.weapon_info (first Shot Fired).
   * Forwards weapon type to the active task-type module.
   */
  function onWeaponInfo(payload) {
    if (_activeModule && _activeModule.onWeaponInfo) {
      _activeModule.onWeaponInfo(payload);
    }
  }

  /**
   * Called on each real-time trial update (session.trial_update).
   * Dispatches to the active module based on task_type:
   *   PVT → onPvtUpdate()   GNG → onGngUpdate()
   *   DISCRIMINATION → onDiscriminationUpdate()
   */
  function onTrialUpdate(msg) {
    const taskType = (msg.task_type || '').toUpperCase();
    if (taskType === 'PVT' && _activeModule?.onPvtUpdate) {
      _activeModule.onPvtUpdate(msg);
    } else if (taskType === 'GNG' && _activeModule?.onGngUpdate) {
      _activeModule.onGngUpdate(msg);
    } else if (taskType === 'DISCRIMINATION' && _activeModule?.onDiscriminationUpdate) {
      _activeModule.onDiscriminationUpdate(msg);
    }
  }

  // ── Internal: activate the right module ────────────────────────────────────

  function _activateModule(taskType, payload) {
    if (_activeModule && _activeModule.unmount) _activeModule.unmount();

    const resolver = TASK_TYPE_MODULES[taskType];
    _activeModule = resolver ? resolver() : null;
    if (!_activeModule) {
      if (taskType === 'COMPOSITE') {
        _renderWaiting('Round and component data appear above');
      } else {
        console.warn(`[AAR] Unsupported task_type="${taskType}"`);
        _renderWaiting('Unsupported drill type');
      }
      return;
    }

    if (_container && _activeModule && _activeModule.mount) {
      _activeModule.mount(_container);
    }
  }

  function onContextUpdated(payload) {
    _activeModule?.onContextUpdated?.(payload);
  }

  function _renderWaiting(label = 'Waiting for a supported drill') {
    if (_container) _container.innerHTML = `<div class="aar-waiting"><div class="aar-waiting-label">${label}</div><div class="aar-waiting-sub">PVT, Go/No-Go, or Behavioral Discrimination</div></div>`;
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return {
    mount,
    unmount,
    reset,
    onScenarioStarted,
    onEvent,
    onMetricsReady,
    onSensorUpdate,
    onScenarioStopped,
    onContextUpdated,
    onTrialUpdate,
    onWeaponInfo,
  };

})();
