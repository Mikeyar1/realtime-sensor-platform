const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = { console };
context.window = context;
context.LabReview = { AAR: {} };
vm.createContext(context);

const frontend = path.resolve(__dirname, '..');
vm.runInContext(fs.readFileSync(path.join(frontend, 'js/features/aar/standardized.js'), 'utf8'), context);
const source = fs.readFileSync(path.join(frontend, 'js/pages/perf-review.js'), 'utf8')
  .replace('return { init };',
    'return { init, _detailCard, _roundSettingCards, _roundMetricSections, _readBiosignal };');
vm.runInContext(source, context);

const page = context.LabReview.PerfReviewPage;
const round = {
  drill_key: 'fitts_pvt_clay',
  settings: {
    active_distance_meters: 10,
    active_visibility_duration_seconds: 2,
    active_targets_per_round: 15,
    preset: { name: 'Default', full_path: '' },
  },
  metrics: {
    total_trials: 15,
    round_duration_s: 32.21754090000013,
    hits_pct: 26.666666666666668,
  },
};

const settings = page._roundSettingCards(round);
assert.match(settings, /Distance<\/span><strong>10 m<\/strong>/);
assert.doesNotMatch(settings, /\[object Object\]|full path|Preset<\/span>/);

const metrics = page._roundMetricSections(round);
assert.match(metrics, /Session<\/h3>/);
assert.match(metrics, /Response Time<\/h3>/);
assert.match(metrics, /Target Engagement and Marksmanship<\/h3>/);
assert.match(metrics, /Round Duration<\/span><strong>32\.2 s<\/strong>/);
assert.match(metrics, /Hits<\/span><strong>26\.7 %<\/strong>/);
assert.doesNotMatch(metrics, /32\.2175409|26\.666666|Session Duration/);

const reusedProfile = { ...round, drill_key: 'new_target_scenario',
  assessment_profile: 'pvt_fitts_core' };
assert.equal(page._roundSettingCards(reusedProfile), settings);
assert.equal(page._roundMetricSections(reusedProfile), metrics);

assert.match(page._detailCard('Unavailable', null, 'ms'), /<strong>—<\/strong>/);
assert.equal(page._readBiosignal({ pupil_l_mean_mm: null, pupil_r_mean_mm: null }, 'pupil_mean_mm'), null);
console.log('Longitudinal round detail formatting checks passed');
