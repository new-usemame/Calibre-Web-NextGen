# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behaviour tests for ``GET /kosync/export``
(cps/progress_syncing/protocols/kosync.py).

Runs the endpoint's real SQL against two in-memory SQLite DBs — the app DB
(``ub.Base``) and the Calibre metadata DB (``cps.db.Base``). They're separate
databases that can't be SQL-joined, so the endpoint stitches them in Python;
only the trust boundary (auth + feature gate) is stubbed.
"""

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import cps
from cps import ub
from cps.progress_syncing.models import KOSyncProgress

pytestmark = pytest.mark.unit

CHECKSUM = "a" * 32  # a KOReader partial-MD5, the shape `document` really holds


def _kosync_module():
    # package __init__ rebinds the ``kosync`` name to the Blueprint; reach past
    # it to the module via sys.modules
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401 — populate sys.modules
    return sys.modules["cps.progress_syncing.protocols.kosync"]


def _calibre_engine():
    # Calibre tables live under an attached ``calibre`` schema (cps/db.py), so
    # ATTACH one before create_all; StaticPool keeps the one connection alive.
    def _creator():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("ATTACH DATABASE ':memory:' AS calibre")
        return conn

    engine = create_engine("sqlite+pysqlite://", creator=_creator, poolclass=StaticPool)
    from cps.db import Base
    Base.metadata.create_all(engine)
    return engine


def _app_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    ub.Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def env(monkeypatch):
    # export route wired to real in-memory app + Calibre DBs; mutable
    # ``user``/``enabled`` let tests flip the caller and the feature gate
    module = _kosync_module()

    app_session = sessionmaker(bind=_app_engine())()
    calibre_session = sessionmaker(bind=_calibre_engine())()

    state = SimpleNamespace(
        user=SimpleNamespace(id=1, name="alice"),
        enabled=True,
        app_session=app_session,
        calibre_session=calibre_session,
    )

    monkeypatch.setattr(module, "ub", SimpleNamespace(
        session=app_session, User=ub.User,
        KoboReadingState=ub.KoboReadingState, KoboBookmark=ub.KoboBookmark))
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: state.enabled)
    monkeypatch.setattr(module, "authenticate_user", lambda: state.user)
    # The export does `from ... import calibre_db`; that resolves cps.calibre_db.
    monkeypatch.setattr(cps, "calibre_db", SimpleNamespace(session=calibre_session), raising=False)

    # The export calls get_common_filters(user_id=..., strict=True), which reads
    # cps.duplicates' own ub/config globals off the request context. Wire them to
    # the in-memory app DB and seed a REAL user row (id=1) so every test exercises
    # the real, fail-closed visibility filter rather than a permissive fallback.
    import cps.duplicates as dup
    monkeypatch.setattr(dup, "ub", SimpleNamespace(
        session=app_session, User=ub.User,
        ArchivedBook=ub.ArchivedBook, UserHiddenBook=ub.UserHiddenBook))
    monkeypatch.setattr(dup, "config", SimpleNamespace(config_restricted_column=0))
    real_user = ub.User()
    real_user.id = 1
    real_user.name = "alice"
    app_session.add(real_user)
    app_session.commit()

    flask_app = Flask(__name__)
    flask_app.register_blueprint(module.kosync)
    state.client = flask_app.test_client()
    yield state
    app_session.close()
    calibre_session.close()


def _seed_progress(session, *, user_id=1, document=CHECKSUM, percentage=45.67,
                   device="KOReader", device_id="dev1", timestamp=None):
    row = KOSyncProgress(
        user_id=user_id, document=document, progress="/body/DocFragment[3]",
        percentage=percentage, device=device, device_id=device_id,
        timestamp=timestamp or datetime(2026, 7, 1, 10, 11, 12, tzinfo=timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


def _seed_book(session, *, title, authors):
    # ``authors`` items are names, or ``(name, sort)`` to pin Authors.sort
    from cps.db import Books, Authors

    pairs = [(a, a) if isinstance(a, str) else a for a in authors]
    now = datetime.now(timezone.utc)
    book = Books(title, title, pairs[0][0], now, now, "1.0", now, "path", 1, [], [])
    book.authors = [Authors(name, sort) for name, sort in pairs]
    session.add(book)
    session.commit()
    return book


def _seed_kobo_bookmark(session, *, user_id, book_id, created_at, last_modified):
    # reading state + bookmark for (user, book): the export's created_at source
    state = ub.KoboReadingState(user_id=user_id, book_id=book_id)
    state.current_bookmark = ub.KoboBookmark(
        created_at=created_at, last_modified=last_modified)
    session.add(state)
    session.commit()
    return state


# ── auth / feature gate (working behaviour) ─────────────────────────────────

def test_unauthenticated_returns_401(env):
    env.user = None
    resp = env.client.get("/kosync/export")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == _kosync_module().ERROR_UNAUTHORIZED_USER


def test_disabled_sync_is_blocked(env):
    env.enabled = False
    resp = env.client.get("/kosync/export")
    assert resp.status_code == 503


def test_no_progress_returns_empty_json_array(env):
    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert resp.get_json() == []


# ── the real export ─────────────────────────────────────────────────────────

def test_export_returns_the_users_progress(env):
    book = _seed_book(env.calibre_session, title="The Dispossessed",
                      authors=["Ursula K. Le Guin"])
    _seed_progress(env.app_session, document=str(book.id), percentage=45.67)

    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    row = body[0]
    assert row["calibre_book_id"] == book.id
    # exported as stored 0–100, not KOReader's 0–1 decimal
    assert row["percentage"] == pytest.approx(45.67)
    assert row["title"] == "The Dispossessed"
    assert row["authors"] == ["Ursula K. Le Guin"]


def test_export_includes_started_and_modified_timestamps(env):
    # created_at is the bookmark's (reading started); last_modified is the
    # progress row's, NOT the bookmark's (Kobo syncs touch the bookmark alone).
    book = _seed_book(env.calibre_session, title="Dune", authors=["Frank Herbert"])
    synced = datetime(2026, 7, 1, 10, 11, 12, tzinfo=timezone.utc)
    _seed_progress(env.app_session, document=str(book.id), timestamp=synced)
    started = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    modified = datetime(2026, 7, 1, 9, 30, 0, tzinfo=timezone.utc)
    _seed_kobo_bookmark(env.app_session, user_id=1, book_id=book.id,
                        created_at=started, last_modified=modified)

    row = env.client.get("/kosync/export").get_json()[0]
    # naive UTC in SQLite re-attaches its offset on the way out
    assert row["created_at"] == started.isoformat()
    assert row["last_modified"] == synced.isoformat()


def test_all_digit_checksum_is_omitted_too(env):
    # All-digit checksum is decimal-parseable; the length cap keeps its int out
    # of the in_() bind, which would otherwise overflow SQLite's int64.
    _seed_progress(env.app_session, document="1" * 32, percentage=5.0)

    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_decimal_document_with_no_library_book_is_omitted(env):
    # Distinct from a checksum: a book id that reaches the Calibre query but
    # matches nothing (book since deleted) — dropped, not a 500.
    _seed_progress(env.app_session, document="999", percentage=20.0)

    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_book_without_kobo_state_has_null_created_at(env):
    # No bookmark to source created_at from, but the row still exports.
    book = _seed_book(env.calibre_session, title="Neuromancer", authors=["William Gibson"])
    _seed_progress(env.app_session, document=str(book.id))

    row = env.client.get("/kosync/export").get_json()[0]
    assert row["title"] == "Neuromancer"
    assert row["created_at"] is None
    assert row["last_modified"] is not None


def test_multiple_authors_listed(env):
    # ordered by Authors.sort, which inverts display-name order here
    book = _seed_book(env.calibre_session, title="Co-Authored", authors=[
        ("Brandon Sanderson", "Sanderson, Brandon"),
        ("Robin Hobb", "Hobb, Robin")])
    _seed_progress(env.app_session, document=str(book.id))

    row = env.client.get("/kosync/export").get_json()[0]
    assert row["authors"] == ["Robin Hobb", "Brandon Sanderson"]


def test_resolvable_and_checksum_rows_coexist(env):
    # Mid-migration (#633): the skip is per-row, so a resolvable row survives
    # alongside a dropped legacy-checksum one.
    book = _seed_book(env.calibre_session, title="Snow Crash", authors=["Neal Stephenson"])
    _seed_progress(env.app_session, document=str(book.id), percentage=30.0)
    _seed_progress(env.app_session, document="c" * 32, percentage=80.0)

    body = env.client.get("/kosync/export").get_json()
    assert [r["calibre_book_id"] for r in body] == [book.id]
    assert body[0]["title"] == "Snow Crash"


def test_export_is_scoped_to_authenticated_user(env):
    mine = _seed_book(env.calibre_session, title="Mine", authors=["A"])
    theirs = _seed_book(env.calibre_session, title="Theirs", authors=["B"])
    _seed_progress(env.app_session, user_id=1, document=str(mine.id))
    _seed_progress(env.app_session, user_id=2, document=str(theirs.id))

    ids = {r["calibre_book_id"] for r in env.client.get("/kosync/export").get_json()}
    assert ids == {mine.id}  # never another user's rows


# ── security: per-user library-visibility restriction ───────────────────────

def _seed_hidden_book(session, *, user_id, book_id):
    # UserHiddenBook is one of the restrictions get_common_filters honours;
    # it's the cheapest to exercise in-harness (no tags/custom-column config).
    session.add(ub.UserHiddenBook(user_id=user_id, book_id=book_id))
    session.commit()


def test_export_excludes_books_hidden_from_this_user(env):
    # SECURITY REGRESSION (#978 review): `document` is attacker-controlled —
    # update_progress stores any non-empty key verbatim — so the export's
    # Calibre enrichment query MUST apply the user's visibility filter, or a
    # restricted account can seed ids 1..N and enumerate the title + authors of
    # books it isn't allowed to see. The env fixture already wires cps.duplicates
    # to the in-memory app DB and seeds the real user; here we just hide one book.
    visible = _seed_book(env.calibre_session, title="Visible", authors=["A"])
    hidden = _seed_book(env.calibre_session, title="Hidden", authors=["B"])
    _seed_progress(env.app_session, document=str(visible.id), percentage=10.0)
    _seed_progress(env.app_session, document=str(hidden.id), percentage=20.0)
    _seed_hidden_book(env.app_session, user_id=1, book_id=hidden.id)

    body = env.client.get("/kosync/export").get_json()
    titles = {r["title"] for r in body}
    ids = {r["calibre_book_id"] for r in body}
    assert titles == {"Visible"}          # the hidden book must not leak
    assert hidden.id not in ids
    assert visible.id in ids               # own visible progress still exported


def test_visibility_filter_fails_closed_not_open(env, monkeypatch):
    # SECURITY REGRESSION (#978 second review): get_common_filters defaults to a
    # permissive true() when it can't build the per-user filter. As an
    # authorization boundary the export must instead FAIL CLOSED — surface an
    # error, never a silently-unrestricted dump. Force the filter build to raise
    # and assert no metadata is returned.
    import cps.duplicates as dup

    def _boom(*a, **k):
        raise RuntimeError("simulated visibility-filter failure")

    monkeypatch.setattr(dup, "get_common_filters", _boom)

    book = _seed_book(env.calibre_session, title="Secret", authors=["A"])
    _seed_progress(env.app_session, document=str(book.id), percentage=10.0)

    resp = env.client.get("/kosync/export")
    assert resp.status_code != 200          # error, not a 200 dump
    assert b"Secret" not in resp.data       # no metadata leaked on failure


def test_get_common_filters_strict_raises_for_unknown_user(env):
    # Unit-level pin of the fail-closed contract: strict=True must raise (not
    # return a permissive filter) when the user can't be reloaded.
    import cps.duplicates as dup

    # known user (id=1, seeded by the fixture) builds a real filter
    built = dup.get_common_filters(user_id=1, strict=True)
    assert built is not None
    # unknown user under strict must raise, not fall open to true()
    with pytest.raises(Exception):
        dup.get_common_filters(user_id=99999, strict=True)
    # …and without strict it stays permissive (backwards-compatible default)
    assert dup.get_common_filters(user_id=99999) is not None


def test_export_chunks_large_id_set_without_bind_overflow(env):
    # DoS/robustness (#978 second review): a user with more numeric progress rows
    # than the SQLite bound-parameter limit must still export — the endpoint
    # chunks the IN() lookup. Seed >500 books (one chunk) + a few more so the
    # loop crosses a chunk boundary.
    n = 550
    books = [_seed_book(env.calibre_session, title=f"B{i}", authors=[f"Author {i}"])
             for i in range(n)]
    for b in books:
        _seed_progress(env.app_session, document=str(b.id), percentage=1.0)

    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == n                 # every book exported across chunks
    assert len({r["calibre_book_id"] for r in body}) == n


def test_non_ascii_digit_document_is_not_aliased(env):
    # `str.isdecimal()` accepts non-ASCII digits (e.g. Arabic-Indic "١") that
    # int() folds onto the same value, so a non-ASCII document could alias — and
    # resolve to — a real book id. Require ASCII digits: seed book id 1, then a
    # progress row whose document is the Arabic-Indic "1"; it must NOT resolve.
    book = _seed_book(env.calibre_session, title="One", authors=["A"])
    assert book.id == 1  # first insert into a fresh in-memory Calibre DB
    assert int("١") == book.id  # the alias int() would otherwise fold to
    _seed_progress(env.app_session, document="١", percentage=5.0)

    resp = env.client.get("/kosync/export")
    assert resp.status_code == 200
    assert resp.get_json() == []  # ASCII guard drops it instead of resolving to book 1
