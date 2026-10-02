# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Declarative environment configuration for the Generic OIDC provider.

The environment owns the *whole* Generic provider as soon as any
``GENERIC_OAUTH_*`` variable is present. This prevents stale database values
from silently filling holes in deployment configuration. Other OAuth providers
remain database-managed.
"""

import logging
import os
from urllib.parse import urlsplit

import requests

from . import constants
from .config_sql import _read_secret_file

log = logging.getLogger(__name__)
ENVIRONMENT_EXTENSION = "cps_generic_oauth_environment"

DEFAULT_SCOPE = "openid profile email"
DEFAULT_ROLE_BITS = {
    "download": constants.ROLE_DOWNLOAD,
    "viewer": constants.ROLE_VIEWER,
    "upload": constants.ROLE_UPLOAD,
    "edit": constants.ROLE_EDIT,
    "passwd": constants.ROLE_PASSWD,
    "delete_books": constants.ROLE_DELETE_BOOKS,
    "edit_shelves": constants.ROLE_EDIT_SHELFS,
}
ENVIRONMENT_FIELDS = frozenset({
    "ENABLED", "CLIENT_ID", "CLIENT_SECRET", "CLIENT_SECRET_FILE",
    "METADATA_URL", "SERVER_URL", "AUTH_URL", "TOKEN_URL", "USERINFO_URL",
    "SCOPE", "USERNAME_MAPPER", "EMAIL_MAPPER", "LOGIN_BUTTON", "GROUP_CLAIM",
    "REQUIRE_GROUP", "ALLOWED_GROUPS", "ADMIN_GROUP", "DEFAULT_ROLE",
})


def environment_managed(environ=None):
    """Whether any deployment declaration claims the Generic OIDC provider."""
    environ = os.environ if environ is None else environ
    return any(name.startswith("GENERIC_OAUTH_") for name in environ)


def _env_bool(environ, name, default=False):
    value = environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError("{} must be true or false".format(name))


def _clean(environ, name, default=""):
    value = environ.get(name)
    return value.strip() if isinstance(value, str) else default


def _is_http_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


def _metadata(url):
    """Read operator-selected OIDC metadata without logging its URL or body."""
    try:
        response = requests.get(url, timeout=5, verify=constants.OAUTH_SSL_STRICT)
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _role_mask(raw):
    """Resolve the same non-admin permission bits offered by the classic UI."""
    selected = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(selected) - set(DEFAULT_ROLE_BITS))
    if unknown:
        raise ValueError("GENERIC_OAUTH_DEFAULT_ROLE contains an unsupported role")
    role = 0
    for name in selected:
        role |= DEFAULT_ROLE_BITS[name]
    return role


def resolve_environment_provider(environ=None, metadata_loader=None):
    """Return a safe, normalized Generic provider config for this process.

    The result never contains the environment mapping or a raw environment
    value in its error field. The client secret is included only in the
    in-process provider settings needed to construct Flask-Dance.
    """
    environ = os.environ if environ is None else environ
    metadata_loader = _metadata if metadata_loader is None else metadata_loader
    managed = environment_managed(environ)
    result = {"managed": managed, "active": False, "settings": None, "error": None}
    if not managed:
        return result

    try:
        unknown = sorted(
            name for name in environ
            if name.startswith("GENERIC_OAUTH_")
            and name.removeprefix("GENERIC_OAUTH_") not in ENVIRONMENT_FIELDS
        )
        if unknown:
            raise ValueError("unknown Generic OAuth environment variable name")
        enabled = _env_bool(environ, "GENERIC_OAUTH_ENABLED", default=False)
        if not enabled:
            return result

        client_id = _clean(environ, "GENERIC_OAUTH_CLIENT_ID")
        client_secret = _clean(environ, "GENERIC_OAUTH_CLIENT_SECRET")
        if not client_secret:
            client_secret = _read_secret_file(environ.get("GENERIC_OAUTH_CLIENT_SECRET_FILE"))
            client_secret = client_secret.strip() if isinstance(client_secret, str) else ""
        if not client_id or not client_secret:
            raise ValueError("client id and client secret are required")

        metadata_url = _clean(environ, "GENERIC_OAUTH_METADATA_URL")
        if metadata_url:
            if not _is_http_url(metadata_url):
                raise ValueError("GENERIC_OAUTH_METADATA_URL must be an absolute HTTP(S) URL")
            metadata = metadata_loader(metadata_url)
            if not metadata:
                raise ValueError("OIDC metadata could not be loaded")
            endpoints = {
                "oauth_base_url": metadata.get("issuer", ""),
                "oauth_authorize_url": metadata.get("authorization_endpoint", ""),
                "oauth_token_url": metadata.get("token_endpoint", ""),
                "oauth_userinfo_url": metadata.get("userinfo_endpoint", ""),
            }
        else:
            endpoints = {
                "oauth_base_url": _clean(environ, "GENERIC_OAUTH_SERVER_URL"),
                "oauth_authorize_url": _clean(environ, "GENERIC_OAUTH_AUTH_URL"),
                "oauth_token_url": _clean(environ, "GENERIC_OAUTH_TOKEN_URL"),
                "oauth_userinfo_url": _clean(environ, "GENERIC_OAUTH_USERINFO_URL"),
            }

        if any(not isinstance(value, str) or not value.strip() for value in endpoints.values()):
            raise ValueError("issuer and authorization, token, and userinfo endpoints are required")
        endpoints = {name: value.strip() for name, value in endpoints.items()}
        if any(not _is_http_url(value) for value in endpoints.values()):
            raise ValueError("issuer and endpoint values must be absolute HTTP(S) URLs")

        scope = _clean(environ, "GENERIC_OAUTH_SCOPE", DEFAULT_SCOPE) or DEFAULT_SCOPE
        scope = " ".join(sorted(scope.split())) or DEFAULT_SCOPE
        settings = {
            **endpoints,
            "oauth_client_id": client_id,
            "oauth_client_secret": client_secret,
            "metadata_url": metadata_url,
            "scope": scope,
            "username_mapper": _clean(environ, "GENERIC_OAUTH_USERNAME_MAPPER", "preferred_username") or "preferred_username",
            "email_mapper": _clean(environ, "GENERIC_OAUTH_EMAIL_MAPPER", "email") or "email",
            "login_button": _clean(environ, "GENERIC_OAUTH_LOGIN_BUTTON", "OpenID Connect") or "OpenID Connect",
            "oauth_group_claim": _clean(environ, "GENERIC_OAUTH_GROUP_CLAIM", "groups") or "groups",
            "oauth_require_group": _env_bool(environ, "GENERIC_OAUTH_REQUIRE_GROUP", default=False),
            "oauth_allowed_groups": _clean(environ, "GENERIC_OAUTH_ALLOWED_GROUPS"),
            "oauth_admin_group": _clean(environ, "GENERIC_OAUTH_ADMIN_GROUP", "admin"),
            # None preserves the existing fallback to the global new-user role.
            "oauth_default_role": (
                _role_mask(environ["GENERIC_OAUTH_DEFAULT_ROLE"])
                if "GENERIC_OAUTH_DEFAULT_ROLE" in environ else None
            ),
        }
        result.update(active=True, settings=settings)
    except ValueError as ex:
        # Error strings name the invalid field/reason only; never include the
        # submitted values, metadata URL, or client secret.
        result["error"] = str(ex)
        log.warning("Generic OAuth environment configuration is inactive: %s", ex)
    return result


def prepare_application(application, runtime_config):
    """Resolve once at app startup and select OAuth in memory when usable."""
    from . import constants

    resolved = resolve_environment_provider()
    application.extensions[ENVIRONMENT_EXTENSION] = resolved
    # The configured login type is normally persisted by the admin page. An
    # environment-owned provider enables OAuth without changing app.db, and
    # must remain selected when ConfigSQL reloads after unrelated admin saves.
    # ConfigSQL.load() reapplies this private startup override after reading
    # stored values; it never resolves the environment or secret file again.
    runtime_config.__dict__["_runtime_login_type_override"] = (
        constants.LOGIN_OAUTH if resolved["managed"] and resolved["active"] else None
    )
    if runtime_config.__dict__["_runtime_login_type_override"] is not None:
        # Bypass ConfigSQL.__setattr__ deliberately: it marks fields dirty for
        # a later save().
        runtime_config.__dict__["config_login_type"] = constants.LOGIN_OAUTH
    return resolved
