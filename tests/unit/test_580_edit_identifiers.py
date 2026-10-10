# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #580 — the SPA edit page can now edit identifiers (ISBN/ASIN/…).

Backend: /api/v1/books/<id>/metadata accepts an `identifiers` list of {type,val},
reconciles it against the book's existing rows via the same modify_identifiers
helper the legacy editor uses, and returns the current identifiers in the editable
metadata so the form can seed the table.
"""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import flask
import pytest

pytestmark = pytest.mark.unit


def _ctx(path, body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": "POST"}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _editor():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False, name="ed",
                           role_edit=lambda: True, role_delete_books=lambda: True, id=1)


def _fake_book(identifiers=None):
    return SimpleNamespace(
        id=5, title="T", authors=[], series=[], series_index=1.0,
        tags=[], publishers=[], languages=[], comments=[], ratings=[],
        identifiers=identifiers if identifiers is not None else [],
    )


def test_editable_metadata_includes_identifiers():
    from cps.api import edit as mod
    book = _fake_book([SimpleNamespace(type="isbn", val="123"),
                       SimpleNamespace(type="amazon", val="B01")])
    out = mod._editable_metadata(book)
    assert out["identifiers"] == [
        {"type": "isbn", "val": "123"},
        {"type": "amazon", "val": "B01"},
    ]


def test_shared_identifier_reconciler_ignores_submitted_internal_marker():
    from cps import editbooks
    session = MagicMock()
    digest = "c" * 64
    submitted_marker = SimpleNamespace(
        type=f"cwng_ingest_sha256_{digest}", val="forged",
    )

    changed, duplicate = editbooks.modify_identifiers(
        [submitted_marker], [], session,
    )

    assert changed is False
    assert duplicate is False
    session.add.assert_not_called()


def test_update_metadata_persists_identifiers_lowercased_and_skips_blank_rows():
    from cps.api import edit as mod
    session = MagicMock()
    captured = {}

    def fake_modify(inp, dbids, sess):
        captured["input"] = inp
        captured["dbids"] = dbids
        return True, False  # changed, no error

    body = {"identifiers": [
        {"type": "ISBN", "val": "9780000000001"},
        {"type": "", "val": "orphan-value"},   # blank type -> skipped
        {"type": "amazon", "val": ""},          # blank value -> skipped
        {"type": "Amazon", "val": "B01ABCDEFG"},
    ]}
    with _ctx("/api/v1/books/5/metadata", body=body):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_filtered_book=lambda *a, **k: _fake_book(), session=session,
                                          get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "modify_identifiers", side_effect=fake_modify), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    # only the two well-formed rows are built, types lowercased, book id threaded
    assert len(captured["input"]) == 2
    assert sorted(i.type for i in captured["input"]) == ["amazon", "isbn"]
    assert all(i.book == 5 for i in captured["input"])
    session.commit.assert_called_once()
    assert resp.status_code == 200


def test_update_metadata_duplicate_identifier_reports_field_error():
    from cps.api import edit as mod
    session = MagicMock()

    def fake_modify(inp, dbids, sess):
        # Realistic duplicate case: modify_identifiers may queue partial add/deletes
        # (changed=True) AND flag the duplicate (error=True). A rejected payload must
        # roll back, not commit the partial changes.
        return True, True

    body = {"identifiers": [{"type": "isbn", "val": "1"}, {"type": "isbn", "val": "2"}]}
    with _ctx("/api/v1/books/5/metadata", body=body):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_filtered_book=lambda *a, **k: _fake_book(), session=session,
                                          get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "modify_identifiers", side_effect=fake_modify), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    payload = json.loads(resp.get_data())
    assert "identifiers" in payload.get("errors", {})
    session.commit.assert_not_called()   # rejected payload must NOT persist
    session.rollback.assert_called()     # partial staged changes discarded


def test_update_metadata_without_identifiers_key_does_not_touch_them():
    """Omitting the key leaves identifiers alone (only present-in-payload fields
    are changed) — no modify_identifiers call, no commit."""
    from cps.api import edit as mod
    session = MagicMock()
    with _ctx("/api/v1/books/5/metadata", body={"title": "New"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_filtered_book=lambda *a, **k: _fake_book(), session=session,
                                          get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "modify_identifiers", side_effect=AssertionError("must not be called")), \
             patch.object(mod, "edit_book_param",
                          return_value=flask.Response(json.dumps({"success": True}),
                                                      mimetype="application/json")), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)
    assert resp.status_code == 200
    session.commit.assert_not_called()


@pytest.fixture
def calibre_session():
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from cps import db

    def creator():
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.execute("ATTACH DATABASE ':memory:' AS calibre")
        return connection

    engine = create_engine("sqlite+pysqlite://", creator=creator, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_book_with_identifiers(session, identifiers):
    from datetime import datetime, timezone
    from cps import db
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    book = db.Books("T", "T", "Author, Test", now, now, "1.0", now, "test/t", False, [], [])
    book.uuid = "uuid-t"
    book.authors = [db.Authors("Test Author", "Author, Test")]
    session.add(book)
    session.commit()
    session.add_all([db.Identifiers(val, typ, book.id) for typ, val in identifiers])
    session.commit()
    session.expire_all()
    return book.id


def _post_identifiers(session, book_id, identifiers):
    from cps import db
    from cps.api import edit as mod

    def get_book(*_a, **_k):
        return session.query(db.Books).filter(db.Books.id == book_id).one()

    with _ctx(f"/api/v1/books/{book_id}/metadata", body={"identifiers": identifiers}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_filtered_book=get_book, session=session,
                                          get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(book_id)
    # The request ends: anything left uncommitted is discarded, as the
    # per-request session teardown does in production.
    session.rollback()
    session.expire_all()
    return json.loads(resp.get_data())


def _stored_identifiers(session, book_id):
    from cps import db
    rows = session.query(db.Identifiers).filter(db.Identifiers.book == book_id).all()
    return sorted((r.type, r.val) for r in rows)


def test_changing_an_existing_identifier_value_is_saved(calibre_session):
    """Fork #2387: a value-only edit of an existing identifier type was echoed
    back as saved but never committed, so a reload showed the old value."""
    book_id = _seed_book_with_identifiers(
        calibre_session, [("hardcover-id", "1893578"), ("isbn", "9780000000001")])

    body = _post_identifiers(calibre_session, book_id, [
        {"type": "hardcover-id", "val": "545675"},
        {"type": "isbn", "val": "9780000000001"},
    ])

    assert "errors" not in body
    assert _stored_identifiers(calibre_session, book_id) == [
        ("hardcover-id", "545675"), ("isbn", "9780000000001")]


def test_resubmitting_identical_identifiers_reports_no_change(calibre_session):
    from cps import db, editbooks
    book_id = _seed_book_with_identifiers(calibre_session, [("isbn", "9780000000001")])
    book = calibre_session.query(db.Books).filter(db.Books.id == book_id).one()

    changed, duplicate = editbooks.modify_identifiers(
        [db.Identifiers("9780000000001", "isbn", book_id)], book.identifiers, calibre_session)

    assert (changed, duplicate) == (False, False)
