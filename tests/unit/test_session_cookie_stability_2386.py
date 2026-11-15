"""The session cookie must not change on every response (#2386).

Covers are served ``private, max-age=31536000, immutable`` with ``Vary: Cookie``.
A browser only reuses a stored response whose ``Cookie`` request header matches
the one it was stored with, so a session cookie that is re-signed on every
response (Flask's default for permanent sessions; "Remember me" makes the
session permanent) gives the next library visit a different ``Cookie`` header
and every cover is downloaded again. Measured on v4.1.46: a new ``Set-Cookie:
session=...`` on every page and every cover response, a new value each second.

The sliding expiry is kept: a cookie past half its lifetime is re-issued, so an
active session still never lapses, and a modified session is always saved.
"""

from __future__ import annotations

import datetime

import flask
import pytest


def _app():
    import cps

    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    cps._configure_base_app(app)

    @app.route("/login")
    def login():
        flask.session["_user_id"] = "1"
        flask.session["_permanent"] = True  # what "Remember me" does
        return "ok"

    @app.route("/page")
    def page():
        return "user %s" % flask.session.get("_user_id")

    @app.route("/touch")
    def touch():
        flask.session["seen"] = flask.session.get("seen", 0) + 1
        return "ok"

    return app


def _session_set_cookies(response):
    return [h for h in response.headers.getlist("Set-Cookie") if h.startswith("session=")]


def _at(monkeypatch, when):
    """Pin the clock itsdangerous signs and checks timestamps with."""
    import itsdangerous.timed

    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: when.timestamp())


def test_reading_a_remembered_session_does_not_reissue_the_cookie(monkeypatch):
    app = _app()
    t0 = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.timezone.utc)
    _at(monkeypatch, t0)
    client = app.test_client()
    assert _session_set_cookies(client.get("/login"))
    first = client.get_cookie("session").value

    # A later visit, with the clock moved on, reads the session without changing it.
    _at(monkeypatch, t0 + datetime.timedelta(hours=6))
    response = client.get("/page")
    assert response.get_data(as_text=True) == "user 1"
    assert _session_set_cookies(response) == []
    assert client.get_cookie("session").value == first


def test_a_remembered_session_past_half_its_lifetime_is_refreshed(monkeypatch):
    app = _app()
    t0 = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.timezone.utc)
    _at(monkeypatch, t0)
    client = app.test_client()
    client.get("/login")
    first = client.get_cookie("session").value

    lifetime = app.permanent_session_lifetime
    _at(monkeypatch, t0 + lifetime / 2 + datetime.timedelta(minutes=1))
    response = client.get("/page")
    assert response.get_data(as_text=True) == "user 1"
    assert _session_set_cookies(response), "an ageing session must slide forward"
    assert client.get_cookie("session").value != first

    # The refreshed cookie is still valid long after the original would have expired.
    _at(monkeypatch, t0 + lifetime + datetime.timedelta(days=5))
    assert client.get("/page").get_data(as_text=True) == "user 1"


def test_a_modified_session_is_always_saved(monkeypatch):
    app = _app()
    t0 = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.timezone.utc)
    _at(monkeypatch, t0)
    client = app.test_client()
    client.get("/login")
    _at(monkeypatch, t0 + datetime.timedelta(seconds=5))
    assert _session_set_cookies(client.get("/touch"))
    assert _session_set_cookies(client.get("/touch"))


@pytest.mark.parametrize("cookie", ["not-a-signed-value", ""])
def test_an_unreadable_cookie_starts_an_empty_session(cookie):
    app = _app()
    client = app.test_client()
    client.set_cookie("session", cookie)
    assert client.get("/page").get_data(as_text=True) == "user None"


def test_magic_shelf_counts_are_cached_per_user_on_the_server(monkeypatch):
    """The classic sidebar's counts used to be cached in the session cookie, so
    the cookie changed whenever one expired. They are cached server-side now."""
    from cps import magic_shelf

    calls = []

    def fake_count(shelf_id):
        calls.append(shelf_id)
        return 10 * len(calls)

    monkeypatch.setattr(magic_shelf, "get_book_count_for_magic_shelf", fake_count)
    magic_shelf.forget_book_counts()
    ttl = magic_shelf.BOOK_COUNT_CACHE_SECONDS

    assert magic_shelf.cached_book_count_for_magic_shelf(1, 7, now=1000) == 10
    assert magic_shelf.cached_book_count_for_magic_shelf(1, 7, now=1000 + ttl - 1) == 10
    # Another user's visibility differs, so their count is their own.
    assert magic_shelf.cached_book_count_for_magic_shelf(2, 7, now=1000) == 20
    # Expiry recomputes.
    assert magic_shelf.cached_book_count_for_magic_shelf(1, 7, now=1000 + ttl) == 30
    # Editing the shelf drops it for every user, and leaves other shelves alone.
    assert magic_shelf.cached_book_count_for_magic_shelf(1, 8, now=1000) == 40
    magic_shelf.forget_book_counts(7)
    assert magic_shelf.cached_book_count_for_magic_shelf(2, 7, now=1001) == 50
    assert magic_shelf.cached_book_count_for_magic_shelf(1, 8, now=1001) == 40
    magic_shelf.forget_book_counts()
