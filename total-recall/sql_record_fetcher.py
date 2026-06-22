import datetime
import sqlite3
import threading

from config_mgr import config
from lsl_metadata import LslMetadata
from replay_sample import ReplaySample

sql_query_stream = config["sql_query_stream"]
sample_utc_datetime_column_index = config["sample_utc_datetime_column_index"]


class SqlRecordFetcher(threading.Thread):
    def __init__(self, lsl_metadata: LslMetadata, sqlite_file: str, end_datetime: datetime) -> None:
        super().__init__(daemon=True)
        self.lsl_metadata = lsl_metadata
        self.sqlite_file = sqlite_file
        self.max_end_datetime_utc = end_datetime
        self.replay_samples = list[ReplaySample]()

        self._stop_event = threading.Event()

    def run(self) -> list[ReplaySample]:
        return self.get_replay_samples()

    def get_replay_samples(self) -> list[ReplaySample]:
        conn = sqlite3.connect(self.sqlite_file)
        cursor = conn.cursor()

        # Build the complete list of lsl_metadata_ids to fetch.
        # extra_segment_ids holds ids from reconnection segments for the same
        # device — combining them gives us the full continuous timeline.
        all_ids = [self.lsl_metadata.lsl_metadata_id] + list(self.lsl_metadata.extra_segment_ids or [])

        base_query = sql_query_stream.replace("[target_table_name]", self.lsl_metadata.target_table_name)

        if len(all_ids) == 1:
            # Original single-segment path — unchanged behaviour
            query = base_query
            params = (all_ids[0],)
        else:
            # Multi-segment: replace "= ?" with "IN (?, ?, ...)" so every
            # reconnection segment is included, ordered chronologically.
            placeholders = ", ".join(["?"] * len(all_ids))
            query = base_query.replace(
                "lsl_metadata_id = ?",
                f"lsl_metadata_id IN ({placeholders})",
            )
            params = tuple(all_ids)
            print(
                f"[SqlRecordFetcher] Stitching {len(all_ids)} segments for "
                f"{self.lsl_metadata.name} (ids: {all_ids})"
            )

        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            print(f"Table not found for {self.lsl_metadata.name} (table: {self.lsl_metadata.target_table_name}): {e}")
            conn.close()
            return self.replay_samples

        conn.close()
        for row in rows:
            replay_sample = ReplaySample(row=row, is_mapped=self.lsl_metadata.is_mapped())
            self.replay_samples.append(replay_sample)

        print(
            f"Fetched {len(self.replay_samples)} samples for {self.lsl_metadata.name} with id {self.lsl_metadata.lsl_metadata_id}"
        )

        self.max_end_datetime_utc = datetime.datetime.fromisoformat(self.lsl_metadata.datetime_utc)
        if len(self.replay_samples) > 0:
            self.max_end_datetime_utc = self.replay_samples[-1].row[sample_utc_datetime_column_index]
            self.max_end_datetime_utc = datetime.datetime.fromisoformat(self.max_end_datetime_utc)

        return self.replay_samples


    # TRod: usused, but throwing it in in case of future usage
    def stop(self) -> None:
        self._stop_event.set()
