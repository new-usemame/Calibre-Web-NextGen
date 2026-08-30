# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Production-lifecycle coverage for reverse-proxy identity on SPA surfaces.

These tests deliberately call :func:`cps.create_app`. Reverse-proxy identity
is installed by its app-wide ``before_request`` hook, not by the SPA or API
routes themselves; a hand-built Flask app would bypass the behavior that this
regression must protect.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import flask
import pytest
from werkzeug.middleware.proxy_fix import ProxyFix


pytestmark = pytest.mark.unit

_IDENTITY_HEADER = "Remote-User"
_CLIENT_HOP = "client-hop"
_PROXY_HOP = "proxy-hop"


def _user():
    from cps import constants, ub

    user = ub.User()
    user.id = 41
    user.name = "proxy-reader"
    user.locale = "en"
    user.theme = 1
    user.role = constants.ROLE_USER
    user.view_settings = {}
    return user


def _factory_app(monkeypatch, tmp_path):
    """Boot the real factory with only its external startup effects stubbed."""
    import sqlalchemy

    import cps
    from cps import calibre_init, constants, cw_babel, schedule, services

    (tmp_path / "index.html").write_text(
        "<!doctype html><head></head><body><div id=root></div></body>",
        encoding="utf-8",
    )

    # cps owns one module-global Flask object in production. Give this test a
    # fresh equivalent so factory hooks and blueprint registrations cannot leak
    # into another unit test, while retaining the production ProxyFix layer.
    app = flask.Flask("test_1931_production_factory")
    app.wsgi_app = ProxyFix(app.wsgi_app, **cps.proxyfix_hops)
    monkeypatch.setattr(cps, "app", app)

    user = _user()
    state = {"known_user": True}
    seen_remote_addresses = []
    db_session = MagicMock()
    db_session.bind = MagicMock()

    def query(*entities):
        result = MagicMock()
        if len(entities) == 1 and entities[0] is cps.ub.User:
            if flask.has_request_context():
                seen_remote_addresses.append(flask.request.remote_addr)
            result.filter.return_value.first.return_value = (
                user if state["known_user"] else None
            )
        else:
            # The factory's authenticated-user hook also loads magic shelves.
            result.filter.return_value.all.return_value = []
        return result

    db_session.query.side_effect = query
    inspector = MagicMock()
    inspector.get_table_names.return_value = [
        "magic_shelf", "hidden_magic_shelf_templates",
    ]

    monkeypatch.setattr(
        constants, "USER_PROFILES_JSON", str(tmp_path / "users.json")
    )
    if cps.csrf is not None:
        monkeypatch.setattr(cps.csrf, "init_app", lambda _app: None)

    startup_values = {
        "init": lambda: None,
        "settings_path": str(tmp_path / "app.db"),
        "user_credentials": None,
        "memory_backend": False,
        "dry_run": False,
    }
    for name, value in startup_values.items():
        monkeypatch.setattr(cps.cli_param, name, value)

    ub_values = {
        "init_db": lambda _path: None,
        "session": db_session,
        "password_change": lambda _credentials: None,
        "backfill_annotation_content_ids": lambda *_args: None,
        "oauth_support": False,
    }
    for name, value in ub_values.items():
        monkeypatch.setattr(cps.ub, name, value)

    config_sql_values = {
        "get_encryption_key": lambda _path: (None, None),
        "load_configuration": lambda *_args: None,
        "get_flask_session_key": lambda _session: "test",
    }
    for name, value in config_sql_values.items():
        monkeypatch.setattr(cps.config_sql, name, value)

    config_values = {
        "init_config": lambda *_args: None,
        "config_oauth_redirect_host": "",
        "config_session": 0,
        "config_ratelimiter": False,
        "config_limiter_uri": "",
        "config_limiter_options": "",
        "schedule_reconnect": False,
        "store_calibre_uuid": lambda *_args: None,
        "config_login_type": constants.LOGIN_STANDARD,
        "config_use_https": False,
        "config_allow_reverse_proxy_header_login": True,
        "config_reverse_proxy_login_header_name": _IDENTITY_HEADER,
        "config_reverse_proxy_auto_create_users": False,
        "config_anonbrowse": 0,
        "config_trustedhosts": "",
        "config_use_google_drive": False,
        "config_use_goodreads": False,
    }
    for name, value in config_values.items():
        monkeypatch.setattr(cps.config, name, value, raising=False)

    monkeypatch.setattr(
        calibre_init, "init_calibre_db_from_config", lambda *_args: None
    )
    calibre_values = {
        "init_db": lambda: None,
        "ensure_session": lambda: None,
        "_desktop_compat": False,
        "session": None,
        "session_factory": None,
    }
    for name, value in calibre_values.items():
        monkeypatch.setattr(cps.calibre_db, name, value)

    monkeypatch.setattr(cps.updater_thread, "init_updater", lambda *_args: None)
    monkeypatch.setattr(cps.updater_thread, "start", lambda: None)
    monkeypatch.setattr(cps, "ReverseProxied", lambda wsgi_app: wsgi_app)
    monkeypatch.setattr(cps, "Principal", lambda _app: None)
    monkeypatch.setattr(cps.web_server, "init_app", lambda *_args: None)
    monkeypatch.setattr(
        cw_babel.babel, "init_app", lambda *_args, **_kwargs: None
    )
    if hasattr(cw_babel.babel, "localeselector"):
        monkeypatch.setattr(
            cw_babel.babel, "localeselector", lambda _selector: None
        )
    monkeypatch.setattr(services, "ldap", None)
    monkeypatch.setattr(services, "goodreads_support", None)
    monkeypatch.setattr(cps.limiter, "init_app", lambda _app: None)
    monkeypatch.setattr(
        schedule, "register_scheduled_tasks", lambda _enabled: None
    )
    monkeypatch.setattr(schedule, "register_startup_tasks", lambda: None)
    monkeypatch.setattr(sqlalchemy, "inspect", lambda _bind: inspector)

    # The assertion target: this call installs _cwa_ensure_db_session on the
    # app. Removing that production hook makes the trusted cases below fail.
    app = cps.create_app()
    app.testing = True
    app.config.update(
        SECRET_KEY="test",
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
    )

    from cps.api import api_v1
    import cps.api.auth as auth
    import cps.spa as spa_mod
    import cps.usermanagement as usermanagement
    from cps.cw_login import current_user

    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.delenv("CWNG_SPA", raising=False)
    app.register_blueprint(api_v1)
    app.register_blueprint(spa_mod.spa)

    class Anonymous:
        is_authenticated = False
        is_anonymous = True
        name = None

    monkeypatch.setattr(cps.lm, "anonymous_user", Anonymous)

    @app.after_request
    def expose_current_user_for_assertion(response):
        if current_user.is_authenticated:
            response.headers["X-Test-Authenticated-As"] = current_user.name
        return response

    fake_limiter = MagicMock()
    fake_limiter.current_limits = []
    monkeypatch.setattr(usermanagement, "limiter", fake_limiter)
    monkeypatch.setattr(
        auth, "_me_payload", lambda value: {"name": value.name}
    )
    create_user = MagicMock()
    monkeypatch.setattr(usermanagement, "create_authenticated_user", create_user)
    db_session.reset_mock()

    return SimpleNamespace(
        app=app,
        config=cps.config,
        create_user=create_user,
        db_session=db_session,
        seen_remote_addresses=seen_remote_addresses,
        state=state,
        user=user,
    )


def _get(client, path):
    return client.get(
        path,
        headers={
            _IDENTITY_HEADER: "proxy-reader",
            "X-Forwarded-For": _CLIENT_HOP,
            "X-Forwarded-Proto": "https",
        },
        environ_overrides={"REMOTE_ADDR": _PROXY_HOP},
    )


def test_production_factory_hook_identifies_header_user_on_spa_surfaces(
    monkeypatch, tmp_path
):
    harness = _factory_app(monkeypatch, tmp_path)
    client = harness.app.test_client()

    shell = _get(client, "/app/")
    me = _get(client, "/api/v1/auth/me")

    assert shell.status_code == 200
    assert shell.headers["X-Test-Authenticated-As"] == harness.user.name
    assert me.status_code == 200
    assert me.get_json() == {"name": harness.user.name}
    assert me.headers["X-Test-Authenticated-As"] == harness.user.name
    assert harness.seen_remote_addresses == [_CLIENT_HOP, _CLIENT_HOP]


def test_factory_hook_ignores_same_header_when_feature_disabled(
    monkeypatch, tmp_path
):
    harness = _factory_app(monkeypatch, tmp_path)
    harness.config.config_allow_reverse_proxy_header_login = False
    client = harness.app.test_client()

    shell = _get(client, "/app/")
    me = _get(client, "/api/v1/auth/me")

    assert shell.status_code == 200
    assert "X-Test-Authenticated-As" not in shell.headers
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "unauthenticated"
    assert "X-Test-Authenticated-As" not in me.headers
    harness.db_session.query.assert_not_called()
    harness.create_user.assert_not_called()


def test_factory_hook_refuses_unknown_user_when_auto_create_is_off(
    monkeypatch, tmp_path
):
    harness = _factory_app(monkeypatch, tmp_path)
    harness.state["known_user"] = False
    client = harness.app.test_client()

    shell = _get(client, "/app/")
    me = _get(client, "/api/v1/auth/me")

    assert shell.status_code == 200
    assert "X-Test-Authenticated-As" not in shell.headers
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "unauthenticated"
    assert "X-Test-Authenticated-As" not in me.headers
    assert harness.seen_remote_addresses == [_CLIENT_HOP, _CLIENT_HOP]
    harness.create_user.assert_not_called()
