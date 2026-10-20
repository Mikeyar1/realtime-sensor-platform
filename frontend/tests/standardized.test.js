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
    this.className = '';
    this.classList = {
      contains: name => this.className.split(' ').includes(name),
      add: name => { if (!this.classList.contains(name)) this.className += ` ${name}`; },
      remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  get innerHTML() { return this._html; }
  append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
  appendChild(child) { this.append(child); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  closest(selector) {
    const name = selector.slice(1);
    for (let node = this; node; node = node.parent) {
      if (node.classList.contains(name)) return node;
    }
    return null;
  }
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
const specs = context.LabReview.AAR.Standardized.specs;
assert.equal(context.LabReview.AAR.Standardized.assessmentProfile({
  drill_key: 'another_scenario', assessment_profile: 'pvt_fitts_core',
}).metricSet, 'PVT_FITTS');
const purePvtPaths = specs.PVT.groups.flatMap(([, cards]) => cards.map(card => card.path))
  .filter(path => !['simulator', 'simulator_system', 'weapon_type'].includes(path));
const fittsPaths = specs.PVT_FITTS.groups.flatMap(([, cards]) => cards.map(card => card.path));
assert.deepEqual(Array.from(fittsPaths), [
  'total_trials', 'round_duration_s', 'session_duration_s', 'completed_rounds',
  'rt_count', 'rt_mean_ms', 'rt_median_ms', 'rt_stdev_ms', 'rt_min_ms', 'rt_max_ms',
  'hits', 'hits_pct', 'omissions', 'misses', 'response_rate_pct', 'omission_rate_pct',
  'total_shots_fired', 'hit_accuracy_pct',
]);
const mixedPvtPaths = specs.DISCRIMINATION.groups.flatMap(([, cards]) => cards.map(card => card.path))
  .filter(path => path.startsWith('pvt.')).map(path => path.slice(4));
assert.deepEqual(Array.from(mixedPvtPaths), Array.from(purePvtPaths));
const root = new Element('div');
dashboard.mount(root);
function findCard(label) {
  function visit(node) {
    if (node.classList.contains('aar-card') &&
        node.children.some(child => child.classList.contains('aar-card-label') && child.textContent === label)) return node;
    for (const child of node.children) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  }
  return visit(root);
}

const roundRoot = new Element('div');
const roundCards = context.LabReview.AAR.Standardized.createMetricCards('PVT_FITTS', 'round');
roundCards.mount(roundRoot, { total_trials: 3, rt_mean_ms: 250, misses: 1, round_duration_s: 8 });
assert.match(roundRoot.textContent, /Total Trials/);
assert.match(roundRoot.textContent, /250 ms/);
assert.match(roundRoot.textContent, /Round Duration|Misses/);
assert.doesNotMatch(roundRoot.textContent, /Session Duration|Completed Rounds/);
assert.doesNotMatch(roundRoot.textContent, /Simulator|Target Presentations|rt_mean_ms/);
roundCards.update({ total_trials: 4, rt_mean_ms: 300 });
assert.match(roundRoot.textContent, /300 ms/);
roundCards.flashVisible();
assert.equal(roundRoot.children[0].children[0].children[1].children[0]
  .classList.contains('aar-card--flash'), true);
const runRoot = new Element('div');
context.LabReview.AAR.Standardized.createMetricCards('PVT_FITTS', 'run')
  .mount(runRoot, { session_duration_s: 18, completed_rounds: 2 });
assert.match(runRoot.textContent, /Session Duration|Completed Rounds/);
assert.doesNotMatch(runRoot.textContent, /Round Duration/);

vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../js/pages/live-intel.js'), 'utf8'),
  context, { filename: 'live-intel.js' });
const deriveAssessmentView = context.LabReview.LiveIntelPage.deriveAssessmentView;
const firstRound = { round_number: 1, attempt: 1, ended_at: null };
const finishedRound = { ...firstRound, ended_at: 5, ended_reason: 'completed' };
const secondRound = { round_number: 2, attempt: 1, ended_at: null };
assert.equal(deriveAssessmentView([], 'CAPTURING', null).showBetween, false);
assert.equal(deriveAssessmentView([firstRound], 'CAPTURING', null).current.round_number, 1);
assert.equal(deriveAssessmentView([finishedRound], 'CAPTURING', null).showBetween, true);
assert.equal(deriveAssessmentView([{ ...finishedRound, ended_reason: 'reset' }],
  'CAPTURING', null).showBetween, true);
assert.equal(deriveAssessmentView([finishedRound, secondRound], 'CAPTURING', null).current.round_number, 2);
assert.equal(deriveAssessmentView([finishedRound], 'RESULTS',
  { completed_rounds: 1, scoring_status: 'scored' }).showFinalCards, true);
assert.equal(deriveAssessmentView([finishedRound], 'RESULTS',
  { completed_rounds: 1, scoring_status: 'pending_stream_contract' }).showFinalCards, false);

dashboard.onScenarioStarted({ task_type: 'PVT', scenario_name: 'PVT example',
  simulator: 'V-AIMS', simulator_system: 'V_300' });
const pvtMetrics = {
  total_trials: 2, duration_s: 7, hits_pct: 50, omissions: 1,
  response_rate_pct: 50, omission_rate_pct: 50, rt_median_ms: 420,
  rt_mean_ms: 420, rt_stdev_ms: 12, rt_min_ms: 408, rt_max_ms: 432,
};
dashboard.onTrialUpdate({ task_type: 'PVT', running_metrics: pvtMetrics });
assert.match(root.textContent, /Hits \(%\)/);
assert.match(root.textContent, /50 %/);
assert.match(root.textContent, /420 ms/);
assert.match(root.textContent, /V-AIMS Simulator/);
assert.match(root.textContent, /V_300 Simulator System/);
assert.equal(findCard('Hits (%)').classList.contains('aar-card--flash'), true);
findCard('Hits (%)').classList.remove('aar-card--flash');
dashboard.onTrialUpdate({ task_type: 'PVT', running_metrics: pvtMetrics });
assert.equal(findCard('Hits (%)').classList.contains('aar-card--flash'), false);
dashboard.onTrialUpdate({ task_type: 'PVT', running_metrics: { ...pvtMetrics, hits_pct: 60 } });
assert.equal(findCard('Hits (%)').classList.contains('aar-card--flash'), true);

dashboard.onScenarioStarted({ task_type: 'PVT_FITTS', scenario_name: 'Fitts-PVT_Clay' });
dashboard.onMetricsReady(pvtMetrics);
assert.match(root.textContent, /Total Trials/);
assert.match(root.textContent, /Response Rate \(%\)/);
assert.match(root.textContent, /Median Response Time/);
assert.doesNotMatch(root.textContent, /Target Presentations|Mean Hit RT/);

dashboard.onScenarioStarted({ task_type: 'GNG', scenario_name: 'GNG example' });
dashboard.onContextUpdated({ scenario_name: 'Multicolor GNG', simulator: 'V-AIMS', simulator_system: 'V_300' });
dashboard.onWeaponInfo({ weapon_type: 'Pistol' });
assert.match(root.textContent, /Multicolor GNG/);
assert.match(root.textContent, /V-AIMS Simulator/);
assert.match(root.textContent, /Pistol Weapon Type/);
dashboard.onMetricsReady({ total_trials: 4, go_trials: 2, no_go_trials: 2,
  omissions: 1, commission_errors: 1, overall_accuracy_pct: 50, rt_n: 1 });
assert.match(root.textContent, /Commission Errors \(False Alarms\)/);
assert.match(root.textContent, /Overall Accuracy \(%\)/);
assert.match(root.textContent, /RT Count \(n\)/);
assert.match(root.textContent, /V_300 Simulator System/);
assert.equal(findCard('Total Trials').classList.contains('aar-card--flash'), false);
dashboard.onTrialUpdate({ task_type: 'GNG', running_metrics: { total_trials: 5 } });
assert.equal(findCard('Total Trials').classList.contains('aar-card--flash'), true);

dashboard.onScenarioStarted({ task_type: 'DISCRIMINATION', scenario_name: 'Discrimination example' });
dashboard.onMetricsReady({ duration_s: 12, pvt: pvtMetrics,
  discrimination: { total_trials: 4, threat_omission_rate_pct: 25, commission_error_rate_pct: 50 } });
assert.match(root.textContent, /Threat Omission Rate \(%\)/);
assert.match(root.textContent, /PVT Phase/);
assert.match(root.textContent, /PVT Duration \(s\)/);
assert.match(root.textContent, /PVT Response Rate \(%\)/);
assert.match(root.textContent, /PVT RT Std Dev \(ms\)/);
assert.match(root.textContent, /PVT Maximum RT \(ms\)/);
assert.match(root.textContent, /432 ms/);
assert.match(root.textContent, /25 %/);
assert.match(root.textContent, /— Simulator/);
dashboard.onTrialUpdate({ task_type: 'DISCRIMINATION', running_metrics: {
  discrimination: { total_trials: 5, threat_omission_rate_pct: 25, commission_error_rate_pct: 50 },
} });
assert.equal(findCard('Total Trials').classList.contains('aar-card--flash'), true);

dashboard.onScenarioStarted({ task_type: 'GENERAL' });
assert.match(root.innerHTML, /Unsupported drill type/);
assert.doesNotMatch(root.innerHTML, /General Drill/);

console.log('Three-drill AAR rendering checks passed');
