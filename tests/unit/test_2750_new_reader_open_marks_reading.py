# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2750: opening a book in the new UI reader marks it Currently Reading.

The classic ``/read`` route has always recorded a reader open (status to
IN_PROGRESS, one more started session, last-open time). The new UI reader
never called it, so a book read only there stayed Unread and the Currently
Reading shelf stayed empty. The SPA now posts ``reader-open`` once per open;
these tests pin what that route does to the caller's row.
"""

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _share_place_switch_off(monkeypatch):
    """The ungated (shared-place) path, as in test_reader_lookup_and_stop_reading_2223."""
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")


def _open(row, *, visible=True):
    from cps import web
    from cps.api import actions

    user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=4,
                           role_browse_global=lambda: False)
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = row
    result = (SimpleNamespace(), False, False) if visible else None
    with web.app.test_request_context(
        "/api/v1/books/23/reader-open", method="POST"
    ), patch.object(actions, "current_user", user), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions.calibre_db, "get_book_read_archived", return_value=result), \
            patch.object(actions.config, "config_read_column", 0, create=True), \
            patch("cps.ub.session", session), \
            patch("cps.ub.session_commit", return_value=True) as commit:
        response = inspect.unwrap(actions.reader_open_book)(23)
    return response, session, commit


@pytest.mark.unit
def test_first_open_creates_an_in_progress_row():
    from cps import ub

    response, session, commit = _open(None)

    assert response.status_code == 200
    added = session.add.call_args.args[0]
    assert (added.user_id, added.book_id) == (4, 23)
    assert added.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert added.times_started_reading == 1
    commit.assert_called_once()


@pytest.mark.unit
def test_open_of_an_unread_book_starts_it_and_counts_one_session():
    from cps import ub

    row = SimpleNamespace(read_status=ub.ReadBook.STATUS_UNREAD, times_started_reading=2,
                          last_time_started_reading=None, read_status_choice_at=None)
    _open(row)

    assert row.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert row.times_started_reading == 3
    assert row.last_time_started_reading is not None


@pytest.mark.unit
def test_reopening_an_in_progress_book_is_not_a_new_session():
    from cps import ub

    row = SimpleNamespace(read_status=ub.ReadBook.STATUS_IN_PROGRESS, times_started_reading=3,
                          last_time_started_reading=None, read_status_choice_at=None)
    _open(row)

    assert row.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert row.times_started_reading == 3


@pytest.mark.unit
def test_opening_a_finished_book_leaves_it_finished():
    from cps import ub

    choice = datetime(2025, 1, 2, tzinfo=timezone.utc)
    row = SimpleNamespace(read_status=ub.ReadBook.STATUS_FINISHED, times_started_reading=1,
                          last_time_started_reading=None, read_status_choice_at=choice)
    _open(row)

    assert row.read_status == ub.ReadBook.STATUS_FINISHED
    assert row.times_started_reading == 1
    assert row.read_status_choice_at == choice


@pytest.mark.unit
def test_a_book_the_caller_cannot_see_is_not_touched():
    response, session, commit = _open(None, visible=False)

    assert response[1] == 404
    session.add.assert_not_called()
    commit.assert_not_called()
