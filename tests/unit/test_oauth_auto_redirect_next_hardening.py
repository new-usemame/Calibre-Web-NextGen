# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Pins on the ``next`` validator and logout invalidation for OAuth auto-start.

The control-character cases here are not theoretical. ``/<TAB>//evil.example``
was accepted by the first revision of ``validated_relative_next`` and was
observed end-to-end in Chrome: a 302 carrying that exact ``Location`` moved the
browser from ``http://localhost:9001/`` to ``http://localhost:9002/pwned``.
``urlsplit`` strips the tab and reports an empty netloc, so the value looks
app-relative to Python, while the WHATWG parser a browser applies to a
``Location`` header strips the same tab and resolves ``///evil.example`` to a
different origin.
"""

import importlib.util
import os

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "cps",
    "oauth_auto_redirect.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "oauth_auto_redirect_under_test", _MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oauth_auto_redirect = _load_module()

TAB = chr(0x09)
DEL = chr(0x7F)


# Each of these is app-relative to a naive reading but reaches a foreign origin
# once a browser applies WHATWG parsing to the Location header, or smuggles a
# control character into a response header.
CONTROL_CHARACTER_TARGETS = [
    pytest.param("/" + TAB + "//evil.example", id="tab-then-double-slash"),
    pytest.param("/" + TAB + "/evil.example", id="tab-then-single-slash"),
    pytest.param("/" + chr(0x00) + "//evil.example", id="nul"),
    pytest.param("/" + chr(0x0B) + "//evil.example", id="vertical-tab"),
    pytest.param("/" + chr(0x0C) + "//evil.example", id="form-feed"),
    pytest.param("/" + chr(0x1F) + "//evil.example", id="unit-separator"),
    pytest.param("/" + DEL + "//evil.example", id="delete"),
    pytest.param("/" + chr(0x0D) + chr(0x0A) + "//evil.example", id="crlf"),
]


@pytest.mark.parametrize("target", CONTROL_CHARACTER_TARGETS)
def test_control_characters_are_rejected(target):
    assert oauth_auto_redirect.validated_relative_next({"next": target}) is None


@pytest.mark.parametrize("target", CONTROL_CHARACTER_TARGETS)
def test_control_characters_are_rejected_when_percent_encoded(target):
    from urllib.parse import quote

    encoded = "/" + quote(target[1:], safe="/")
    assert oauth_auto_redirect.validated_relative_next({"next": encoded}) is None


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example",
        "https://evil.example",
        "/\\evil.example",
        "/%2F%2Fevil.example",
        "/%5Cevil.example",
        "http://evil.example/path",
    ],
)
def test_absolute_and_backslash_targets_stay_rejected(target):
    assert oauth_auto_redirect.validated_relative_next({"next": target}) is None


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/shelf/3",
        "/book/12?utm=x",
        "/search?query=hello%20world",
        "/admin/view#anchor",
    ],
)
def test_ordinary_relative_targets_still_pass(target):
    assert oauth_auto_redirect.validated_relative_next({"next": target}) == target


def test_next_budget_is_measured_in_utf8_bytes_not_characters():
    """A 511-character CJK path is ~1533 bytes and must not reach the cookie."""
    multibyte = "/" + ("中" * 511)
    assert len(multibyte) <= 512
    assert len(multibyte.encode("utf-8")) > 512
    assert oauth_auto_redirect.validated_relative_next({"next": multibyte}) is None

    # A target at the byte budget is still accepted.
    at_budget = "/" + ("a" * 511)
    assert len(at_budget.encode("utf-8")) == 512
    assert oauth_auto_redirect.validated_relative_next({"next": at_budget}) == at_budget


def test_logout_clears_every_provider_oauth_state():
    """Logout must invalidate the transaction, not just our bookkeeping.

    Otherwise a flow started before logout still validates its state on return
    and logs the browser straight back in.
    """
    session = {
        "github_oauth_state": "A",
        "google_oauth_state": "B",
        "generic_oauth_state": "C",
        oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY: {"A": {"provider": "github"}},
        "unrelated": "keep-me",
    }

    oauth_auto_redirect.clear_auto_redirect_state(session)
    oauth_auto_redirect.clear_provider_oauth_states(session)

    assert "github_oauth_state" not in session
    assert "google_oauth_state" not in session
    assert "generic_oauth_state" not in session
    assert oauth_auto_redirect.AUTO_REDIRECT_STATES_KEY not in session
    assert session["unrelated"] == "keep-me"


def test_logout_state_clearing_covers_every_supported_provider():
    """Pin the cleanup to the provider list so a new provider can't be missed."""
    session = {
        f"{name}_oauth_state": "x"
        for name in oauth_auto_redirect._PROVIDER_ENDPOINTS
    }
    oauth_auto_redirect.clear_provider_oauth_states(session)
    assert session == {}


def test_shared_logout_helper_invokes_provider_state_cleanup():
    """cps/logout.py must call the helper, not just define it."""
    import ast

    logout_path = os.path.join(os.path.dirname(_MODULE_PATH), "logout.py")
    with open(logout_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "clear_provider_oauth_states" in called
    assert "clear_auto_redirect_state" in called
