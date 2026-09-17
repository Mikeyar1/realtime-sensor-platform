// LabReview API — Message Type Constants (JavaScript)

// Client -> Server

export const MSG = Object.freeze({

  // Live mode control
  LIVE_ACTIVATE: 'live.activate',
  LIVE_DEACTIVATE: 'live.deactivate',

  // Streams
  STREAMS_GET_CATALOG: 'streams.get_catalog',
  STREAMS_SUBSCRIBE: 'streams.subscribe',
  STREAMS_UNSUBSCRIBE: 'streams.unsubscribe',

  // System
  SYSTEM_GET_STATE: 'system.get_state',
  SYSTEM_PING: 'system.ping',

  // Server -> Client

  // Session state
  SESSION_STATE: 'session.state',

  // Streams
  STREAMS_CATALOG: 'streams.catalog',
  STREAMS_SUBSCRIBED: 'streams.subscribed',
  STREAM_SAMPLE: 'stream.sample',
  STREAM_SAMPLES: 'stream.samples',
  STREAM_LOST: 'stream.lost',

  // System
  SYSTEM_PONG: 'system.pong',
  API_ERROR: 'api.error',

  // Scenario lifecycle
  SCENARIO_STARTED: 'session.scenario_started',
  SCENARIO_STOPPED: 'session.scenario_stopped',

  // Analysis results
  METRICS_READY: 'session.metrics_ready',

  // Session record save flow
  SESSION_SAVE_RECORD: 'session.save_record',
  SESSION_RECORD_SAVED: 'session.record_saved',

  // Real-time signals
  VIRTRA_EVENT: 'session.virtra_event',
  BIOSIGNAL_UPDATE: 'session.biosignal_update',
  TRIAL_UPDATE: 'session.trial_update',

  SPEECH_UPDATE: 'session.speech_update',


});

// Session States

export const STATE = Object.freeze({
  IDLE: 'idle',
  LISTENING: 'listening',
});

// Modes

export const MODE = Object.freeze({
  LIVE: 'live',
});

// Error Codes

export const ERR = Object.freeze({
  INTERNAL: 'INTERNAL_ERROR',
});

// Stream Status

export const STREAM_STATUS = Object.freeze({
  ACTIVE: 'active',
  INACTIVE: 'inactive',
  LOST: 'lost',
});

// LSL Stream Types

export const LSL_TYPE = Object.freeze({
  ECG: 'ECG',
  HR: 'HR',
  ACC: 'ACC',
  GYRO: 'GYRO',
  PPG: 'PPG',
  PPI: 'PPI',
  MAG_3D: 'MAG_3D',
  MAG_COMPASS: 'MAG_COMPASS',
  GAZE: 'Gaze',
  EVENT: 'Event',
  EYE_EVENTS: 'eye_events',
  IMU: 'IMU',
  INDICATOR: 'Indicator',
  SPEECH: 'speech_transcription',
  WEBCAM_FATIGUE: 'WebcamFatigue',
  POSE: 'Pose',
  VIRTRA: 'VirTraEvents',
});

// Streams excluded from dashboard visualisation
export const LSL_TYPES_EXCLUDED = new Set([
  LSL_TYPE.MAG_3D,
  LSL_TYPE.MAG_COMPASS,
  LSL_TYPE.INDICATOR,
  LSL_TYPE.POSE,
]);
