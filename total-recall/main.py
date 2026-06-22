import datetime
import sqlite3

from config_mgr import config
from lsl_metadata import LslMetadata
from lsl_replay_publisher import LslReplayPublisher
from util import get_max_sample_datetime_utc

sql_query_metadata = config["sql_query_metadata"]
sqlite_file = "dnlc_sample_sqlite_database_07Aug2025.db"
conn = sqlite3.connect(sqlite_file)
start_date = "2025-08-07T14:16:42"  # "2025-08-04T15:50:11"
end_date = "2025-08-08T00:00:00"
cursor = conn.cursor()
cursor.execute(sql_query_metadata, (start_date, end_date))
rows = cursor.fetchall()
conn.close()
lsl_metadata_list = list[LslMetadata]()

for row in rows:
    # TRod: below block covers the case where we were writing the channel format as an integer. This has seen been fixed and the string is being written now
    # TRod: pylsl.lib.fmt2string and pylsl.lib.string2fmt covers it if one wants to do it manually
    try:
        channel_format_int = int(row[10])
        channel_format_str = LslMetadata.get_channel_format(channel_format_int)
    except ValueError:
        channel_format_str = row[10]

    lsl_metadata = LslMetadata(
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
        channel_format=channel_format_str,
        session_id=row[11],
        hostname=row[12],
        desc=row[13],
    )
    lsl_metadata_list.append(lsl_metadata)

local_machine_start_datetime = datetime.datetime.now(tz=datetime.timezone.utc)
database_start_datetime = datetime.datetime.fromisoformat(start_date)
database_start_datetime = database_start_datetime.replace(tzinfo=datetime.timezone.utc)
delta = local_machine_start_datetime - database_start_datetime

max_overall_sample_datetime_utc = database_start_datetime

lsl_replay_publishers_list = list[LslReplayPublisher]()
for lsl_metadata in lsl_metadata_list:
    lsl_replay_publisher = LslReplayPublisher(
        lsl_metadata=lsl_metadata,
        sqlite_file=sqlite_file,
        start_datetime=start_date,
        end_datetime=end_date,
        time_delta=delta,
    )
    lsl_replay_publishers_list.append(lsl_replay_publisher)
    lsl_replay_publisher.fetch_sql_records()
    if lsl_replay_publisher.max_sample_datetime_utc > max_overall_sample_datetime_utc:
        max_overall_sample_datetime_utc = lsl_replay_publisher.max_sample_datetime_utc
print(f"Max overall sample datetime_utc: {max_overall_sample_datetime_utc}")

max_overall_sample_datetime_utc = get_max_sample_datetime_utc(lsl_replay_publishers_list)
print(f"Max overall sample datetime_utc: {max_overall_sample_datetime_utc}")

# Loop 1 starts all streams
for lsl_replay_publisher in lsl_replay_publishers_list:
    lsl_replay_publisher.start()

# Loop 2 waits for all streams to finish
for lsl_replay_publisher in lsl_replay_publishers_list:
    lsl_replay_publisher.join()


print("All publisher threads have completed")
