# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #751: configured interface fonts seed newly created users only."""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

from cps import constants
from cps.ui_font_preferences import seed_new_user_ui_font_defaults


def _ctx(path, method="POST", body=None, form=False):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        if isinstance(body, dict) and not form:
            kwargs["json"] = body
            kwargs["content_type"] = "application/json"
        else:
            kwargs["data"] = body
    return app.test_request_context(path, **kwargs)


def _admin():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False,
                           role_admin=lambda: True, id=1)


@pytest.mark.unit
def test_new_user_font_defaults_seed_independently_and_invalid_stored_keys_cannot_inject_css():
    settings = SimpleNamespace(
        config_default_ui_font_body="serif",
        config_default_ui_font_display="mono",
    )
    user = SimpleNamespace(ui_font_body="", ui_font_display="")

    seed_new_user_ui_font_defaults(user, settings)
    assert (user.ui_font_body, user.ui_font_display) == ("serif", "mono")

    settings.config_default_ui_font_body="url(https://example.invalid/font)"
    settings.config_default_ui_font_display=""
    another_user = SimpleNamespace(ui_font_body="", ui_font_display="")
    seed_new_user_ui_font_defaults(another_user, settings)
    assert (another_user.ui_font_body, another_user.ui_font_display) == ("", "")


@pytest.mark.unit
def test_admin_can_save_font_defaults_and_invalid_slug_rejects_the_entire_update():
    from cps.api import admin as mod

    cfg = SimpleNamespace(
        config_theme=1,
        config_books_per_page=20,
        config_random_books=4,
        config_authors_max=0,
        config_calibre_web_title="Library",
        config_default_language="all",
        config_default_locale="en",
        config_default_ui_font_body="",
        config_default_ui_font_display="",
        config_server_announcement="",
        save=MagicMock(),
    )
    with _ctx("/api/v1/admin/config", body={
        "config_default_ui_font_body": "serif",
        "config_default_ui_font_display": "mono",
    }):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "config", cfg), \
             patch.object(mod, "locale_options", return_value=[]), \
             patch.object(mod, "book_language_options", return_value=[]):
            response = inspect.unwrap(mod.admin_update_config)()
    assert response.status_code == 200
    assert (cfg.config_default_ui_font_body, cfg.config_default_ui_font_display) == ("serif", "mono")
    cfg.save.assert_called_once()

    cfg.save.reset_mock()
    with _ctx("/api/v1/admin/config", body={
        "config_calibre_web_title": "must-not-stick",
        "config_default_ui_font_body": "not-a-font",
    }):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "config", cfg):
            rejected = inspect.unwrap(mod.admin_update_config)()
    assert rejected[1] == 400
    assert json.loads(rejected[0].get_data())["error"]["code"] == "invalid_request"
    assert cfg.config_calibre_web_title == "Library"
    assert cfg.config_default_ui_font_body == "serif"
    cfg.save.assert_not_called()


@pytest.mark.unit
def test_classic_admin_rejects_unknown_font_before_mutating_other_settings():
    from cps import admin as mod

    with _ctx("/admin/viewconfig", form=True, body={
        "config_default_ui_font_body": "serif",
        "config_default_ui_font_display": "comic-sans-url",
        "config_calibre_web_title": "must-not-stick",
    }):
        with patch.object(mod, "_", side_effect=lambda value: value), \
             patch.object(mod, "config", SimpleNamespace(config_default_role=0, config_default_show=0)), \
             patch.object(mod, "flash") as flash, \
             patch.object(mod, "view_configuration", return_value="rendered") as render, \
             patch.object(mod, "_config_string") as mutate_string, \
             patch.object(mod, "persist_configured_columns") as mutate_columns:
            result = inspect.unwrap(mod.update_view_configuration)()

    assert result == "rendered"
    flash.assert_called_once()
    assert "display" in flash.call_args.args[0].lower()
    render.assert_called_once()
    mutate_string.assert_not_called()
    mutate_columns.assert_not_called()


@pytest.mark.unit
def test_api_created_user_receives_both_configured_font_defaults():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    created = {}

    class User:
        id = 8
        email = None
        kindle_mail = None

        def __init__(self):
            created["user"] = self

    mock_ub.User = User
    cfg = SimpleNamespace(
        config_default_role=constants.ROLE_DOWNLOAD,
        config_default_locale="en",
        config_default_language="all",
        config_default_show=0,
        config_allowed_tags="",
        config_denied_tags="",
        config_allowed_column_value="",
        config_denied_column_value="",
        config_theme=1,
        config_default_ui_font_body="serif",
        config_default_ui_font_display="mono",
    )
    with _ctx("/api/v1/admin/users", body={"name": "font-user", "password": "S3cret!pw"}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", cfg), \
             patch.object(mod, "check_username", side_effect=lambda n: n), \
             patch.object(mod, "valid_password", side_effect=lambda p: p), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: p):
            response = inspect.unwrap(mod.admin_create_user)()

    assert response[1] == 201
    assert (created["user"].ui_font_body, created["user"].ui_font_display) == ("serif", "mono")
