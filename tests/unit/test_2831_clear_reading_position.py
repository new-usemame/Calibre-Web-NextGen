# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2831: clearing a book's reading position reaches every place that holds it.

``reset_reading_position`` (what "Mark as unread" calls) cleared the web
bookmark, the Kobo bookmark and the shared KOSync rows, but never the
per-device journal (``DeviceReadingPosition``). ``GET /kosync/syncs/progress``
serves an identified device its own journal row when there is no shared row, so
a reset book was handed straight back to the KOReader that had reported it.

The same helper now also backs the position-only clear
(``POST /api/v1/books/<id>/clear-position``), which leaves the read status
alone so a finished book can be reset for a re-read and stay read.
"""
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cps import helper, ub
from tests.unit.test_share_place_kosync_gates import (  # noqa: F401 - shared real HTTP/SQLite fixture
    BOOK, _get, _get_json, _put, _register, _share, world,
)


def _journal(world, book_id=BOOK):
    world.session.expire_all()
    return (world.session.query(ub.DeviceReadingPosition)
            .filter_by(book_id=book_id).all())


def _reset(world, **kwargs):
    cleared = helper.reset_reading_position(
        world.session, world.reader.id, BOOK, **kwargs)
    world.session.commit()
    world.session.expire_all()
    return cleared


def _served_position(world, **kwargs):
    response = _get(world, **kwargs)
    if response.status_code != 200:
        return None
    return response.get_json().get("percentage")


@pytest.mark.unit
@pytest.mark.parametrize("shared", [False, True], ids=["private-device", "shared-device"])
def test_reset_is_not_handed_back_to_the_koreader_that_reported_it(world, shared):
    device = _register(world, "kindle-a", "Kindle A")
    if shared:
        _share(world, device, True)
    _put(world, 70, device_id="kindle-a", name="Kindle A")
    assert _served_position(world, device_id="kindle-a", name="Kindle A") == pytest.approx(0.70)
    assert len(_journal(world)) == 1

    _reset(world)

    assert _journal(world) == []
    assert _served_position(world, device_id="kindle-a", name="Kindle A") in (None, 0, 0.0)


@pytest.mark.unit
def test_position_only_clear_keeps_the_read_status(world):
    world.session.add(ub.ReadBook(
        user_id=world.reader.id, book_id=BOOK,
        read_status=ub.ReadBook.STATUS_FINISHED))
    world.session.commit()
    _register(world, "kindle-a", "Kindle A")
    _put(world, 70, device_id="kindle-a", name="Kindle A")
    assert len(_journal(world)) == 1

    _reset(world, keep_read_status=True)

    assert _journal(world) == []
    assert world.session.query(ub.ReadBook).filter_by(
        user_id=world.reader.id, book_id=BOOK).one().read_status == ub.ReadBook.STATUS_FINISHED


@pytest.mark.unit
def test_position_only_clear_leaves_the_currently_reading_marker_but_unread_folds_it(world):
    _register(world, "kindle-a", "Kindle A")
    _put(world, 40, device_id="kindle-a", name="Kindle A")
    # A private device's push does not touch the shared status, so set the
    # Currently reading marker the way the web reader / a shared sync would.
    world.session.add(ub.ReadBook(
        user_id=world.reader.id, book_id=BOOK,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    world.session.commit()

    def status():
        world.session.expire_all()
        return world.session.query(ub.ReadBook).filter_by(
            user_id=world.reader.id, book_id=BOOK).one().read_status

    assert status() == ub.ReadBook.STATUS_IN_PROGRESS
    _reset(world, keep_read_status=True)
    assert status() == ub.ReadBook.STATUS_IN_PROGRESS

    _reset(world)  # "Mark as unread"
    assert status() == ub.ReadBook.STATUS_UNREAD


@pytest.mark.unit
def test_clear_covers_kobo_devices_and_is_scoped_to_this_user_and_book(world):
    other = world.add_user("someone-else")
    mine = ub.Device(user_id=world.reader.id, kind="kobo", display_name="My Kobo")
    theirs = ub.Device(user_id=other.id, kind="kobo", display_name="Their Kobo")
    world.session.add_all([mine, theirs])
    world.session.flush()
    world.session.add_all([
        ub.DeviceReadingPosition(device_id=mine.id, book_id=BOOK, progress_percent=55.0),
        ub.DeviceReadingPosition(device_id=mine.id, book_id=2, progress_percent=10.0),
        ub.DeviceReadingPosition(device_id=theirs.id, book_id=BOOK, progress_percent=80.0),
    ])
    # The echo guard holds an earlier inexact delivery's place per device and
    # book. Left behind, a Kobo report matching that old place after a clear
    # is still read as an echo of a delivery rather than as a real move.
    armed = datetime.now(timezone.utc).replace(tzinfo=None)
    world.session.add_all([
        ub.KoboPlaceEchoGuard(device_id=device.id, book_id=book, progress_percent=55.0,
                              armed_at=armed)
        for device, book in ((mine, BOOK), (mine, 2), (theirs, BOOK))
    ])
    world.session.commit()

    _reset(world, keep_read_status=True)

    expected = sorted([(mine.id, 2), (theirs.id, BOOK)])
    for model in (ub.DeviceReadingPosition, ub.KoboPlaceEchoGuard):
        remaining = sorted(
            (row.device_id, row.book_id) for row in world.session.query(model).all())
        assert remaining == expected, model.__name__


# -- the route ----------------------------------------------------------------

def _call_route(world, *, visible=True, cleared=3, commit_ok=True):
    from cps.api import actions

    session = MagicMock()
    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=4,
                           role_browse_global=lambda: False)
    result = (SimpleNamespace(), False, False) if visible else None
    with world.app.test_request_context(
        "/api/v1/books/23/clear-position", method="POST"
    ), patch.object(actions, "current_user", user), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions.calibre_db, "get_book_read_archived",
                         return_value=result) as visible_book, \
            patch.object(actions.config, "config_read_column", 0, create=True), \
            patch.object(actions, "reset_reading_position", return_value=cleared) as reset, \
            patch.object(actions.ub, "session_commit", return_value=commit_ok) as commit, \
            patch.object(actions.ub, "session", session):
        response = inspect.unwrap(actions.clear_book_reading_position)(23)
    return response, reset, commit, visible_book, session


@pytest.mark.unit
def test_clear_position_route_clears_position_only_and_commits(world):
    response, reset, commit, visible_book, session = _call_route(world)

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "cleared": 3}
    reset.assert_called_once_with(session, 4, 23, keep_read_status=True)
    commit.assert_called_once()
    visible_book.assert_called_once_with(
        23, 0, allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=False, allow_public_shelf_books=True)


@pytest.mark.unit
def test_clear_position_route_is_idempotent_when_nothing_is_stored(world):
    response, reset, commit, *_ = _call_route(world, cleared=0)

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "cleared": 0}
    commit.assert_not_called()


@pytest.mark.unit
def test_clear_position_route_404s_for_a_book_the_user_cannot_see(world):
    response, reset, commit, *_ = _call_route(world, visible=False)

    assert response[1] == 404
    reset.assert_not_called()
    commit.assert_not_called()


@pytest.mark.unit
def test_clear_position_route_reports_a_failed_commit(world):
    response, *_ = _call_route(world, commit_ok=False)

    assert response[1] == 500
