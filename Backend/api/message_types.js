/**
 * LabReplay API — Message Type Constants (JavaScript)
 * Mirror of api/message_types.py — keep both in sync.
 *
 * Usage:
 *   import { MSG, STATE, MODE, ERR } from '/api/message_types.js';
 *   ws.send(JSON.stringify({ type: MSG.SESSION_LOAD, path: "..." }));
 */

// ── Client → Server ──────────────────────────────────────────────────────────

export const MSG = Object.freeze({

  // Session (Replay mode only)
  SESSION_LIST:         'session.list',
  SESSION_LOAD:         'session.load',
  SESSION_UNLOAD:       'session.unload',

  // Playback (Replay mode only)
  PLAYBACK_PLAY:        'playback.play',
  PLAYBACK_PAUSE:       'playback.pause',
  PLAYBACK_STOP:        'playback.stop',
  PLAYBACK_SEEK:        'playback.seek',

  // Live mode
  LIVE_ACTIVATE:        'live.activate',
  LIVE_DEACTIVATE:      'live.deactivate',

  // Post-session SessionInfo query
  SESSION_GET_INFO:     'session.get_info',

  // Streams (both modes)
  STREAMS_GET_CATALOG:  'streams.get_catalog',
  STREAMS_SUBSCRIBE:    'streams.subscribe',
  STREAMS_UNSUBSCRIBE:  'streams.unsubscribe',

  // System
  SYSTEM_GET_STATE:     'system.get_state',
  SYSTEM_PING:          'system.ping',

  // ── Server → Client ────────────────────────────────────────────────────────

  SESSION_STATE:        'session.state',
  SESSION_LIST_RESULT:  'session.list.result',

  STREAMS_CATALOG:      'streams.catalog',
  STREAMS_SUBSCRIBED:   'streams.subscribed',
  STREAM_SAMPLE:        'stream.sample',
  STREAM_LOST:          'stream.lost',

  SYSTEM_PONG:          'system.pong',
  API_ERROR:            'api.error',

  // Post-session SessionInfo result
  SESSION_INFO_RESULT:  'session.info_result',
});

// ── Session States ────────────────────────────────────────────────────────────

export const STATE = Object.freeze({
  IDLE:      'idle',
  LOADING:   'loading',
  PLAYING:   'playing',
  PAUSED:    'paused',
  STOPPED:   'stopped',
  FINISHED:  'finished',
  LISTENING: 'listening',  // live mode only
});

// ── Modes ─────────────────────────────────────────────────────────────────────

export const MODE = Object.freeze({
  REPLAY: 'replay',
  LIVE:   'live',
});

// ── Error Codes ───────────────────────────────────────────────────────────────

export const ERR = Object.freeze({
  SESSION_NOT_FOUND:    'SESSION_NOT_FOUND',
  SESSION_INVALID:      'SESSION_INVALID',
  SESSION_LOAD_FAILED:  'SESSION_LOAD_FAILED',
  SEEK_NOT_IMPLEMENTED: 'SEEK_NOT_IMPLEMENTED',
  WRONG_MODE:           'WRONG_MODE',
  NO_SESSION:           'NO_SESSION',
  INTERNAL:             'INTERNAL_ERROR',
});

// ── Stream Status ─────────────────────────────────────────────────────────────

export const STREAM_STATUS = Object.freeze({
  ACTIVE:   'active',
  INACTIVE: 'inactive',
  LOST:     'lost',
});

// ── LSL Stream Types ──────────────────────────────────────────────────────────

export const LSL_TYPE = Object.freeze({
  ECG:                'ECG',
  HR:                 'HR',
  ACC:                'ACC',
  GYRO:               'GYRO',
  PPG:                'PPG',
  PPI:                'PPI',
  MAG_3D:             'MAG_3D',
  MAG_COMPASS:        'MAG_COMPASS',
  GAZE:               'Gaze',
  EVENT:              'Event',
  EYE_EVENTS:         'eye_events',
  IMU:                'IMU',
  INDICATOR:          'Indicator',
  SPEECH:             'speech_transcription',
  WEBCAM_FATIGUE:     'WebcamFatigue',
  POSE:               'Pose',
  VIRTRA:             'VirTra',
});

// Streams excluded from dashboard visualisation
export const LSL_TYPES_EXCLUDED = new Set([
  LSL_TYPE.MAG_3D,
  LSL_TYPE.MAG_COMPASS,
  LSL_TYPE.INDICATOR,
  LSL_TYPE.POSE,
]);
