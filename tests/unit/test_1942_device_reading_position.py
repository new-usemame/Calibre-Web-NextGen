# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1942 M3: per-device positions, resolution, and rehydrate safety."""

from datetime import datetime, timezone
import sys
from types import SimpleNamespace

import pytest
from flask import Flask, g
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest

from cps import ub


pytestmark = pytest.mark.unit
USER_ID = 17
BOOK_ID = 42
_DEFAULT_LOCATION = object()


def _clock(hour):
    return datetime(2026, 8, 29, hour, 0, tzinfo=timezone.utc)


@pytest.fixture
def position_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    devices = [
        ub.Device(
            user_id=USER_ID,
            kind=kind,
            display_name=name,
            active=True,
            created_by="auto",
        )
        for kind, name in (("kobo", "Clara"), ("webreader", "Browser"))
    ]
    session.add_all(devices)
    session.commit()
    yield session, devices
    session.close()
    engine.dispose()


def test_per_device_upsert_keeps_one_row_per_device_and_preserves_latch(
        position_session):
    from cps.services import device_reading_position as positions

    session, (kobo, browser) = position_session
    positions.stage_position(
        device_id=kobo.id,
        book_id=BOOK_ID,
        progress_percent=25.0,
        location_value="kobo.1",
        client_modified_at=_clock(10),
    )
    positions.mark_rehydrate_needed(kobo.id, [BOOK_ID])
    positions.stage_position(
        device_id=kobo.id,
        book_id=BOOK_ID,
        progress_percent=35.0,
        location_value="kobo.2",
        client_modified_at=_clock(11),
    )
    positions.stage_position(
        device_id=browser.id,
        book_id=BOOK_ID,
        progress_percent=55.0,
        cfi="epubcfi(/6/4!/4/2:9)",
        client_modified_at=_clock(12),
    )
    session.commit()

    rows = session.query(ub.DeviceReadingPosition).order_by(
        ub.DeviceReadingPosition.device_id,
    ).all()
    assert len(rows) == 2
    by_device = {row.device_id: row for row in rows}
    assert by_device[kobo.id].progress_percent == 35.0
    assert by_device[kobo.id].location_value == "kobo.2"
    assert by_device[kobo.id].rehydrate_needed is True
    assert by_device[browser.id].progress_percent == 55.0
    assert by_device[browser.id].cfi == "epubcfi(/6/4!/4/2:9)"


def test_web_reader_records_its_exact_cfi_even_when_resolved_progress_is_lower(
        position_session, monkeypatch):
    from cps.services import reading_position
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]

    session, (_kobo, browser) = position_session
    read = ub.ReadBook(
        user_id=USER_ID,
        book_id=BOOK_ID,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    state = ub.KoboReadingState(user_id=USER_ID, book_id=BOOK_ID)
    state.current_bookmark = ub.KoboBookmark(progress_percent=80.0)
    read.kobo_reading_state = state
    session.add(read)
    session.commit()
    monkeypatch.setattr(kosync, "update_book_read_status", lambda *_a, **_k: None)
    monkeypatch.setattr(
        kosync, "record_percentage_only_progress", lambda *_a, **_k: None,
    )

    advanced = reading_position.record_web_reader_progress(
        SimpleNamespace(id=USER_ID),
        BOOK_ID,
        20.0,
        origin_device_id=browser.id,
        cfi="epubcfi(/6/8!/4/6:2)",
    )
    session.commit()

    assert advanced is False
    assert state.current_bookmark.progress_percent == 80.0
    journal = session.query(ub.DeviceReadingPosition).one()
    assert journal.device_id == browser.id
    assert journal.progress_percent == 20.0
    assert journal.cfi == "epubcfi(/6/8!/4/6:2)"


@pytest.fixture
def state_harness(monkeypatch):
    from cps import kobo

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    device = ub.Device(
        user_id=USER_ID,
        kind="kobo",
        display_name="Clara",
        active=True,
        created_by="auto",
    )
    read = ub.ReadBook(
        user_id=USER_ID,
        book_id=BOOK_ID,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        last_modified=_clock(10),
        times_started_reading=1,
    )
    state = ub.KoboReadingState(
        user_id=USER_ID,
        book_id=BOOK_ID,
        last_modified=_clock(10),
        priority_timestamp=_clock(10),
    )
    state.current_bookmark = ub.KoboBookmark(
        progress_percent=80.0,
        content_source_progress_percent=80.0,
        location_value="resolved.80",
        location_type="KoboSpan",
        location_source="kepub",
        last_modified=_clock(10),
    )
    state.statistics = ub.KoboStatistics(
        spent_reading_minutes=10,
        remaining_time_minutes=20,
        last_modified=_clock(10),
    )
    read.kobo_reading_state = state
    session.add_all([device, read])
    session.commit()

    book = SimpleNamespace(
        id=BOOK_ID,
        uuid="00000000-0000-0000-0000-000000001942",
        data=[SimpleNamespace(format="KEPUB")],
        identifiers=[],
    )
    user = SimpleNamespace(id=USER_ID, name="m3-reader")
    fanout = []

    monkeypatch.setattr(ub, "session", session)

    def commit(*_args, **_kwargs):
        session.commit()
        return True

    monkeypatch.setattr(ub, "session_commit", commit)
    monkeypatch.setattr(kobo, "current_user", user)
    monkeypatch.setattr(
        kobo,
        "calibre_db",
        SimpleNamespace(get_book_by_uuid_for_kobo=lambda *_a, **_k: book),
    )
    monkeypatch.setattr(kobo, "get_or_create_reading_state", lambda _book_id: state)
    monkeypatch.setattr(
        kobo,
        "push_reading_state_to_hardcover",
        lambda *_args: fanout.append(("hardcover", _args[-1])),
    )
    monkeypatch.setattr(
        kobo,
        "share_kobo_progress_with_koreader",
        lambda *_args: fanout.append(("kosync", _args[-1])),
    )

    app = Flask(__name__)

    def put(
            percent, *, clock, status="Reading", spent=10,
            location=_DEFAULT_LOCATION):
        if location is _DEFAULT_LOCATION:
            location = {
                "Value": "device.{}".format(percent),
                "Type": "KoboSpan",
                "Source": "kepub",
            }
        payload = {"ReadingStates": [{
            "LastModified": clock,
            "CurrentBookmark": {
                "ProgressPercent": percent,
                "ContentSourceProgressPercent": percent,
                "Location": location,
            },
            "Statistics": {
                "SpentReadingMinutes": spent,
                "RemainingTimeMinutes": max(0, 100 - spent),
            },
            "StatusInfo": {"Status": status},
        }]}
        with app.test_request_context(method="PUT", json=payload):
            g.annotation_origin_device_id = device.id
            return kobo.HandleStateRequest.__wrapped__(book.uuid)

    yield SimpleNamespace(
        app=app,
        book=book,
        device=device,
        fanout=fanout,
        put=put,
        read=read,
        session=session,
        state=state,
    )
    session.close()
    engine.dispose()


def test_newer_backward_jump_updates_resolved_row_and_both_fanouts(
        state_harness):
    harness = state_harness

    lower = harness.put(
        20.0,
        clock="2026-08-29T14:00:00Z",
        status="Finished",
        spent=40,
    )
    assert lower.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()
    assert harness.state.current_bookmark.progress_percent == 20.0
    assert harness.read.read_status == ub.ReadBook.STATUS_FINISHED
    assert harness.state.statistics.spent_reading_minutes == 40
    journal = harness.session.query(ub.DeviceReadingPosition).one()
    assert journal.progress_percent == 20.0
    assert harness.fanout == [("hardcover", 20.0), ("kosync", 20.0)]

    higher = harness.put(
        90.0,
        clock="2026-08-29T09:00:00Z",
        status="ReadyToRead",
        spent=5,
    )
    assert higher.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()
    assert harness.state.current_bookmark.progress_percent == 90.0
    assert harness.read.read_status == ub.ReadBook.STATUS_FINISHED
    assert harness.state.statistics.spent_reading_minutes == 40
    assert harness.fanout == [
        ("hardcover", 20.0),
        ("kosync", 20.0),
        ("hardcover", 90.0),
        ("kosync", 90.0),
    ]


def test_stored_80_and_older_device_70_stays_80(state_harness):
    """F-b521d3: a trailing Kobo cannot rewind the resolved carrier."""
    harness = state_harness

    response = harness.put(70.0, clock="2026-08-29T09:00:00Z")
    assert response.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()

    assert harness.state.current_bookmark.progress_percent == 80.0
    assert harness.state.current_bookmark.location_value == "resolved.80"
    assert harness.fanout == []


def test_stored_70_and_older_device_80_becomes_80(state_harness):
    """F-b521d3: greater progress wins even when its source clock is older."""
    harness = state_harness
    harness.state.current_bookmark.progress_percent = 70.0
    harness.state.current_bookmark.content_source_progress_percent = 70.0
    harness.state.current_bookmark.location_value = "resolved.70"
    harness.session.commit()

    response = harness.put(80.0, clock="2026-08-29T09:00:00Z")
    assert response.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()

    assert harness.state.current_bookmark.progress_percent == 80.0
    assert harness.state.current_bookmark.location_value == "device.80.0"
    assert harness.fanout == [("hardcover", 80.0), ("kosync", 80.0)]


def test_older_device_clock_cannot_move_resolved_clocks_backwards(
        state_harness):
    """F-b521d3: even an accepted further position keeps monotonic clocks."""
    harness = state_harness
    bookmark_clock = harness.state.current_bookmark.last_modified
    state_clock = harness.state.last_modified

    response = harness.put(90.0, clock="2026-08-29T09:00:00Z")
    assert response.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()

    assert harness.state.current_bookmark.progress_percent == 90.0
    assert harness.state.current_bookmark.last_modified >= bookmark_clock
    assert harness.state.last_modified >= state_clock


@pytest.mark.parametrize(
    "clock",
    ["2026-08-29T10:00:00Z", "2026-08-29T09:00:00Z"],
)
def test_equal_progress_refreshes_location_without_regressing_clocks(
        state_harness, clock):
    """Equal percentage accepts a Kobo locator with an equal/older clock."""
    harness = state_harness
    bookmark_clock = harness.state.current_bookmark.last_modified
    state_clock = harness.state.last_modified

    response = harness.put(80.0, clock=clock)
    assert response.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()

    assert harness.state.current_bookmark.progress_percent == 80.0
    assert harness.state.current_bookmark.location_value == "device.80.0"
    assert harness.state.current_bookmark.last_modified >= bookmark_clock
    assert harness.state.last_modified >= state_clock
    assert harness.fanout == [("hardcover", 80.0), ("kosync", 80.0)]


def test_malformed_nonempty_location_is_rejected(state_harness):
    """Kobo's non-empty Location contract requires Value, Type, and Source."""
    harness = state_harness

    with pytest.raises(BadRequest):
        harness.put(
            80.0,
            clock="2026-08-29T10:00:00Z",
            location={"Type": "KoboSpan", "Source": "kepub"},
        )

    harness.session.expire_all()
    assert harness.state.current_bookmark.location_value == "resolved.80"


def test_supplied_null_location_value_clears_the_stored_value(state_harness):
    """A valid supplied Location with Value:null remains an explicit clear."""
    harness = state_harness

    response = harness.put(
        80.0,
        clock="2026-08-29T09:00:00Z",
        location={
            "Value": None,
            "Type": "KoboPage",
            "Source": "application/epub+zip",
        },
    )
    assert response.get_json()["RequestResult"] == "Success"
    harness.session.expire_all()

    bookmark = harness.state.current_bookmark
    assert bookmark.progress_percent == 80.0
    assert bookmark.location_value is None
    assert bookmark.location_type == "KoboPage"
    assert bookmark.location_source == "application/epub+zip"


def test_rehydrate_latch_survives_cover_reset_until_sync_clears_it(
        state_harness):
    from cps.services import device_reading_position as positions

    harness = state_harness
    positions.mark_rehydrate_needed(harness.device.id, [BOOK_ID])
    harness.session.commit()

    harness.put(0.0, clock="2026-08-29T15:00:00Z", spent=0)
    harness.session.expire_all()
    position = harness.session.query(ub.DeviceReadingPosition).one()
    assert position.progress_percent == 0.0, "the device journal is truthful"
    assert position.rehydrate_needed is True
    assert harness.state.current_bookmark.progress_percent == 80.0
    assert harness.fanout == [], "an armed cover reset must not escape"

    # HandleSyncRequest clears this field only after it has appended the state;
    # this direct clear models that single request-level commit boundary.
    position.rehydrate_needed = False
    harness.session.commit()
    assert harness.session.query(
        ub.DeviceReadingPosition.rehydrate_needed,
    ).scalar() is False


def test_armed_non_cover_backward_jump_is_not_misclassified_as_reset(
        state_harness):
    from cps.services import device_reading_position as positions

    harness = state_harness
    positions.mark_rehydrate_needed(harness.device.id, [BOOK_ID])
    harness.session.commit()
    harness.put(30.0, clock="2026-08-29T15:00:00Z", spent=25)
    harness.session.expire_all()

    position = harness.session.query(ub.DeviceReadingPosition).one()
    assert position.progress_percent == 30.0
    assert position.rehydrate_needed is True
    assert harness.state.current_bookmark.progress_percent == 30.0
    assert harness.fanout == [("hardcover", 30.0), ("kosync", 30.0)]


@pytest.mark.parametrize(
    ("cover_progress", "suppressed"),
    [(0.0, True), (1.0, True), (1.01, False)],
)
def test_cover_reset_guard_is_latch_and_epsilon_scoped(
        state_harness, cover_progress, suppressed):
    from cps.services import device_reading_position as positions

    harness = state_harness
    positions.mark_rehydrate_needed(harness.device.id, [BOOK_ID])
    harness.session.commit()

    harness.put(
        cover_progress,
        clock="2026-08-29T15:00:00Z",
        spent=0,
    )
    harness.session.expire_all()

    expected = 80.0 if suppressed else cover_progress
    assert harness.state.current_bookmark.progress_percent == expected
    assert harness.session.query(
        ub.DeviceReadingPosition.progress_percent,
    ).scalar() == cover_progress
    assert bool(harness.fanout) is not suppressed


def test_duplicate_merge_keeps_newer_client_clock_then_progress_tie_break(
        position_session):
    from cps.user_book_data import PER_USER_BOOK_MODELS, migrate_user_book_data

    session, (device_a, device_b) = position_session
    assert "DeviceReadingPosition" in PER_USER_BOOK_MODELS
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=device_a.id,
            book_id=1,
            progress_percent=60.0,
            client_modified_at=_clock(12),
            server_modified_at=_clock(12),
        ),
        ub.DeviceReadingPosition(
            device_id=device_a.id,
            book_id=2,
            progress_percent=90.0,
            client_modified_at=_clock(11),
            server_modified_at=_clock(11),
            rehydrate_needed=True,
        ),
        ub.DeviceReadingPosition(
            device_id=device_b.id,
            book_id=1,
            progress_percent=70.0,
            client_modified_at=_clock(12),
            server_modified_at=_clock(12),
        ),
        ub.DeviceReadingPosition(
            device_id=device_b.id,
            book_id=2,
            progress_percent=65.0,
            client_modified_at=_clock(12),
            server_modified_at=_clock(12),
        ),
    ])
    session.commit()

    migrate_user_book_data(1, 2, session=session)
    session.commit()

    rows = session.query(ub.DeviceReadingPosition).order_by(
        ub.DeviceReadingPosition.device_id,
    ).all()
    assert [(row.device_id, row.book_id, row.progress_percent) for row in rows] == [
        (device_a.id, 2, 60.0),
        (device_b.id, 2, 70.0),
    ]
    assert rows[0].rehydrate_needed is True, "merge must not consume repair work"


def test_book_delete_purges_device_positions_for_every_user(position_session):
    from cps.user_book_data import purge_user_book_data

    session, (device_a, device_b) = position_session
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=device_a.id,
            book_id=BOOK_ID,
            server_modified_at=_clock(10),
        ),
        ub.DeviceReadingPosition(
            device_id=device_b.id,
            book_id=BOOK_ID,
            server_modified_at=_clock(10),
        ),
        ub.DeviceReadingPosition(
            device_id=device_a.id,
            book_id=BOOK_ID + 1,
            server_modified_at=_clock(10),
        ),
    ])
    session.commit()

    purge_user_book_data(
        book_id=BOOK_ID,
        session=session,
        remove_backup_files=False,
    )
    session.commit()

    remaining = session.query(ub.DeviceReadingPosition).one()
    assert remaining.book_id == BOOK_ID + 1


def test_additive_migration_is_idempotent_and_indexed():
    engine = create_engine("sqlite:///:memory:")
    ub.migrate_device_reading_position_slice(engine, None)
    ub.migrate_device_reading_position_slice(engine, None)

    schema = inspect(engine)
    assert "device_reading_position" in schema.get_table_names()
    columns = {column["name"] for column in schema.get_columns(
        "device_reading_position",
    )}
    assert {
        "device_id",
        "book_id",
        "progress_percent",
        "cfi",
        "client_modified_at",
        "server_modified_at",
        "rehydrate_needed",
    } <= columns
    indexes = {index["name"] for index in schema.get_indexes(
        "device_reading_position",
    )}
    assert {
        "ix_device_reading_position_book",
        "ix_device_reading_position_rehydrate",
    } <= indexes
    engine.dispose()
