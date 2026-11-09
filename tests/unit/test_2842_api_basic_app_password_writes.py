# SPDX-License-Identifier: GPL-3.0-or-later
"""#2842: a native client holding an app password can set read status,
archive a book and mark it read through /api/v1, without a browser session.

Real pieces (built by tests/unit/api_basic_auth_world.py): the api_v1 blueprint with its before_request hooks, the
project's own CSRFProtect instance (``cps.csrf``, the one production
initialises), the HTTP Basic verifier with app-password digests, and SQLite
user rows. Substituted: only the book-side writers each route calls, which
record the user they ran as. That is what makes the identity observable: a
write that ran as the guest, as the cookie user, or not at all is told apart
from one that ran as the Basic user.
"""
import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import config, constants, ub
from cps.cw_login import current_user
from cps.services import app_passwords
from tests.unit.api_basic_auth_world import build_api_app

pytestmark = pytest.mark.unit

APP_PASSWORD_A = "app-pass-alice-0123456789"


def _basic(name, password):
    raw = f"{name}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    settings = {
        "config_login_type": constants.LOGIN_STANDARD,
        "config_anonbrowse": 0,
        "config_allow_reverse_proxy_header_login": False,
        "config_reverse_proxy_login_header_name": "X-Remote-User",
        "config_disable_standard_login": False,
        "config_read_column": 0,
        "config_default_locale": "en",
        "config_theme": 1,
    }
    for key, value in settings.items():
        monkeypatch.setattr(config, key, value, raising=False)
    monkeypatch.setattr("cps.cw_login.utils.CWA_DB", Mock())

    alice = ub.User(name="alice", email="alice@example.invalid",
                    password=generate_password_hash("alice-account-pw"),
                    role=constants.ROLE_DOWNLOAD)
    bob = ub.User(name="bob", email="bob@example.invalid",
                  password=generate_password_hash("bob-account-pw"),
                  role=constants.ROLE_DOWNLOAD)
    guest = ub.User(name="Guest", email="guest@example.invalid", password="",
                    role=constants.ROLE_ANONYMOUS)
    session.add_all([alice, bob, guest])
    session.commit()
    app_passwords.mint(alice.id, "reader app", session=session)
    row = session.query(ub.UserAppPassword).filter_by(user_id=alice.id).one()
    row.password_hash = generate_password_hash(APP_PASSWORD_A)
    row.token_digest = app_passwords.token_digest(APP_PASSWORD_A)
    session.commit()

    writes = []

    def record(kind):
        def _write(book_id, *args, **kwargs):
            writes.append((kind, current_user.name, book_id, args))
            return "" if kind == "read-status" else True
        return _write

    from cps.api import actions, books
    monkeypatch.setattr(books, "set_explicit_book_read_status",
                        record("read-status"))
    monkeypatch.setattr(books, "edit_book_read_status", record("read"))
    monkeypatch.setattr(actions, "change_archived_books", record("archived"))
    monkeypatch.setattr(actions, "remove_synced_book", lambda *_a, **_k: None)
    monkeypatch.setattr(books.calibre_db, "get_book_read_archived",
                        lambda *a, **k: (object(), None, None), raising=False)
    monkeypatch.setattr(books.user_library, "contains_book", lambda *a, **k: True)
    monkeypatch.setattr(books, "_can_browse_global", lambda: True, raising=False)

    app = build_api_app(session, secret_key="basic-2842")
    client = app.test_client()

    def sign_in(name, password):
        token = client.get("/api/v1/auth/csrf").json["csrf_token"]
        response = client.post("/api/v1/auth/login",
                               json={"username": name, "password": password},
                               headers={"X-CSRFToken": token})
        assert response.status_code == 200, response.data
        return client.get("/api/v1/auth/csrf").json["csrf_token"]

    yield SimpleNamespace(client=client, writes=writes, sign_in=sign_in,
                          session=session)
    session.close()
    engine.dispose()


ALLOWED_WRITES = [
    ("/api/v1/books/7/read-status", {"status": "did_not_finish"}, "read-status"),
    ("/api/v1/books/7/read", {"read": True}, "read"),
    ("/api/v1/books/7/archived", None, "archived"),
]


@pytest.mark.parametrize("path,body,kind", ALLOWED_WRITES)
def test_app_password_writes_as_its_owner_without_session_or_csrf(api, path, body, kind):
    response = api.client.post(path, json=body, headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert [(w[0], w[1], w[2]) for w in api.writes] == [(kind, "alice", 7)]


def test_read_status_value_reaches_the_writer(api):
    response = api.client.post("/api/v1/books/7/read-status", json={"status": "on_hold"},
                               headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200
    assert response.json == {"status": "on_hold"}
    assert api.writes == [("read-status", "alice", 7, ("on_hold",))]


@pytest.mark.parametrize("anonbrowse", [0, 1])
def test_wrong_app_password_is_refused_never_answered_as_guest(api, monkeypatch, anonbrowse):
    monkeypatch.setattr(config, "config_anonbrowse", anonbrowse)
    response = api.client.post("/api/v1/books/7/read-status", json={"status": "finished"},
                               headers=_basic("alice", "not-the-password"))
    assert response.status_code == 401
    assert response.json["error"]["code"] == "unauthorized"
    assert api.writes == []


def test_guest_credentials_cannot_write(api, monkeypatch):
    monkeypatch.setattr(config, "config_anonbrowse", 1)
    response = api.client.post("/api/v1/books/7/read", json={"read": True},
                               headers=_basic("Guest", ""))
    assert response.status_code == 401
    assert api.writes == []


def test_cookie_plus_wrong_basic_does_not_fall_back_to_the_cookie(api):
    api.sign_in("bob", "bob-account-pw")
    response = api.client.post("/api/v1/books/7/read-status", json={"status": "finished"},
                               headers=_basic("alice", "not-the-password"))
    assert response.status_code == 401
    assert api.writes == []


def test_cookie_plus_valid_basic_writes_as_the_basic_user(api):
    api.sign_in("bob", "bob-account-pw")
    response = api.client.post("/api/v1/books/7/archived",
                               headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert [(w[0], w[1]) for w in api.writes] == [("archived", "alice")]


def test_cookie_session_still_needs_the_csrf_token(api):
    token = api.sign_in("bob", "bob-account-pw")
    refused = api.client.post("/api/v1/books/7/read-status", json={"status": "finished"})
    assert refused.status_code == 400
    assert api.writes == []
    allowed = api.client.post("/api/v1/books/7/read-status", json={"status": "finished"},
                              headers={"X-CSRFToken": token})
    assert allowed.status_code == 200, allowed.data
    assert [(w[0], w[1]) for w in api.writes] == [("read-status", "bob")]


def test_basic_from_another_site_is_rejected(api):
    headers = {**_basic("alice", APP_PASSWORD_A), "Origin": "https://evil.example"}
    response = api.client.post("/api/v1/books/7/read", json={"read": True}, headers=headers)
    assert response.status_code == 403
    assert api.writes == []


def test_basic_is_not_accepted_on_writes_outside_the_allow_list(api, monkeypatch):
    from cps.api import actions
    hits = []
    monkeypatch.setattr(actions, "_require_real_user", lambda: hits.append(1) or None)
    response = api.client.post("/api/v1/books/7/favorite",
                               headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 400          # the CSRF refusal, not a Basic sign-in
    assert b"CSRF" in response.data
    assert hits == []


def test_proxy_basic_header_on_a_browser_session_write_stays_a_session_write(api):
    """A proxy's own auth_basic layer makes the browser replay its credentials on
    every request. The SPA's write carries the CSRF token, so it is a session
    write: it must not be refused as a wrong app password, nor count towards
    sign-in pacing."""
    token = api.sign_in("bob", "bob-account-pw")
    headers = {**_basic("proxyuser", "proxy-pw"), "X-CSRFToken": token}
    for _ in range(5):
        response = api.client.post("/api/v1/books/7/read-status",
                                   json={"status": "finished"}, headers=headers)
        assert response.status_code == 200, response.data
    assert {(w[0], w[1]) for w in api.writes} == {("read-status", "bob")}


@pytest.mark.parametrize("proxy_header", [None, "bob"])
def test_reverse_proxy_login_does_not_replace_the_basic_user(api, monkeypatch, proxy_header):
    monkeypatch.setattr(config, "config_allow_reverse_proxy_header_login", True)
    headers = _basic("alice", APP_PASSWORD_A)
    if proxy_header:
        headers["X-Remote-User"] = proxy_header
    response = api.client.post("/api/v1/books/7/read-status", json={"status": "finished"},
                               headers=headers)
    assert response.status_code == 200, response.data
    assert [(w[0], w[1]) for w in api.writes] == [("read-status", "alice")]
