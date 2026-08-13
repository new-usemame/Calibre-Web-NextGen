# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 deep security config (admin_security.py).

Focus on the genuinely-new logic in this module: admin gating, the
security-critical write-only invariant (secrets MUST NOT appear in the GET
payload), and the legacy-error message extractor. The POST orchestration reuses
cps.admin's already-tested LDAP/OAuth helpers and is additionally verified live
end-to-end, so it isn't re-mocked here.
"""
import inspect
import json

import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from cps import constants


def _ctx(path="/api/v1/admin/security", method="GET", body=None):
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


def _fake_config(**over):
    """A config stand-in carrying every attribute _security_payload reads, with a
    secret bind password + a value for each scalar."""
    base = dict(
        config_login_type=0,
        config_ldap_provider_url="ldap.example.org", config_ldap_port=389,
        config_ldap_encryption=0, config_ldap_authentication=2,
        config_ldap_serv_username="cn=admin,dc=example,dc=org",
        config_ldap_serv_password_e="SUPER-SECRET-LDAP-PW",   # must never serialize
        config_ldap_auto_create_users=True, config_ldap_dn="dc=example,dc=org",
        config_ldap_user_object="uid=%s", config_ldap_member_user_object="",
        config_ldap_group_object_filter="(&(cn=%s))", config_ldap_group_members_field="memberUid",
        config_ldap_group_name="calibreweb", config_ldap_openldap=True,
        config_ldap_cacert_path="", config_ldap_cert_path="", config_ldap_key_path="",
        config_oauth_redirect_host="", config_disable_standard_login=False,
        config_enable_oauth_group_admin_management=True,
        config_use_https=False, config_certfile="", config_keyfile="",
        config_remote_login=True,
        config_allow_reverse_proxy_header_login=False,
        config_reverse_proxy_login_header_name="", config_reverse_proxy_auto_create_users=False,
    )
    base.update(over)
    ns = SimpleNamespace(**base)
    ns.save = lambda: None  # no-op persist for POST-path tests
    return ns


def _fake_builtin_rows():
    return [
        SimpleNamespace(provider_name="github", id=1, oauth_client_id="gh-id",
                        oauth_client_secret="GITHUB-SECRET", active=True),
        SimpleNamespace(provider_name="google", id=2, oauth_client_id="",
                        oauth_client_secret="", active=False),
    ]


def _fake_generic_row():
    return SimpleNamespace(
        oauth_client_id="my-client-id",
        oauth_client_secret="SUPER-SECRET-OAUTH-SECRET",   # must never serialize
        oauth_base_url="https://idp.example.org", oauth_authorize_url="https://idp/auth",
        oauth_token_url="https://idp/token", oauth_userinfo_url="https://idp/userinfo",
        oauth_admin_group="admins", metadata_url="https://idp/.well-known/openid-configuration",
        scope="openid profile email", username_mapper="preferred_username",
        email_mapper="email", login_button="OpenID Connect", active=True,
        oauth_group_claim="groups", oauth_require_group=True,
        oauth_allowed_groups="calibre-user, calibre-admin",
        oauth_default_role=constants.ROLE_DOWNLOAD | constants.ROLE_VIEWER,
    )


# ── gating ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_security_requires_admin():
    from cps.api import admin_security as mod
    with _ctx():
        from cps.api import admin as admin_mod
        with patch.object(admin_mod, "current_user", _admin(is_admin=False)):
            resp = inspect.unwrap(mod.admin_get_security)()
    assert resp[1] == 403


@pytest.mark.unit
def test_get_security_anonymous_401():
    from cps.api import admin_security as mod
    with _ctx():
        from cps.api import admin as admin_mod
        with patch.object(admin_mod, "current_user", _admin(anon=True)):
            resp = inspect.unwrap(mod.admin_get_security)()
    assert resp[1] == 401


# ── write-only secret invariant ──────────────────────────────────────────────

@pytest.mark.unit
def test_get_security_never_leaks_secrets():
    """The serialized payload must reduce both secrets to booleans and never
    contain the raw secret values anywhere."""
    from cps.api import admin_security as mod
    with _ctx():
        from cps.api import admin as admin_mod
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config()), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows):
            resp = inspect.unwrap(mod.admin_get_security)()
    payload = resp.get_json()
    blob = json.dumps(payload)
    assert "SUPER-SECRET-LDAP-PW" not in blob
    assert "SUPER-SECRET-OAUTH-SECRET" not in blob
    assert "GITHUB-SECRET" not in blob
    # The booleans that replace them are present and true.
    assert payload["ldap"]["has_password"] is True
    assert payload["oauth"]["generic"]["has_secret"] is True
    # Non-secret fields are still surfaced for the form.
    assert payload["oauth"]["generic"]["client_id"] == "my-client-id"
    assert payload["ldap"]["user_object"] == "uid=%s"
    assert "oauth_client_secret" not in blob
    assert "config_ldap_serv_password_e" not in blob


@pytest.mark.unit
def test_get_security_no_oauth_row_has_secret_false():
    from cps.api import admin_security as mod
    with _ctx():
        from cps.api import admin as admin_mod
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config(config_ldap_serv_password_e=None)), \
             patch.object(mod, "_generic_oauth_row", lambda: None), \
             patch.object(mod, "_builtin_oauth_rows", lambda: []):
            resp = inspect.unwrap(mod.admin_get_security)()
    payload = resp.get_json()
    assert payload["ldap"]["has_password"] is False
    assert payload["oauth"]["generic"]["has_secret"] is False
    assert payload["oauth"]["generic"]["active"] is False


# ── legacy-error extraction ──────────────────────────────────────────────────

@pytest.mark.unit
def test_helper_error_message_extracts_danger():
    from cps.api import admin_security as mod
    resp = flask.Response(
        json.dumps({"result": [{"type": "danger", "message": "Bad LDAP filter"}], "reboot": False}),
        mimetype="application/json")
    assert mod._helper_error_message(resp) == "Bad LDAP filter"


@pytest.mark.unit
def test_helper_error_message_falls_back():
    from cps.api import admin_security as mod
    resp = flask.Response("not json at all", mimetype="application/json")
    assert mod._helper_error_message(resp) == "Invalid security configuration"


# ── lockout-prevention regression (the login-type switch must not be persisted
#    before LDAP/OAuth validation passes) ──────────────────────────────────────

@pytest.mark.unit
def test_login_type_not_committed_when_ldap_validation_fails(monkeypatch):
    """REGRESSION: posting login_type=LDAP with an invalid LDAP config must return
    400 WITHOUT ever committing config_login_type — otherwise the admin is locked
    out after the restart this endpoint requests. The login-type commit goes
    through _config_int at the very end, so if validation fails _config_int must
    never be called for config_login_type."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    import unittest.mock as um

    bad = flask.Response(json.dumps({"result": [{"type": "danger",
          "message": 'LDAP User Object Filter needs to Have One "%s" Format Identifier'}]}),
          mimetype="application/json")
    config_int_spy = um.MagicMock(return_value=False)
    with _ctx(method="POST", body={"login_type": 1, "ldap": {"user_object": "bad-no-format"}}):
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config()), \
             patch.object(mod, "_configuration_ldap_helper", lambda to_save: (False, bad)), \
             patch.object(mod, "_config_int", config_int_spy):
            resp = inspect.unwrap(mod.admin_update_security)()
    assert resp[1] == 400
    # login-type was never pushed through _config_int -> never committed.
    called_keys = [c.args[1] for c in config_int_spy.call_args_list if len(c.args) > 1]
    assert "config_login_type" not in called_keys


@pytest.mark.unit
def test_unknown_login_type_rejected():
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    with _ctx(method="POST", body={"login_type": 999}):
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config()):
            resp = inspect.unwrap(mod.admin_update_security)()
    assert resp[1] == 400
    assert "login type" in resp[0].get_json()["error"]["message"].lower()


@pytest.mark.unit
def test_reverse_proxy_auto_without_enabled_rejected_before_mutation():
    """auto-create-users without header-login enabled must 400 BEFORE any config
    mutation (no divergent half-applied state)."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    import unittest.mock as um
    checkbox_spy = um.MagicMock(return_value=False)
    with _ctx(method="POST",
              body={"reverse_proxy": {"enabled": False, "auto_create_users": True, "header_name": ""}}):
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config()), \
             patch.object(mod, "_config_checkbox", checkbox_spy):
            resp = inspect.unwrap(mod.admin_update_security)()
    assert resp[1] == 400
    # validation fired before we applied any reverse-proxy checkbox to config.
    checkbox_spy.assert_not_called()


# ── OAuth group-based access control parity (#494/#495) ───────────────────────

@pytest.mark.unit
def test_get_exposes_oauth_group_access_fields():
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    with _ctx():
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config()), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows):
            resp = inspect.unwrap(mod.admin_get_security)()
    g = resp.get_json()["oauth"]["generic"]
    assert g["group_claim"] == "groups"
    assert g["require_group"] is True
    assert g["allowed_groups"] == "calibre-user, calibre-admin"
    assert g["default_roles"]["download"] is True
    assert g["default_roles"]["viewer"] is True
    assert g["default_roles"]["upload"] is False


@pytest.mark.unit
def test_post_oauth_preserves_group_access_fields(monkeypatch):
    """REGRESSION (#494/#495 merge): the SPA OAuth save must forward the group-
    access fields into to_save, otherwise the legacy helper resets them — silently
    disabling require_group (a security control) and clearing allowed groups."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    captured = {}

    def fake_oauth_helper(to_save):
        captured.update(to_save)
        return (False, None)

    body = {"login_type": 2, "oauth": {"generic": {
        "client_id": "cid", "require_group": True, "allowed_groups": "g1, g2",
        "group_claim": "roles", "default_roles": {"download": True, "edit": True}}}}
    with _ctx(method="POST", body=body):
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config(config_login_type=2)), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows), \
             patch.object(mod, "_configuration_oauth_helper", fake_oauth_helper), \
             patch.object(mod, "_config_int", lambda *a, **k: False), \
             patch.object(mod, "_config_string", lambda *a, **k: False), \
             patch.object(mod, "_config_checkbox", lambda *a, **k: False), \
             patch.object(mod, "_security_payload", lambda: {}):
            inspect.unwrap(mod.admin_update_security)()
    assert captured.get("config_generic_oauth_group_claim") == "roles"
    assert captured.get("config_generic_oauth_allowed_groups") == "g1, g2"
    assert captured.get("config_generic_oauth_require_group") == "on"          # checkbox present = true
    assert captured.get("config_generic_oauth_default_download_role") == "on"
    assert captured.get("config_generic_oauth_default_edit_role") == "on"
    assert "config_generic_oauth_default_upload_role" not in captured          # unchecked = absent
    # built-in github/google keys supplied (preserved current values) so the
    # legacy helper doesn't KeyError on config_<id>_oauth_client_id.
    assert captured.get("config_1_oauth_client_id") == "gh-id"
    assert captured.get("config_1_oauth_client_secret") == "GITHUB-SECRET"      # write-only preserve
    assert captured.get("config_2_oauth_client_id") == ""


# ── OAuth auto-forward setting on the SPA admin surface ──────────────────────

@pytest.mark.unit
def test_get_exposes_oauth_auto_forward():
    """The React Security page has to be able to SEE the setting.

    It was added to the classic config page first; a setting that exists only
    there is invisible to an admin who never opens the Jinja form."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    with _ctx():
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config(config_enable_oauth_auto_forward=True)), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows):
            resp = inspect.unwrap(mod.admin_get_security)()
    assert resp.get_json()["oauth"]["enable_oauth_auto_forward"] is True


@pytest.mark.unit
def test_get_oauth_auto_forward_defaults_false_before_migration():
    """A config row predating the column must read as off, not raise."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    cfg = _fake_config()
    assert not hasattr(cfg, "config_enable_oauth_auto_forward")
    with _ctx():
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", cfg), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows):
            resp = inspect.unwrap(mod.admin_get_security)()
    assert resp.get_json()["oauth"]["enable_oauth_auto_forward"] is False


def _capture_oauth_save(auto_forward):
    """Run the SPA security POST and return the form-shaped to_save it built.

    The security checkboxes are appended AFTER ``_configuration_oauth_helper``
    runs, so this keeps the live dict rather than a copy taken at call time -
    a snapshot here would miss every checkbox and read as "not saved"."""
    from cps.api import admin_security as mod
    from cps.api import admin as admin_mod
    captured = {}

    def fake_oauth_helper(to_save):
        captured["live"] = to_save
        return (False, None)

    body = {"login_type": 2, "oauth": {"enable_oauth_auto_forward": auto_forward,
                                       "generic": {"client_id": "cid"}}}
    with _ctx(method="POST", body=body):
        with patch.object(admin_mod, "current_user", _admin()), \
             patch.object(mod, "config", _fake_config(config_login_type=2)), \
             patch.object(mod, "_generic_oauth_row", _fake_generic_row), \
             patch.object(mod, "_builtin_oauth_rows", _fake_builtin_rows), \
             patch.object(mod, "_configuration_oauth_helper", fake_oauth_helper), \
             patch.object(mod, "_config_int", lambda *a, **k: False), \
             patch.object(mod, "_config_string", lambda *a, **k: False), \
             patch.object(mod, "_config_checkbox", lambda *a, **k: False), \
             patch.object(mod, "_security_payload", lambda: {}):
            inspect.unwrap(mod.admin_update_security)()
    return captured["live"]


@pytest.mark.unit
def test_post_oauth_auto_forward_true_is_saved():
    assert _capture_oauth_save(True).get("config_enable_oauth_auto_forward") == "on"


@pytest.mark.unit
def test_post_oauth_auto_forward_false_is_absent_so_checkbox_clears_it():
    # Checkbox semantics: absent from the form means off. Sending "on" for a
    # false value would make the setting impossible to turn back off.
    assert "config_enable_oauth_auto_forward" not in _capture_oauth_save(False)
