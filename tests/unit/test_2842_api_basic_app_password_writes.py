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


# #2842 part 2: a native client asks for a format the book lacks (an EPUB of an
# AZW3-only book) with the same app password, then follows the conversion task.

@pytest.fixture
def convert_api(api, monkeypatch):
    from cps.api import edit, info
    queued = []

    def fake_convert(book_id, calibre_path, src, dst, user_name, queued_tasks=None):
        queued.append((book_id, src, dst, user_name, current_user.name))
        queued_tasks.append(SimpleNamespace(id="task-42"))
        return None

    monkeypatch.setattr(edit, "_editable_book", lambda book_id: SimpleNamespace(id=book_id))
    monkeypatch.setattr(edit, "get_convert_options", lambda book: (["azw3"], ["epub", "kepub"]))
    monkeypatch.setattr(edit, "convert_book_format", fake_convert)
    monkeypatch.setattr(config, "get_book_path", lambda: "/library", raising=False)
    def task(task_id):
        return SimpleNamespace(id=task_id, name="Convert", message="", start_time=None,
                               stat=0, progress=0, is_cancellable=False, error=None)
    worker = SimpleNamespace(tasks=[(None, "alice", None, task("task-alice"), None),
                                    (None, "bob", None, task("task-bob"), None)])
    monkeypatch.setattr(info.WorkerThread, "get_instance", staticmethod(lambda: worker))
    alice = api.session.query(ub.User).filter_by(name="alice").one()
    alice.role = constants.ROLE_DOWNLOAD | constants.ROLE_EDIT
    api.session.commit()
    api.queued = queued
    return api


def test_app_password_queues_a_conversion_as_its_owner(convert_api):
    response = convert_api.client.post("/api/v1/books/7/convert",
                                       json={"from": "AZW3", "to": "EPUB"},
                                       headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert response.json["task_id"] == "task-42"
    assert convert_api.queued == [(7, "AZW3", "EPUB", "alice", "alice")]


def test_app_password_convert_still_needs_the_edit_role(convert_api):
    alice = convert_api.session.query(ub.User).filter_by(name="alice").one()
    alice.role = constants.ROLE_DOWNLOAD
    convert_api.session.commit()
    response = convert_api.client.post("/api/v1/books/7/convert",
                                       json={"from": "AZW3", "to": "EPUB"},
                                       headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 403
    assert response.json["error"]["code"] == "forbidden"
    assert convert_api.queued == []


@pytest.mark.parametrize("anonbrowse", [0, 1])
def test_wrong_app_password_never_queues_a_conversion(convert_api, monkeypatch, anonbrowse):
    monkeypatch.setattr(config, "config_anonbrowse", anonbrowse)
    response = convert_api.client.post("/api/v1/books/7/convert",
                                       json={"from": "AZW3", "to": "EPUB"},
                                       headers=_basic("alice", "not-the-password"))
    assert response.status_code == 401
    assert convert_api.queued == []


def test_cookie_session_convert_still_needs_the_csrf_token(convert_api):
    token = convert_api.sign_in("bob", "bob-account-pw")
    bob = convert_api.session.query(ub.User).filter_by(name="bob").one()
    bob.role = constants.ROLE_DOWNLOAD | constants.ROLE_EDIT
    convert_api.session.commit()
    refused = convert_api.client.post("/api/v1/books/7/convert",
                                      json={"from": "AZW3", "to": "EPUB"})
    assert refused.status_code == 400
    assert convert_api.queued == []
    allowed = convert_api.client.post("/api/v1/books/7/convert",
                                      json={"from": "AZW3", "to": "EPUB"},
                                      headers={"X-CSRFToken": token})
    assert allowed.status_code == 200, allowed.data
    assert [q[3] for q in convert_api.queued] == ["bob"]


@pytest.mark.parametrize("anonbrowse", [0, 1])
def test_app_password_reads_its_own_task_list(convert_api, monkeypatch, anonbrowse):
    monkeypatch.setattr(config, "config_anonbrowse", anonbrowse)
    response = convert_api.client.get("/api/v1/tasks", headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert [row["task_id"] for row in response.json["items"]] == ["task-alice"]


def test_admin_app_password_reads_every_task(convert_api):
    alice = convert_api.session.query(ub.User).filter_by(name="alice").one()
    alice.role = constants.ROLE_DOWNLOAD | constants.ROLE_ADMIN
    convert_api.session.commit()
    response = convert_api.client.get("/api/v1/tasks", headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert [row["task_id"] for row in response.json["items"]] == ["task-alice", "task-bob"]
