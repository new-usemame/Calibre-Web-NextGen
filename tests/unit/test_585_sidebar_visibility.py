# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #585 — the new-UI (SPA) sidebar ignored the
per-user / per-instance sidebar-visibility configuration that the classic UI
has always honoured (the ``sidebar_view`` bitmask + ``check_visibility``).

Two guards:
  * Behavioural — ``serialize_user`` now emits a ``sidebar`` map of
    visibility keys → booleans, computed from the user's ``check_visibility``,
    so the SPA can hide entries the admin/user disabled.
  * Source-pin — ``Sidebar.tsx`` reads that map and filters its nav entries,
    so the wiring can't be silently removed.
"""
import pathlib

import pytest

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.unit
def test_serialize_user_exposes_sidebar_visibility():
    from cps.api.serializers import serialize_user
    from cps import ub, constants

    u = ub.User()
    u.id, u.name, u.locale, u.theme = 1, "reader", "en", 1
    u.role = constants.ROLE_USER
    # Enable only Authors + Hot; everything else off.
    u.sidebar_view = constants.SIDEBAR_AUTHOR | constants.SIDEBAR_HOT

    out = serialize_user(u)
    assert "sidebar" in out, "serialize_user must expose a sidebar visibility map"
    sb = out["sidebar"]
    # Enabled bits are True.
    assert sb["author"] is True
    assert sb["hot"] is True
    # Disabled bits are False — the SPA must be able to hide these.
    assert sb["series"] is False
    assert sb["publisher"] is False
    assert sb["favorites"] is False
    assert sb["archived"] is False
    assert sb["duplicates"] is False


@pytest.mark.unit
def test_serialize_user_sidebar_keys_cover_spa_entries():
    """Every SPA sidebar nav entry that maps to a classic visibility bit must
    have a key in the serialized map (so none silently defaults to always-on)."""
    from cps.api.serializers import serialize_user
    from cps import ub, constants

    u = ub.User()
    u.id, u.name, u.locale, u.theme = 2, "x", "en", 1
    u.role = constants.ROLE_USER
    u.sidebar_view = constants.ADMIN_USER_SIDEBAR  # all on

    sb = serialize_user(u)["sidebar"]
    for key in ("author", "series", "category", "publisher", "language",
                "rating", "format", "hot", "random", "best_rated",
                "archived", "favorites", "list", "duplicates"):
        assert key in sb, f"missing sidebar visibility key: {key}"
        assert sb[key] is True


@pytest.mark.unit
def test_me_type_declares_sidebar():
    src = (_FE / "lib" / "api.ts").read_text()
    # The Me interface must carry the sidebar visibility map. Slice from the
    # interface start to the next top-level `export` (robust to braces inside
    # doc comments).
    start = src.index("export interface Me")
    me_block = src[start:src.index("export interface", start + 1)]
    assert "sidebar?" in me_block, "Me interface must declare sidebar"


@pytest.mark.unit
def test_sidebar_component_honors_visibility():
    src = (_FE / "components" / "Sidebar.tsx").read_text()
    # Reads the visibility map off the current user.
    assert "sidebar" in src
    # Entries carry a visibility key and are filtered by it.
    assert "vis" in src
    assert "me?.sidebar" in src or "me.sidebar" in src
    # Specific mappings present (a couple of representative ones).
    assert "'author'" in src or '"author"' in src
    assert "'hot'" in src or '"hot"' in src
    assert "'best_rated'" in src or '"best_rated"' in src
