import dataclasses
from datetime import datetime

from pylsl.lib import fmt2string

from config_mgr import config

unmapped_table_contains_text = config["unmapped_table_contains_text"]


@dataclasses.dataclass
class LslMetadata:
    lsl_metadata_id: int
    datetime_utc: datetime
    datetime_local: datetime
    unix_timestamp_seconds: float
    target_table_name: str
    name: str
    type: str
    channels: int
    sample_rate_hz: float
    source_id: str
    channel_format: str
    session_id: str
    hostname: str
    desc: str
    # IDs of additional recording segments for the same stream (same device,
    # same table). When non-empty, SqlRecordFetcher stitches all segments
    # together into one continuous timeline instead of just the primary id.
    extra_segment_ids: list = dataclasses.field(default_factory=list)

    @staticmethod
    def get_channel_format(channel_format: int) -> str:
        channel_format_str = fmt2string[channel_format]
        return channel_format_str

    def is_mapped(self) -> bool:
        return unmapped_table_contains_text not in self.target_table_name
