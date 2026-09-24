/** Shared card layout and metric contract for the three supported drills. */
window.LabReview = window.LabReview || {};
LabReview.AAR = LabReview.AAR || {};

LabReview.AAR.Standardized = (function () {
  const metric = (path, label, unit, description, detailPath) =>
    ({ path, label, unit, description, detailPath });
  const context = (path, label, description) =>
    ({ path, label, unit: '', description, source: 'scenario' });
  const simulatorContext = [
    context('simulator', 'Simulator', 'Simulator software reported by VirTra.'),
    context('simulator_system', 'Simulator System', 'Simulator hardware or system reported by VirTra.'),
  ];

  const SPECS = {
    PVT: {
      label: 'PVT', construct: 'Psychomotor Vigilance Task · Sustained attention / vigilance', groups: [
        ['Session', [
          metric('total_trials', 'Total Trials', '', 'Valid stimulus presentations.'),
          metric('duration_s', 'Session Duration', 's', 'First valid trial activation through the final trial.'),
          ...simulatorContext,
        ]],
        ['Response', [
          metric('hits_pct', 'Hits', '%', 'Target hits divided by all valid trials.'),
          metric('omissions', 'Omissions', '', 'Trials with no shot in the response window.'),
          metric('response_rate_pct', 'Response Rate', '%', 'Trials with a shot, hit or miss, divided by all trials.'),
          metric('omission_rate_pct', 'Omission Rate', '%', 'Omissions divided by all trials.'),
        ]],
        ['Response Time', [
          metric('rt_median_ms', 'Median RT', 'ms', 'Median stimulus-to-shot latency for valid responses.'),
          metric('rt_mean_ms', 'Mean RT', 'ms', 'Mean latency for valid responses.'),
          metric('rt_stdev_ms', 'RT Std Dev', 'ms', 'Sample standard deviation of valid response times.'),
          metric('rt_min_ms', 'Minimum RT', 'ms', 'Smallest valid response time.'),
          metric('rt_max_ms', 'Maximum RT', 'ms', 'Largest valid response time.'),
        ]],
      ],
    },
    GNG: {
      label: 'Go/No-Go', construct: 'Go/No-Go Task · Response inhibition / inhibitory control', groups: [
        ['Session', [
          metric('total_trials', 'Total Trials', '', 'Go trials plus No-Go trials.'),
          metric('go_trials', 'Go Trials', '', 'Valid trials requiring a shooting response.'),
          metric('no_go_trials', 'No-Go Trials', '', 'Valid trials requiring the participant to withhold fire.'),
          ...simulatorContext,
        ]],
        ['Decision', [
          metric('hit_rate_pct', 'Hit Rate', '%', 'Correct Go hits divided by Go trials.'),
          metric('omissions', 'Omissions', '', 'Go trials with no shooting response.'),
          metric('commission_errors', 'Commission Errors (False Alarms)', '', 'No-Go trials with an attributable shooting response.'),
          metric('correct_rejection_rate_pct', 'Correct Rejection Rate', '%', 'No-Go trials with no shooting response divided by No-Go trials.'),
          metric('overall_accuracy_pct', 'Overall Accuracy', '%', 'Correct Go hits plus correct rejections divided by all trials.'),
        ]],
        ['Response Time', [
          metric('rt_median_ms', 'Median RT', 'ms', 'Median latency for correct Go hits.'),
          metric('rt_mean_ms', 'Mean RT', 'ms', 'Mean latency for correct Go hits.'),
          metric('rt_stdev_ms', 'RT Std Dev', 'ms', 'Sample standard deviation for correct Go hits.'),
          metric('rt_n', 'RT Count', 'n', 'Number of valid correct Go response times.'),
        ]],
      ],
    },
    DISCRIMINATION: {
      label: 'Behavioral Discrimination', construct: 'Discrimination / choice-response task · Perceptual / decision discrimination', groups: [
        ['Session', [
          metric('discrimination.total_trials', 'Total Trials', '', 'Threat and non-threat trials only.'),
          metric('duration_s', 'Session Duration', 's', 'First valid trial activation through the final trial, across both phases.'),
          ...simulatorContext,
        ]],
        ['Threat Discrimination', [
          metric('discrimination.threat_hit_rate_pct', 'Threat Hit Rate', '%', 'Correct threat hits divided by threat trials.'),
          metric('discrimination.threat_omission_rate_pct', 'Threat Omission Rate', '%', 'Threat trials with no shooting response divided by threat trials.'),
          metric('discrimination.commission_error_rate_pct', 'Commission Errors (False Alarms)', '%', 'Non-threat trials with an attributable shooting response divided by non-threat trials.'),
          metric('discrimination.correct_rejection_rate_pct', 'Correct Rejection Rate', '%', 'Correctly withheld responses divided by non-threat trials.'),
          metric('discrimination.discrimination_accuracy_pct', 'Discrimination Accuracy', '%', 'Correct threat hits plus correct rejections divided by threat and non-threat trials.'),
        ]],
        ['Threat Response Time', [
          metric('discrimination.threat_rt_median_ms', 'Median Threat RT', 'ms', 'Median response time for correct threat hits.'),
          metric('discrimination.threat_rt_mean_ms', 'Mean Threat RT', 'ms', 'Mean response time for correct threat hits.'),
          metric('discrimination.threat_rt_stdev_ms', 'Threat RT Std Dev', 'ms', 'Sample standard deviation for correct threat hits.'),
        ]],
        ['PVT Phase', [
          metric('pvt.total_trials', 'PVT Trials', '', 'Valid trials in the first phase.'),
          metric('pvt.hits_pct', 'PVT Hits', '%', 'PVT hits divided by PVT trials.'),
          metric('pvt.omissions', 'PVT Omissions', '', 'PVT trials with no shooting response.'),
          metric('pvt.rt_median_ms', 'PVT Median RT', 'ms', 'Median response time for valid PVT phase shots.'),
        ]],
      ],
    },
  };

  function readPath(object, path) {
    return path.split('.').reduce((value, part) => value == null ? undefined : value[part], object);
  }

  function display(value, unit) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const number = Number(value);
    const digits = Number.isInteger(number) ? 0 : 1;
    return `${number.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: 1 })}${unit && unit !== 'n' ? ` ${unit}` : ''}`;
  }

  function create(taskType) {
    const spec = SPECS[taskType];
    let container = null;
    let values = new Map();

    function mount(element) {
      container = element;
      reset();
    }

    function reset() {
      values = new Map();
      if (container) container.innerHTML = '<div class="aar-waiting"><div class="aar-waiting-label">Waiting for a supported drill</div><div class="aar-waiting-sub">PVT, Go/No-Go, or Behavioral Discrimination</div></div>';
    }

    function onScenarioStarted(payload) {
      if (!container) return;
      values = new Map();
      const panel = document.createElement('div');
      panel.className = 'aar-panel aar-standard';
      const header = document.createElement('div');
      header.className = 'aar-header';
      const title = document.createElement('div');
      title.className = 'aar-scenario-name';
      title.textContent = payload.scenario_name || spec.label;
      const badge = document.createElement('span');
      badge.className = 'aar-task-badge';
      badge.textContent = spec.label;
      header.append(title, badge);
      panel.appendChild(header);
      const construct = document.createElement('p');
      construct.className = 'aar-construct';
      construct.textContent = spec.construct;
      panel.appendChild(construct);

      for (const [name, cards] of spec.groups) {
        const section = document.createElement('section');
        section.className = 'aar-section';
        const heading = document.createElement('h3');
        heading.className = 'aar-section-label';
        heading.textContent = name;
        section.appendChild(heading);
        const grid = document.createElement('div');
        grid.className = 'aar-grid';
        for (const def of cards) {
          const card = document.createElement('div');
          card.className = def.source === 'scenario' ? 'aar-card aar-card--text' : 'aar-card';
          card.title = def.description;
          const value = document.createElement('div');
          value.className = 'aar-card-value';
          value.textContent = def.source === 'scenario'
            ? String(readPath(payload, def.path) || '').trim() || '—'
            : '—';
          const label = document.createElement('div');
          label.className = 'aar-card-label';
          label.textContent = `${def.label}${def.unit === '%' ? ' (%)' : def.unit === 'ms' ? ' (ms)' : def.unit === 's' ? ' (s)' : def.unit === 'n' ? ' (n)' : ''}`;
          const info = document.createElement('button');
          info.className = 'aar-info-btn';
          info.type = 'button';
          info.textContent = 'i';
          info.setAttribute('aria-label', `${def.label}: ${def.description}`);
          info.setAttribute('data-tip', def.description);
          card.append(value, label, info);
          grid.appendChild(card);
          if (def.source !== 'scenario') values.set(def.path, { value, unit: def.unit });
        }
        section.appendChild(grid);
        panel.appendChild(section);
      }
      container.replaceChildren(panel);
    }

    function update(metrics) {
      if (!metrics) return;
      for (const [path, entry] of values) entry.value.textContent = display(readPath(metrics, path), entry.unit);
    }

    function onTrialUpdate(message) { update(message?.running_metrics); }
    function unmount() { container = null; values = new Map(); }

    return {
      mount, reset, unmount, onScenarioStarted,
      onPvtUpdate: onTrialUpdate,
      onGngUpdate: onTrialUpdate,
      onDiscriminationUpdate: onTrialUpdate,
      onMetricsReady: update,
      onScenarioStopped() {},
      onSensorUpdate() {},
      onWeaponInfo() {},
    };
  }

  return { create, specs: SPECS };
})();
