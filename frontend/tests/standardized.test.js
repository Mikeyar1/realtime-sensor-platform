const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this._text = '';
    this._html = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  get innerHTML() { return this._html; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = String(value); }
}

const context = { document: { createElement: name => new Element(name) }, console };
context.window = context;
context.LabReview = {};
vm.createContext(context);
const aarDir = path.resolve(__dirname, '../js/features/aar');
for (const filename of ['standardized.js', 'aar-dashboard.js']) {
  vm.runInContext(fs.readFileSync(path.join(aarDir, filename), 'utf8'), context, { filename });
}

const dashboard = context.LabReview.AAR.Dashboard;
const root = new Element('div');
dashboard.mount(root);

dashboard.onScenarioStarted({ task_type: 'PVT', scenario_name: 'PVT example',
  simulator: 'V-AIMS', simulator_system: 'V_300' });
dashboard.onTrialUpdate({ task_type: 'PVT', running_metrics: {
  total_trials: 2, hits_pct: 50, omissions: 1, response_rate_pct: 50,
  omission_rate_pct: 50, rt_median_ms: 420,
} });
assert.match(root.textContent, /Hits \(%\)/);
assert.match(root.textContent, /50 %/);
assert.match(root.textContent, /420 ms/);
assert.match(root.textContent, /V-AIMS Simulator/);
assert.match(root.textContent, /V_300 Simulator System/);

dashboard.onScenarioStarted({ task_type: 'GNG', scenario_name: 'GNG example' });
dashboard.onMetricsReady({ total_trials: 4, go_trials: 2, no_go_trials: 2,
  omissions: 1, commission_errors: 1, overall_accuracy_pct: 50, rt_n: 1 });
assert.match(root.textContent, /Commission Errors \(False Alarms\)/);
assert.match(root.textContent, /Overall Accuracy \(%\)/);
assert.match(root.textContent, /RT Count \(n\)/);
assert.match(root.textContent, /— Simulator System/);

dashboard.onScenarioStarted({ task_type: 'DISCRIMINATION', scenario_name: 'Discrimination example' });
dashboard.onMetricsReady({ duration_s: 7, pvt: { total_trials: 2, hits_pct: 50 },
  discrimination: { total_trials: 4, threat_omission_rate_pct: 25, commission_error_rate_pct: 50 } });
assert.match(root.textContent, /Threat Omission Rate \(%\)/);
assert.match(root.textContent, /PVT Phase/);
assert.match(root.textContent, /25 %/);
assert.match(root.textContent, /— Simulator/);

dashboard.onScenarioStarted({ task_type: 'GENERAL' });
assert.match(root.innerHTML, /Unsupported drill type/);
assert.doesNotMatch(root.innerHTML, /General Drill/);

console.log('Three-drill AAR rendering checks passed');
