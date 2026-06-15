/**
 * stream-registry.js — Stream → Chart Descriptor Map
 *
 * Only streams explicitly registered here will produce a chart.
 * Everything else is silently ignored.
 *
 * Add one stream at a time. Each entry requires a verified mapping
 * from a known DB table / LSL stream type to a chart descriptor.
 *
 * ── Registered so far ──
 *   Phase 1: HR (polar_h10_heart_rate → beats_per_minute)
 */

window.LabReplay = window.LabReplay || {};

LabReplay.StreamRegistry = (function () {

  // ── Chart Descriptors ──────────────────────────────────────────────────────

  const DESCRIPTORS = {

    // ── Phase 1: Cardiac ────────────────────────────────────────────────────

    HR: {
      tab:      'heart',
      cardType: 'hr-line',
      title:    'Heart Rate',
      subtitle: 'Polar Verity Sense',
      icon:     '❤',
      color:    '#C94444',
      rate:     1,
      channels: [{ key: 'heart_rate_bpm', label: 'HR', unit: 'BPM' }],
      yAxis:    { label: 'BPM', min: 40, max: 160 },
      priority: 1,
    },

    // ── Phase 2: Pupil Diameter ──────────────────────────────────────────────
    // Source: neon_gaze table, stream type=Gaze, 200 Hz
    // CH 2 → pupil_diameter_left_millimeters
    // CH 9 → pupil_diameter_right_millimeters
    PUPIL: {
      tab:      'gaze',
      cardType: 'pupil-line',
      title:    'Pupil Diameter',
      subtitle: 'Neon Eye Tracker',
      icon:     '◉',
      color:    '#22C4AC',
      rate:     200,
      channels: [
        { key: 'pupil_diameter_left_millimeters',  label: 'Left',  unit: 'mm' },
        { key: 'pupil_diameter_right_millimeters', label: 'Right', unit: 'mm' },
      ],
      yAxis:    { label: 'mm', min: 0, max: 8 },
      priority: 2,
    },

    // ── Phase 2b: Gaze Position Scatterplot ────────────────────────────────
    // Same source stream as PUPIL (neon_gaze / type=Gaze)
    // CH 0 → scene_x_pixels
    // CH 1 → scene_y_pixels
    GAZE_SCATTER: {
      tab:      'gaze',
      cardType: 'gaze-scatter',
      title:    'Gaze Position',
      subtitle: 'Neon Eye Tracker',
      icon:     '⊕',
      color:    '#4B9CF7',
      rate:     200,
      channels: [
        { key: 'scene_x_pixels', label: 'X', unit: 'px' },
        { key: 'scene_y_pixels', label: 'Y', unit: 'px' },
      ],
      sceneWidth:  1600,
      sceneHeight: 1200,
      priority: 3,
    },

    // ── Future phases (add here one at a time) ───────────────────────────────
    // ECG, HR Variability, Accelerometer, etc.
    // ── Phase 3: HRV – RMSSD ───────────────────────────────────────────
    // Source: lsl_unmapped_samples (HRV_Live stream, type=HRV, ~0.2 Hz)
    // Unmapped float array: [mean_hr, sdnn, rmssd, pnn50, sd1, sd2, ...]
    //   data[2] = rmssd_ms
    HRV_RMSSD: {
      tab:      'heart',
      cardType: 'hrv-rmssd',
      title:    'RMSSD',
      subtitle: 'HRV Live',
      icon:     '♥',
      color:    '#2ECC71',
      rate:     0.2,
      channels: [{ key: 'rmssd_ms', label: 'RMSSD', unit: 'ms' }],
      yAxis:    { label: 'ms', min: 40, max: 100 },
      priority: 4,
    },

    // ── Phase 4: Motion Intensity (FUSION) ───────────────────────────
    // Derived signal from TWO streams fused at runtime:
    //   'acc'  ← PhoneSensor_Linear Acceleration  [ax, ay, az] m/s²
    //   'gyro' ← PhoneSensor_Gyroscope            [gx, gy, gz] rad/s
    //
    // intensity = 0.4*(acc_mag/9) + 0.6*(gyro_mag/3)  → [0..1]
    //
    // fusion:true triggers multi-stream wiring in buildLandingPage.
    // streams[] is used by resolve() to tag each incoming stream.
    MOTION_INTENSITY: {
      fusion:   true,
      streams: [
        { tag: 'acc',  match: (n) => n === 'PhoneSensor_Linear Acceleration' },
        { tag: 'gyro', match: (n) => n === 'PhoneSensor_Gyroscope' },
      ],
      tab:      'motion',
      cardType: 'motion-strip',
      title:    'Motion Intensity',
      subtitle: 'Acc × Gyro Fusion',
      icon:     '◆',
      color:    '#F39C12',
      rate:     50,
      priority: 5,
    },

  };

  // ── Match Rules ────────────────────────────────────────────────────────────
  // Order matters — first match wins.
  // null descriptor = explicitly skip (do not chart).

  const MATCH_RULES = [

    // ── Phase 1: Registered streams ─────────────────────────────────────────
    // Polar Verity Sense HR — all streams from the polar_verity_sense_hr table.
    // Stream names can be:
    //   synthesised (no lsl_metadata table): "Polar Sense HR"
    //   device-specific (lsl_metadata table): "Polar Sense D851B82E_HR", "Polar Sense A2E01326_HR", etc.
    // All start with "polar sense" and have type HR.
    {
      test: (n, t) => {
        const nl = n.toLowerCase();
        const tl = t.toLowerCase();
        return (nl.startsWith('polar sense') && tl === 'hr')
          || nl === 'polar sense hr'
          || nl.includes('verity')
          || nl.includes('polar_verity_sense_hr');
      },
      descriptor: 'HR',
    },

    // Polar H10 HR — explicitly skip (polar_h10_heart_rate table, different device)
    {
      test: (n, t) => n.toLowerCase().includes('polar h10') && t.toLowerCase() === 'hr',
      descriptor: null,
    },

    // Generic HR fallback — any remaining stream_type=HR not caught above
    { test: (n, t) => t.toLowerCase() === 'hr', descriptor: 'HR' },


    // ── Explicitly skip — handled elsewhere or not yet registered ───────────
    { test: (n, t) => t === 'ECG',                   descriptor: null },
    { test: (n, t) => t === 'HRV',                   descriptor: 'HRV_RMSSD' },
    { test: (n, t) => t === 'PPG',                   descriptor: null },
    { test: (n, t) => t === 'PPI',                   descriptor: null },
    // Gaze stream → Pupil Diameter + Gaze Position scatterplot
    // Matches NeonCom007b_Neon Gaze (lsl_metadata) and synthesised "Neon Gaze".
    // Array means this one stream spawns TWO charts.
    {
      test: (n, t) => t === 'Gaze' || n.toLowerCase().includes('neon gaze'),
      descriptor: ['PUPIL', 'GAZE_SCATTER'],
    },

    { test: (n, t) => t === 'eye_events',             descriptor: null },
    { test: (n, t) => n.includes('eye_events'),       descriptor: null },
    { test: (n, t) => t === 'IMU',                   descriptor: null },
    { test: (n, t) => t === 'ACC',                   descriptor: null },
    { test: (n, t) => t === 'GYRO',                  descriptor: null },
    { test: (n, t) => t === 'MAG',                   descriptor: null },
    // ── Motion Intensity (fusion) — MUST come before the Motion catch-all ───
    // Two specific phone sensor streams fuse into one canvas strip chart.
    { test: (n) => n === 'PhoneSensor_Linear Acceleration', descriptor: 'MOTION_INTENSITY' },
    { test: (n) => n === 'PhoneSensor_Gyroscope',           descriptor: 'MOTION_INTENSITY' },

    { test: (n, t) => t === 'Motion',                descriptor: null },
    { test: (n, t) => t === 'Pose',                  descriptor: null },
    { test: (n, t) => t === 'VirTraEvents',          descriptor: null },
    { test: (n, t) => t === 'speech_transcription',  descriptor: null },
    { test: (n, t) => t === 'SessionData',           descriptor: null },
    { test: (n, t) => t === 'SessionInfo',           descriptor: null },
    { test: (n, t) => t === 'Indicator',             descriptor: null },
    { test: (n, t) => t === 'Event',                 descriptor: null },
    { test: (n, t) => t === 'Mixed',                 descriptor: null },
  ];

  /**
   * Look up the chart descriptor for a stream from the catalog.
   * Returns the descriptor object, or null if the stream should not be charted.
   *
   * @param {Object} streamMeta - { name, stream_type, ... } from the catalog
   * @returns {Object|null}
   */
  function resolve(streamMeta) {
    const name = streamMeta.name || '';
    const type = streamMeta.stream_type || streamMeta.type || '';

    console.log(`[StreamRegistry] resolve → name="${name}" type="${type}"`);

    for (const rule of MATCH_RULES) {
      if (rule.test(name, type)) {
        if (rule.descriptor === null) return null;

        // Array: stream should produce multiple charts
        if (Array.isArray(rule.descriptor)) {
          return rule.descriptor
            .map(k => {
              const desc = DESCRIPTORS[k];
              return desc ? { ...desc, _key: k } : null;
            })
            .filter(Boolean);
        }

        // Single descriptor
        const desc = DESCRIPTORS[rule.descriptor];
        if (!desc) return null;

        // Fusion descriptor: tag the resolved descriptor with which role this stream plays.
        // The streams[] matchers in the descriptor determine the tag.
        if (desc.fusion && Array.isArray(desc.streams)) {
          const streamConfig = desc.streams.find(sc => sc.match(name, type));
          if (!streamConfig) return null;  // matches the rule but not any fusion role
          return { ...desc, _key: rule.descriptor, _fusionTag: streamConfig.tag };
        }

        return { ...desc, _key: rule.descriptor };
      }
    }

    // Unrecognized stream — not yet registered. Silent skip.
    return null;
  }

  function getAll() {
    return { ...DESCRIPTORS };
  }

  return { resolve, getAll };
})();
