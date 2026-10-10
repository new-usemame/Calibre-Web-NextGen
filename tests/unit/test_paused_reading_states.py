# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paused choices survive progress traffic, legacy projections and user isolation.

#1081: exercise persisted carriers and real SQL filters, rather than the source
shape of the guards. Removing the automatic-progress pause guard must fail.
"""
import inspect
import sys
from datetime import datetime
from types import SimpleNamespace

import flask
from flask_babel import Babel
import pytest
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker

from cps import db, helper, ub
from cps.api import books as api_books
from cps.progress_syncing.models import KOSyncProgress

pytestmark = pytest.mark.unit
CC_ID = 10819
USER = SimpleNamespace(id=7, name="reader", is_authenticated=True,
                       is_anonymous=False, role_browse_global=lambda: True,
                       view_settings={})


@pytest.fixture
def world(monkeypatch):
    if CC_ID not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes([SimpleNamespace(id=CC_ID, datatype="bool")])
    column = db.cc_classes[CC_ID]
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for bid in range(42, 50):
        session.execute(db.Books.__table__.insert().values(
            id=bid, title=str(bid), sort=str(bid), author_sort="A",
            uuid=str(bid), series_index=1, path="A/" + str(bid), has_cover=0))
    session.add(column(book=42, value=True))
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: session.commit() or True)
    monkeypatch.setattr(ub, "session_flush", lambda *a, **k: session.flush())
    monkeypatch.setattr(helper, "current_user", USER)
    monkeypatch.setattr(api_books, "current_user", USER)
    monkeypatch.setattr(helper.config, "config_read_column", CC_ID, raising=False)
    monkeypatch.setattr(helper, "queue_hardcover_mark_read", lambda *a, **k: None)
    monkeypatch.setattr(helper, "queue_hardcover_reading_progress", lambda *a, **k: None)
    monkeypatch.setattr(helper, "calibre_db", SimpleNamespace(
        session=session, get_book=lambda bid: session.get(db.Books, bid),
        get_filtered_book=lambda bid, *a, **k: session.get(db.Books, bid)))
    yield SimpleNamespace(session=session, column=column, monkeypatch=monkeypatch)
    session.close()
    engine.dispose()


def seed(world, status=2, user_id=7, book_id=42):
    row = ub.ReadBook(user_id=user_id, book_id=book_id, read_status=status,
                      times_started_reading=5, last_time_started_reading=datetime(2020, 1, 1),
                      read_status_choice_at=datetime(2020, 1, 1))
    row.kobo_reading_state = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    row.kobo_reading_state.current_bookmark = ub.KoboBookmark(
        progress_percent=35, location_source="chapter.xhtml", location_type="KoboSpan",
        location_value="kobo.3.1", created_at=datetime(2020, 1, 1))
    row.kobo_reading_state.statistics = ub.KoboStatistics(spent_reading_minutes=18)
    world.session.add(row)
    world.session.commit()
    return row


def snapshot(session, models):
    return {model.__tablename__: [dict(row._mapping) for row in
            session.execute(model.__table__.select().order_by(model.id))]
            for model in models}


@pytest.mark.parametrize("status,code", [("did_not_finish", 3), ("on_hold", 4)])
def test_explicit_pause_changes_only_callers_status_and_retains_every_carrier(world, status, code):
    """Real status write cannot reset positions, history, annotations or another user."""
    session = world.session
    row = seed(world)
    other = seed(world, status=1, user_id=8)
    session.add_all([
        ub.Bookmark(user_id=7, book_id=42, format="epub", bookmark_key="epubcfi(/6/4)",
                    updated_at=datetime(2020, 1, 1)),
        KOSyncProgress(user_id=7, document="digest", progress="/body/p[2]",
                       percentage=35, device="KOReader"),
        ub.Annotation(user_id=7, book_id=42, annotation_id="note", note_text="Keep me"),
        ub.Downloads(user_id=7, book_id=42),
    ])
    session.commit()
    carriers = [ub.Bookmark, KOSyncProgress, ub.Annotation, ub.Downloads,
                ub.KoboBookmark, ub.KoboStatistics]
    before = snapshot(session, carriers)
    other_before = (other.read_status, other.last_modified, other.times_started_reading)
    assert helper.set_explicit_book_read_status(42, status) == ""
    session.expire_all()
    assert row.read_status == code
    assert (row.times_started_reading, row.last_time_started_reading) == (5, datetime(2020, 1, 1))
    assert snapshot(session, carriers) == before
    assert (other.read_status, other.last_modified, other.times_started_reading) == other_before
    assert session.query(world.column).filter_by(book=42).one().value is True
    assert helper.read_statuses_for_books([(42, True)], CC_ID, USER) == {42: status}
    other_user = SimpleNamespace(id=8, is_authenticated=True, is_anonymous=False)
    assert helper.read_statuses_for_books([(42, True)], CC_ID, other_user) == {42: "finished"}


@pytest.mark.parametrize("paused", [3, 4])
def test_automatic_web_and_kosync_progress_advance_while_pause_and_shared_bool_survive(world, paused):
    """Removing the percentage-writer pause guard unpauses and increments start count."""
    from cps.services import reading_position
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    row = seed(world, paused)
    choice_clock = row.last_modified
    writes = []
    world.monkeypatch.setattr(kosync, "get_book_checksums", lambda bid: ["digest"])
    world.monkeypatch.setattr(kosync, "_mark_custom_read_column", writes.append)
    assert reading_position.record_web_reader_progress(USER, 42, 60) is True
    world.session.commit()
    assert row.read_status == paused
    assert row.times_started_reading == 5
    assert world.session.query(KOSyncProgress).filter_by(document="42").one().percentage == 60
    assert kosync.update_book_read_status(USER, 42, 100).accepted is True
    world.session.commit()
    assert row.kobo_reading_state.current_bookmark.progress_percent == 100
    assert row.read_status == paused
    assert row.times_started_reading == 5
    assert writes == []
    assert row.last_modified == choice_clock


@pytest.mark.parametrize("paused", [3, 4])
@pytest.mark.parametrize("source", ["percentage", "kobo", "custom_column"])
def test_automatic_writer_with_stale_identity_cannot_erase_committed_pause(world, paused, source):
    """The persisted choice must win even when a second session cached Reading."""
    from cps import kobo
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    row = seed(world)
    writer = sessionmaker(bind=world.session.get_bind())()
    patch = world.monkeypatch
    try:
        stale = writer.query(ub.ReadBook).filter_by(user_id=7, book_id=42).one()
        stale_state = stale.kobo_reading_state
        assert stale.read_status == 2
        assert helper.set_explicit_book_read_status(
            42, "did_not_finish" if paused == 3 else "on_hold") == ""
        choice_clock = row.last_modified
        patch.setattr(ub, "session", writer)
        patch.setattr(ub, "session_commit", lambda *a, **k: writer.commit() or True)
        shared_writes = []
        patch.setattr(kosync, "_mark_custom_read_column", shared_writes.append)
        patch.setattr(helper, "set_custom_read_column_value",
                      lambda *a, **k: shared_writes.append(a) or True)
        patch.setattr(kobo, "current_user", USER)
        if source == "percentage":
            assert kosync.update_book_read_status(USER, 42, 100).accepted
        elif source == "custom_column":
            patch.setattr(kobo, "calibre_db", helper.calibre_db)
            assert kobo.reconcile_custom_read_column_for_kobo([42], datetime(2020, 1, 1)) == 0
        else:
            patch.setattr(kobo.calibre_db, "get_book_by_uuid_for_kobo",
                          lambda *a, **k: SimpleNamespace(id=42, uuid="42", data=[object()]))
            patch.setattr(kobo, "get_or_create_reading_state", lambda bid: stale_state)
            patch.setattr(kobo, "push_reading_state_to_hardcover", lambda *a, **k: None)
            patch.setattr(kobo, "share_kobo_progress_with_koreader", lambda *a, **k: None)
            payload = {"ReadingStates": [{"LastModified": "2090-01-01T00:00:00Z",
                "CurrentBookmark": {"ProgressPercent": 100},
                "Statistics": {"SpentReadingMinutes": 25}, "StatusInfo": {"Status": "Finished"}}]}
            app = flask.Flask(__name__)
            with app.test_request_context("/v1/library/42/state", method="PUT", json=payload):
                result = inspect.unwrap(kobo.HandleStateRequest)("42")
                assert (result[1] if isinstance(result, tuple) else result.status_code) == 200
        writer.commit()
        world.session.expire_all()
        assert (row.read_status, row.last_modified, row.times_started_reading) == (paused, choice_clock, 5)
        assert shared_writes == []
        if source != "custom_column":
            assert row.kobo_reading_state.current_bookmark.progress_percent == 100
        if source == "kobo":
            assert row.kobo_reading_state.statistics.spent_reading_minutes == 25
    finally:
        writer.close()


def test_passive_progress_on_duplicate_does_not_outrank_newer_explicit_resume(world):
    """Merging after a later progress report uses the pause/resume choice clock."""
    from cps.user_book_data import migrate_user_book_data
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    paused = seed(world, 4, book_id=43)
    resumed = seed(world, 2, book_id=42)
    paused.last_modified = paused.read_status_choice_at = datetime(2020, 1, 1)
    resumed.last_modified = resumed.read_status_choice_at = datetime(2025, 1, 1)
    world.session.commit()
    kosync.update_book_read_status(USER, 43, 60)
    world.session.commit()
    assert paused.last_modified == datetime(2020, 1, 1)
    migrate_user_book_data(43, 42, world.session)
    world.session.commit()
    assert (resumed.read_status, resumed.last_modified) == (2, datetime(2025, 1, 1))
    assert resumed.times_started_reading == 10


@pytest.mark.parametrize("paused_status", [3, 4])
@pytest.mark.parametrize("percentage", [60, 100])
def test_active_duplicate_progress_cannot_erase_newer_explicit_pause(world, paused_status, percentage):
    """Both unchanged Reading and derived Finished remain activity, not resume intent."""
    from cps.user_book_data import migrate_user_book_data
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    active = seed(world, 2, book_id=43)
    paused = seed(world, paused_status, book_id=42)
    active.last_modified = active.read_status_choice_at = datetime(2020, 1, 1)
    paused.last_modified = paused.read_status_choice_at = datetime(2025, 1, 1)
    world.session.commit()
    assert kosync.update_book_read_status(USER, 43, percentage).accepted
    world.session.commit()
    assert active.read_status == (1 if percentage == 100 else 2)
    assert active.read_status_choice_at == datetime(2020, 1, 1)
    migrate_user_book_data(43, 42, world.session)
    world.session.commit()
    assert (paused.read_status, paused.read_status_choice_at) == (paused_status, datetime(2025, 1, 1))
    assert paused.times_started_reading == 10


@pytest.mark.parametrize("paused_status", [3, 4])
def test_automatic_created_duplicate_has_no_choice_clock_to_override_pause(world, paused_status):
    from cps.user_book_data import migrate_user_book_data
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    paused = seed(world, paused_status)
    paused.read_status_choice_at = datetime(2025, 1, 1)
    world.session.commit()
    assert kosync.update_book_read_status(USER, 43, 100).accepted
    world.session.commit()
    automatic = world.session.query(ub.ReadBook).filter_by(user_id=7, book_id=43).one()
    assert automatic.read_status == 1
    assert automatic.read_status_choice_at is None
    migrate_user_book_data(43, 42, world.session)
    world.session.commit()
    assert (paused.read_status, paused.read_status_choice_at) == (paused_status, datetime(2025, 1, 1))


def test_duplicate_relocation_preserves_paused_choice_clock(world):
    """Repointing a lone paused row is maintenance, not a fresh user choice."""
    from cps.user_book_data import migrate_user_book_data
    row = seed(world, 3, book_id=43)
    row.last_modified = datetime(2020, 1, 1)
    world.session.commit()
    migrate_user_book_data(43, 42, world.session)
    world.session.commit()
    world.session.expire_all()
    assert (row.book_id, row.read_status, row.last_modified) == (42, 3, datetime(2020, 1, 1))


def test_nonpaused_automatic_transition_uses_stored_counter_and_notifies_feed(world):
    """A stale Reading object cannot hide a real persisted Unread→Reading start."""
    from cps.services.reading_status import update_automatic_read_status
    row = seed(world)
    writer = sessionmaker(bind=world.session.get_bind())()
    try:
        stale = writer.query(ub.ReadBook).filter_by(user_id=7, book_id=42).one()
        stale.kobo_reading_state
        row.read_status = 0
        row.last_modified = datetime(2025, 1, 1)
        world.session.commit()
        world.monkeypatch.setattr(ub, "session", writer)
        assert stale.read_status == 2
        assert update_automatic_read_status(stale, 2, observed_clock=datetime(2030, 1, 1),
                                          require_newer_clock=True)
        writer.commit()
        world.session.expire_all()
        assert (row.read_status, row.times_started_reading, row.last_modified) == (2, 6, datetime(2030, 1, 1))
        assert row.kobo_reading_state.last_modified == datetime(2030, 1, 1)
    finally:
        writer.close()


def test_explicit_resume_finished_and_legacy_unread_follow_existing_carrier_rules(world):
    """The escape hatch is explicit; resume keeps positions, unread retains legacy reset."""
    row = seed(world, 4)
    assert helper.set_explicit_book_read_status(42, "in_progress") == ""
    assert row.read_status == 2
    assert row.times_started_reading == 6
    assert row.kobo_reading_state.current_bookmark.progress_percent == 35
    assert world.session.query(world.column).filter_by(book=42).one().value is False
    assert helper.set_explicit_book_read_status(42, "in_progress") == ""
    assert row.times_started_reading == 6
    assert helper.set_explicit_book_read_status(42, "finished") == ""
    assert row.read_status == 1
    assert world.session.query(world.column).filter_by(book=42).one().value is True
    assert helper.edit_book_read_status(42, False) == ""
    assert row.read_status == 0
    assert row.kobo_reading_state.current_bookmark.progress_percent is None
    assert world.session.query(world.column).filter_by(book=42).one().value is False


def call_status(app, book_id, body):
    with app.test_request_context("/api/v1/books/%s/read-status" % book_id,
                                  method="POST", json=body):
        result = inspect.unwrap(api_books.set_book_read_status)(book_id)
        response, code = result if isinstance(result, tuple) else (result, 200)
        return response.get_json(), code


@pytest.mark.parametrize("custom", [False, True])
def test_explicit_api_choices_and_legacy_boolean_toggle_stamp_intent_in_both_carriers(world, custom):
    """Every explicit choice, including same-state, records intent; counts stay stable."""
    world.monkeypatch.setattr(helper.config, "config_read_column", CC_ID if custom else 0)
    world.monkeypatch.setattr(api_books.calibre_db, "get_book_read_archived", lambda *a, **k: (object(), 2, False))
    world.monkeypatch.setattr(api_books.user_library, "contains_book", lambda *a: True)
    row = seed(world)
    app = flask.Flask(__name__)
    for status in ("unread", "finished", "in_progress", "did_not_finish", "on_hold"):
        row.read_status_choice_at = datetime(2020, 1, 1)
        world.session.commit()
        assert call_status(app, 42, {"status": status}) == ({"status": status}, 200)
        first = row.read_status_choice_at
        starts = row.times_started_reading
        assert first > datetime(2020, 1, 1)
        assert call_status(app, 42, {"status": status}) == ({"status": status}, 200)
        assert row.read_status_choice_at > first
        assert row.times_started_reading == starts
    for read in (True, False):
        previous = row.read_status_choice_at
        with app.test_request_context("/api/v1/books/42/read", method="POST", json={"read": read}):
            response = inspect.unwrap(api_books.toggle_book_read)(42)
            assert response.get_json() == {"read": read}
        assert row.read_status_choice_at > previous
        assert row.read_status == (1 if read else 0)


def test_api_status_contract_rejects_invalid_hidden_and_outside_library_writes(world):
    """Drive the status API to persistence; invalid/forbidden/missing writes leave it alone."""
    app = flask.Flask(__name__)
    row = seed(world)
    world.monkeypatch.setattr(api_books.calibre_db, "get_book_read_archived",
                            lambda bid, *a, **k: (object(), 2, False) if bid == 42 else None)
    world.monkeypatch.setattr(api_books.user_library, "contains_book", lambda user, bid: True)
    for body in (None, [], {"status": "reading"}, {"status": True}, {"status": []}):
        assert call_status(app, 42, body)[1] == 400
    assert row.read_status == 2
    assert call_status(app, 999, {"status": "on_hold"})[1] == 404
    world.monkeypatch.setattr(api_books.user_library, "contains_book", lambda user, bid: False)
    assert call_status(app, 42, {"status": "on_hold"})[1] == 403
    assert row.read_status == 2
    world.monkeypatch.setattr(api_books.user_library, "contains_book", lambda user, bid: True)
    assert call_status(app, 42, {"status": "on_hold"}) == ({"status": "on_hold"}, 200)
    assert row.read_status == 4
    world.monkeypatch.setattr(api_books, "current_user", SimpleNamespace(
        id=8, is_authenticated=True, is_anonymous=True))
    assert call_status(app, 42, {"status": "finished"})[1] == 403
    assert world.session.query(world.column).filter_by(book=42).one().value is True


@pytest.mark.parametrize("custom", [False, True])
def test_exact_and_legacy_filters_agree_across_catalog_search_classic_and_magic(world, custom):
    """Real SQL preserves historical unread/in-progress overlap, excludes pauses and isolates users."""
    from cps import search, web, magic_shelf
    patch = world.monkeypatch
    patch.setattr(helper.config, "config_read_column", CC_ID if custom else 0)
    patch.setattr(search, "current_user", USER)
    patch.setattr(web, "current_user", USER)
    states = {42: 4, 43: 3, 44: 2, 45: 1}
    for bid, status in states.items():
        seed(world, status, book_id=bid)
    seed(world, 4, user_id=8, book_id=46)
    if custom:
        world.session.add(world.column(book=45, value=True))
        world.session.commit()
    query = world.session.query(db.Books.id).outerjoin(
        ub.ReadBook, and_(ub.ReadBook.book_id == db.Books.id, ub.ReadBook.user_id == 7))
    if custom:
        query = query.outerjoin(world.column, world.column.book == db.Books.id)
    def ids(condition):
        return sorted(bid for bid, in query.filter(condition))
    expected = {"on_hold": [42], "did_not_finish": [43], "in_progress": [44],
                "read": [45], "unread": [44, 46, 47, 48, 49]}
    for status, found in expected.items():
        assert ids(api_books._build_read_filter(status)) == found
        assert ids(search.adv_search_read_status("finished" if status == "read" else status)) == found
        code = {"on_hold": 4, "did_not_finish": 3, "in_progress": 2, "read": 1, "unread": 0}[status]
        rule = {"id": "read_status", "operator": "equal", "value": code}
        assert ids(magic_shelf.build_filter_from_rule(rule, user_id=7)) == found
    captured = []
    def fill(*args, **kwargs):
        captured.append(ids(args[3]))
        return [], None, SimpleNamespace(total_count=0)
    patch.setattr(web.calibre_db, "fill_indexpage", fill)
    patch.setattr(web, "render_title_template", lambda *a, **k: k)
    app = flask.Flask(__name__)
    Babel(app)
    with app.test_request_context():
        web.render_read_books(1, False, order=([], "stored"))
        web.render_personal_read_status_books(1, 4, order=([], "stored"))
    assert captured == [expected["unread"], [42]]


@pytest.mark.parametrize("paused", [3, 4])
def test_kobo_automatic_status_echo_preserves_pause_even_with_completed_bookmark(world, paused):
    """The real Kobo PUT may advance position/stats but cannot change caller or shared bool."""
    from cps import kobo
    row = seed(world, paused)
    patch = world.monkeypatch
    patch.setattr(kobo, "current_user", USER)
    patch.setattr(kobo.calibre_db, "get_book_by_uuid_for_kobo",
                  lambda *a, **k: SimpleNamespace(id=42, uuid="42", data=[object()]))
    patch.setattr(kobo, "get_or_create_reading_state", lambda bid: row.kobo_reading_state)
    patch.setattr(kobo, "push_reading_state_to_hardcover", lambda *a, **k: None)
    patch.setattr(kobo, "share_kobo_progress_with_koreader", lambda *a, **k: None)
    shared_writes = []
    patch.setattr(helper, "set_custom_read_column_value", lambda *a, **k: shared_writes.append(a) or True)
    app = flask.Flask(__name__)
    for status in ("ReadyToRead", "Reading", "Finished"):
        payload = {"ReadingStates": [{"LastModified": "2090-01-01T00:00:00Z",
            "CurrentBookmark": {"ProgressPercent": 100},
            "Statistics": {"SpentReadingMinutes": 25}, "StatusInfo": {"Status": status}}]}
        with app.test_request_context("/v1/library/42/state", method="PUT", json=payload):
            result = inspect.unwrap(kobo.HandleStateRequest)("42")
            assert (result[1] if isinstance(result, tuple) else result.status_code) == 200
        assert row.read_status == paused
        assert row.times_started_reading == 5
    assert shared_writes == []
    assert row.kobo_reading_state.current_bookmark.progress_percent == 100
    assert row.kobo_reading_state.statistics.spent_reading_minutes == 25
    assert kobo.get_read_status_for_kobo(row) == "ReadyToRead"


@pytest.mark.parametrize("source_status,target_status,source_newer,expected", [
    (3, 1, True, 3), (1, 4, False, 4), (2, 4, True, 2)])
def test_duplicate_merge_keeps_latest_paused_choice_and_explicit_resume(
        world, source_status, target_status, source_newer, expected):
    """Duplicate removal must not rank DNF/Hold as unread or discard latest explicit resume."""
    from cps.user_book_data import migrate_user_book_data
    source = seed(world, source_status, book_id=43)
    target = seed(world, target_status, book_id=42)
    source.last_modified = source.read_status_choice_at = datetime(2025 if source_newer else 2020, 1, 1)
    target.last_modified = target.read_status_choice_at = datetime(2020 if source_newer else 2025, 1, 1)
    world.session.commit()
    migrate_user_book_data(43, 42, world.session)
    world.session.commit()
    assert target.read_status == expected
    assert target.last_modified == datetime(2025, 1, 1)
    assert target.times_started_reading == 10
    assert target.kobo_reading_state.current_bookmark.progress_percent == 35


def test_ordinary_reader_open_explicitly_resumes_custom_column_pause_once(world):
    """An ordinary read open resumes without rewinding; refresh does not add a start."""
    from cps import web
    from cps import cwa_db_loader
    row = seed(world, 4)
    patch = world.monkeypatch
    patch.setattr(web, "current_user", USER)
    patch.setattr(web, "calibre_db", SimpleNamespace(
        get_filtered_book=lambda *a, **k: world.session.get(db.Books, 42),
        order_authors=lambda *a, **k: []))
    patch.setattr(web, "render_title_template", lambda *a, **k: k)
    patch.setattr(cwa_db_loader, "load_cwa_db", lambda: SimpleNamespace(
        CWA_DB=lambda: SimpleNamespace(log_activity=lambda *a, **k: None)))
    app = flask.Flask(__name__)
    previous_choice = row.read_status_choice_at
    for _ in range(2):
        with app.test_request_context("/read/42/epub"):
            result = inspect.unwrap(web.read_book)(42, "epub")
        assert result["kosync_progress"] == 35
        assert row.read_status == 2
        assert row.times_started_reading == 6
        assert row.read_status_choice_at > previous_choice
        previous_choice = row.read_status_choice_at
    assert world.session.query(world.column).filter_by(book=42).one().value is False
    assert row.kobo_reading_state.current_bookmark.location_value == "kobo.3.1"


def test_legacy_koreader_projection_and_explicit_device_choice(world):
    """Legacy device clients get unread; an explicit device menu choice can resume."""
    from cps.services import koreader_library
    import cps
    world.monkeypatch.setattr(cps, "calibre_db", helper.calibre_db)
    row = seed(world, 3)
    cdb = SimpleNamespace(session=world.session)
    assert koreader_library.read_statuses(USER, {42}, session=world.session,
                                         cdb=cdb, read_column=CC_ID) == {42: "unread"}
    assert koreader_library.set_read_status(USER, 42, "reading") == ""
    assert row.read_status == 2
    assert row.times_started_reading == 6
    assert row.read_status_choice_at > datetime(2020, 1, 1)
    assert row.kobo_reading_state.current_bookmark.progress_percent == 35


def test_list_wire_status_is_exact_even_when_shared_bool_is_true(world):
    """Actual API list serialization retains 3/4 and keeps both legacy booleans false."""
    seed(world, 4)
    row = world.session.query(db.Books, world.column.value).join(
        world.column, world.column.book == db.Books.id).filter(db.Books.id == 42).one()
    app = flask.Flask(__name__)
    with app.test_request_context():
        item = api_books._rows_to_items([row])[0]
    assert (item["read_status"], item["read"], item["in_progress"]) == ("on_hold", False, False)


@pytest.mark.parametrize("status", [3, 4])
def test_kobo_custom_column_reconciliation_preserves_personal_pause(world, status):
    """A shared finished column cannot erase an explicit pause during an ordinary sync."""
    from cps import kobo
    row = seed(world, status)
    world.monkeypatch.setattr(kobo, "current_user", USER)
    world.monkeypatch.setattr(kobo, "calibre_db", helper.calibre_db)
    before = (row.last_modified, row.kobo_reading_state.last_modified,
              row.kobo_reading_state.current_bookmark.progress_percent)
    assert kobo.reconcile_custom_read_column_for_kobo([42], datetime(2020, 1, 1)) == 0
    world.session.commit()
    assert row.read_status == status
    assert (row.last_modified, row.kobo_reading_state.last_modified,
            row.kobo_reading_state.current_bookmark.progress_percent) == before


@pytest.mark.parametrize("status,label", [(3, "Did not finish"), (4, "On hold")])
def test_classic_card_paused_overlay_suppresses_shared_read_badge(status, label):
    """Render the real macro with a true shared carrier and a personal paused overlay."""
    from pathlib import Path
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parents[2] / "cps/templates")))
    env.globals.update(_=lambda text: text, url_for=lambda *a, **k: "#",
                       g=SimpleNamespace(paused_read_statuses={42: status},
                                         book_shelves_map={}, favorite_book_ids=set()))
    for name in ("get_cover_srcset", "get_series_srcset", "last_modified", "cache_timestamp"):
        env.filters[name] = lambda *a, **k: ""
    template = env.from_string("{% import 'image.html' as image %}{{ image.cover_badges(book, true) }}")
    output = template.render(book=SimpleNamespace(id=42))
    assert label in output
    assert "cover-badge-paused" in output
    assert "cover-badge-read" not in output


@pytest.mark.parametrize("status,code", [("did_not_finish", 3), ("on_hold", 4)])
def test_exact_pause_filter_survives_unavailable_shared_read_column(world, status, code):
    """A personal pause filter must not widen when the shared bool is missing."""
    seed(world, code)
    world.monkeypatch.setattr(helper.config, "config_read_column", CC_ID + 1)
    ids = [bid for (bid,) in world.session.query(db.Books.id)
           .filter(api_books._build_read_filter(status)).all()]
    assert ids == [42]
