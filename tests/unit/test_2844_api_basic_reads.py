# SPDX-License-Identifier: GPL-3.0-or-later
"""#2844, the /api/v1 half: a native client's HTTP Basic credentials decide who
it reads as. Right credentials read as their owner (not as the guest), wrong
ones get the JSON 401 even with anonymous browsing on, and a request that names
no account still gets the guest view.

A browser is the case that must not change. A reverse proxy with its own Basic
login makes the browser replay the proxy's credentials on every fetch the new UI
makes; those name no CWNG account (or the wrong password for one), and refusing
them would sign out every anonymous visitor on that setup. Browsers mark their
fetches with Sec-Fetch-Site and a signed-in browser has a session; either keeps
the request on the session path, exactly as before.

Real pieces (tests/unit/api_basic_auth_world.py): the api_v1 blueprint and its
before_request hooks, cw_login sessions over SQLite user rows, the HTTP Basic
verifier with app-password digests. Substituted: only the /account payload
builder, which reports the user it ran as.
"""
import base64
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
BROWSER = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}


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
        "config_anonbrowse": 1,
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

    from cps.api import account
    monkeypatch.setattr(account, "_serialize_account",
                        lambda: {"name": current_user.name})

    app = build_api_app(session, secret_key="basic-2844")
    app.login_manager.anonymous_user = ub.Anonymous     # as cps/__init__.py wires it
    client = app.test_client()

    def sign_in(name, password):
        token = client.get("/api/v1/auth/csrf").json["csrf_token"]
        response = client.post("/api/v1/auth/login",
                               json={"username": name, "password": password},
                               headers={"X-CSRFToken": token})
        assert response.status_code == 200, response.data

    client.sign_in = sign_in
    yield client
    session.close()
    engine.dispose()


def _reader_name(response):
    return response.json.get("name") or response.json.get("username")


@pytest.mark.parametrize("anonbrowse", [0, 1])
@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/account"])
def test_right_app_password_reads_as_its_owner(api, monkeypatch, anonbrowse, path):
    monkeypatch.setattr(config, "config_anonbrowse", anonbrowse)
    response = api.get(path, headers=_basic("alice", APP_PASSWORD_A))
    assert response.status_code == 200, response.data
    assert _reader_name(response) == "alice"


@pytest.mark.parametrize("anonbrowse", [0, 1])
@pytest.mark.parametrize("name", ["alice", "nobody-by-this-name"])
@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/account"])
def test_wrong_credentials_get_401_never_the_guest(api, monkeypatch, anonbrowse, name, path):
    monkeypatch.setattr(config, "config_anonbrowse", anonbrowse)
    response = api.get(path, headers=_basic(name, "not-the-password"))
    assert response.status_code == 401, response.data
    assert response.json["error"]["code"] in ("unauthorized", "unauthenticated")


@pytest.mark.parametrize("credentials", [None, ("", ""), ("Guest", "")])
def test_a_request_naming_no_account_still_reads_as_the_guest(api, credentials):
    headers = _basic(*credentials) if credentials else {}
    response = api.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200, response.data
    assert _reader_name(response) == "Guest"


def test_browser_replaying_proxy_basic_stays_an_anonymous_visitor(api):
    """The anonymous visitor behind a proxy's auth_basic: its fetches carry the
    proxy's credentials, which are no CWNG account. It keeps the guest view."""
    for name in ("proxyuser", "alice"):
        response = api.get("/api/v1/auth/me",
                           headers={**_basic(name, "proxy-pw"), **BROWSER})
        assert response.status_code == 200, response.data
        assert _reader_name(response) == "Guest"


def test_signed_in_browser_keeps_its_session_under_proxy_basic(api):
    api.sign_in("bob", "bob-account-pw")
    for headers in (_basic("proxyuser", "proxy-pw"),
                    {**_basic("proxyuser", "proxy-pw"), **BROWSER}):
        response = api.get("/api/v1/account", headers=headers)
        assert response.status_code == 200, response.data
        assert _reader_name(response) == "bob"
