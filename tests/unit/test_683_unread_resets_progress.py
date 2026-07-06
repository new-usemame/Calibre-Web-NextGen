# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #683 — marking a book *unread* must reset its
stored reading position.

Reporter @uschi1 opened a book "to test it", got stuck showing a ghost
"0.6% read", and the read/unread toggle would not clear it. Root cause:
``helper.edit_book_read_status`` only flipped the read-status bit; it never
cleared the two stores that surface as the "% read" the user sees:

  * ``KoboReadingState.current_bookmark.progress_percent`` — the KOReader/Kobo
    synced percentage rendered on the classic detail page and the new-UI book
    page (fork #587);
  * ``ub.Bookmark`` — the web-reader (epub.js) resume point (keyed per format),
    which would re-derive the percentage on the next reader scroll if left in
    place.

These tests pin that ``reset_reading_position`` clears both, scoped to the one
user+book, and that ``edit_book_read_status`` calls it in *both* read-status
backends (built-in ``ReadBook`` and the custom read column) whenever the
resulting status is unread.
"""
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub, helper


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_progress(session, user_id, book_id, percent):
    krs = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    krs.current_bookmark = ub.KoboBookmark(
        progress_percent=percent, content_source_progress_percent=percent)
    krs.statistics = ub.KoboStatistics()
    session.add(krs)
    return krs


# --- behavioral: the reset helper -----------------------------------------

@pytest.mark.unit
def test_reset_clears_kobo_progress_percent(session):
    _make_progress(session, 1, 42, 0.6)
    session.commit()

    cleared = helper.reset_reading_position(session, 1, 42)
    session.commit()

    krs = session.query(ub.KoboReadingState).filter_by(user_id=1, book_id=42).first()
    assert krs.current_bookmark.progress_percent is None
    assert krs.current_bookmark.content_source_progress_percent is None
    assert cleared >= 1


@pytest.mark.unit
def test_reset_deletes_web_bookmark_all_formats(session):
    session.add(ub.Bookmark(user_id=1, book_id=42, format="EPUB", bookmark_key="epubcfi(/6/2)"))
    session.add(ub.Bookmark(user_id=1, book_id=42, format="KEPUB", bookmark_key="epubcfi(/6/4)"))
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    assert session.query(ub.Bookmark).filter_by(user_id=1, book_id=42).count() == 0


@pytest.mark.unit
def test_reset_scoped_to_user_and_book(session):
    _make_progress(session, 1, 42, 0.6)
    _make_progress(session, 1, 99, 0.5)   # same user, other book
    _make_progress(session, 2, 42, 0.7)   # other user, same book
    session.add(ub.Bookmark(user_id=1, book_id=99, format="EPUB", bookmark_key="x"))
    session.add(ub.Bookmark(user_id=2, book_id=42, format="EPUB", bookmark_key="y"))
    session.commit()

    helper.reset_reading_position(session, 1, 42)
    session.commit()

    # neighbors untouched
    assert session.query(ub.KoboReadingState).filter_by(
        user_id=1, book_id=99).first().current_bookmark.progress_percent == 0.5
    assert session.query(ub.KoboReadingState).filter_by(
        user_id=2, book_id=42).first().current_bookmark.progress_percent == 0.7
    assert session.query(ub.Bookmark).filter_by(user_id=1, book_id=99).count() == 1
    assert session.query(ub.Bookmark).filter_by(user_id=2, book_id=42).count() == 1


@pytest.mark.unit
def test_reset_noop_when_nothing_stored(session):
    # no KoboReadingState, no Bookmark → returns 0, never crashes
    assert helper.reset_reading_position(session, 1, 42) == 0


@pytest.mark.unit
def test_reset_handles_state_without_bookmark(session):
    session.add(ub.KoboReadingState(user_id=1, book_id=42))  # no current_bookmark
    session.commit()
    assert helper.reset_reading_position(session, 1, 42) == 0


@pytest.mark.unit
def test_reset_only_clears_already_zero_returns_zero(session):
    # progress already None: nothing to clear, but bookmark-less → 0
    krs = ub.KoboReadingState(user_id=1, book_id=42)
    krs.current_bookmark = ub.KoboBookmark()  # progress_percent defaults to None
    session.add(krs)
    session.commit()
    assert helper.reset_reading_position(session, 1, 42) == 0


# --- source-pins: the integration into edit_book_read_status ---------------

@pytest.mark.unit
def test_edit_read_status_resets_progress_when_unread():
    src = inspect.getsource(helper.edit_book_read_status)
    assert "reset_reading_position" in src, \
        "marking a book unread must reset its reading progress (#683)"
    # guarded by an unread notion, never called unconditionally on every toggle
    assert "now_unread" in src


@pytest.mark.unit
def test_edit_read_status_resets_in_both_status_backends():
    """Both the built-in ReadBook branch and the custom-read-column branch must
    reset — a future refactor that drops the reset from one path trips this."""
    src = inspect.getsource(helper.edit_book_read_status)
    assert src.count("reset_reading_position(") == 2


@pytest.mark.unit
def test_reset_helper_clears_both_percent_fields_and_bookmark():
    src = inspect.getsource(helper.reset_reading_position)
    assert "progress_percent" in src
    assert "content_source_progress_percent" in src, \
        "content-source percent must also be cleared or the ghost survives"
    assert "Bookmark" in src, "web-reader resume point must also be cleared (#683)"
