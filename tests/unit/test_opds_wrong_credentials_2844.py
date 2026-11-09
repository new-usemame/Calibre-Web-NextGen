"""#2844: with anonymous browsing on, OPDS must refuse credentials that were
sent and are wrong, instead of answering as the guest.

A client that sends a username and a wrong password used to get the guest
catalog with 200, so its "test connection" passed and per-user feeds were
silently missing. KOSync already answers 401 for the same credentials.

The guest fallback stays for the cases #121 added it for: no Authorization
header at all, and an empty-username probe (Readest sends one before the
real credentials). Explicit Guest credentials still get the guest catalog.

The decorator is driven over HTTP with a stand-in for ``auth.authenticate``
that behaves like ``verify_password``: the right password returns the user,
anything else returns None, and the Guest account authenticates whenever
anonymous browsing is on.
"""

import base64

import pytest


def _basic(username, password):
    raw = ("%s:%s" % (username, password)).encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


class _User:
    def __init__(self, name, is_anonymous=False):
        self.name = name
        self.is_anonymous = is_anonymous


@pytest.fixture
def opds(mocker):
    from flask import Flask, Response, g, jsonify

    from cps import usermanagement as um

    app = Flask(__name__)
    app.config["TESTING"] = True

    mocker.patch.object(um.auth, "auth_error_callback",
                        lambda status=401: Response("Unauthorized", status=status))
    mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
    mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

    guest = _User("Guest", is_anonymous=True)
    alice = _User("alice")

    def fake_authenticate(authorisation, _stored):
        username = (authorisation or {}).get("username") or ""
        password = (authorisation or {}).get("password") or ""
        if not username:
            return None
        if username.lower() == "guest":
            return guest if um.config.config_anonbrowse == 1 else None
        if username == "alice" and password == "right":
            return alice
        return None

    mocker.patch.object(um.auth, "authenticate", side_effect=fake_authenticate)

    @app.route("/opds/")
    @um.requires_basic_auth_if_no_ano
    def feed():
        user = g.get("flask_httpauth_user")
        return jsonify({"user": getattr(user, "name", None)})

    return app.test_client(), um


@pytest.mark.unit
class TestWrongCredentialsAreRefusedUnderAnonymousBrowsing:

    def test_known_user_wrong_password_gets_401(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic("alice", "WRONG"))
        assert resp.status_code == 401, resp.get_data(as_text=True)

    def test_unknown_user_gets_401_like_a_known_one(self, opds):
        # Same answer for an unknown name, so the status does not reveal
        # which accounts exist.
        client, _ = opds
        resp = client.get("/opds/", headers=_basic("nobody", "whatever"))
        assert resp.status_code == 401

    def test_right_password_still_gets_the_user(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic("alice", "right"))
        assert resp.status_code == 200
        assert resp.get_json()["user"] == "alice"

    def test_no_header_still_gets_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/")
        assert resp.status_code == 200
        assert resp.get_json()["user"] == "Guest"

    def test_empty_username_probe_still_gets_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic("", ""))
        assert resp.status_code == 200
        assert resp.get_json()["user"] == "Guest"

    def test_explicit_guest_credentials_get_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic("Guest", "anything"))
        assert resp.status_code == 200
        assert resp.get_json()["user"] == "Guest"

    def test_anonymous_browsing_off_wrong_password_still_401(self, opds, mocker):
        client, um = opds
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        resp = client.get("/opds/", headers=_basic("alice", "WRONG"))
        assert resp.status_code == 401
