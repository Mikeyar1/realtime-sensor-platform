# LabReplay API — Message Type Constants (Python)
# Mirror of api/message_types.js — keep both in sync.

# Client → Server

# Session (Replay mode only)
MSG_SESSION_LIST       = "session.list"
MSG_SESSION_LOAD       = "session.load"
MSG_SESSION_UNLOAD     = "session.unload"

# Playback (Replay mode only)
MSG_PLAYBACK_PLAY      = "playback.play"
MSG_PLAYBACK_PAUSE     = "playback.pause"
MSG_PLAYBACK_STOP      = "playback.stop"
MSG_PLAYBACK_SEEK      = "playback.seek"

# Live mode
MSG_LIVE_ACTIVATE      = "live.activate"
MSG_LIVE_DEACTIVATE    = "live.deactivate"

# Post-session SessionInfo query
MSG_SESSION_GET_INFO   = "session.get_info"

# Streams (both modes)
MSG_STREAMS_GET_CATALOG  = "streams.get_catalog"
MSG_STREAMS_SUBSCRIBE    = "streams.subscribe"
MSG_STREAMS_UNSUBSCRIBE  = "streams.unsubscribe"

# System
MSG_SYSTEM_GET_STATE   = "system.get_state"
MSG_SYSTEM_PING        = "system.ping"

# Server → Client

# Session state (single source of truth)
MSG_SESSION_STATE        = "session.state"
MSG_SESSION_LIST_RESULT  = "session.list.result"

# Streams
MSG_STREAMS_CATALOG      = "streams.catalog"
MSG_STREAMS_SUBSCRIBED   = "streams.subscribed"
MSG_STREAM_SAMPLE        = "stream.sample"
MSG_STREAM_LOST          = "stream.lost"

# System
MSG_SYSTEM_PONG          = "system.pong"
MSG_API_ERROR            = "api.error"

# Post-session SessionInfo result
MSG_SESSION_INFO_RESULT  = "session.info_result"


# Session States

STATE_IDLE       = "idle"
STATE_LOADING    = "loading"
STATE_PLAYING    = "playing"
STATE_PAUSED     = "paused"
STATE_STOPPED    = "stopped"
STATE_FINISHED   = "finished"
STATE_LISTENING  = "listening"  # live mode only

# Modes

MODE_REPLAY = "replay"
MODE_LIVE   = "live"

# Error Codes

ERR_SESSION_NOT_FOUND      = "SESSION_NOT_FOUND"
ERR_SESSION_INVALID        = "SESSION_INVALID"
ERR_SESSION_LOAD_FAILED    = "SESSION_LOAD_FAILED"
ERR_SEEK_NOT_IMPLEMENTED   = "SEEK_NOT_IMPLEMENTED"
ERR_WRONG_MODE             = "WRONG_MODE"
ERR_NO_SESSION             = "NO_SESSION"
ERR_INTERNAL               = "INTERNAL_ERROR"


# Stream Status

STREAM_STATUS_ACTIVE   = "active"
STREAM_STATUS_INACTIVE = "inactive"
STREAM_STATUS_LOST     = "lost"

# Stream Loss Reasons

LOST_REASON_FINISHED    = "publisher_finished"
LOST_REASON_DISCONNECT  = "lsl_disconnect"

# LSL Types

LSL_TYPE_ECG             = "ECG"
LSL_TYPE_HR              = "HR"
LSL_TYPE_ACC             = "ACC"
LSL_TYPE_GYRO            = "GYRO"
LSL_TYPE_PPG             = "PPG"
LSL_TYPE_PPI             = "PPI"
LSL_TYPE_MAG_3D          = "MAG_3D"
LSL_TYPE_MAG_COMPASS     = "MAG_COMPASS"
LSL_TYPE_GAZE            = "Gaze"
LSL_TYPE_EVENT           = "Event"
LSL_TYPE_EYE_EVENTS      = "eye_events"
LSL_TYPE_IMU             = "IMU"
LSL_TYPE_INDICATOR       = "Indicator"
LSL_TYPE_SPEECH          = "speech_transcription"
LSL_TYPE_WEBCAM_FATIGUE  = "WebcamFatigue"
LSL_TYPE_POSE            = "Pose"
LSL_TYPE_VIRTRA          = "VirTra"

# Streams excluded from dashboard (not visualisable as 2D charts)
LSL_TYPES_EXCLUDED = {LSL_TYPE_MAG_3D, LSL_TYPE_MAG_COMPASS, LSL_TYPE_INDICATOR, LSL_TYPE_POSE}
