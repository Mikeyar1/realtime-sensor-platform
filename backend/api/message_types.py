# LabReview API — Message Type Constants (Python)

# Client -> Server 

# Live mode control
MSG_LIVE_ACTIVATE      = "live.activate"
MSG_LIVE_DEACTIVATE    = "live.deactivate"

# Streams
MSG_STREAMS_GET_CATALOG  = "streams.get_catalog"
MSG_STREAMS_SUBSCRIBE    = "streams.subscribe"
MSG_STREAMS_UNSUBSCRIBE  = "streams.unsubscribe"

# System
MSG_SYSTEM_GET_STATE   = "system.get_state"
MSG_SYSTEM_PING        = "system.ping"

# Server -> Client 

# Session state (single source of truth)
MSG_SESSION_STATE        = "session.state"

# Streams
MSG_STREAMS_CATALOG      = "streams.catalog"
MSG_STREAMS_SUBSCRIBED   = "streams.subscribed"
MSG_STREAM_SAMPLE        = "stream.sample"
MSG_STREAM_LOST          = "stream.lost"

# System
MSG_SYSTEM_PONG          = "system.pong"
MSG_API_ERROR            = "api.error"

# Live session scenario lifecycle (broadcast by LiveService)
MSG_SCENARIO_STARTED     = "session.scenario_started"
MSG_SCENARIO_STOPPED     = "session.scenario_stopped"

# Post-scenario metric computation result
MSG_METRICS_READY        = "session.metrics_ready"

# Database — participant management & session confirm/discard (Client → Server)
MSG_DB_GET_PARTICIPANTS    = "db.get_participants"
MSG_DB_CONFIRM_SESSION     = "db.confirm_session"     # {session_uuid, participant_name, notes}
MSG_DB_DISCARD_SESSION     = "db.discard_session"     # {session_uuid}
MSG_DB_UPDATE_PARTICIPANT  = "db.update_participant"  # {old_name, new_name}

# Database — server responses (Server → Client)
MSG_DB_PARTICIPANTS_LIST   = "db.participants_list"   # {names: [...]}
MSG_DB_SESSION_SAVED       = "db.session_saved"       # {session_uuid, participant_name, success}
MSG_DB_SESSION_DISCARDED   = "db.session_discarded"   # {session_uuid, success}
MSG_DB_PARTICIPANT_UPDATED = "db.participant_updated" # {old_name, new_name, success}

# Performance Review — Session History (Client -> Server)
MSG_PERF_QUERY  = "perf.query"   # {participant_name, drill_key?, limit?}
MSG_PERF_DELETE_SESSION = "perf.delete_session"  # {participant_name, session_uuid}
MSG_PERF_DELETE_ROUND = "perf.delete_round"  # {participant_name, round_id}
MSG_PERF_DELETE_PARTICIPANT = "perf.delete_participant"  # {participant_name}

# Performance Review — Session History (Server -> Client)
MSG_PERF_RESULT = "perf.result"  # {participant_name, drill_key, sessions: [...]}
MSG_PERF_DELETE_RESULT = "perf.delete_result"  # {operation, participant_name, success, deleted_count}


# Real-time VirTra event (broadcast by LiveService on every VirTra sample)
MSG_VIRTRA_EVENT         = "session.virtra_event"

# Real-time custom VirTra LSL event (script-driven stream, distinct from the
# native machine stream above)
MSG_CUSTOM_LSL_EVENT     = "session.custom_lsl_event"

MSG_BIOSIGNAL_UPDATE     = "session.biosignal_update"

# Real-time trial update — one message per completed trial, for all drill types.
MSG_TRIAL_UPDATE         = "session.trial_update"

# Speech transcription update (broadcast by LiveService)
MSG_SPEECH_UPDATE        = "session.speech_update"





# Session States 

STATE_IDLE       = "idle"
STATE_LISTENING  = "listening"   # live mode active


# Modes

MODE_LIVE = "live"


# Error Codes

ERR_INTERNAL = "INTERNAL_ERROR"


# Stream Status

STREAM_STATUS_ACTIVE   = "active"
STREAM_STATUS_INACTIVE = "inactive"
STREAM_STATUS_LOST     = "lost"


# LSL Stream Types

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
LSL_TYPE_VIRTRA          = "VirTraEvents"

# Custom, script-driven VirTra stream (distinct from the native machine
# stream above — note the space in the type string)
LSL_TYPE_VIRTRA_CUSTOM   = "VirTra Events"

# Streams excluded from the live chart dashboard (not visualisable as 2D charts)
LSL_TYPES_EXCLUDED = {LSL_TYPE_MAG_3D, LSL_TYPE_MAG_COMPASS, LSL_TYPE_INDICATOR, LSL_TYPE_POSE}
