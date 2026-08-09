# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import flask
import pytest

import cps.web


pytestmark = pytest.mark.unit


class _Anonymous:
    is_authenticated = False


class _Authenticated:
    is_authenticated = True


def _app():
    app = flask.Flask(__name__)
    app.testing = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(cps.web.web)
    # ``web.login`` redirects straight to the Flask-Dance endpoint. The
    # endpoint-resolution suite pins the real blueprints separately; this
    # route keeps these tests focused on the actual /login wiring and session.
    app.add_url_rule(
        "/login/generic",
        endpoint="generic.login",
        view_func=lambda: "provider",
    )
    return app


def _oauth_only_patches(*, spa_preferred=False, standard_login_disabled=True):
    # ``config_login_type`` and ``config_disable_standard_login`` are separate
    # settings: the first picks the mechanism, the second decides whether the
    # local form is still offered. Auto-start requires both, so every case here
    # has to state which combination it is exercising.
    return (
        patch("cps.web.current_user", _Anonymous()),
        patch(
            "cps.web.config.config_login_type",
            cps.web.constants.LOGIN_OAUTH,
            create=True,
        ),
        patch(
            "cps.web.config.config_disable_standard_login",
            standard_login_disabled,
            create=True,
        ),
        patch.dict(cps.web.feature_support, {"oauth": True}),
        patch.object(
            cps.web.oauth_bb,
            "oauthblueprints",
            [{"provider_name": "generic", "active": True}],
        ),
        patch(
            "cps.web.spa.preferred_spa_html_request",
            return_value=spa_preferred,
        ),
    )


def test_login_route_starts_provider_and_forwards_relative_next():
    app = _app()
    client = app.test_client()

    p1, p2, p3, p4, p5, p6 = _oauth_only_patches()
    with p1, p2, p3, p4, p5, p6:
        response = client.get("/login?next=/book/7")

    assert response.status_code == 302
    parsed = urlparse(response.location)
    assert parsed.path == "/login/generic"
    assert parse_qs(parsed.query) == {
        cps.web.oauth_auto_redirect.AUTO_REDIRECT_PARAMETER: [
            cps.web.oauth_auto_redirect.AUTO_REDIRECT_VALUE,
        ],
        "next": ["/book/7"],
    }
    with client.session_transaction() as session_store:
        assert session_store[
            cps.web.oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY
        ] == 1

def test_local_parameter_falls_through_to_spa_preference():
    app = _app()
    p1, p2, p3, p4, p5, p6 = _oauth_only_patches(spa_preferred=True)

    with p1, p2, p3, p4, p5, p6, \
            patch("cps.web.spa.spa_shell_url", return_value="/app"), \
            patch("cps.web.render_login") as render_login:
        response = app.test_client().get("/login?local=1")

    assert response.status_code == 302
    assert response.location.endswith("/app")
    render_login.assert_not_called()


def test_local_parameter_falls_through_to_classic_login():
    app = _app()
    client = app.test_client()
    with client.session_transaction() as session_store:
        session_store[
            cps.web.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] = {"state-from-other-tab": {"provider": "generic", "next": "/book/3"}}

    p1, p2, p3, p4, p5, p6 = _oauth_only_patches(spa_preferred=False)
    with p1, p2, p3, p4, p5, p6, \
            patch("cps.web.render_login", return_value="classic") as render_login:
        response = client.get("/login?local=1")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "classic"
    render_login.assert_called_once_with()
    with client.session_transaction() as session_store:
        assert session_store[
            cps.web.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] == {"state-from-other-tab": {"provider": "generic", "next": "/book/3"}}


def test_login_route_stops_auto_start_after_existing_limit():
    app = _app()
    client = app.test_client()
    with client.session_transaction() as session_store:
        session_store[
            cps.web.oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY
        ] = 4

    p1, p2, p3, p4, p5, p6 = _oauth_only_patches(spa_preferred=False)
    with p1, p2, p3, p4, p5, p6, \
            patch("cps.web.render_login", return_value="classic"):
        response = client.get("/login")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "classic"
    with client.session_transaction() as session_store:
        assert session_store[
            cps.web.oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY
        ] == 4
        assert cps.web.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY not in session_store


def test_no_auto_start_while_standard_login_is_still_enabled():
    # OAuth as the mechanism does not by itself retire the local form:
    # ``config_disable_standard_login`` defaults to False and is set in its own
    # admin section. ``login_post`` still accepts local credentials in that
    # state, so ``GET /login`` has to keep rendering the form to submit them.
    # Auto-starting here strands an admin at the canonical URL whenever the
    # provider is unreachable.
    app = _app()
    client = app.test_client()

    p1, p2, p3, p4, p5, p6 = _oauth_only_patches(
        spa_preferred=False, standard_login_disabled=False
    )
    with p1, p2, p3, p4, p5, p6, \
            patch("cps.web.render_login", return_value="classic") as render_login:
        response = client.get("/login")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "classic"
    render_login.assert_called_once_with()


def test_authenticated_login_route_clears_auto_attempts():
    app = _app()
    client = app.test_client()
    with client.session_transaction() as session_store:
        session_store[
            cps.web.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY
        ] = {"state-1": {"provider": "generic", "next": "/book/7"}}

    with patch("cps.web.current_user", _Authenticated()):
        response = client.get("/login")

    assert response.status_code == 302
    with client.session_transaction() as session_store:
        assert cps.web.oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY not in session_store
