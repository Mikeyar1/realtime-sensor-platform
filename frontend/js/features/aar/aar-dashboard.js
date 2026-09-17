/**
 * features/aar/aar-dashboard.js
 *
 * After Action Review Dashboard — central router and task-type dispatcher.
 *
 * Responsibilities:
 *   1. Receive the session.scenario_started payload (contains task_type)
 *   2. Activate the correct task-type display module (GNG, PVT, General)
 *   3. Forward all events and metric results to the active module
 *
 * Task-type to module mapping (driven by Config/drill_registry.toml):
 *   GNG          → LabReview.AAR.GNG
 *   PVT          → LabReview.AAR.PVT
 *   DISCRIMINATION → LabReview.AAR.Discrimination
 *   GENERAL      → LabReview.AAR.General  (fallback)
 *   MARKSMANSHIP → LabReview.AAR.General  (until marksmanship module is built)
 *
 * Adding a new task type:
 *   1. Add task_type = "MYTYPE" to Config/drill_registry.toml
 *   2. Create features/aar/task-types/mytype.js exporting LabReview.AAR.MYTYPE
 *   3. Add the mapping below under TASK_TYPE_MODULES
 *   4. Load the script in index.html
 *   Done — no other code changes needed.
 */

window.LabReview = window.LabReview || {};
window.LabReview.AAR = window.LabReview.AAR || {};

LabReview.AAR.Dashboard = (function () {

  // ── Task-type → module registry ────────────────────────────────────────────
  // Keys must match the task_type values in drill_registry.toml (uppercase).
  // Values are resolved lazily at runtime so modules can load in any order.
  const TASK_TYPE_MODULES = {
    'GNG':            () => LabReview.AAR.GNG,
    'PVT':            () => LabReview.AAR.PVT,
    'DISCRIMINATION': () => LabReview.AAR.Discrimination,
    'GENERAL':        () => LabReview.AAR.General,
    'MARKSMANSHIP':   () => LabReview.AAR.General,   // placeholder until marksmanship module
  };

  // ── State ──────────────────────────────────────────────────────────────────
  let _container   = null;
  let _activeModule = null;   // the task-type display module currently in use

  // ── Mount / Unmount ────────────────────────────────────────────────────────

  function mount(containerEl) {
    _container = containerEl;
    // Show the General waiting state until a scenario starts
    _activateModule('GENERAL', {});
  }

  function unmount() {
    if (_activeModule && _activeModule.unmount) _activeModule.unmount();
    _activeModule = null;
    _container    = null;
  }

  function reset() {
    if (_activeModule && _activeModule.reset) _activeModule.reset();
    else _activateModule('GENERAL', {});
  }

  // ── Event forwarding ────────────────────────────────────────────────────────

  /**
   * Called when Backend broadcasts session.scenario_started.
   * payload.task_type determines which AAR module to activate.
   */
  function onScenarioStarted(payload) {
    const taskType = (payload.task_type || 'GENERAL').toUpperCase();
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

    const resolver = TASK_TYPE_MODULES[taskType] || TASK_TYPE_MODULES['GENERAL'];
    const module   = resolver ? resolver() : null;

    if (!module) {
      console.warn(`[AAR] No display module found for task_type="${taskType}" — falling back to General`);
      _activeModule = (TASK_TYPE_MODULES['GENERAL'])();
    } else {
      _activeModule = module;
    }

    if (_container && _activeModule && _activeModule.mount) {
      _activeModule.mount(_container);
    }
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
    onTrialUpdate,
    onWeaponInfo,
  };

})();
