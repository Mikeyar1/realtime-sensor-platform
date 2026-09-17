/**
 * js/config/stream_catalog.js — LSL stream display configuration.
 *
 * Central config for how LabReview recognises and labels incoming LSL streams
 * in the sidebar "Connected Streams" panel.
 *
 * HOW TO USE
 * ──────────
 *  • To show a new stream in the sidebar:
 *      Add an entry with namePattern and/or typePattern + a label.
 *
 *  • To suppress a stream from the sidebar (backend still receives it):
 *      Add an entry with display: false.
 *
 *  • To rename how an existing stream appears:
 *      Update the label field of its entry below.
 *
 * MATCHING RULES
 * ──────────────
 *  A catalog entry from the backend matches a descriptor if:
 *    namePattern matches the LSL stream NAME  (e.g. "V_300")
 *    OR
 *    typePattern matches the LSL stream TYPE  (e.g. "VirTraEvents")
 *
 *  The first matching descriptor wins (order matters).
 *  Streams with no matching descriptor are silently ignored in the sidebar.
 *
 * SCHEMA
 * ──────
 *  {
 *    namePattern  {RegExp}   Matched against the LSL stream name (case-insensitive).
 *    typePattern  {RegExp?}  Matched against the LSL stream type. Optional.
 *    label        {string}   Human-readable label shown in the sidebar.
 *    display      {boolean}  true = show in sidebar (default). false = hide from sidebar.
 *  }
 */

window.LabReview = window.LabReview || {};

LabReview.StreamCatalog = [

  // ── Sensors & hardware ────────────────────────────────────────────────────

  {
    namePattern: /polar/i,
    label:       'Polar HR Monitor',
    display:     true,
  },
  {
    namePattern: /neon|pupil/i,
    label:       'Neon Pupil Labs',
    display:     true,
  },
  {
    namePattern: /biopac|gsr/i,
    label:       'BioPac GSR',
    display:     true,
  },
  {
    namePattern: /accel|imu/i,
    label:       'IMU / Accelerometer',
    display:     true,
  },
  {
    namePattern: /eeg/i,
    label:       'EEG Headset',
    display:     true,
  },
  {
    namePattern: /resp/i,
    label:       'Respiration Belt',
    display:     true,
  },

  // ── VirTra simulator ──────────────────────────────────────────────────────
  // Matched by EITHER name (V_300, VirTra*) OR stream type (VirTraEvents).
  // This covers the V-300 hardware whose LSL stream name is literally "V_300".

  {
    namePattern: /v_?300|virtra/i,
    typePattern: /virtraevents/i,
    label:       'VirTra Simulator (V-300)',
    display:     true,
  },

  // ── Participant metadata ───────────────────────────────────────────────────

  {
    namePattern: /participant/i,
    label:       'Participant Info',
    display:     true,
  },

  // ── VirTra SessionData streams ────────────────────────────────────────────
  // These streams are produced by the V-300 system alongside VirTraEvents.
  // The backend ingests them, but they carry no metrics relevant to the UI.
  // display: false suppresses them from the sidebar without blocking ingestion.

  {
    namePattern: /TMARSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'TMAR Session Provider',
    display:     false,
  },
  {
    namePattern: /DeviceServiceProvider/i,
    typePattern: /SessionData/i,
    label:       'Device Service Provider',
    display:     false,
  },
  {
    namePattern: /QuestionnaireResultSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'Questionnaire Result Provider',
    display:     false,
  },
  {
    namePattern: /MetronomeSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'Metronome Session Provider',
    display:     false,
  },
  {
    namePattern: /AimTraceCollectorSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'Aim Trace Collector',
    display:     false,
  },
  {
    namePattern: /TraineeNoteSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'Trainee Note Provider',
    display:     false,
  },
  {
    namePattern: /ProTimerSessionProvider/i,
    typePattern: /SessionData/i,
    label:       'ProTimer Session Provider',
    display:     false,
  },

  // ── Catch-all for any remaining SessionData stream ─────────────────────────
  // If a new SessionData stream appears that has no explicit entry above,
  // suppress it from the sidebar by default.

  {
    namePattern: /./,           // matches any name
    typePattern: /SessionData/i,
    label:       'SessionData Stream',
    display:     false,
  },

];
