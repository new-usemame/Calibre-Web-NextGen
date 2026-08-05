# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

LOCAL_LOGIN_PARAMETER = "local"
LOCAL_LOGIN_VALUE = "1"
AUTO_REDIRECT_GUARD_KEY = "_oauth_auto_redirect_pending"

_PROVIDER_ENDPOINTS = {
    "github": "github.login",
    "google": "google.login",
    "generic": "generic.login",
}


def local_login_requested(query_args):
    """Return whether the request explicitly asks for the local login form."""
    return query_args.get(LOCAL_LOGIN_PARAMETER) == LOCAL_LOGIN_VALUE


def clear_auto_redirect_guard(session_store):
    """Clear the one-shot redirect guard from the browser session."""
    session_store.pop(AUTO_REDIRECT_GUARD_KEY, None)


def single_active_oauth_endpoint(oauth_blueprints):
    """Return the login endpoint when exactly one known provider is active."""
    active_providers = [
        blueprint
        for blueprint in (oauth_blueprints or ())
        if blueprint.get("active")
    ]
    if len(active_providers) != 1:
        return None

    provider_name = active_providers[0].get("provider_name")
    return _PROVIDER_ENDPOINTS.get(provider_name)


def auto_redirect_decision(query_args, oauth_blueprints, session_store):
    """Return ``(endpoint, render_local)`` for an OAuth-only login request.

    ``?local=1`` always renders the local login form. A one-shot session guard
    is consumed when OAuth returns to ``/login`` after an error or cancellation,
    so the browser sees the login page instead of immediately restarting OAuth.
    """
    if local_login_requested(query_args):
        clear_auto_redirect_guard(session_store)
        return None, True

    endpoint = single_active_oauth_endpoint(oauth_blueprints)
    if endpoint is None:
        clear_auto_redirect_guard(session_store)
        return None, False

    if session_store.pop(AUTO_REDIRECT_GUARD_KEY, False):
        return None, True

    session_store[AUTO_REDIRECT_GUARD_KEY] = True
    return endpoint, False
