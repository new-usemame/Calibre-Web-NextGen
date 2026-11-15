# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #121 reopen — pins the load-bearing
invariants behind the OPDS anon/auth multiplex.

@droM4X reopened #121 on 2026-05-11 with two concerns:

1. ``If valid credentials are provided, show all content available to
   the authenticated user`` — claimed unfixed in v4.0.46.

2. ``In anonymous browsing mode strings are still shown in English`` —
   asked for a ``?lang=xx`` URL override.

Both halves of the reopen were addressed (see ``notes/2026-05-OPDS-DEEP-FIX-RETRO.md``
waves 1 + 2 for the lang work landed in v4.0.56/57; the auth multiplex
was already correct in v4.0.46, just not behaviourally pinned). This
file closes the gap on (1) — multiple load-bearing invariants in the
auth chain were untested at the source level, and a silent refactor of
any of them would re-introduce exactly the symptom @droM4X reported.

The 6-quadrant verification matrix this file pins:

| anon | creds  | result                            |
|------|--------|-----------------------------------|
| on   | none   | 200, Guest catalogue              |
| on   | bad    | 200, Guest catalogue (fallback)   |
| on   | valid  | 200, USER catalogue (USER locale) |
| off  | none   | 401                               |
| off  | bad    | 401                               |
| off  | valid  | 200, USER catalogue (USER locale) |

Live-verified against cwn-local under image
``calibre-web-nextgen:local`` (built off main @ 6b71e258) with admin /
Guest / cwng84test users — see ``notes/121-reopen-VERIFICATION.md`` for
the curl evidence. These tests drive the code that produces that
matrix (the decorator through a test client, ``Anonymous`` from a real
app.db, ``verify_password`` directly), so a refactor that keeps the
behaviour passes and one that breaks it goes red (#2939 replaced the
source-text pins this file used to carry).
"""

import pytest


@pytest.mark.unit
class TestFlaskHttpAuthUserBridge:
    """The load-bearing invariant: ``cps.cw_login.utils._get_user()``
    must consult ``g.flask_httpauth_user`` BEFORE Flask-Login's session
    or cookie loader. Without this, OPDS requests authenticated via
    HTTP Basic auth would surface as Anonymous/Guest to every consumer
    of ``current_user`` — including ``CalibreDB.common_filters()`` —
    and valid credentials would silently render Guest content.

    The bridge was the missing piece for @droM4X's reopen concern (1):
    sidebar visibility worked because the OPDS routes pass
    ``auth.current_user()`` (flask_httpauth) into
    ``get_opds_root_entries``, but the actual book filtering goes
    through ``common_filters`` which reads ``current_user`` (flask-login).
    Only the bridge ties them together.
    """

    def test_get_user_returns_httpauth_user_when_set(self, mocker):
        """Behavioral: with ``g.flask_httpauth_user`` set, _get_user
        returns that value, not the Flask-Login-resolved one."""
        from cps.cw_login import utils

        sentinel_user = object()

        mocker.patch.object(utils, "has_request_context", return_value=True)
        fake_g = mocker.MagicMock()
        fake_g.__contains__ = lambda self, k: k == "flask_httpauth_user"
        fake_g.flask_httpauth_user = sentinel_user
        mocker.patch.object(utils, "g", fake_g)
        fake_app = mocker.MagicMock()
        fake_app.login_manager._load_user.side_effect = AssertionError(
            "should not be called when flask_httpauth_user is set"
        )
        mocker.patch.object(utils, "current_app", fake_app)

        result = utils._get_user()
        assert result is sentinel_user

    def test_get_user_falls_through_to_login_manager_when_no_httpauth_user(self, mocker):
        """Behavioral: with NO ``g.flask_httpauth_user`` and no
        cached login user, _get_user must trigger
        ``login_manager._load_user()`` and return ``g._login_user``.
        This is the fallback path that anonymous OPDS requests take.
        """
        from cps.cw_login import utils

        sentinel_anon = object()
        mocker.patch.object(utils, "has_request_context", return_value=True)
        fake_g = mocker.MagicMock()
        state = {"keys": set()}
        fake_g.__contains__ = lambda self, k: k in state["keys"]
        fake_g._login_user = sentinel_anon
        mocker.patch.object(utils, "g", fake_g)
        fake_app = mocker.MagicMock()

        def _populate_login_user():
            state["keys"].add("_login_user")
        fake_app.login_manager._load_user.side_effect = _populate_login_user
        mocker.patch.object(utils, "current_app", fake_app)

        result = utils._get_user()
        fake_app.login_manager._load_user.assert_called_once()
        assert result is sentinel_anon


@pytest.mark.unit
class TestAnonymousUserMirrorsGuestRow:
    """The second load-bearing invariant: ``ub.Anonymous`` must mirror
    EVERY field of the Guest DB row that ``CalibreDB.common_filters``
    and the OPDS visibility callbacks consume. If any field stops being
    propagated, anonymous OPDS requests start surfacing the WRONG
    restrictions — typically admin's, or none at all — and Guest
    content leaks or hides incorrectly.

    @droM4X's setup has Guest with restricted ``denied_tags`` to gate
    adult content from unauthenticated visitors. The 6-quadrant matrix
    only behaves correctly if Anonymous propagates that field.
    """

    REQUIRED_FIELDS = (
        # Fields read by CalibreDB.common_filters
        "denied_tags",
        "allowed_tags",
        "denied_column_value",
        "allowed_column_value",
        "default_language",
        # Fields read by OPDS visibility callbacks + sidebar checks
        "sidebar_view",
        "locale",
        # Fields read by OPDS shelf-exposure filter (#179 / v4.0.56)
        "kobo_only_shelves_sync",
        "opds_only_shelves_sync",
        "view_settings",
        # Identity fields read by per-user-state queries
        "id",
        "name",
        "role",
    )

    @pytest.fixture
    def guest_row(self, tmp_path):
        """A real app.db whose Guest row has a distinct value in every field
        the filters and OPDS read, so a field Anonymous fails to copy shows
        up as the constructor default instead of the Guest's value."""
        from cps import ub
        ub.init_db(str(tmp_path / "app.db"))
        guest = ub.session.query(ub.User).filter(ub.User.name == "Guest").one()
        guest.denied_tags = "adult"
        guest.allowed_tags = "kids"
        guest.denied_column_value = "explicit"
        guest.allowed_column_value = "family"
        guest.default_language = "fra"
        guest.sidebar_view = 4097
        guest.locale = "hu"
        guest.kobo_only_shelves_sync = 1
        guest.opds_only_shelves_sync = 1
        guest.view_settings = {"shelf": {"sort": "new"}}
        ub.session.commit()
        return {field: getattr(guest, field) for field in self.REQUIRED_FIELDS}

    def test_anonymous_carries_the_guest_rows_restrictions(self, guest_row):
        """Every field common_filters and the OPDS callbacks read on the
        current user comes from the Guest row, so an anonymous OPDS request
        is restricted the way the admin configured Guest."""
        from cps import ub
        anon = ub.Anonymous()
        wrong = {field: (getattr(anon, field, "<missing>"), expected)
                 for field, expected in guest_row.items()
                 if getattr(anon, field, "<missing>") != expected}
        assert not wrong, (
            "Anonymous does not carry the Guest row's value for %s (got, "
            "expected); anonymous OPDS requests would not get Guest's "
            "restrictions (fork issue #121)" % wrong)

    def test_anonymous_is_anonymous_true_is_authenticated_false(self, guest_row):
        """The OPDS root entries gate the ``read`` / ``unread`` rows on
        ``not user.is_anonymous``; if these flip, Guest sees per-user
        read-status entries that have no meaning."""
        from cps import ub
        anon = ub.Anonymous()
        assert anon.is_anonymous is True
        assert anon.is_authenticated is False


@pytest.mark.unit
class TestSixQuadrantBehavioralMatrix:
    """Behavioral assertions over a minimal Flask app with
    ``requires_basic_auth_if_no_ano`` wired up. We mock the
    ``auth.authenticate`` callsite so we can drive each quadrant
    without standing up a full DB — the goal is to pin the decorator's
    branching logic end-to-end at the HTTP-response level, mirroring
    what cwn-local verified.
    """

    @pytest.fixture
    def app_with_decorator(self, mocker):
        """A Flask app with a single ``/opds/`` route guarded by
        ``requires_basic_auth_if_no_ano``. The auth verifier and
        config knob are mockable per-test."""
        from flask import Flask, jsonify, g

        from cps import usermanagement

        app = Flask(__name__)
        app.config["TESTING"] = True

        def _error_cb(status=401):
            from flask import Response
            return Response("Unauthorized", status=status)

        mocker.patch.object(usermanagement.auth, "auth_error_callback", _error_cb)

        @app.route("/opds/")
        @usermanagement.requires_basic_auth_if_no_ano
        def feed_index():
            user = g.get("flask_httpauth_user")
            return jsonify({
                "user_name": getattr(user, "name", None),
                "is_anon": getattr(user, "is_anonymous", None),
            })

        return app, usermanagement

    def _make_user_stub(self, name, is_anonymous=False):
        class _U:
            pass
        u = _U()
        u.name = name
        u.is_anonymous = is_anonymous
        return u

    def test_q1_anon_on_no_creds_returns_guest(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        guest = self._make_user_stub("Guest", is_anonymous=True)
        mocker.patch.object(um.auth, "authenticate", return_value=guest)

        client = app.test_client()
        resp = client.get("/opds/")
        assert resp.status_code == 200, "Q1: anon-on + no creds must serve Guest, not 401"
        assert resp.get_json()["user_name"] == "Guest"

    def test_q2_anon_on_valid_creds_returns_user(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        user = self._make_user_stub("cwng84test", is_anonymous=False)
        mocker.patch.object(um.auth, "authenticate", return_value=user)

        client = app.test_client()
        resp = client.get("/opds/", headers={
            "Authorization": "Basic Y3duZzg0dGVzdDpjd25nLXRlc3QtODQ=",
        })
        assert resp.status_code == 200, "Q2: anon-on + valid creds must serve user catalog"
        body = resp.get_json()
        assert body["user_name"] == "cwng84test", (
            "Q2 regression: valid OPDS credentials surfaced as %r instead "
            "of the authenticated user — droM4X reopen scenario"
            % body["user_name"]
        )

    def test_q3_anon_on_bad_creds_returns_401(self, app_with_decorator, mocker):
        """#2844 reverses the v4.0.46 fallback for credentials that name an
        account: anon=1 + wrong password is 401, as KOSync and the web
        sign-in form answer it. An empty-username probe still gets Guest
        (TestWrongCredentialsAreRefusedUnderAnonymousBrowsing2844 below)."""
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        guest = self._make_user_stub("Guest", is_anonymous=True)
        mocker.patch.object(
            um.auth,
            "authenticate",
            side_effect=[None, guest],
        )

        client = app.test_client()
        resp = client.get("/opds/", headers={
            "Authorization": "Basic Y3duZzg0dGVzdDpXUk9OR1BBU1M=",
        })
        assert resp.status_code == 401, "Q3: anon-on + wrong creds for an account must 401"

    def test_q4_anon_off_no_creds_returns_401(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        mocker.patch.object(um.auth, "authenticate", return_value=None)

        client = app.test_client()
        resp = client.get("/opds/")
        assert resp.status_code == 401, "Q4: anon-off + no creds must 401"

    def test_q5_anon_off_bad_creds_returns_401(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        mocker.patch.object(um.auth, "authenticate", return_value=None)

        client = app.test_client()
        resp = client.get("/opds/", headers={
            "Authorization": "Basic Y3duZzg0dGVzdDpXUk9OR1BBU1M=",
        })
        assert resp.status_code == 401, "Q5: anon-off + bad creds must 401"

    def test_q6_anon_off_valid_creds_returns_user(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)

        user = self._make_user_stub("cwng84test", is_anonymous=False)
        mocker.patch.object(um.auth, "authenticate", return_value=user)

        client = app.test_client()
        resp = client.get("/opds/", headers={
            "Authorization": "Basic Y3duZzg0dGVzdDpjd25nLXRlc3QtODQ=",
        })
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "cwng84test"


    def test_basic_credentials_win_over_a_reverse_proxy_header(self, app_with_decorator, mocker):
        """With reverse-proxy login on, a request that sends Basic
        credentials is that user; the proxy header is read only when none
        were sent, so a misconfigured proxy header cannot replace them."""
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 1, create=True)
        proxied = self._make_user_stub("proxied")
        from_header = mocker.patch.object(um, "load_user_from_reverse_proxy_header",
                                          return_value=proxied)
        user = self._make_user_stub("cwng84test")
        mocker.patch.object(um.auth, "authenticate", return_value=user)

        resp = app.test_client().get("/opds/", headers={
            "Authorization": "Basic Y3duZzg0dGVzdDpjd25nLXRlc3QtODQ=",
        })
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "cwng84test"
        from_header.assert_not_called()

    def test_reverse_proxy_user_without_basic_credentials(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 1, create=True)
        mocker.patch.object(um, "load_user_from_reverse_proxy_header",
                            return_value=self._make_user_stub("proxied"))
        authenticate = mocker.patch.object(um.auth, "authenticate", return_value=None)

        resp = app.test_client().get("/opds/")
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "proxied"
        authenticate.assert_not_called()


@pytest.mark.unit
class TestVerifyPasswordGuestShortCircuit:
    """When a client sends ``Authorization: Basic Guest:anything`` and
    anonymous browsing is on, ``verify_password`` returns Guest without
    a password. This is what makes the Guest fallback in the decorator
    round-trip: the substituted credentials are valid by design. With
    anonymous browsing off, Guest is an account like any other and needs
    its password.
    """

    app_with_decorator = TestSixQuadrantBehavioralMatrix.app_with_decorator

    @pytest.fixture
    def verify(self, app_with_decorator, mocker):
        """``verify_password`` as a catalogue request reaches it: inside a
        request (a refused sign-in logs the client address), with one Guest
        account in the database and no password that matches it."""
        from cps import constants, ub, usermanagement
        guest = ub.User()
        guest.id = 2
        guest.name = "Guest"
        guest.password = "not-a-hash"
        session = mocker.MagicMock()
        session.query.return_value.filter.return_value.first.return_value = guest
        mocker.patch.object(usermanagement.ub, "session", session)
        mocker.patch.object(usermanagement, "limiter", None)
        mocker.patch.object(usermanagement, "_verify_app_password_digest", return_value=False)
        mocker.patch.object(usermanagement, "_verify_app_password_older", return_value=False)
        mocker.patch.object(usermanagement, "check_password_hash", return_value=False)
        mocker.patch.object(usermanagement.config, "config_login_type",
                            constants.LOGIN_STANDARD, create=True)
        app, _ = app_with_decorator
        with app.test_request_context("/opds/"):
            yield usermanagement, guest, session

    def test_guest_signs_in_without_a_password_when_anonymous_browsing_is_on(self, verify, mocker):
        um, guest, _ = verify
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        assert um.verify_password("Guest", "anything") is guest
        assert um.verify_password("guest", "") is guest

    def test_guest_needs_its_password_when_anonymous_browsing_is_off(self, verify, mocker):
        """Without the gate, an anonymous-off server would let any client
        browse as Guest with no password."""
        um, _, _ = verify
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        assert um.verify_password("Guest", "anything") is None

    def test_empty_username_returns_none_without_a_database_lookup(self, verify, mocker):
        """An OPDS client's empty-username probe is no credentials, not a
        failed sign-in (#121 bug 1)."""
        um, _, session = verify
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        assert um.verify_password("", "") is None
        session.query.assert_not_called()

def _basic_2844(username, password):
    import base64
    raw = ("%s:%s" % (username, password)).encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


@pytest.mark.unit
class TestWrongCredentialsAreRefusedUnderAnonymousBrowsing2844:
    """#2844: with anonymous browsing on, credentials that name an account and
    are wrong get 401 instead of the guest catalog. The guest fallback stays
    for no header, an empty-username probe (#121) and Guest credentials.

    ``auth.authenticate`` is replaced with a stand-in that behaves like
    ``verify_password``: the right password returns the user, anything else
    None, and Guest authenticates whenever anonymous browsing is on."""

    app_with_decorator = TestSixQuadrantBehavioralMatrix.app_with_decorator
    _make_user_stub = TestSixQuadrantBehavioralMatrix._make_user_stub

    @pytest.fixture
    def opds(self, app_with_decorator, mocker):
        app, um = app_with_decorator
        mocker.patch.object(um.config, "config_anonbrowse", 1, create=True)
        mocker.patch.object(um.config, "config_allow_reverse_proxy_header_login", 0, create=True)
        guest = self._make_user_stub("Guest", is_anonymous=True)
        alice = self._make_user_stub("alice")

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
        return app.test_client(), um

    def test_known_user_wrong_password_gets_401(self, opds):
        client, _ = opds
        assert client.get("/opds/", headers=_basic_2844("alice", "WRONG")).status_code == 401

    def test_unknown_user_gets_401_like_a_known_one(self, opds):
        # Same answer for an unknown name, so the status does not reveal
        # which accounts exist.
        client, _ = opds
        assert client.get("/opds/", headers=_basic_2844("nobody", "whatever")).status_code == 401

    def test_right_password_still_gets_the_user(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic_2844("alice", "right"))
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "alice"

    def test_no_header_still_gets_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/")
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "Guest"

    def test_empty_username_probe_still_gets_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic_2844("", ""))
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "Guest"

    def test_explicit_guest_credentials_get_the_guest(self, opds):
        client, _ = opds
        resp = client.get("/opds/", headers=_basic_2844("Guest", "anything"))
        assert resp.status_code == 200
        assert resp.get_json()["user_name"] == "Guest"

    def test_anonymous_browsing_off_wrong_password_still_401(self, opds, mocker):
        client, um = opds
        mocker.patch.object(um.config, "config_anonbrowse", 0, create=True)
        assert client.get("/opds/", headers=_basic_2844("alice", "WRONG")).status_code == 401
