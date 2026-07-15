# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 admin user management — admin gating, role-bit
serialization, the last-admin lockout guard, and delete gating."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from cps import constants
from cps import ui_themes


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _admin(is_admin=True, anon=False, uid=1):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon,
                           role_admin=lambda: is_admin, id=uid)


def _user(uid=2, name="maggie", role=constants.ROLE_DOWNLOAD):
    return SimpleNamespace(id=uid, name=name, email="m@x.com", kindle_mail="",
                           locale="en", default_language="all", role=role)


# ── gating ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_list_users_requires_admin():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users", method="GET"):
        with patch.object(mod, "current_user", _admin(is_admin=False)):
            resp = inspect.unwrap(mod.admin_list_users)()
    assert resp[1] == 403


@pytest.mark.unit
def test_list_users_anonymous_401():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users", method="GET"):
        with patch.object(mod, "current_user", _admin(anon=True)):
            resp = inspect.unwrap(mod.admin_list_users)()
    assert resp[1] == 401


# ── password reset parity (#745) ────────────────────────────────────────────

@pytest.mark.unit
def test_reset_password_requires_admin():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin(is_admin=False)):
            resp = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert resp[1] == 403


@pytest.mark.unit
def test_reset_password_blocks_self_before_lookup():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users/1/reset-password"):
        with patch.object(mod, "current_user", _admin(uid=1)):
            resp = inspect.unwrap(mod.admin_reset_user_password)(1)
    assert resp[1] == 409


@pytest.mark.unit
def test_reset_password_requires_valid_email_and_mail_config():
    from cps.api import admin as mod
    target = _user()
    target.email = ""
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert resp[1] == 409
    assert json.loads(resp[0].get_data())["error"]["code"] == "email_required"

    target.email = "not-an-email"
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin()), patch.object(mod, "ub", mock_ub):
            invalid = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert invalid[1] == 409

    target.email = "m@x.com"
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod.config, "get_mail_server_configured", return_value=False):
            no_mail = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert no_mail[1] == 409
    assert json.loads(no_mail[0].get_data())["error"]["code"] == "mail_not_configured"


@pytest.mark.unit
def test_reset_password_success_never_returns_password():
    from cps.api import admin as mod
    target = _user()
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod.config, "get_mail_server_configured", return_value=True), \
             patch.object(mod, "reset_password", return_value=(1, target.name)) as core:
            resp, status = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert status == 202
    payload = json.loads(resp.get_data())
    assert payload == {"ok": True, "message": "A password reset email has been queued."}
    assert "Temp123!" not in json.dumps(payload)
    core.assert_called_once_with(2)


@pytest.mark.unit
def test_reset_password_failure_is_not_reported_as_success():
    from cps.api import admin as mod
    target = _user()
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2/reset-password"):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod.config, "get_mail_server_configured", return_value=True), \
             patch.object(mod, "reset_password", return_value=(0, None)):
            resp = inspect.unwrap(mod.admin_reset_user_password)(2)
    assert resp[1] == 500


# ── serialization ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_serialize_user_roles_from_bitmask():
    from cps.api import admin as mod
    u = _user(role=constants.ROLE_ADMIN | constants.ROLE_UPLOAD)
    out = mod._serialize_user(u)
    assert out["roles"]["admin"] is True
    assert out["roles"]["upload"] is True
    assert out["roles"]["edit"] is False
    assert out["roles"]["delete_books"] is False


# ── role update + lockout guard ──────────────────────────────────────────────

@pytest.mark.unit
def test_update_user_last_admin_lockout():
    from cps.api import admin as mod
    target = _user(uid=1, name="admin", role=constants.ROLE_ADMIN)
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/1", body={"roles": {"admin": False}}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_other_admin_count", return_value=0):
            resp = inspect.unwrap(mod.admin_update_user)(1)
    assert resp[1] == 400
    assert json.loads(resp[0].get_data())["error"]["code"] == "conflict"
    # role must be unchanged (still admin)
    assert target.role & constants.ROLE_ADMIN


@pytest.mark.unit
def test_update_user_toggles_roles():
    from cps.api import admin as mod
    target = _user(uid=2, name="maggie", role=constants.ROLE_DOWNLOAD)
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2", body={"roles": {"upload": True, "download": False}}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.admin_update_user)(2)
    assert resp.status_code == 200
    assert target.role & constants.ROLE_UPLOAD
    assert not (target.role & constants.ROLE_DOWNLOAD)


@pytest.mark.unit
def test_update_user_not_found_404():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/admin/users/99", body={"roles": {}}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.admin_update_user)(99)
    assert resp[1] == 404


# ── delete ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_delete_self_blocked():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users/1/delete"):
        with patch.object(mod, "current_user", _admin(uid=1)):
            resp = inspect.unwrap(mod.admin_delete_user)(1)
    assert resp[1] == 400
    assert "own account" in json.loads(resp[0].get_data())["error"]["message"]


@pytest.mark.unit
def test_delete_user_reuses_core_204():
    from cps.api import admin as mod
    target = _user(uid=2)
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2/delete"):
        with patch.object(mod, "current_user", _admin(uid=1)), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_delete_user", return_value="deleted") as core:
            resp = inspect.unwrap(mod.admin_delete_user)(2)
    assert resp[1] == 204
    core.assert_called_once_with(target)


@pytest.mark.unit
def test_delete_user_last_admin_guard_surfaces_as_400():
    from cps.api import admin as mod
    target = _user(uid=2, name="admin2", role=constants.ROLE_ADMIN)
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = target
    with _ctx("/api/v1/admin/users/2/delete"):
        with patch.object(mod, "current_user", _admin(uid=1)), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "_delete_user", side_effect=Exception("No admin user remaining")):
            resp = inspect.unwrap(mod.admin_delete_user)(2)
    assert resp[1] == 400
    assert "admin" in json.loads(resp[0].get_data())["error"]["message"].lower()


# ── user creation ─────────────────────────────────────────────────────────────

def _create_config(config_theme=ui_themes.DEFAULT_THEME_CODE):
    """A config double carrying the defaults _handle_new_user reads.

    ``config_theme`` mirrors the real Settings column: it is the instance default
    theme a new account is seeded with, so it is a parameter here rather than a
    constant — the seeding is behaviour worth pinning at more than one value.
    """
    return SimpleNamespace(
        config_default_role=constants.ROLE_DOWNLOAD,
        config_default_locale="en",
        config_default_language="all",
        config_default_show=0,
        config_allowed_tags="", config_denied_tags="",
        config_allowed_column_value="", config_denied_column_value="",
        config_theme=config_theme,
    )


@pytest.mark.unit
def test_create_user_requires_admin():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users", body={"name": "x", "password": "y"}):
        with patch.object(mod, "current_user", _admin(is_admin=False)):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 403


@pytest.mark.unit
def test_create_user_anonymous_401():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users", body={"name": "x", "password": "y"}):
        with patch.object(mod, "current_user", _admin(anon=True)):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 401


@pytest.mark.unit
def test_create_user_missing_fields_400():
    from cps.api import admin as mod
    with _ctx("/api/v1/admin/users", body={"name": "onlyname"}):
        with patch.object(mod, "current_user", _admin()):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 400
    assert "required" in json.loads(resp[0].get_data())["error"]["message"].lower()


@pytest.mark.unit
def test_create_user_valid_sets_roles_and_commits():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    created = {}

    class _U:
        # Defaults for the columns _serialize_user reads but the body may omit.
        id = 6
        email = None
        kindle_mail = None

        def __init__(self):
            created["obj"] = self
    mock_ub.User = _U

    with _ctx("/api/v1/admin/users",
              body={"name": "maggie", "password": "S3cret!pw",
                    "roles": {"upload": True, "download": True}}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", _create_config()), \
             patch.object(mod, "check_username", side_effect=lambda n: n), \
             patch.object(mod, "valid_password", side_effect=lambda p: p), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: "HASH:" + p):
            resp = inspect.unwrap(mod.admin_create_user)()

    # 201 + the new user committed with the requested role bits.
    assert resp[1] == 201
    mock_ub.session.add.assert_called_once()
    mock_ub.session.commit.assert_called_once()
    obj = created["obj"]
    assert obj.name == "maggie"
    assert obj.password == "HASH:S3cret!pw"
    assert obj.role & constants.ROLE_UPLOAD
    assert obj.role & constants.ROLE_DOWNLOAD
    assert not (obj.role & constants.ROLE_ADMIN)
    assert obj.theme == ui_themes.DEFAULT_THEME_CODE  # seeded from config_theme


@pytest.mark.unit
def test_create_user_defaults_role_when_unspecified():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    created = {}

    class _U:
        # Defaults for the columns _serialize_user reads but the body may omit.
        id = 6
        email = None
        kindle_mail = None

        def __init__(self):
            created["obj"] = self
    mock_ub.User = _U

    with _ctx("/api/v1/admin/users", body={"name": "bob", "password": "S3cret!pw"}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", _create_config()), \
             patch.object(mod, "check_username", side_effect=lambda n: n), \
             patch.object(mod, "valid_password", side_effect=lambda p: p), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: p):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 201
    assert created["obj"].role == constants.ROLE_DOWNLOAD  # the configured default


@pytest.mark.unit
@pytest.mark.parametrize("config_theme, expected_code", [
    (2, 2),   # admin picked light -> the new account boots light
    (3, 3),   # a non-legacy theme the pre-#736 form could not even express
    (0, 2),   # the legacy "Light" the old form wrote raw; honour it as light
    (99, ui_themes.DEFAULT_THEME_CODE),   # unknown code degrades to dark, never NULL
])
def test_create_user_seeds_theme_from_config(config_theme, expected_code):
    """#736: a new account inherits the instance default theme.

    The old code hardcoded ``theme = 1``, so an admin who set the default to
    Light still got dark accounts and the setting drove nothing. Pinning this at
    several values is what makes the test behavioural — an assertion against
    dark alone stays green if the hardcode ever comes back.
    """
    from cps.api import admin as mod
    mock_ub = MagicMock()
    created = {}

    class _U:
        id = 7
        email = None
        kindle_mail = None

        def __init__(self):
            created["obj"] = self
    mock_ub.User = _U

    with _ctx("/api/v1/admin/users", body={"name": "ada", "password": "S3cret!pw"}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", _create_config(config_theme=config_theme)), \
             patch.object(mod, "check_username", side_effect=lambda n: n), \
             patch.object(mod, "valid_password", side_effect=lambda p: p), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: p):
            resp = inspect.unwrap(mod.admin_create_user)()

    assert resp[1] == 201
    assert created["obj"].theme == expected_code


def _ui_config():
    """A config double for the SPA-editable UI settings admin_update_config writes."""
    return SimpleNamespace(
        config_theme=ui_themes.DEFAULT_THEME_CODE,
        config_books_per_page=20, config_random_books=4, config_authors_max=0,
        config_calibre_web_title="t", config_default_language="all",
        config_default_locale="en", config_server_announcement="",
        save=lambda: None,
    )


@pytest.mark.unit
@pytest.mark.parametrize("slug, expected_code", [
    ("light", 2),
    ("sepia", 3),
    ("system", 6),
])
def test_update_config_stores_a_valid_theme_slug_as_its_code(slug, expected_code):
    from cps.api import admin as mod
    cfg = _ui_config()
    with _ctx("/api/v1/admin/config", body={"config_theme": slug}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "config", cfg), \
             patch.object(mod, "get_available_locale", return_value=[]):
            # The 200 path echoes the config payload, which enumerates locales
            # through babel; this test is about what gets stored, so stub it.
            resp = inspect.unwrap(mod.admin_update_config)()
    assert cfg.config_theme == expected_code


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["hot-pink", "", "Light", 0, 2, None, True])
def test_update_config_rejects_a_theme_that_is_not_a_known_slug(bad):
    """#736: the raw int is the drift vector — the pre-#736 form POSTed
    config_theme=0 and the endpoint stored it, which then read back as dark. An
    unknown value must 400 rather than be stored and silently resolved, so ints
    (including the legacy 0) are rejected here alongside nonsense slugs.
    """
    from cps.api import admin as mod
    cfg = _ui_config()
    before = cfg.config_theme
    with _ctx("/api/v1/admin/config", body={"config_theme": bad}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "config", cfg):
            resp = inspect.unwrap(mod.admin_update_config)()
    assert resp[1] == 400
    assert cfg.config_theme == before  # nothing stored


@pytest.mark.unit
def test_create_user_duplicate_name_surfaces_400():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    mock_ub.User = SimpleNamespace
    with _ctx("/api/v1/admin/users", body={"name": "dupe", "password": "S3cret!pw"}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", _create_config()), \
             patch.object(mod, "check_username", side_effect=Exception("This username is already taken")), \
             patch.object(mod, "valid_password", side_effect=lambda p: p), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: p):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 400
    assert "taken" in json.loads(resp[0].get_data())["error"]["message"].lower()


@pytest.mark.unit
def test_create_user_password_policy_surfaces_400():
    from cps.api import admin as mod
    mock_ub = MagicMock()
    mock_ub.User = SimpleNamespace
    with _ctx("/api/v1/admin/users", body={"name": "weak", "password": "abc"}):
        with patch.object(mod, "current_user", _admin()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", _create_config()), \
             patch.object(mod, "check_username", side_effect=lambda n: n), \
             patch.object(mod, "valid_password", side_effect=Exception("Password doesn't comply with password validation rules")), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: p):
            resp = inspect.unwrap(mod.admin_create_user)()
    assert resp[1] == 400
    assert "password" in json.loads(resp[0].get_data())["error"]["message"].lower()
