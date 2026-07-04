# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #585 v2 — the in-SPA sidebar Customize surface.

v1 (#624) made the SPA *honour* the classic ``sidebar_view`` visibility config
(read-only). v2 lets the user set visibility AND reorder entries from the new
UI itself (@alva-seal: "we can't set these in the new UI yet"; "move entries up
or down").

Guards:
  * ``serialize_user`` now also emits ``sidebar_order`` (the saved per-user
    order, from ``view_settings['sidebar']['order']``).
  * ``POST /api/v1/account/sidebar`` writes visibility (flips ``sidebar_view``
    bits) + order (``set_view_property``), validating every key against the
    known set and rejecting unknown keys / non-list order / dupes.
  * Source-pins — the SPA modal + Sidebar render the order and the a11y
    reorder controls, so the wiring can't be silently removed.
"""
import inspect
import json
import pathlib

import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def _ctx(body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": "POST"}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context("/api/v1/account/sidebar", **kwargs)


# ── serializer: sidebar_order ────────────────────────────────────────────────

@pytest.mark.unit
def test_serialize_user_exposes_sidebar_order():
    from cps.api.serializers import serialize_user
    from cps import ub, constants

    u = ub.User()
    u.id, u.name, u.locale, u.theme = 1, "reader", "en", 1
    u.role = constants.ROLE_USER
    u.sidebar_view = constants.ADMIN_USER_SIDEBAR
    u.view_settings = {"sidebar": {"order": ["shelves", "hot", "author"]}}

    out = serialize_user(u)
    assert out.get("sidebar_order") == ["shelves", "hot", "author"]


@pytest.mark.unit
def test_serialize_user_sidebar_order_defaults_empty():
    from cps.api.serializers import serialize_user
    from cps import ub, constants

    u = ub.User()
    u.id, u.name, u.locale, u.theme = 2, "x", "en", 1
    u.role = constants.ROLE_USER
    u.sidebar_view = constants.ADMIN_USER_SIDEBAR
    u.view_settings = {}
    assert serialize_user(u)["sidebar_order"] == []


# ── endpoint: write visibility (bit flips) ───────────────────────────────────

class _FakeUser:
    """Minimal current_user with a real bitmask + view_settings store."""
    def __init__(self, sidebar_view=0, view_settings=None):
        self.is_authenticated = True
        self.is_anonymous = False
        self.id = 1
        self.name = "maggie"
        self.sidebar_view = sidebar_view
        self.view_settings = view_settings if view_settings is not None else {}

    # mirror ub.User.get/set_view_property semantics
    def get_view_property(self, page, prop):
        if not self.view_settings.get(page):
            return None
        return self.view_settings[page].get(prop)

    def set_view_property(self, page, prop, value):
        if not self.view_settings.get(page):
            self.view_settings[page] = dict()
        self.view_settings[page][prop] = value


def _call(body, user):
    from cps.api import account as mod
    with _ctx(body=body):
        with patch.object(mod, "current_user", user), \
             patch.object(mod.ub, "session", SimpleNamespace(commit=lambda: None,
                                                             rollback=lambda: None)):
            return inspect.unwrap(mod.update_sidebar)()


@pytest.mark.unit
def test_sidebar_visibility_enables_bit():
    from cps import constants
    u = _FakeUser(sidebar_view=0)
    resp = _call({"visibility": {"hot": True}}, u)
    status = resp[1] if isinstance(resp, tuple) else 200
    assert status == 200
    assert u.sidebar_view & constants.SIDEBAR_HOT


@pytest.mark.unit
def test_sidebar_visibility_disables_bit():
    from cps import constants
    u = _FakeUser(sidebar_view=constants.SIDEBAR_HOT | constants.SIDEBAR_AUTHOR)
    _call({"visibility": {"hot": False}}, u)
    assert not (u.sidebar_view & constants.SIDEBAR_HOT)
    # untouched bit preserved
    assert u.sidebar_view & constants.SIDEBAR_AUTHOR


@pytest.mark.unit
def test_sidebar_visibility_unknown_key_400():
    u = _FakeUser()
    resp = _call({"visibility": {"not_a_key": True}}, u)
    assert isinstance(resp, tuple) and resp[1] == 400
    assert json.loads(resp[0].get_data())["error"]["code"] == "invalid_request"


# ── endpoint: write order ────────────────────────────────────────────────────

@pytest.mark.unit
def test_sidebar_order_persists():
    u = _FakeUser()
    _call({"order": ["shelves", "hot", "author"]}, u)
    assert u.get_view_property("sidebar", "order") == ["shelves", "hot", "author"]


@pytest.mark.unit
def test_sidebar_order_rejects_unknown_key_400():
    u = _FakeUser()
    resp = _call({"order": ["shelves", "bogus"]}, u)
    assert isinstance(resp, tuple) and resp[1] == 400


@pytest.mark.unit
def test_sidebar_order_rejects_duplicates_400():
    u = _FakeUser()
    resp = _call({"order": ["hot", "hot"]}, u)
    assert isinstance(resp, tuple) and resp[1] == 400


@pytest.mark.unit
def test_sidebar_order_rejects_non_list_400():
    u = _FakeUser()
    resp = _call({"order": "hot,author"}, u)
    assert isinstance(resp, tuple) and resp[1] == 400


@pytest.mark.unit
def test_sidebar_anonymous_401():
    from cps.api import account as mod
    with _ctx(body={"order": []}):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(mod.update_sidebar)()
    assert resp[1] == 401


# ── orderable-key set is the SPA's customizable entries ──────────────────────

@pytest.mark.unit
def test_orderable_keys_match_spa_entries():
    from cps.api.serializers import ORDERABLE_SIDEBAR_KEYS
    # browse-by + discovery + the shelves block token
    expected = {"author", "series", "category", "publisher", "language",
                "rating", "format", "favorites", "hot", "random", "best_rated",
                "archived", "shelves"}
    assert set(ORDERABLE_SIDEBAR_KEYS) == expected


# ── source-pins: SPA wiring ──────────────────────────────────────────────────

@pytest.mark.unit
def test_sidebar_component_applies_saved_order():
    src = (_FE / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert "sidebar_order" in src or "sidebarOrder" in src, \
        "Sidebar.tsx must consume the saved order"


@pytest.mark.unit
def test_customize_modal_has_keyboard_reorder():
    src = (_FE / "components" / "SidebarCustomize.tsx").read_text(encoding="utf-8")
    # keyboard move (ArrowUp/ArrowDown) + live announce — a11y reorder, not drag-only
    assert "ArrowUp" in src and "ArrowDown" in src, \
        "reorder must be keyboard-operable (arrow keys)"
    assert "aria-live" in src or "ariaLive" in src or "announce" in src.lower(), \
        "position changes must be announced for screen readers"


@pytest.mark.unit
def test_customize_modal_posts_to_sidebar_endpoint():
    modal = (_FE / "components" / "SidebarCustomize.tsx").read_text(encoding="utf-8")
    assert "useUpdateSidebar" in modal, "modal must persist via the useUpdateSidebar mutation"
    # the mutation hits POST /account/sidebar (the endpoint path lives in the query layer)
    queries = (_FE / "lib" / "queries.ts").read_text(encoding="utf-8")
    assert "/account/sidebar" in queries, "useUpdateSidebar must POST /api/v1/account/sidebar"
