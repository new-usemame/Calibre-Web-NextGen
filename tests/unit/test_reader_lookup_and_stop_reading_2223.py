# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral coverage for lookup-mode reader writes and stop-reading."""

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from cps.services.read_status import stop_reading


@pytest.mark.unit
def test_stop_reading_changes_only_active_status_and_preserves_carriers():
    Base = declarative_base()

    class ReadBook(Base):
        __tablename__ = "read_book"
        STATUS_UNREAD = 0
        STATUS_FINISHED = 1
        STATUS_IN_PROGRESS = 2
        id = Column(Integer, primary_key=True)
        user_id = Column(Integer, nullable=False)
        book_id = Column(Integer, nullable=False)
        read_status = Column(Integer, nullable=False)
        last_modified = Column(DateTime(timezone=True))
        read_status_choice_at = Column(DateTime(timezone=True))
        last_time_started_reading = Column(DateTime(timezone=True))
        times_started_reading = Column(Integer, nullable=False)

    class SavedCarrier(Base):
        __tablename__ = "saved_carrier"
        id = Column(Integer, primary_key=True)
        user_id = Column(Integer, nullable=False)
        book_id = Column(Integer, nullable=False)
        bookmark = Column(String, nullable=False)
        annotation = Column(String, nullable=False)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    modified = datetime(2025, 1, 2, tzinfo=timezone.utc)
    started = datetime(2024, 12, 3, tzinfo=timezone.utc)
    caller = ReadBook(user_id=10, book_id=7, read_status=ReadBook.STATUS_IN_PROGRESS,
                      last_modified=modified, last_time_started_reading=started,
                      times_started_reading=4)
    other_user = ReadBook(user_id=11, book_id=7, read_status=ReadBook.STATUS_IN_PROGRESS,
                          last_modified=modified, last_time_started_reading=started,
                          times_started_reading=2)
    finished = ReadBook(user_id=10, book_id=8, read_status=ReadBook.STATUS_FINISHED,
                        last_modified=modified, last_time_started_reading=started,
                        times_started_reading=1)
    carrier = SavedCarrier(user_id=10, book_id=7, bookmark="epubcfi(/6/8)",
                           annotation="highlight text")
    session.add_all([caller, other_user, finished, carrier])
    session.commit()

    assert stop_reading(session, 10, 7, ReadBook) is True
    session.commit()
    session.expire_all()

    changed = session.query(ReadBook).filter_by(user_id=10, book_id=7).one()
    untouched = session.query(ReadBook).filter_by(user_id=11, book_id=7).one()
    still_finished = session.query(ReadBook).filter_by(user_id=10, book_id=8).one()
    saved = session.query(SavedCarrier).filter_by(user_id=10, book_id=7).one()
    assert changed.read_status == ReadBook.STATUS_UNREAD
    assert changed.read_status_choice_at > modified.replace(tzinfo=None)
    assert untouched.read_status_choice_at is None
    assert still_finished.read_status_choice_at is None
    assert (changed.last_time_started_reading, changed.times_started_reading) == (
        started.replace(tzinfo=None), 4,
    )
    assert untouched.read_status == ReadBook.STATUS_IN_PROGRESS
    assert still_finished.read_status == ReadBook.STATUS_FINISHED
    assert (saved.bookmark, saved.annotation) == ("epubcfi(/6/8)", "highlight text")
    assert stop_reading(session, 10, 7, ReadBook) is False
    session.close()


@pytest.mark.unit
@pytest.mark.parametrize("lookup", [True, False])
def test_classic_reader_open_only_records_progress_outside_lookup_mode(lookup):
    from cps import web

    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=4, name="reader")
    book = SimpleNamespace(title="Fixture", data=[], series=[], ordered_authors=[])
    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = None
    db_session.query.return_value.filter.return_value.one_or_none.return_value = None
    query = "/read/23/pdf" + ("?lookup=1" if lookup else "")

    with web.app.test_request_context(query), \
            patch.object(web, "current_user", user), \
            patch.object(web.calibre_db, "get_filtered_book", return_value=book), \
            patch.object(web.calibre_db, "order_authors", return_value=[]), \
            patch.object(web.ub, "session", db_session), \
            patch.object(web.ub, "session_commit") as commit, \
            patch.object(web, "render_title_template", return_value="reader") as render:
        result = inspect.unwrap(web.read_book)(23, "pdf")

    assert result == "reader"
    assert render.call_args.kwargs["lookup_mode"] is lookup
    if lookup:
        assert db_session.query.call_count == 2  # read-only bookmark and Kobo lookups
        commit.assert_not_called()
        db_session.add.assert_not_called()
    else:
        assert commit.call_count == 1
        db_session.add.assert_called_once()


@pytest.mark.unit
def test_lookup_bookmark_post_is_noop_before_database_or_progress_side_effects():
    from cps import web

    with web.app.test_request_context(
        "/ajax/bookmark/23/epub?lookup=1", method="POST", data={"bookmark": ""}
    ), patch.object(web, "current_user", SimpleNamespace(id=4, is_authenticated=True)), \
            patch.object(web.ub, "session", MagicMock()) as db_session:
        response = inspect.unwrap(web.set_bookmark)(23, "epub")

    assert response == ("", 204)
    db_session.query.assert_not_called()
    db_session.session_commit.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("case,status", [("guest", 401), ("unavailable", 404),
                                         ("custom_finished", 409), ("finished", 409)])
def test_stop_reading_endpoint_guards_guest_visibility_and_finished_states(case, status):
    from cps.api import actions
    from cps import ub

    user = SimpleNamespace(is_authenticated=case != "guest", is_anonymous=case == "guest",
                           id=4, role_browse_global=lambda: False)
    session = MagicMock()
    row = SimpleNamespace(read_status=ub.ReadBook.STATUS_FINISHED)
    result = None if case == "unavailable" else (
        SimpleNamespace(), True if case == "custom_finished" else False, False)
    config_read_column = "#read" if case == "custom_finished" else 0

    with flask.Flask(__name__).test_request_context(
        "/api/v1/books/23/stop-reading", method="POST"
    ), patch.object(actions, "current_user", user), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions.calibre_db, "get_book_read_archived", return_value=result), \
            patch.object(actions.config, "config_read_column", config_read_column, create=True), \
            patch.object(actions.ub, "session", session):
        session.query.return_value.filter.return_value.one_or_none.return_value = row
        response = inspect.unwrap(actions.stop_reading_book)(23)

    assert response[1] == status if isinstance(response, tuple) else response.status_code == status
    session.commit.assert_not_called()


@pytest.mark.unit
def test_stop_reading_api_returns_idempotent_contract_and_updates_only_status():
    from cps.api import actions
    from cps import ub

    started = datetime(2024, 12, 3, tzinfo=timezone.utc)
    row = SimpleNamespace(
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
        last_modified=datetime(2025, 1, 2, tzinfo=timezone.utc),
        last_time_started_reading=started,
        times_started_reading=3,
    )
    session = MagicMock()
    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=4,
                           role_browse_global=lambda: False)

    with flask.Flask(__name__).test_request_context(
        "/api/v1/books/23/stop-reading", method="POST"
    ), patch.object(actions, "current_user", user), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions.calibre_db, "get_book_read_archived",
                         return_value=(SimpleNamespace(), False, False)) as visible_book, \
            patch.object(actions.config, "config_read_column", "#read", create=True), \
            patch.object(actions.ub, "session_commit", return_value=True) as commit, \
            patch.object(actions.ub, "session", session):
        session.query.return_value.filter.return_value.one_or_none.return_value = row
        response = inspect.unwrap(actions.stop_reading_book)(23)

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "changed": True}
    assert row.read_status == ub.ReadBook.STATUS_UNREAD
    assert (row.last_time_started_reading, row.times_started_reading) == (started, 3)
    commit.assert_called_once()
    visible_book.assert_called_once_with(
        23, "#read", allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=False, allow_public_shelf_books=True,
    )
