"""Activity-stats queries behind the admin stats dashboard (#2545).

scripts/cwa_db.py used to define these three methods twice; Python kept the
later copies. These tests pin what the dashboard actually receives, so they
fail if the earlier, differently-behaving copies are the ones left standing
(ISO-date week labels instead of ``YYYY-Www``, EMAIL counted as a format use).
"""

import pytest

pytestmark = pytest.mark.unit

RANGE = {"start_date": "2025-01-01", "end_date": "2025-01-31"}

# (user_id, user_name, event_type, item_id, extra_data, timestamp)
ROWS = [
    (1, "alice", "READ", 10, '{"format": "epub"}', "2025-01-06 09:15:00"),  # Mon, ISO week 01
    (1, "alice", "READ", 11, '{"format": "pdf"}', "2025-01-07 21:00:00"),   # Tue, week 01
    (1, "alice", "EMAIL", 12, '{"format": "epub"}', "2025-01-08 10:00:00"),  # Wed
    (1, "alice", "DOWNLOAD", 10, '{"format": "epub"}', "2025-01-14 09:40:00"),  # Tue, week 02
    (2, "bob", "READ", 10, "{}", "2025-01-14 09:05:00"),                    # Tue, week 02
    (2, "bob", "READ", 13, "{}", "2024-12-20 09:05:00"),                    # outside the range
]


@pytest.fixture
def activity_db(temp_cwa_db):
    temp_cwa_db.cur.executemany(
        "INSERT INTO cwa_user_activity "
        "(user_id, user_name, event_type, item_id, extra_data, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ROWS,
    )
    temp_cwa_db.con.commit()
    return temp_cwa_db


def test_hourly_heatmap_counts_every_event_by_weekday_and_hour(activity_db):
    assert activity_db.get_hourly_activity_heatmap(**RANGE) == [
        (1, 9, 1),
        (2, 9, 2),
        (2, 21, 1),
        (3, 10, 1),
    ]


def test_reading_velocity_counts_distinct_books_read_per_week_label(activity_db):
    assert activity_db.get_reading_velocity(**RANGE) == [
        ("2025-W01", 2),
        ("2025-W02", 1),
    ]
    assert activity_db.get_reading_velocity(user_id=2, **RANGE) == [("2025-W02", 1)]


def test_format_preferences_count_reads_and_downloads_only(activity_db):
    assert activity_db.get_format_preferences(**RANGE) == [
        ("alice", "EPUB", 2),
        ("alice", "PDF", 1),
        ("bob", "UNKNOWN", 1),
    ]
