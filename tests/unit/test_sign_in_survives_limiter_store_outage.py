# SPDX-License-Identifier: GPL-3.0-or-later
"""A limiter store outage never locks devices out or fails a finished sign-in.

An administrator can point the rate limiter at Redis or Memcached. When that
store is down, Kobo sync still authenticates by its token, and a web sign-in
whose password was right is not turned into an error by clearing its limits.
A real limiter counts the requests; only its store is made to fail.
"""

from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from flask_limiter import Limiter, RateLimitExceeded

from cps import kobo_auth, ub, web
from cps.services import device_registry


pytestmark = pytest.mark.unit

ATTEMPTS_PER_MINUTE = 3
GOOD_TOKEN = "good-token"


class _TokenLookup:
    """ub.session.query(User).join(...).filter(token == ...) for one token."""

    def __init__(self, token):
        self.token = token

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return SimpleNamespace(id=7) if self.token == GOOD_TOKEN else None


def _kobo_statuses(tokens, *store_faults):
    app = flask.Flask(__name__)
    limiter = Limiter(key_func=lambda: "unused", auto_check=False, storage_uri="memory://")
    kobo = flask.Blueprint("kobo_under_test", __name__)
    current = {}

    @kobo.route("/<auth_token>/v1/library/sync")
    @kobo_auth.requires_kobo_auth
    def sync(auth_token):
        return "synced", 200

    limiter.limit(f"{ATTEMPTS_PER_MINUTE}/minute", key_func=lambda: "one-client")(kobo)
    app.register_blueprint(kobo)
    limiter.init_app(app)

    @app.errorhandler(RateLimitExceeded)
    def _too_many(_):
        return "slow down", 429

    patches = [
        patch.object(kobo_auth, "limiter", limiter),
        patch.object(kobo_auth, "get_auth_token", lambda: current["token"]),
        patch.object(kobo_auth, "login_user", lambda _user: None),
        patch.object(ub, "session", SimpleNamespace(
            query=lambda *_args: _TokenLookup(current["token"]))),
        patch.object(device_registry, "register_kobo_device_best_effort",
                     lambda **_kwargs: None),
    ]
    patches += [patch.object(target(limiter), name, side_effect=ConnectionError("store down"))
                for target, name in store_faults]
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        statuses = []
        for token in tokens:
            current["token"] = token
            statuses.append(client.get(f"/{token}/v1/library/sync").status_code)
        return statuses
    finally:
        for p in reversed(patches):
            p.stop()


CHECK_FAILS = (lambda limiter: limiter, "check")
CLEAR_FAILS = (lambda limiter: limiter.limiter.storage, "clear")


def test_unknown_kobo_tokens_are_still_paced():
    statuses = _kobo_statuses(["wrong"] * (ATTEMPTS_PER_MINUTE + 1))
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429]


def test_a_kobo_syncs_while_the_limiter_store_is_down():
    statuses = _kobo_statuses([GOOD_TOKEN, "wrong", GOOD_TOKEN], CHECK_FAILS, CLEAR_FAILS)
    assert statuses == [200, 401, 200]


def test_a_kobo_syncs_when_its_limits_cannot_be_cleared():
    statuses = _kobo_statuses(["wrong", GOOD_TOKEN, GOOD_TOKEN], CLEAR_FAILS)
    assert statuses == [401, 200, 200]


def test_a_web_sign_in_completes_when_its_limits_cannot_be_cleared():
    app = flask.Flask(__name__)
    app.secret_key = "outage-test"
    limiter = Limiter(key_func=lambda: "one-client", auto_check=False, storage_uri="memory://")
    limiter.init_app(app)
    signed_in = []

    with patch.object(web, "limiter", limiter), \
            patch.object(web, "login_user", lambda user, remember: signed_in.append(user)), \
            patch.object(web, "get_redirect_location", lambda _next, _default: "/library"), \
            patch.object(limiter.limiter.storage, "clear",
                         side_effect=ConnectionError("store down")), \
            app.test_request_context("/login", method="POST"):
        with patch.object(type(limiter), "current_limits",
                          new=[SimpleNamespace(key="LIMITER/one-client/login")]):
            response = web.handle_login_user(
                SimpleNamespace(id=7, name="alice"), False, "Signed in", "success")

    assert signed_in and signed_in[0].name == "alice"
    assert response.status_code == 302
    assert response.location == "/library"
