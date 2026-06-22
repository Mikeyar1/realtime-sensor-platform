import datetime
import threading
import time
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone

import pylsl

from config_mgr import config
from lsl_metadata import LslMetadata
from replay_sample import ReplaySample
from sql_record_fetcher import SqlRecordFetcher

metadata_utc_datetime_column_index = config["metadata_utc_datetime_column_index"]
publisher_thread_sleep_interval_seconds = config.get("publisher_thread_sleep_interval_seconds", 0.01)


class LslReplayPublisher(threading.Thread):
    def __init__(
        self,
        lsl_metadata: LslMetadata,
        sqlite_file: str,
        start_datetime: datetime,
        end_datetime: datetime,
        time_delta: timedelta,
    ) -> None:
        self.lsl_metadata = lsl_metadata
        self.outlet = None
        self.sqlite_file = sqlite_file
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.time_delta = time_delta
        self.max_sample_datetime_utc = end_datetime
        self.init_outlet_datetime = None
        self.replay_samples = list[ReplaySample]()
        self.current_replay_sample_index = 0
        self._stop_event = threading.Event()
        super().__init__(daemon=True)

    def init_outlet(self, lsl_metadata: LslMetadata) -> pylsl.StreamOutlet:
        info = pylsl.StreamInfo(
            name=lsl_metadata.name,
            type=lsl_metadata.type,
            channel_count=lsl_metadata.channels,
            nominal_srate=lsl_metadata.sample_rate_hz,
            channel_format=lsl_metadata.channel_format,
            source_id=lsl_metadata.source_id,
        )
        LslReplayPublisher.add_channel_metadata_to_stream_info(info, lsl_metadata)

        outlet = pylsl.StreamOutlet(info)
        self.init_outlet_datetime = datetime.datetime.now()
        return outlet

    def run(self) -> None:
        print(f"Starting LSL Replay Publisher for {self.lsl_metadata.name}")
        while not self.stopped():
            self.publish_samples(time_delta=self.time_delta)
            # sleep for a short time to avoid busy waiting
            time.sleep(publisher_thread_sleep_interval_seconds)

    def fetch_sql_records(self) -> list[ReplaySample]:
        sql_record_fetcher = SqlRecordFetcher(
            lsl_metadata=self.lsl_metadata,
            sqlite_file=self.sqlite_file,
            end_datetime=self.end_datetime,
        )
        sql_record_fetcher.start()
        sql_record_fetcher.join()
        self.replay_samples = sql_record_fetcher.replay_samples
        self.max_sample_datetime_utc = sql_record_fetcher.max_end_datetime_utc
        print(
            f"Max sample datetime_utc: {self.max_sample_datetime_utc} for {self.lsl_metadata.name} with id {self.lsl_metadata.lsl_metadata_id}"
        )
        return self.replay_samples

    def stop(self) -> None:
        self._stop_event.set()
        self.outlet = None

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def publish_samples(self, time_delta: timedelta) -> None:
        # get all samples from the current sample index <= the current datetime
        normalized_datetime = datetime.datetime.now(tz=datetime.timezone.utc) - time_delta
        for i in range(self.current_replay_sample_index, len(self.replay_samples)):
            replay_sample = self.replay_samples[i]
            sample_values = replay_sample.get_sample()
            sample_timestamp = datetime.datetime.fromisoformat(replay_sample.row[metadata_utc_datetime_column_index])
            if sample_timestamp <= normalized_datetime and not replay_sample.is_published:
                if self.outlet is None:
                    self.outlet = self.init_outlet(self.lsl_metadata)
                    print(f"Initialized LSL Outlet for {self.lsl_metadata.name}")
                self.outlet.push_sample(sample_values)
                replay_sample.is_published = True
                self.current_replay_sample_index += 1
                if self.current_replay_sample_index == len(self.replay_samples):
                    print(
                        f"All {len(self.replay_samples)} samples published for {self.lsl_metadata.name} with id {self.lsl_metadata.lsl_metadata_id}"
                    )
                    self.stop()
                    break
            else:
                break

    def is_outlet_active(self) -> bool:
        return self.outlet is not None and not self.stopped()

    def get_publish_rate_per_second(self) -> float:
        # if the outlet is active, then calculate the rate based on the current time
        if len(self.replay_samples) == 0:
            return 0.0
        if self.current_replay_sample_index == 0:
            return 0.0

        if self.is_outlet_active():
            last_publish_datetime_utc = datetime.datetime.now(timezone.utc)
        else:
            last_publish_datetime_utc = self.max_sample_datetime_utc

        first_publish_datetime_string_utc = self.replay_samples[0].row[metadata_utc_datetime_column_index]
        first_publish_datetime_utc = datetime.datetime.fromisoformat(first_publish_datetime_string_utc).replace(
            tzinfo=timezone.utc
        )
        total_time_seconds = (last_publish_datetime_utc - self.time_delta - first_publish_datetime_utc).total_seconds()
        if total_time_seconds == 0:
            return 0.0
        return self.current_replay_sample_index / total_time_seconds

    def reset(self) -> None:
        self.current_replay_sample_index = 0
        for replay_sample in self.replay_samples:
            replay_sample.is_published = False
        self.outlet = None
        self._stop_event.clear()

    @staticmethod
    def add_channel_metadata_to_stream_info(stream_info: pylsl.StreamInfo, lsl_metadata: LslMetadata) -> None:
        desc = lsl_metadata.desc
        tree = ET.ElementTree(ET.fromstring(desc))
        root = tree.getroot()

        outlet_desc = stream_info.desc()
        desc_xml = root.find("desc")
        channels = desc_xml.find("channels")
        if channels is not None:
            channels_outlet = outlet_desc.append_child("channels")
            for channel in channels:
                channel_outlet = channels_outlet.append_child("channel")
                for child in channel:
                    child_text = child.text
                    if child.text is None:
                        child_text = ""
                    channel_outlet.append_child_value(child.tag, child_text)

    def create_restart(self) -> "LslReplayPublisher":
        # Returns a fresh publisher with the same config and data
        new_publisher = LslReplayPublisher(
            lsl_metadata=self.lsl_metadata,
            sqlite_file=self.sqlite_file,
            start_datetime=self.start_datetime,
            end_datetime=self.end_datetime,
            time_delta=self.time_delta,
        )
        new_publisher.replay_samples = self.replay_samples
        new_publisher.max_sample_datetime_utc = self.max_sample_datetime_utc
        return new_publisher