# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import flask
import pytest
from flask_babel import Babel
from flask_dance.consumer import OAuth2ConsumerBlueprint, oauth_error

import cps.oauth_bb


pytestmark = pytest.mark.unit


def _app_with_oauth_blueprint():
    app = flask.Flask(__name__)
    app.testing = True
    app.config["SECRET_KEY"] = "test"
    Babel(app)

    web = flask.Blueprint("web", __name__)

    @web.route("/")
    def index():
        return "index"

    @web.route("/login")
    def login():
        return "login"

    @web.route("/logout")
    def logout():
        return "logout"

    app.register_blueprint(web)

    blueprint = OAuth2ConsumerBlueprint(
        "generic",
        __name__,
        client_id="client-id",
        client_secret="client-secret",
        base_url="https://idp.example/",
        authorization_url="https://idp.example/authorize",
        token_url="https://idp.example/token",
    )
    app.register_blueprint(blueprint, url_prefix="/login")
    with patch.object(cps.oauth_bb, "app", app):
        cps.oauth_bb._register_auto_redirect_hooks([{"blueprint": blueprint}])
    return app, blueprint


def _state(provider, next_url):
    return {"provider": provider, "next": next_url}


def test_real_flask_dance_login_remembers_generated_state_and_next():
    app, _ = _app_with_oauth_blueprint()
    client = app.test_client()

    response = client.get(
        "/login/generic?_oauth_auto=1&next=/book/7"
    )

    assert response.status_code == 302
    assert response.location.startswith("https://idp.example/authorize?")
    with client.session_transaction() as session_store:
        oauth_state = session_store["generic_oauth_state"]
        assert session_store[
            cps.oauth_bb.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] == {oauth_state: _state("generic", "/book/7")}


def test_callback_without_recoverable_session_state_stops_at_login():
    app, _ = _app_with_oauth_blueprint()

    response = app.test_client().get(
        "/login/generic/authorized?state=lost-state&code=code"
    )

    assert response.status_code == 302
    parsed = urlparse(response.location)
    assert parsed.path == "/login"
    assert parse_qs(parsed.query) == {"local": ["1"]}


def test_provider_error_response_consumes_only_its_attempt():
    app, blueprint = _app_with_oauth_blueprint()

    @oauth_error.connect_via(blueprint)
    def provider_error(sender, error, error_description=None, error_uri=None):
        del error, error_description, error_uri
        return cps.oauth_bb._oauth_failure_redirect(sender.name)

    client = app.test_client()
    with client.session_transaction() as session_store:
        session_store[
            cps.oauth_bb.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] = {
            "state-a": _state("generic", "/book/1"),
            "state-b": _state("generic", "/book/2"),
        }

    response = client.get(
        "/login/generic/authorized?error=access_denied&state=state-a"
    )

    assert response.status_code == 302
    parsed = urlparse(response.location)
    assert parsed.path == "/login"
    assert parse_qs(parsed.query) == {
        "local": ["1"],
        "next": ["/book/1"],
    }
    with client.session_transaction() as session_store:
        assert session_store[
            cps.oauth_bb.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] == {"state-b": _state("generic", "/book/2")}


def test_successful_oauth_binding_uses_saved_next_and_clears_flow_state():
    app, _ = _app_with_oauth_blueprint()
    user = SimpleNamespace(id=7, name="alice")
    oauth_entry = SimpleNamespace(user=user, user_id=user.id)
    db_session = MagicMock()
    db_session.query.return_value.filter_by.return_value.first.return_value = (
        oauth_entry
    )
    anonymous = SimpleNamespace(is_authenticated=False)

    with app.test_request_context(
        "/login/generic/authorized?state=state-a&code=code"
    ):
        flask.session[
            cps.oauth_bb.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] = {"state-a": _state("generic", "/book/1")}
        flask.session[
            cps.oauth_bb.oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY
        ] = 1

        with patch("cps.oauth_bb.ub.session", db_session), \
                patch("cps.oauth_bb.current_user", anonymous), \
                patch("cps.oauth_bb.login_user") as login_user, \
                patch("cps.oauth_bb.flash"):
            response = cps.oauth_bb.bind_oauth_or_register(
                "3",
                "provider-user",
                "generic.login",
                "generic",
            )

        login_user.assert_called_once_with(user)
        assert response.status_code == 302
        assert response.location.endswith("/book/1")
        assert (
            cps.oauth_bb.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
            not in flask.session
        )
        assert (
            cps.oauth_bb.oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY
            not in flask.session
        )
