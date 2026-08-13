# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep admin security configuration for /api/v1 (admin-only).

Covers the login/authentication surface that the legacy ``config_edit.html``
exposes: login type (standard / LDAP / OAuth), the full LDAP connection +
filter + client-cert config, the generic OAuth/OIDC provider, server SSL/HTTPS,
reverse-proxy header login, and remote (magic-link) login.

SINGLE SOURCE OF TRUTH: the genuinely security-critical validation — LDAP filter
parenthesis/format checks, service-account/password requirements, client-cert
existence, OAuth metadata auto-discovery, write-only secret handling — is NOT
reimplemented here. We build the same form-shaped ``to_save`` dict the legacy
form posts and call ``cps.admin._configuration_ldap_helper`` /
``_configuration_oauth_helper`` directly, so the SPA and the legacy page enforce
byte-for-byte identical rules. Only the thin orchestration (login-type switch +
scalar SSL/reverse-proxy/remote-login fields + save) is re-expressed here, mirroring
``cps.admin._configuration_update_helper``.

SECRETS ARE WRITE-ONLY. GET never returns ``config_ldap_serv_password_e`` or the
OAuth ``oauth_client_secret`` — only a ``has_password`` / ``has_secret`` boolean.
A secret is overwritten only when the client sends a non-empty replacement.

RESTART: changing the login type / LDAP / OAuth requires a server restart for the
auth blueprints to re-register. Unlike the legacy form (which calls
``web_server.stop(True)`` itself), this endpoint does NOT restart the server from
within the request — it returns ``reboot_required: true`` and the SPA surfaces a
"restart required" banner so the admin restarts deliberately via the existing
control. This avoids a config endpoint tearing down the worker mid-response.

SECURITY-REVIEW: this module writes auth/session/secret configuration and adds new
routes — CLAUDE.md hard-rule 3c requires ``/security-review`` and operator merge
before this ships. It must NOT be admin-auto-merged.
"""
import json

from flask import jsonify, request

from . import api_v1
from .. import ub, config, constants, logger
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from .admin import _require_admin, _err
from ..admin import (
    _config_int, _config_string, _config_checkbox,
    _configuration_ldap_helper, _configuration_oauth_helper,
)
from .. import apply_https_runtime_config

log = logger.create()

_LOGIN_TYPES = [
    {"id": constants.LOGIN_STANDARD, "name": "Standard (username / password)"},
    {"id": constants.LOGIN_LDAP, "name": "LDAP"},
    {"id": constants.LOGIN_OAUTH, "name": "OAuth / OpenID Connect"},
]

# LDAP service-account bind mode. Mirrors the legacy select; the labels match
# config_edit.html. (ANONYMOUS=0, UNAUTHENTICATE=1, SIMPLE bind uses a username
# + password — legacy reuses value 0/2 semantics via the >ANONYMOUS / >UNAUTH
# comparisons in _configuration_ldap_helper.)
_LDAP_AUTH_LEVELS = [
    {"id": constants.LDAP_AUTH_ANONYMOUS, "name": "Anonymous"},
    {"id": constants.LDAP_AUTH_UNAUTHENTICATE, "name": "Unauthenticated"},
    {"id": 2, "name": "Simple (service account)"},
]

_LDAP_ENCRYPTION_LEVELS = [
    {"id": 0, "name": "None"},
    {"id": 1, "name": "TLS"},
    {"id": 2, "name": "SSL"},
]

# Generic-OAuth default-role bits (the roles auto-granted to a new OAuth-created
# user). The legacy form posts one checkbox per role (config_generic_oauth_default_
# <key>_role); the helper ORs them into the oauth_default_role bitmask. We expose
# the bitmask as a {key: bool} dict and rebuild the checkbox keys on POST. Keys
# match the legacy form-field stems so _selected_generic_oauth_default_role reads
# them unchanged.
_OAUTH_DEFAULT_ROLE_BITS = {
    "download": constants.ROLE_DOWNLOAD,
    "viewer": constants.ROLE_VIEWER,
    "upload": constants.ROLE_UPLOAD,
    "edit": constants.ROLE_EDIT,
    "delete": constants.ROLE_DELETE_BOOKS,
    "passwd": constants.ROLE_PASSWD,
    "edit_shelf": constants.ROLE_EDIT_SHELFS,
}


def _generic_oauth_row():
    """The generic OIDC provider row (the SPA-configurable one). Reading the DB
    table reflects current state even before a restart rebuilds the in-memory
    blueprint list."""
    return (ub.session.query(ub.OAuthProvider)
            .filter(ub.OAuthProvider.provider_name == "generic")
            .first())


def _builtin_oauth_rows():
    """The built-in GitHub + Google provider rows (client-id/secret only). The
    legacy oauth helper iterates ALL providers and direct-indexes
    config_<id>_oauth_client_id/secret for these, so the POST must always supply
    them (current values, write-only secret) or the helper KeyErrors. Returned in
    a stable github-then-google order."""
    rows = (ub.session.query(ub.OAuthProvider)
            .filter(ub.OAuthProvider.provider_name.in_(["github", "google"]))
            .all())
    order = {"github": 0, "google": 1}
    return sorted(rows, key=lambda r: order.get(r.provider_name, 99))


def _security_payload():
    """Current security config for the SPA form. Secrets are reduced to booleans."""
    g = _generic_oauth_row()
    oauth_generic = {
        "client_id": (g.oauth_client_id if g else "") or "",
        "has_secret": bool(g and g.oauth_client_secret),
        "base_url": (g.oauth_base_url if g else "") or "",
        "authorize_url": (g.oauth_authorize_url if g else "") or "",
        "token_url": (g.oauth_token_url if g else "") or "",
        "userinfo_url": (g.oauth_userinfo_url if g else "") or "",
        "admin_group": (g.oauth_admin_group if g else "") or "",
        "metadata_url": (g.metadata_url if g else "") or "",
        "scope": (g.scope if g else "") or "",
        "username_mapper": (g.username_mapper if g else "") or "",
        "email_mapper": (g.email_mapper if g else "") or "",
        "login_button": (g.login_button if g else "") or "",
        "active": bool(g and g.active),
        # Group-based access control (#494/#495).
        "group_claim": (g.oauth_group_claim if g else "") or "groups",
        "require_group": bool(g and g.oauth_require_group),
        "allowed_groups": (g.oauth_allowed_groups if g else "") or "",
        "default_roles": {key: bool((int(g.oauth_default_role) if (g and g.oauth_default_role) else 0) & bit)
                          for key, bit in _OAUTH_DEFAULT_ROLE_BITS.items()},
    }
    return {
        "login_type": config.config_login_type,
        "login_types": _LOGIN_TYPES,
        "ldap_auth_levels": _LDAP_AUTH_LEVELS,
        "ldap_encryption_levels": _LDAP_ENCRYPTION_LEVELS,
        "ldap": {
            "provider_url": config.config_ldap_provider_url or "",
            "port": config.config_ldap_port,
            "encryption": config.config_ldap_encryption,
            "authentication": config.config_ldap_authentication,
            "serv_username": config.config_ldap_serv_username or "",
            "has_password": bool(getattr(config, "config_ldap_serv_password_e", None)),
            "auto_create_users": bool(config.config_ldap_auto_create_users),
            "dn": config.config_ldap_dn or "",
            "user_object": config.config_ldap_user_object or "",
            "member_user_object": config.config_ldap_member_user_object or "",
            "group_object_filter": config.config_ldap_group_object_filter or "",
            "group_members_field": config.config_ldap_group_members_field or "",
            "group_name": config.config_ldap_group_name or "",
            "openldap": bool(config.config_ldap_openldap),
            "cacert_path": config.config_ldap_cacert_path or "",
            "cert_path": config.config_ldap_cert_path or "",
            "key_path": config.config_ldap_key_path or "",
        },
        "oauth": {
            "redirect_host": config.config_oauth_redirect_host or "",
            "disable_standard_login": bool(config.config_disable_standard_login),
            "enable_oauth_auto_forward": bool(getattr(config, "config_enable_oauth_auto_forward", False)),
            "enable_group_admin_management": bool(config.config_enable_oauth_group_admin_management),
            "generic": oauth_generic,
            # Built-in GitHub/Google providers (client id + write-only secret).
            "providers": [{"name": p.provider_name,
                           "client_id": p.oauth_client_id or "",
                           "has_secret": bool(p.oauth_client_secret),
                           "active": bool(p.active)}
                          for p in _builtin_oauth_rows()],
        },
        "ssl": {
            "use_https": bool(config.config_use_https),
            "certfile": config.config_certfile or "",
            "keyfile": config.config_keyfile or "",
        },
        "remote_login": bool(config.config_remote_login),
        "reverse_proxy": {
            "enabled": bool(config.config_allow_reverse_proxy_header_login),
            "header_name": config.config_reverse_proxy_login_header_name or "",
            "auto_create_users": bool(config.config_reverse_proxy_auto_create_users),
        },
    }


@api_v1.route("/admin/security")
@login_required_if_no_ano
def admin_get_security():
    """Read the deep auth/security config (admin only). Secrets are never returned."""
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(_security_payload())


def _helper_error_message(message_response):
    """The legacy LDAP/OAuth helpers return a flask Response built by
    _configuration_result on validation failure. Pull the human message out of it
    so the SPA can show the same text the legacy form would flash."""
    try:
        data = json.loads(message_response.get_data(as_text=True))
        items = data.get("result") or []
        for item in items:
            if item.get("type") == "danger" and item.get("message"):
                return item["message"]
        if items and items[0].get("message"):
            return items[0]["message"]
    except Exception:  # pragma: no cover - defensive
        pass
    return "Invalid security configuration"


@api_v1.route("/admin/security", methods=["POST"])
@login_required_if_no_ano
def admin_update_security():
    """Update the deep auth/security config (admin only).

    Reuses the legacy LDAP/OAuth helpers verbatim for validation + write-only
    secret handling. Returns the refreshed payload plus ``reboot_required`` (the
    SPA shows a restart banner; the server is NOT torn down from here). See the
    module docstring for the security-review gate."""
    guard = _require_admin()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}

    # ``to_save`` is the form-shaped dict the legacy helpers consume. Checkboxes
    # are present-as-"on" / absent-as-off (HTML form semantics), so we only set a
    # checkbox key when the JSON flag is truthy.
    to_save = {}
    reboot_required = False

    def put_bool(form_key, value):
        if value:
            to_save[form_key] = "on"

    # --- login type ---------------------------------------------------------
    # SECURITY/ROBUSTNESS: do NOT commit config_login_type yet. The legacy LDAP
    # helper calls config.save() *before* its own validation, so committing the
    # login type up front and then failing LDAP/OAuth validation would persist a
    # broken auth mode and lock the admin out after the restart this endpoint asks
    # for. We compute the *target* type, validate everything against it, and only
    # commit the login-type switch at the very end once all validation has passed.
    target_lt = config.config_login_type
    if "login_type" in data:
        try:
            target_lt = int(data["login_type"])
        except (TypeError, ValueError):
            return _err("invalid_request", "login_type must be a number", 400)
        if target_lt not in {o["id"] for o in _LOGIN_TYPES}:
            return _err("invalid_request", "Unknown login type", 400)

    # --- LDAP (only when LDAP is the target login type) ---------------------
    if target_lt == constants.LOGIN_LDAP:
        ldap = data.get("ldap") or {}
        for skey, fkey in (("provider_url", "config_ldap_provider_url"),
                           ("serv_username", "config_ldap_serv_username"),
                           ("dn", "config_ldap_dn"),
                           ("user_object", "config_ldap_user_object"),
                           ("member_user_object", "config_ldap_member_user_object"),
                           ("group_object_filter", "config_ldap_group_object_filter"),
                           ("group_members_field", "config_ldap_group_members_field"),
                           ("group_name", "config_ldap_group_name"),
                           ("cacert_path", "config_ldap_cacert_path"),
                           ("cert_path", "config_ldap_cert_path"),
                           ("key_path", "config_ldap_key_path")):
            if skey in ldap:
                to_save[fkey] = str(ldap[skey] or "")
        for skey, fkey in (("port", "config_ldap_port"),
                           ("encryption", "config_ldap_encryption"),
                           ("authentication", "config_ldap_authentication")):
            if skey in ldap:
                try:
                    to_save[fkey] = int(ldap[skey])
                except (TypeError, ValueError):
                    return _err("invalid_request", "%s must be a number" % skey, 400)
        put_bool("config_ldap_openldap", ldap.get("openldap"))
        put_bool("config_ldap_auto_create_users", ldap.get("auto_create_users"))
        # member-user-object filter on/off mirror (legacy "ldap_import_user_filter")
        to_save["ldap_import_user_filter"] = "1" if (ldap.get("member_user_object") or "").strip() else "0"
        # Write-only: only overwrite the bind password when a new one is supplied.
        new_pw = ldap.get("serv_password")
        if new_pw:
            to_save["config_ldap_serv_password_e"] = str(new_pw)

        reboot, message = _configuration_ldap_helper(to_save)
        if message:
            return _err("invalid_request", _helper_error_message(message), 400)
        reboot_required |= reboot

    # --- remote (magic-link) login -----------------------------------------
    if "remote_login" in data:
        put_bool("config_remote_login", data.get("remote_login"))
        _config_checkbox(to_save, "config_remote_login")
        if not config.config_remote_login:
            # Mirror legacy: drop outstanding remote-auth tokens when disabling.
            ub.session.query(ub.RemoteAuthToken).filter(ub.RemoteAuthToken.token_type == 0).delete()

    # --- OAuth (only when OAuth is the active login type) --------------------
    oauth = data.get("oauth") or {}
    if "redirect_host" in oauth:
        to_save["config_oauth_redirect_host"] = str(oauth["redirect_host"] or "")
        _config_string(to_save, "config_oauth_redirect_host")
    if target_lt == constants.LOGIN_OAUTH:
        gen = oauth.get("generic") or {}
        current = _generic_oauth_row()
        # Direct-indexed keys the helper requires (KeyError if absent).
        to_save["config_generic_oauth_client_id"] = str(gen.get("client_id") or "")
        # Write-only secret: send the existing secret when the client didn't type
        # a new one, so the helper sees "unchanged" and preserves it.
        new_secret = gen.get("client_secret")
        to_save["config_generic_oauth_client_secret"] = (
            str(new_secret) if new_secret else ((current.oauth_client_secret if current else "") or "")
        )
        to_save["config_generic_oauth_admin_group"] = str(gen.get("admin_group") or "")
        to_save["config_generic_oauth_server_url"] = str(gen.get("base_url") or "")
        to_save["config_generic_oauth_metadata_url"] = str(gen.get("metadata_url") or "")
        to_save["config_generic_oauth_auth_url"] = str(gen.get("authorize_url") or "")
        to_save["config_generic_oauth_token_url"] = str(gen.get("token_url") or "")
        to_save["config_generic_oauth_userinfo_url"] = str(gen.get("userinfo_url") or "")
        to_save["config_generic_oauth_scope"] = str(gen.get("scope") or "")
        to_save["config_generic_oauth_username_mapper"] = str(gen.get("username_mapper") or "")
        to_save["config_generic_oauth_email_mapper"] = str(gen.get("email_mapper") or "")
        to_save["config_generic_oauth_login_button"] = str(gen.get("login_button") or "")
        # Group-based access control (#494/#495). The helper resets these to
        # defaults when absent, so we MUST always send the current/edited values
        # to avoid silently wiping an admin's group restrictions (require_group is
        # a security control). group_claim/allowed_groups are plain strings;
        # require_group + each default-role are checkbox-presence ("on" = true).
        to_save["config_generic_oauth_group_claim"] = str(gen.get("group_claim") or "groups")
        to_save["config_generic_oauth_allowed_groups"] = str(gen.get("allowed_groups") or "")
        put_bool("config_generic_oauth_require_group", gen.get("require_group"))
        default_roles = gen.get("default_roles") or {}
        for key in _OAUTH_DEFAULT_ROLE_BITS:
            put_bool("config_generic_oauth_default_%s_role" % key, default_roles.get(key))

        # Built-in GitHub/Google providers: the helper iterates these too and
        # direct-indexes config_<id>_oauth_client_id/secret, so always supply them.
        # Write-only secret: keep the current one unless a new value is sent.
        providers_in = {p.get("name"): p for p in (oauth.get("providers") or []) if isinstance(p, dict)}
        for row in _builtin_oauth_rows():
            pin = providers_in.get(row.provider_name, {})
            cid = pin.get("client_id")
            to_save["config_%d_oauth_client_id" % row.id] = str(cid if cid is not None else (row.oauth_client_id or ""))
            new_sec = pin.get("client_secret")
            to_save["config_%d_oauth_client_secret" % row.id] = (
                str(new_sec) if new_sec else (row.oauth_client_secret or ""))

        reboot, message = _configuration_oauth_helper(to_save)
        if message:
            return _err("invalid_request", _helper_error_message(message), 400)
        reboot_required |= reboot

    # --- security checkboxes (always applicable) ----------------------------
    if "oauth" in data:
        put_bool("config_disable_standard_login", oauth.get("disable_standard_login"))
        put_bool("config_enable_oauth_auto_forward", oauth.get("enable_oauth_auto_forward"))
        put_bool("config_enable_oauth_group_admin_management", oauth.get("enable_group_admin_management"))
        _config_checkbox(to_save, "config_disable_standard_login")
        _config_checkbox(to_save, "config_enable_oauth_auto_forward")
        _config_checkbox(to_save, "config_enable_oauth_group_admin_management")

    # --- SSL / HTTPS --------------------------------------------------------
    https_changed = False
    if "ssl" in data:
        ssl = data.get("ssl") or {}
        if "certfile" in ssl:
            to_save["config_certfile"] = str(ssl["certfile"] or "")
            _config_string(to_save, "config_certfile")
        if "keyfile" in ssl:
            to_save["config_keyfile"] = str(ssl["keyfile"] or "")
            _config_string(to_save, "config_keyfile")
        put_bool("config_use_https", ssl.get("use_https"))
        https_changed = _config_checkbox(to_save, "config_use_https")

    # --- reverse-proxy header login ----------------------------------------
    if "reverse_proxy" in data:
        rp = data.get("reverse_proxy") or {}
        # Validate the *effective* values (incoming, falling back to current) BEFORE
        # mutating config, so an invalid combination returns 400 without leaving the
        # running process in a divergent half-applied state.
        eff_enabled = bool(rp["enabled"]) if "enabled" in rp else config.config_allow_reverse_proxy_header_login
        eff_auto = bool(rp["auto_create_users"]) if "auto_create_users" in rp else config.config_reverse_proxy_auto_create_users
        eff_header = (str(rp["header_name"]) if "header_name" in rp
                      else (config.config_reverse_proxy_login_header_name or "")).strip()
        if eff_auto and not eff_enabled:
            return _err("invalid_request",
                        "Auto-creating reverse-proxy users requires reverse-proxy header login to be enabled", 400)
        if eff_auto and not eff_header:
            return _err("invalid_request",
                        "Auto-creating reverse-proxy users requires a header name", 400)
        put_bool("config_allow_reverse_proxy_header_login", rp.get("enabled"))
        if "header_name" in rp:
            to_save["config_reverse_proxy_login_header_name"] = str(rp["header_name"] or "")
            _config_string(to_save, "config_reverse_proxy_login_header_name")
        put_bool("config_reverse_proxy_auto_create_users", rp.get("auto_create_users"))
        _config_checkbox(to_save, "config_allow_reverse_proxy_header_login")
        _config_checkbox(to_save, "config_reverse_proxy_auto_create_users")

    # --- commit the login-type switch LAST, now that all validation passed --
    if "login_type" in data:
        to_save["config_login_type"] = target_lt
        reboot_required |= _config_int(to_save, "config_login_type")

    try:
        config.save()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not save security configuration: %s" % ex, 500)

    if https_changed:
        try:
            apply_https_runtime_config()
        except Exception as ex:  # pragma: no cover - runtime cert apply is environment-specific
            log.warning("apply_https_runtime_config failed after SPA security update: %s", ex)

    payload = _security_payload()
    payload["reboot_required"] = bool(reboot_required)
    return jsonify(payload)
