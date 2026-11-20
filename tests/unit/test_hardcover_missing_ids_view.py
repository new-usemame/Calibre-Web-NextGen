# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The admin "books without a Hardcover ID" view and its actions.

Until now the only way to see which books never got a Hardcover ID was a
smart shelf, which says nothing about why: waiting in the review queue,
rejected (auto-fetch never retries those), or simply not found yet. Progress
for those books silently never reaches Hardcover.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import admin, db, ub
from tests.unit.hardcover_admin_world import hardcover_admin_app  # noqa: F401

pytestmark = pytest.mark.unit


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture
def env(monkeypatch, hardcover_admin_app):
    cal_engine = create_engine("sqlite://")

    @event.listens_for(cal_engine, "connect")
    def _attach(conn, _record):
        conn.execute("ATTACH DATABASE ':memory:' AS calibre")

    db.Base.metadata.create_all(cal_engine)
    cal = sessionmaker(bind=cal_engine)()
    for book_id, title in [(1, "Play Nice"), (2, "La Villa aux étoffes"), (3, "Dune"),
                           (4, "Press Reset"), (5, "Je suis Romane Monnier")]:
        cal.execute(db.Books.__table__.insert().values(
            id=book_id, title=title, sort=title, author_sort="x", path=f"p{book_id}",
            uuid=f"u{book_id}", has_cover=0, series_index="1"))
    cal.add(db.Identifiers("123", "hardcover-id", 3))      # Dune is matched
    cal.add(db.Identifiers("press-reset", "hardcover-slug", 4))  # matched by slug
    cal.add(db.Identifiers("9782070368228", "isbn", 5))     # an ISBN is not enough
    cal.commit()

    ub_engine = create_engine("sqlite://")
    ub.HardcoverMatchQueue.__table__.create(ub_engine)
    us = sessionmaker(bind=ub_engine)()

    def queue(book_id, reviewed, action=None):
        us.add(ub.HardcoverMatchQueue(
            book_id=book_id, book_title="t", book_authors="a", search_query="q",
            reviewed=reviewed, review_action=action, created_at="2026-10-07",
            hardcover_results=json.dumps([]), confidence_scores=json.dumps([])))

    queue(1, 0)                 # Play Nice waits for review
    queue(2, 1, "reject")       # La Villa was rejected
    queue(4, 0)                 # Press Reset: stale row, matched since
    us.commit()

    rendered = {}
    monkeypatch.setattr(admin, "calibre_db", SimpleNamespace(session=cal))
    monkeypatch.setattr(admin.ub, "session", us)
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(name="admin"))
    monkeypatch.setattr(admin, "config", SimpleNamespace(
        hardcover_sync_enabled=lambda: True, resolved_hardcover_token=lambda: "token"))
    monkeypatch.setattr(admin, "render_title_template",
                        lambda template, **kwargs: rendered.update(template=template, **kwargs) or "ok")
    yield SimpleNamespace(app=hardcover_admin_app, cal=cal, us=us, rendered=rendered)
    cal.close()
    us.close()
    cal_engine.dispose()
    ub_engine.dispose()


def test_lists_only_books_without_any_hardcover_identifier(env):
    with env.app.test_request_context("/admin/hardcover/missing"):
        _unwrap(admin.hardcover_missing_ids)()
    r = env.rendered
    assert r["template"] == "hardcover_missing_ids.html"
    assert r["total"] == 3
    assert {row["title"]: row["status"] for row in r["rows"]} == {
        "Play Nice": "pending",
        "La Villa aux étoffes": "rejected",
        "Je suis Romane Monnier": "unmatched",
    }
    assert r["counts"] == {"pending": 1, "rejected": 1, "skipped": 0, "unmatched": 1}


def test_status_filter_narrows_rows_but_keeps_counts(env):
    with env.app.test_request_context("/admin/hardcover/missing?status=rejected"):
        _unwrap(admin.hardcover_missing_ids)()
    r = env.rendered
    assert [row["title"] for row in r["rows"]] == ["La Villa aux étoffes"]
    assert r["status_filter"] == "rejected"
    assert r["counts"]["pending"] == 1


def test_unknown_status_falls_back_to_all(env):
    with env.app.test_request_context("/admin/hardcover/missing?status=bogus"):
        _unwrap(admin.hardcover_missing_ids)()
    assert env.rendered["status_filter"] == "all"
    assert len(env.rendered["rows"]) == 3


def test_allow_retry_lifts_the_rejection_only_for_that_book(env):
    with env.app.test_request_context("/admin/hardcover/allow-retry/2", method="POST"):
        response = _unwrap(admin.hardcover_allow_retry)(2)
    assert response.status_code == 302
    rows = env.us.query(ub.HardcoverMatchQueue.book_id, ub.HardcoverMatchQueue.review_action).all()
    assert (2, "retry") in rows
    assert (2, "reject") not in rows


def test_rematch_queues_a_run_that_includes_pending_reviews(env, monkeypatch):
    queued = []
    monkeypatch.setattr(admin, "_enqueue_hardcover_auto_fetch",
                        lambda include_pending: queued.append(include_pending))
    with env.app.test_request_context("/admin/hardcover/rematch", method="POST"):
        response = _unwrap(admin.hardcover_rematch)()
    assert response.status_code == 302
    assert queued == [True]


def test_rematch_refuses_without_a_token(env, monkeypatch):
    queued = []
    monkeypatch.setattr(admin, "_enqueue_hardcover_auto_fetch",
                        lambda include_pending: queued.append(include_pending))
    monkeypatch.setattr(admin, "config", SimpleNamespace(
        hardcover_sync_enabled=lambda: True, resolved_hardcover_token=lambda: None))
    with env.app.test_request_context("/admin/hardcover/rematch", method="POST"):
        _unwrap(admin.hardcover_rematch)()
    assert queued == []


def test_review_page_hides_books_matched_since_they_were_queued(env):
    with env.app.test_request_context("/admin/hardcover/review-matches"):
        _unwrap(admin.hardcover_review_matches)()
    r = env.rendered
    assert [m["book_id"] for m in r["matches"]] == [1]
    assert r["missing_count"] == 3


def test_pagination_reaches_every_missing_book_and_keeps_status_filter(env, monkeypatch):
    """The display limit must not make later books impossible to inspect."""
    monkeypatch.setattr(admin, "HARDCOVER_MISSING_PAGE_LIMIT", 1)
    seen = []
    for page in range(1, 4):
        with env.app.test_request_context(f"/admin/hardcover/missing?page={page}"):
            _unwrap(admin.hardcover_missing_ids)()
        seen.extend(row["id"] for row in env.rendered["rows"])
    assert sorted(seen) == [1, 2, 5]
    with env.app.test_request_context("/admin/hardcover/missing?status=rejected&page=99"):
        _unwrap(admin.hardcover_missing_ids)()
    assert [row["id"] for row in env.rendered["rows"]] == [2]
    assert env.rendered["status_filter"] == "rejected"


@pytest.mark.parametrize("actor, expected", [(None, 401), ("reader", 403), ("admin", 200)])
def test_new_routes_enforce_login_admin_and_csrf(env, monkeypatch, actor, expected):
    """Drive the decorated handlers: denied requests cannot enqueue or lift a rejection."""
    from cps import cw_login, usermanagement

    monkeypatch.setattr(admin, "current_user", cw_login.current_user)
    monkeypatch.setattr(admin, "render_title_template", lambda template, **kwargs: kwargs["title"])
    monkeypatch.setattr(usermanagement.config, "config_allow_reverse_proxy_header_login", False, raising=False)
    queued = []
    monkeypatch.setattr(admin, "_enqueue_hardcover_auto_fetch", lambda include_pending: queued.append(include_pending))
    client = env.app.test_client()
    if actor:
        with client.session_transaction() as session:
            session["_user_id"] = actor
            session["_random"] = "test-random"
            session["_id"] = "test-session"
    response = client.get("/admin/hardcover/missing")
    assert response.status_code == expected
    if actor == "admin":
        assert response.get_data(as_text=True) == "Livres sans identifiant Hardcover"
    token = client.get("/csrf").get_data(as_text=True)
    for path in ["/admin/hardcover/rematch", "/admin/hardcover/allow-retry/2"]:
        assert client.post(path).status_code == 400
        assert client.post(path, data={"csrf_token": token}).status_code == (302 if actor == "admin" else expected)
    assert queued == ([True] if actor == "admin" else [])
    action = env.us.query(ub.HardcoverMatchQueue).filter_by(book_id=2).one().review_action
    assert action == ("retry" if actor == "admin" else "reject")


def test_rendered_page_keeps_filter_in_pagination_and_escapes_book_titles(env, monkeypatch):
    """Users can reach later filtered pages; library metadata stays plain text."""
    from pathlib import Path
    from flask import render_template
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    monkeypatch.setattr(admin, "HARDCOVER_MISSING_PAGE_LIMIT", 1)
    env.cal.query(db.Books).filter_by(id=1).update({"title": "<script>alert(1)</script>"})
    env.cal.commit()
    monkeypatch.setattr(env.app, "jinja_loader", ChoiceLoader([
        DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
        FileSystemLoader(Path(admin.__file__).parent / "templates"),
    ]))
    with env.app.test_request_context("/admin/hardcover/missing?status=all&page=3"):
        _unwrap(admin.hardcover_missing_ids)()
        context = {key: value for key, value in env.rendered.items() if key != "template"}
        html = render_template(env.rendered["template"], **context)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "status=all&amp;page=2" in html
