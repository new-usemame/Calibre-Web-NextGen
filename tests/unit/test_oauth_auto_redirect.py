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
    return {"provider_name": name, "active": active}


@pytest.mark.parametrize(
    ("provider_name", "expected_endpoint"),
    (
        ("github", "github.login"),
        ("google", "google.login"),
        ("generic", "generic.login"),
    ),
)
def test_single_active_provider_starts_and_counts_redirect(
    provider_name,
    expected_endpoint,
):
    session_store = {}

    endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
        {},
        [_provider(provider_name)],
        session_store,
    )

    assert endpoint == expected_endpoint
    assert next_url is None
    assert session_store[oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY] == 1


def test_local_parameter_suppresses_start_without_consuming_other_tab():
    session_store = {
        oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY: 2,
        oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY: {
            "state-a": {"provider": "generic", "next": "/book/7"},
        },
    }

    endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
        {"local": "1"},
        [_provider("generic")],
        session_store,
    )

    assert endpoint is None
    assert next_url is None
    assert session_store[oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY] == 2
    assert "state-a" in session_store[oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY]


@pytest.mark.parametrize("local_value", ("", "0", "true", "yes"))
def test_only_exact_local_value_suppresses_start(local_value):
    endpoint, _ = oauth_auto_redirect.auto_redirect_decision(
        {"local": local_value},
        [_provider("generic")],
        {},
    )

    assert endpoint == "generic.login"


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
    session_store = {}

    endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
        {},
        providers,
        session_store,
    )

    assert endpoint is None
    assert next_url is None
    assert session_store == {}


def test_existing_redirect_counter_stops_auto_start_after_limit():
    session_store = {oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY: 4}

    endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
        {},
        [_provider("generic")],
        session_store,
    )

    assert endpoint is None
    assert next_url is None
    assert session_store[oauth_auto_redirect.LOGIN_REDIRECT_COUNT_KEY] == 4


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        ("/book/7", "/book/7"),
        ("/book/7?format=epub#download", "/book/7?format=epub#download"),
        ("", None),
        ("book/7", None),
        ("//evil.example/path", None),
        ("https://evil.example/path", None),
        ("/\\evil.example/path", None),
        ("/book/7\r\nX-Test: injected", None),
        ("/%5cevil.example/path", None),
        ("/%2f%2fevil.example/path", None),
        ("/" + "x" * 512, None),
    ),
)
def test_next_target_is_kept_only_when_relative(target, expected):
    endpoint, next_url = oauth_auto_redirect.auto_redirect_decision(
        {"next": target},
        [_provider("generic")],
        {},
    )

    assert endpoint == "generic.login"
    assert next_url == expected


def test_oauth_state_is_provider_scoped_and_attempt_scoped():
    session_store = {}
    assert oauth_auto_redirect.remember_oauth_state(
        session_store,
        "generic",
        "state-a",
        "/book/1",
    )
    assert oauth_auto_redirect.remember_oauth_state(
        session_store,
        "generic",
        "state-b",
        "/book/2",
    )

    assert not oauth_auto_redirect.restore_provider_oauth_state(
        session_store,
        "google",
        "state-a",
    )
    assert oauth_auto_redirect.restore_provider_oauth_state(
        session_store,
        "generic",
        "state-a",
    )
    assert session_store["generic_oauth_state"] == "state-a"

    assert oauth_auto_redirect.consume_oauth_next(
        session_store,
        "generic",
        "state-a",
    ) == "/book/1"
    assert "state-b" in session_store[oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY]


def test_clear_removes_states_and_legacy_boolean_guard():
    session_store = {
        oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY: {
            "state-a": {"provider": "generic", "next": None},
        },
        "_oauth_auto_redirect_pending": True,
        "unrelated": "keep",
    }

    oauth_auto_redirect.clear_auto_redirect_state(session_store)

    assert session_store == {"unrelated": "keep"}
