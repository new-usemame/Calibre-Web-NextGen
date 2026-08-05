# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[2] / "cps" / "oauth_auto_redirect.py"
_SPEC = importlib.util.spec_from_file_location("oauth_auto_redirect", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
oauth_auto_redirect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oauth_auto_redirect)

pytestmark = pytest.mark.unit


def _provider(name, active=True):
    return {
        "provider_name": name,
        "active": active,
    }


@pytest.mark.parametrize(
    ("provider_name", "expected_endpoint"),
    (
        ("github", "github.login"),
        ("google", "google.login"),
        ("generic", "generic.login"),
    ),
)
def test_single_active_provider_is_selected(provider_name, expected_endpoint):
    session_store = {}

    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {},
        [_provider(provider_name)],
        session_store,
    )

    assert endpoint == expected_endpoint
    assert render_local is False
    assert session_store[
        oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY
    ] is True


def test_local_login_parameter_bypasses_oauth_and_clears_guard():
    session_store = {
        oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY: True,
    }

    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {"local": "1"},
        [_provider("generic")],
        session_store,
    )

    assert endpoint is None
    assert render_local is True
    assert oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY not in session_store


@pytest.mark.parametrize("local_value", ("", "0", "true", "yes"))
def test_only_exact_local_value_bypasses_oauth(local_value):
    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {"local": local_value},
        [_provider("generic")],
        {},
    )

    assert endpoint == "generic.login"
    assert render_local is False


@pytest.mark.parametrize(
    "providers",
    (
        [],
        [_provider("generic", active=False)],
        [_provider("generic"), _provider("github")],
        [_provider("unknown")],
    ),
)
def test_redirect_requires_exactly_one_known_active_provider(providers):
    session_store = {
        oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY: True,
    }

    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {},
        providers,
        session_store,
    )

    assert endpoint is None
    assert render_local is False
    assert oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY not in session_store


def test_pending_guard_renders_login_once_then_allows_new_redirect():
    session_store = {
        oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY: True,
    }

    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {},
        [_provider("generic")],
        session_store,
    )

    assert endpoint is None
    assert render_local is True
    assert oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY not in session_store

    endpoint, render_local = oauth_auto_redirect.auto_redirect_decision(
        {},
        [_provider("generic")],
        session_store,
    )

    assert endpoint == "generic.login"
    assert render_local is False
    assert session_store[
        oauth_auto_redirect.AUTO_REDIRECT_GUARD_KEY
    ] is True
