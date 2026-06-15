import datetime

from lsl_replay_publisher import LslReplayPublisher


def get_max_sample_datetime_utc(lsl_publishers: list[LslReplayPublisher]) -> datetime:
    max_datetime_utc = None
    for publisher in lsl_publishers:
        if max_datetime_utc is None or publisher.max_sample_datetime_utc > max_datetime_utc:
            max_datetime_utc = publisher.max_sample_datetime_utc
    return max_datetime_utc


def sqlite_datetime_string_to_datetime(dt_str: str) -> datetime:
    """Convert a SQLite datetime string to a datetime object."""
    try:
        # Try to parse with microseconds
        return datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        # Fallback to parsing without microseconds
        return datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
