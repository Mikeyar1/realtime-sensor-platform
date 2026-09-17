/**
 * drill_metrics.js — Central metric card registry.
 *
 * Defines the behavioral and physiological metric cards for every drill type.
 * The MetricCardRenderer reads this at runtime so adding/removing a metric
 * for a given drill requires only editing this file — no display JS changes.
 *
 * Card schema:
 *   id        {string}  Unique card identifier (used for DOM id + update targeting)
 *   label     {string}  Human-readable card label
 *   unit      {string}  Unit suffix displayed next to the value ('' for unitless)
 *   format    {string}  'number' | 'percent' | 'ms' | 'time' | 'decimal'
 *   source    {string}  'trial' | 'biosignal' | 'session' (which update event populates it)
 *   field     {string}  Key name inside the update payload object
 *   size      {string}  'lg' | 'md' | 'sm'  — controls card width in the grid
 *   showWhen  {string?} Optional: 'always' (default) | 'nonzero' (hide when value === 0)
 *
 * Physiological cards (source: 'biosignal') are always rendered in a separate
 * visual section with a teal accent border, regardless of drill type.
 */

window.LabReview = window.LabReview || {};

LabReview.DrillMetrics = {

  // ── Physiological cards — shown for ALL drills ─────────────────────────────
  // These live in a separate "Physiological" section, not the behavioral grid.
  //
  // Field paths use dot-notation to reach nested values in the biosignal payload.
  // Backend payload shape (session.biosignal_update):
  //   { hr: { mean, latest, n }, hrv: { rmssd_ms, n_intervals }, pupil: { mean_mm, left_mean_mm, right_mean_mm } }
  physiological: [
    {
      id: 'heart_rate',
      label: 'Heart Rate',
      unit: 'bpm',
      format: 'number',
      source: 'biosignal',
      field: 'hr.mean',        // hr.mean from BiosignalRollingMonitor
      size: 'md',
    },
    {
      id: 'hrv_rmssd',
      label: 'HRV (RMSSD)',
      unit: 'ms',
      format: 'ms',
      source: 'biosignal',
      field: 'hrv.rmssd_ms',   // hrv.rmssd_ms from BiosignalRollingMonitor
      size: 'md',
    },
    {
      id: 'pupil_diameter',
      label: 'Pupil Diameter',
      unit: 'mm',
      format: 'decimal',
      source: 'biosignal',
      field: 'pupil.mean_mm',  // pupil.mean_mm from BiosignalRollingMonitor
      size: 'md',
    },
  ],

  // ── Behavioral metric cards — per drill type ────────────────────────────────

  pvt: [
    {
      id: 'rt_mean',
      label: 'Mean RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'mean_rt_ms',
      size: 'lg',
    },
    {
      id: 'rt_median',
      label: 'Median RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'median_rt_ms',
      size: 'md',
    },
    {
      id: 'rt_sd',
      label: 'RT Std Dev',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'sd_rt_ms',
      size: 'md',
    },
    {
      id: 'omissions',
      label: 'Omissions',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'omissions',
      size: 'md',
    },
    {
      id: 'hits',
      label: 'Hits',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'hits',
      size: 'sm',
    },
    {
      id: 'trials_completed',
      label: 'Trials',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'total_trials',
      size: 'sm',
    },
  ],

  // Go / No-Go (inhibition_assessment)
  inhibition_assessment: [
    {
      id: 'hit_rate',
      label: 'Hit Rate',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'hit_rate',
      size: 'lg',
    },
    {
      id: 'false_alarm_rate',
      label: 'False Alarm',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'fa_rate',
      size: 'md',
    },
    {
      id: 'correct_rejection_rate',
      label: 'Correct Reject',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'cr_rate',
      size: 'md',
    },
    {
      id: 'mean_hit_rt',
      label: 'Hit RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'mean_rt_ms',
      size: 'sm',
    },
  ],

  // Marksmanship / shoot-no-shoot (general VirTra scenarios)
  general: [
    {
      id: 'hit_rate',
      label: 'Hit Rate',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'hit_rate',
      size: 'lg',
    },
    {
      id: 'miss_rate',
      label: 'Miss Rate',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'miss_rate',
      size: 'md',
    },
    {
      id: 'rt_mean',
      label: 'Mean RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'mean_rt_ms',
      size: 'md',
    },
    {
      id: 'shot_count',
      label: 'Shots',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'shots',
      size: 'sm',
    },
    {
      id: 'commission_errors',
      label: 'Commission Err.',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'commission_errors',
      size: 'sm',
      showWhen: 'nonzero',
    },
  ],

  marksmanship: [
    {
      id: 'hit_rate',
      label: 'Hit Rate',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'hit_rate',
      size: 'lg',
    },
    {
      id: 'shot_placement_mean',
      label: 'Placement Error',
      unit: 'px',
      format: 'number',
      source: 'trial',
      field: 'mean_placement_px',
      size: 'md',
    },
    {
      id: 'shot_count',
      label: 'Shots',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'shots',
      size: 'sm',
    },
    {
      id: 'rt_mean',
      label: 'Mean RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'mean_rt_ms',
      size: 'sm',
    },
  ],

  behavioral_discrimination: [
    {
      id: 'hit_rate',
      label: 'Hit Rate',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'hit_rate',
      size: 'lg',
    },
    {
      id: 'false_alarm_rate',
      label: 'False Alarm',
      unit: '%',
      format: 'percent',
      source: 'trial',
      field: 'fa_rate',
      size: 'md',
    },
    {
      id: 'rt_mean',
      label: 'Mean RT',
      unit: 'ms',
      format: 'ms',
      source: 'trial',
      field: 'mean_rt_ms',
      size: 'md',
    },
    {
      id: 'commission_errors',
      label: 'Commission Err.',
      unit: '',
      format: 'number',
      source: 'trial',
      field: 'commission_errors',
      size: 'sm',
      showWhen: 'nonzero',
    },
  ],

};
