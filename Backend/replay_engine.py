"""
replay_engine.py

Wraps Total Recall's classes as an in-process service.
The WebSocket bridge imports this and calls:

    engine = ReplayEngine(db_scan_dir, total_recall_dir)
    files  = engine.scan()                  # list .db files
    info   = engine.load(db_path)           # start streaming
    engine.stop()                           # stop streaming

No subprocess, no config.toml mutation.
Total Recall code is imported directly into this process.
"""

import datetime
import glob
import os
import sqlite3
import sys
import threading
import time
from typing import Callable, List, Optional


class ReplayEngine:
    """
    Controls Total Recall as an in-process replay engine.
    """

    def __init__(self, db_scan_dir: str, total_recall_dir: str):
        self._db_scan_dir = os.path.abspath(db_scan_dir)
        self._tr_dir = os.path.abspath(total_recall_dir)
        self._publishers: list = []
        self._lock = threading.Lock()
        self._loaded_db: str = ""
        self._session_info: dict = {}
        self._load_start_time: float = 0.0   # monotonic clock when load() began playing
        self._db_start_dt = None
        self._db_end_dt   = None
        self._pause_wall_utc = None           # set by pause_publishers(), cleared by resume_publishers()

        # Inject Total Recall's directory into sys.path so we can import it.
        # We do this lazily in load() to avoid polluting the path until needed.
        self._tr_imported = False

    # ── Public API ──────────────────────────────────────────────────────────

    def scan(self) -> List[dict]:
        """
        Scan db_scan_dir for .db / .sqlite files.
        Returns a list of dicts: { name, path, size_mb, valid, invalid_reason? }
        """
        files = []
        if not os.path.isdir(self._db_scan_dir):
            print(f"[ReplayEngine] Scan dir does not exist: {self._db_scan_dir}")
            return files

        for ext in ("*.db", "*.sqlite", "*.sqlite3"):
            pattern = os.path.join(self._db_scan_dir, "**", ext)
            for path in glob.glob(pattern, recursive=True):
                try:
                    size_mb = round(os.path.getsize(path) / (1024 * 1024), 1)
                    abs_path = os.path.abspath(path).replace("\\", "/")
                    valid, reason = self._validate_db(path)
                    entry = {
                        "name":    os.path.basename(path),
                        "path":    abs_path,
                        "size_mb": size_mb,
                        "valid":   valid,
                    }
                    if not valid:
                        entry["invalid_reason"] = reason
                    files.append(entry)
                except OSError:
                    pass

        print(f"[ReplayEngine] Found {len(files)} DB files in {self._db_scan_dir}")
        return files

    def preload(self, db_path: str) -> dict:
        """
        Full preparation without starting threads.
        Reads metadata AND fetches SQL records for every publisher.
        This is the slow step (O(n_streams) SQLite queries).
        Call start_publishers() after this — it is instant.
        """
        db_path = os.path.abspath(db_path)
        if not os.path.isfile(db_path):
            raise FileNotFoundError(f"DB file not found: {db_path}")

        self.stop()  # kill any running session
        print(f"[ReplayEngine] Preloading: {db_path}")
        self._loaded_db = db_path

        self._ensure_tr_imported()
        metadata_list, start_dt, end_dt = self._read_metadata(db_path)

        if not metadata_list:
            raise RuntimeError(f"No LSL metadata found in: {db_path}")

        # Store datetimes so start_publishers() can compute a fresh time_delta
        self._db_start_dt = start_dt
        self._db_end_dt   = end_dt

        from lsl_replay_publisher import LslReplayPublisher

        # Placeholder time_delta — will be recomputed at start_publishers() time
        placeholder_delta = datetime.datetime.now(tz=datetime.timezone.utc) - \
                            start_dt.replace(tzinfo=datetime.timezone.utc)

        with self._lock:
            self._publishers = []
            for meta in metadata_list:
                pub = LslReplayPublisher(
                    lsl_metadata=meta,
                    sqlite_file=db_path,
                    start_datetime=start_dt.isoformat(),
                    end_datetime=end_dt.isoformat(),
                    time_delta=placeholder_delta,
                )
                pub.fetch_sql_records()   # slow — one SQLite query per stream
                self._publishers.append(pub)

        self._session_info = {
            "db_path":          db_path,
            "session_id":       os.path.splitext(os.path.basename(db_path))[0],
            "stream_count":     len(metadata_list),
            "streams":          [m.name for m in metadata_list],
            "start":            start_dt.isoformat(),
            "end":              end_dt.isoformat(),
            "duration_seconds": (end_dt - start_dt).total_seconds(),
        }
        print(f"[ReplayEngine] Prepared {len(self._publishers)} publisher(s) — ready to start")
        return self._session_info

    def start_publishers(self) -> dict:
        """
        Start the pre-prepared publisher threads. Must call preload() first.
        Recomputes time_delta to NOW so playback timing is accurate.
        This is instant — threads start in microseconds.
        """
        with self._lock:
            if not self._publishers:
                raise RuntimeError("No publishers prepared. Call preload() first.")

            # Fresh time_delta: accurate to the moment the user clicked Play
            now_utc       = datetime.datetime.now(tz=datetime.timezone.utc)
            db_start_utc  = self._db_start_dt.replace(tzinfo=datetime.timezone.utc)
            fresh_delta   = now_utc - db_start_utc

            for pub in self._publishers:
                try:
                    pub.time_delta = fresh_delta  # update before starting
                except AttributeError:
                    pass  # immutable on this TR version — accept small offset
                pub.start()

        self._load_start_time = time.monotonic()
        print(f"[ReplayEngine] Started {len(self._publishers)} stream(s)")
        return self._session_info

    def is_prepared(self) -> bool:
        """True if publishers are ready (SQL fetched) but threads not yet started."""
        with self._lock:
            return bool(self._publishers) and not any(p.is_alive() for p in self._publishers)

    def get_db_start_unix(self) -> float:
        """Unix timestamp of session t=0 (the original recording start)."""
        if self._db_start_dt is None:
            return 0.0
        return self._db_start_dt.replace(tzinfo=datetime.timezone.utc).timestamp()

    def load(self, db_path: str) -> dict:
        """Convenience: preload + start_publishers in one call."""
        self.preload(db_path)
        return self.start_publishers()

    def stop(self):
        """Stop all running publisher threads."""
        with self._lock:
            for pub in self._publishers:
                if pub.is_alive():
                    pub.stop()
            # Wait for all threads to finish
            for pub in self._publishers:
                if pub.is_alive():
                    pub.join(timeout=3.0)
            self._publishers = []

        if self._loaded_db:
            print(f"[ReplayEngine] Stopped replay of {os.path.basename(self._loaded_db)}")
        self._loaded_db = ""

    def is_running(self) -> bool:
        with self._lock:
            return any(p.is_alive() for p in self._publishers)

    def pause_publishers(self):
        """
        Record the wall-clock moment of pause.
        Publishers keep running; WsBridge gates their LSL output.
        We store the timestamp so resume_publishers() can compensate time_delta.
        """
        self._pause_wall_utc = datetime.datetime.now(tz=datetime.timezone.utc)

    def resume_publishers(self):
        """
        After a pause, add the pause duration to every publisher's time_delta.
        This keeps `normalized_datetime = now_utc - time_delta` frozen at the
        exact session moment where we paused — no catch-up burst on resume.
        """
        if not self._pause_wall_utc:
            return  # wasn't paused via pause_publishers()

        pause_duration = datetime.datetime.now(tz=datetime.timezone.utc) - self._pause_wall_utc
        self._pause_wall_utc = None

        with self._lock:
            for pub in self._publishers:
                try:
                    pub.time_delta = pub.time_delta + pause_duration
                except Exception:
                    pass  # publisher already stopped, ignore

    def get_elapsed(self) -> float:
        """Seconds since load() was called. Returns 0 if not running."""
        if not self._load_start_time or not self._loaded_db:
            return 0.0
        return time.monotonic() - self._load_start_time

    def get_state(self) -> str:
        """Returns idle | playing | finished."""
        if not self._loaded_db:
            return 'idle'
        if self.is_running():
            return 'playing'
        return 'finished'

    def get_session_info(self) -> dict:
        return self._session_info.copy()

    # Known stream tables produced by Total Recall / python-lsl-logger.
    # Any of these means it's a valid recording.
    _KNOWN_STREAM_TABLES = frozenset({
        # Polar H10
        'polar_h10_ecg', 'polar_h10_heart_rate', 'polar_h10_ppi', 'polar_h10_acceleration',
        # Polar Verity Sense
        'polar_verity_sense_hr', 'polar_verity_sense_ppg',
        'polar_verity_sense_acceleration', 'polar_verity_sense_gyro',
        'polar_verity_sense_mag_3d', 'polar_verity_sense_mag_compass',
        'polar_verity_sense_ppi',
        # Neon
        'neon_gaze', 'neon_events', 'neon_middleware_eye_events',
        'neon_middleware_speech_transcription', 'neon_middleware_imu',
        'neon_middleware_scene_video_frame',
        # Other
        'pose3d_stereo',
        'lsl_unmapped_samples',
    })

    @staticmethod
    def _validate_db(path: str) -> tuple:
        """Check if a .db file is a valid Total Recall database.
        Returns (valid: bool, reason: str).

        Accepts two schemas:
          Modern (has lsl_metadata)          → valid
          Older  (no lsl_metadata but has recognisable stream tables) → valid
        """
        try:
            conn = sqlite3.connect(path)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in c.fetchall()}
            conn.close()

            if 'lsl_metadata' in tables:
                return True, ''

            # Older schema: check for any known stream table
            if tables & ReplayEngine._KNOWN_STREAM_TABLES:
                return True, ''

            return False, 'No lsl_metadata or recognisable stream tables found'
        except Exception as e:
            return False, str(e)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _ensure_tr_imported(self):
        """Add Total Recall's directory to sys.path so its modules can be imported."""
        if self._tr_imported:
            return

        tr_dir = self._tr_dir
        if not os.path.isdir(tr_dir):
            raise RuntimeError(f"Total Recall directory not found: {tr_dir}")

        if tr_dir not in sys.path:
            sys.path.insert(0, tr_dir)

        self._tr_imported = True
        print(f"[ReplayEngine] Total Recall imported from {tr_dir}")

    def _read_metadata(self, db_path: str):
        """
        Query the lsl_metadata table to get all streams and the overall
        time range. Returns (metadata_list, start_datetime, end_datetime).

        For older databases that lack the lsl_metadata table, synthesise
        metadata rows from the known stream tables that are present.
        """
        from lsl_metadata import LslMetadata

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # ── Check if lsl_metadata exists ──────────────────────────────────
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lsl_metadata'")
        has_meta_table = cursor.fetchone() is not None

        if not has_meta_table:
            return self._synthesise_metadata(conn, cursor, db_path, LslMetadata)

        # Get the full time range from the DB (90 day window like original)
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        range_start = (now - datetime.timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%S")
        range_end   = now.strftime("%Y-%m-%dT%H:%M:%S")

        sql = """
            SELECT lsl_metadata_id, datetime_utc, datetime_local,
                   unix_timestamp_seconds, target_table_name, name, type,
                   channels, sampling_rate_hz, source_id, channel_format,
                   session_id, hostname, desc
            FROM lsl_metadata
            WHERE datetime_utc BETWEEN ? AND ?
            ORDER BY datetime_utc ASC
        """
        cursor.execute(sql, (range_start, range_end))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return [], None, None

        metadata_list = []
        earliest = None
        latest = None

        for row in rows:
            # Handle channel_format stored as int or string
            try:
                ch_fmt = LslMetadata.get_channel_format(int(row[10]))
            except (ValueError, TypeError):
                ch_fmt = row[10]

            meta = LslMetadata(
                lsl_metadata_id=row[0],
                datetime_utc=row[1],
                datetime_local=row[2],
                unix_timestamp_seconds=row[3],
                target_table_name=row[4],
                name=row[5],
                type=row[6],
                channels=row[7],
                sample_rate_hz=row[8],
                source_id=row[9],
                channel_format=ch_fmt,
                session_id=row[11],
                hostname=row[12],
                desc=row[13],
            )
            metadata_list.append(meta)

            # Track time range
            dt = self._parse_dt(row[1])
            if earliest is None or dt < earliest:
                earliest = dt
            if latest is None or dt > latest:
                latest = dt

        # ── Stitch reconnection segments ─────────────────────────────────────
        # A device that disconnects and reconnects during recording creates a
        # new lsl_metadata row with the same name and target_table_name but a
        # different lsl_metadata_id.  Publishing two LSL outlets with the same
        # name simultaneously causes the InletManager to lose the first one.
        #
        # Instead: group entries by (name, target_table_name).  Pick the entry
        # with the most rows as the "primary" publisher; all other IDs go into
        # extra_segment_ids.  SqlRecordFetcher will query all IDs in one shot,
        # ordered chronologically — giving the complete continuous timeline.
        conn2 = sqlite3.connect(db_path)
        cur2  = conn2.cursor()

        # Build groups: key -> list of (meta, row_count)
        groups: dict = {}
        for meta in metadata_list:
            key = (meta.name, meta.target_table_name)
            try:
                cur2.execute(
                    f"SELECT COUNT(*) FROM [{meta.target_table_name}] "
                    f"WHERE lsl_metadata_id = ?",
                    (meta.lsl_metadata_id,)
                )
                count = cur2.fetchone()[0]
            except Exception:
                count = 0
            groups.setdefault(key, []).append((meta, count))
        conn2.close()

        stitched_list = []
        for key, entries in groups.items():
            # Primary = the entry with the most rows (most complete segment)
            entries.sort(key=lambda x: x[1], reverse=True)
            primary, _ = entries[0]
            extra_ids = [m.lsl_metadata_id for m, _ in entries[1:] if m.lsl_metadata_id != primary.lsl_metadata_id]
            primary.extra_segment_ids = extra_ids
            stitched_list.append(primary)
            if extra_ids:
                print(
                    f"[ReplayEngine] Stitching {len(extra_ids)+1} segments for "
                    f'"{primary.name}" → ids {[primary.lsl_metadata_id] + extra_ids}'
                )

        metadata_list = stitched_list

        # Use a generous end window so all samples are included
        end_dt = latest + datetime.timedelta(hours=24)
        return metadata_list, earliest, end_dt

    @staticmethod
    def _parse_dt(s: str) -> datetime.datetime:
        """Parse any ISO 8601 datetime string, including timezone-aware ones."""
        if not s:
            raise ValueError("Empty datetime string")
        dt = datetime.datetime.fromisoformat(str(s))
        return dt.replace(tzinfo=None)

    # Table name → (stream_name, stream_type, channels, hz [, channel_format])
    # Derived from python-lsl-logger config.site.160th.toml + python-neon-middleware config.
    # channel_format defaults to 'float32' if not specified.
    _TABLE_TO_META = {
        # Polar H10
        'polar_h10_ecg':               ('Polar H10 ECG',          'ECG',   1,   130.0),
        'polar_h10_heart_rate':         ('Polar H10 HR',           'HR',    1,     1.0),
        'polar_h10_ppi':                ('Polar H10 PPI',          'PPI',   4,     0.0),
        'polar_h10_acceleration':       ('Polar H10 ACC',          'ACC',   3,   200.0),
        # Polar Verity Sense
        'polar_verity_sense_hr':        ('Polar Sense HR',         'HR',    1,     1.0),
        'polar_verity_sense_ppg':       ('Polar Sense PPG',        'PPG',   4,    55.0),
        'polar_verity_sense_acceleration': ('Polar Sense ACC',     'ACC',   3,    52.0),
        'polar_verity_sense_gyro':      ('Polar Sense GYRO',       'GYRO',  3,    52.0),
        'polar_verity_sense_mag_3d':    ('Polar Sense MAG 3D',     'MAG_3D', 3,  100.0),
        'polar_verity_sense_mag_compass': ('Polar Sense MAG Compass', 'MAG_COMPASS', 4, 100.0),
        'polar_verity_sense_ppi':       ('Polar Sense PPI',        'PPI',   4,     0.0),
        # Neon
        'neon_gaze':                    ('Neon Companion_Neon Gaze', 'Gaze', 22, 200.0),
        'neon_events':                  ('Neon Events',            'Event', 1,    0.0, 'string'),
        'neon_middleware_eye_events':   ('Neon Eye Events',        'eye_events', 14, 0.0),
        'neon_middleware_speech_transcription': ('Speech',         'speech_transcription', 3, 0.0, 'string'),
        'neon_middleware_imu':          ('Neon IMU',               'IMU',   11,   30.0),
        'neon_middleware_scene_video_frame': ('Neon Scene Frame',  'Indicator', 2,  1.0),
        # Pose
        'pose3d_stereo':                ('Pose3D',                 'Pose',  111,  15.0),
        # Fallback
        'lsl_unmapped_samples':         ('Unmapped Samples',       'Mixed', 1,    0.0, 'string'),
    }

    def _synthesise_metadata(self, conn, cursor, db_path, LslMetadata):
        """
        Build synthetic metadata for older databases that lack lsl_metadata.
        Derives stream identity from known table names, and infers the time
        range from the first/last timestamps in each data table.
        """
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        present_tables = {r[0] for r in cursor.fetchall()}

        metadata_list = []
        earliest = None
        latest   = None

        for tbl, spec in self._TABLE_TO_META.items():
            name, stype, channels, hz = spec[0], spec[1], spec[2], spec[3]
            ch_fmt = spec[4] if len(spec) > 4 else 'float32'
            if tbl not in present_tables:
                continue
            if tbl == 'lsl_unmapped_samples':
                continue   # skip the catch-all table for synthesis

            try:
                cursor.execute(
                    f"SELECT MIN(datetime_utc), MAX(datetime_utc) FROM [{tbl}]"
                )
                row = cursor.fetchone()
                if not row or not row[0]:
                    continue

                dt_start = self._parse_dt(row[0])
                dt_end   = self._parse_dt(row[1])

                if earliest is None or dt_start < earliest:
                    earliest = dt_start
                if latest is None or dt_end > latest:
                    latest = dt_end

                # Construct a synthetic LslMetadata that matches the real schema
                # Read the real lsl_metadata_id that Total Recall wrote into this
                # table at recording time. SqlRecordFetcher queries:
                #   SELECT * FROM [table] WHERE lsl_metadata_id = ?
                # so this value MUST match what is actually in the rows.
                cursor.execute(
                    f"SELECT DISTINCT lsl_metadata_id FROM [{tbl}] LIMIT 1"
                )
                real_id_row = cursor.fetchone()
                if not real_id_row:
                    continue
                real_meta_id = real_id_row[0]

                cursor.execute(
                    f"SELECT unix_timestamp_seconds FROM [{tbl}] "
                    f"ORDER BY unix_timestamp_seconds ASC LIMIT 1"
                )
                first_ts = cursor.fetchone()
                unix_ts  = first_ts[0] if first_ts else 0.0

                meta = LslMetadata(
                    lsl_metadata_id     = real_meta_id,
                    datetime_utc        = row[0],
                    datetime_local      = row[0],
                    unix_timestamp_seconds = unix_ts,
                    target_table_name   = tbl,
                    name                = name,
                    type                = stype,
                    channels            = channels,
                    sample_rate_hz      = hz,
                    source_id           = '',
                    channel_format      = ch_fmt,
                    session_id          = os.path.splitext(os.path.basename(db_path))[0],
                    hostname            = '',
                    desc                = '<info><desc><channels/></desc></info>',
                )
                metadata_list.append(meta)

            except Exception as ex:
                print(f"[ReplayEngine] Skipping table {tbl} during synthesis: {ex}")
                continue

        conn.close()

        if not metadata_list or earliest is None:
            return [], None, None

        end_dt = latest + datetime.timedelta(hours=24)
        print(f"[ReplayEngine] Synthesised {len(metadata_list)} metadata entries "
              f"for older-schema DB: {os.path.basename(db_path)}")
        return metadata_list, earliest, end_dt
