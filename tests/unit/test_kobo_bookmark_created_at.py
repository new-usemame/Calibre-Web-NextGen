# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the KoboBookmark.created_at field — the "started reading" date.

Covers each piece of the change:
  * before_flush stamps created_at once, when progress first crosses > 0
  * the reading-state merge keeps the *earliest* created_at
  * the detail API surfaces created_at/last_modified and stays None-safe
  * the schema migration adds the column idempotently
"""
import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import flask
import pytest
from sqlalchemy import create_engine, text


# --- before_flush: created_at stamping ---------------------------------------

def _session(new=(), dirty=()):
    return SimpleNamespace(new=list(new), dirty=list(dirty), deleted=[])


@pytest.mark.unit
def test_before_flush_stamps_created_at_on_first_progress():
    from cps import ub
    bm = ub.KoboBookmark()
    bm.progress_percent = 12.5
    assert bm.created_at is None
    ub.receive_before_flush(_session(new=[bm]), None, None)
    assert isinstance(bm.created_at, datetime)


@pytest.mark.unit
def test_before_flush_no_stamp_at_zero_progress():
    from cps import ub
    bm = ub.KoboBookmark()
    bm.progress_percent = 0
    ub.receive_before_flush(_session(new=[bm]), None, None)
    assert bm.created_at is None


@pytest.mark.unit
def test_before_flush_does_not_overwrite_existing_created_at():
    from cps import ub
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bm = ub.KoboBookmark()
    bm.created_at = original
    bm.progress_percent = 80
    ub.receive_before_flush(_session(dirty=[bm]), None, None)
    assert bm.created_at == original


# --- reading-state merge: earliest created_at wins ---------------------------

def _bookmark(created_at, last_modified, progress):
    return SimpleNamespace(
        created_at=created_at, last_modified=last_modified,
        progress_percent=progress, content_source_progress_percent=None,
        location_source=None, location_type=None, location_value=None,
    )


@pytest.mark.unit
def test_merge_keeps_earliest_created_at_even_when_winner_is_newer():
    from cps import ub
    early = datetime(2026, 1, 1)
    late = datetime(2026, 3, 1)
    # winner's bookmark is the newer one (higher last_modified) but started later
    winner = SimpleNamespace(id=1, current_bookmark=_bookmark(late, datetime(2026, 4, 1), 50))
    loser = SimpleNamespace(id=2, current_bookmark=_bookmark(early, datetime(2026, 2, 1), 30))
    ub._merge_kobo_bookmark(None, winner, loser)
    assert winner.current_bookmark.created_at == early


@pytest.mark.unit
def test_merge_fills_created_at_when_winner_has_none():
    from cps import ub
    early = datetime(2026, 1, 1)
    winner = SimpleNamespace(id=1, current_bookmark=_bookmark(None, datetime(2026, 4, 1), 50))
    loser = SimpleNamespace(id=2, current_bookmark=_bookmark(early, datetime(2026, 2, 1), 30))
    ub._merge_kobo_bookmark(None, winner, loser)
    assert winner.current_bookmark.created_at == early


@pytest.mark.unit
def test_merge_keeps_winner_created_at_when_it_is_earlier():
    from cps import ub
    early = datetime(2026, 1, 1)
    winner = SimpleNamespace(id=1, current_bookmark=_bookmark(early, datetime(2026, 4, 1), 50))
    loser = SimpleNamespace(id=2, current_bookmark=_bookmark(datetime(2026, 3, 1), datetime(2026, 2, 1), 30))
    ub._merge_kobo_bookmark(None, winner, loser)
    assert winner.current_bookmark.created_at == early


# --- detail API: surfaces timestamps, stays None-safe ------------------------

def _fake_book():
    return SimpleNamespace(
        id=7, title="T", series_index="1.0", has_cover=0,
        authors=[], series=[], data=[], comments=[], tags=[],
        languages=[], publishers=[], identifiers=[], pubdate=None,
    )


def _call_detail_with_bookmark(bookmark):
    from cps.api import books as books_mod
    from cps import ub

    def query_side_effect(model):
        q = MagicMock()
        if model is ub.KoboReadingState:
            q.filter.return_value.first.return_value = SimpleNamespace(current_bookmark=bookmark)
        else:
            q.filter.return_value.first.return_value = None
        return q

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/7"):
        with patch.object(books_mod.calibre_db, "get_book_read_archived",
                          return_value=(_fake_book(), 0, False)), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(books_mod.ub, "session", MagicMock(query=MagicMock(side_effect=query_side_effect))), \
             patch("cps.api.books.get_locale", return_value="en"), \
             patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            resp = inspect.unwrap(books_mod.book_detail)(7)
    return json.loads(resp.get_data(as_text=True))


@pytest.mark.unit
def test_detail_surfaces_timestamps():
    modified = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    started = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    data = _call_detail_with_bookmark(SimpleNamespace(
        progress_percent=45.0, last_modified=modified, created_at=started))
    assert data["kosync_progress"] == 45.0
    assert data["kosync_progress_timestamp"] == modified.isoformat()
    assert data["kosync_progress_created_at"] == started.isoformat()


@pytest.mark.unit
def test_detail_none_safe_when_created_at_missing():
    """Regression: a pre-migration bookmark has created_at=None. The endpoint
    must still return progress/timestamp instead of swallowing everything."""
    modified = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    data = _call_detail_with_bookmark(SimpleNamespace(
        progress_percent=45.0, last_modified=modified, created_at=None))
    assert data["kosync_progress"] == 45.0
    assert data["kosync_progress_timestamp"] == modified.isoformat()
    assert data["kosync_progress_created_at"] is None


@pytest.mark.unit
def test_detail_treats_naive_datetimes_as_utc():
    """DB rows come back naive (SQLite has no tz) but represent UTC wall-clock.
    The serialized ISO string must carry a +00:00 offset regardless of the
    server's local timezone, so the naive value is pinned to UTC."""
    modified = datetime(2026, 3, 1, 12, 0)  # naive, as read back from SQLite
    started = datetime(2026, 1, 1, 8, 0)
    data = _call_detail_with_bookmark(SimpleNamespace(
        progress_percent=45.0, last_modified=modified, created_at=started))
    assert data["kosync_progress_timestamp"] == modified.replace(tzinfo=timezone.utc).isoformat()
    assert data["kosync_progress_created_at"] == started.replace(tzinfo=timezone.utc).isoformat()


# --- migration: adds the column idempotently ---------------------------------

@pytest.mark.unit
def test_migration_adds_column_and_is_idempotent():
    from cps import ub
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE kobo_bookmark (id INTEGER PRIMARY KEY)"))

    ub.migrate_kobo_bookmark_created_at(engine, None)
    ub.migrate_kobo_bookmark_created_at(engine, None)  # second run must be a no-op

    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(kobo_bookmark)")).fetchall()}
    assert "created_at" in cols


@pytest.mark.unit
def test_migration_uses_lock_retry_ddl_helper():
    from cps import ub
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE kobo_bookmark (id INTEGER PRIMARY KEY)"))

    with patch.object(ub, "_run_ddl_with_retry", wraps=ub._run_ddl_with_retry) as retry:
        ub.migrate_kobo_bookmark_created_at(engine, None)

    retry.assert_called_once_with(
        engine, "ALTER TABLE kobo_bookmark ADD COLUMN created_at DATETIME"
    )


@pytest.mark.unit
def test_migration_noop_without_table():
    from cps import ub
    engine = create_engine("sqlite://")
    ub.migrate_kobo_bookmark_created_at(engine, None)  # must not raise
