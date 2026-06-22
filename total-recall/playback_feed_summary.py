from datetime import datetime
from typing import Optional

from lsl_metadata import LslMetadata


class PlaybackFeedSummary:
    def __init__(self, lsl_metadata: LslMetadata) -> None:
        self.lsl_metadata: LslMetadata = lsl_metadata
        self.outlet_active: bool = False
        self.records_published: int = 0
        self.publishing_rate: float = 0.0
        self.outlet_creation_time: Optional[datetime] = None
        self.outlet_end_time: Optional[datetime] = None

    def get_publishing_rate_str(self) -> str:
        if self.publishing_rate <= 0.0:
            return "------"
        publishing_rate_per_second_str = f"{abs(self.publishing_rate):.1f}"
        return publishing_rate_per_second_str
