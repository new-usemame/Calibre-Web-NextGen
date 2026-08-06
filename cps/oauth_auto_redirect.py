# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from urllib.parse import unquote, urlsplit


LOCAL_LOGIN_PARAMETER = "local"
LOCAL_LOGIN_VALUE = "1"
AUTO_REDIRECT_PARAMETER = "_oauth_auto"
AUTO_REDIRECT_VALUE = "1"
AUTO_REDIRECT_STATES_KEY = "_oauth_auto_redirect_states"
LOGIN_REDIRECT_COUNT_KEY = "_login_redirect_count"
MAX_LOGIN_REDIRECTS = 3
_MAX_STATES = 4
_MAX_NEXT_LENGTH = 512

# Clear sessions created by the first revision of this feature too.
_LEGACY_GUARD_KEY = "_oauth_auto_redirect_pending"

_PROVIDER_ENDPOINTS = {
    "github": "github.login",
    "google": "google.login",
    "generic": "generic.login",
}


def local_login_requested(query_args):
    """Return whether this request suppresses automatic provider startup."""
    return query_args.get(LOCAL_LOGIN_PARAMETER) == LOCAL_LOGIN_VALUE


def validated_relative_next(query_args):
    """Return a safe app-relative ``next`` target, or ``None``."""
    target = query_args.get("next")
    if not isinstance(target, str) or not target or len(target) > _MAX_NEXT_LENGTH:
        return None
    if (
        not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or "\r" in target
        or "\n" in target
    ):
        return None

    parsed = urlsplit(target)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or decoded_path.startswith("//")
        or "\\" in decoded_path
        or "\r" in decoded_path
        or "\n" in decoded_path
    ):
        return None
    return target


def clear_auto_redirect_state(session_store):
    """Clear all automatic OAuth state from the browser session."""
    session_store.pop(AUTO_REDIRECT_STATES_KEY, None)
    session_store.pop(_LEGACY_GUARD_KEY, None)


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
    """Return ``(endpoint, next_url)`` for an OAuth-only login request.

    ``?local=1`` suppresses automatic startup and falls through to the normal
    Classic-or-SPA routing. Automatic starts participate in the existing login
    redirect counter so a surviving session cannot loop without bound.
    """
    if local_login_requested(query_args):
        return None, None

    endpoint = single_active_oauth_endpoint(oauth_blueprints)
    if endpoint is None:
        return None, None

    try:
        redirect_count = int(session_store.get(LOGIN_REDIRECT_COUNT_KEY, 0))
    except (TypeError, ValueError):
        redirect_count = 0

    if redirect_count > MAX_LOGIN_REDIRECTS:
        return None, None

    session_store[LOGIN_REDIRECT_COUNT_KEY] = redirect_count + 1
    return endpoint, validated_relative_next(query_args)


def _states(session_store):
    stored = session_store.get(AUTO_REDIRECT_STATES_KEY, {})
    return dict(stored) if isinstance(stored, dict) else {}


def _save_states(session_store, states):
    while len(states) > _MAX_STATES:
        states.pop(next(iter(states)))
    if states:
        # Reassign so Flask marks its cookie-backed session as modified.
        session_store[AUTO_REDIRECT_STATES_KEY] = states
    else:
        session_store.pop(AUTO_REDIRECT_STATES_KEY, None)


def remember_oauth_state(session_store, provider_name, oauth_state, next_url=None):
    """Remember one automatically started OAuth attempt by provider state."""
    if not provider_name or not oauth_state:
        return False

    states = _states(session_store)
    states[oauth_state] = {
        "provider": provider_name,
        "next": next_url,
    }
    _save_states(session_store, states)
    return True


def restore_provider_oauth_state(session_store, provider_name, oauth_state):
    """Restore Flask-Dance state for the matching provider and attempt."""
    state = _states(session_store).get(oauth_state)
    if not isinstance(state, dict) or state.get("provider") != provider_name:
        return False
    session_store[f"{provider_name}_oauth_state"] = oauth_state
    return True


def consume_oauth_next(session_store, provider_name, oauth_state):
    """Consume one matching attempt and return its saved relative target."""
    if not oauth_state:
        return None

    states = _states(session_store)
    state = states.get(oauth_state)
    if not isinstance(state, dict) or state.get("provider") != provider_name:
        return None

    states.pop(oauth_state)
    _save_states(session_store, states)
    return state.get("next")
