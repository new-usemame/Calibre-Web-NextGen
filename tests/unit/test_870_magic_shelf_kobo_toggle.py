# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#870 — Kobo-sync toggle for smart shelves in the new UI.

The backend already synced kobo_sync magic shelves to devices; the SPA had no
way to set the flag because the only writer was the whole-shelf classic edit
form. These pin the narrow /api/v1 write and the two payload additions the SPA
gates the button on: the per-shelf ``kobo_sync`` field and the instance-level
``kobo_sync_magic_shelves`` feature flag.
"""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _shelf(**kw):
    defaults = dict(id=3, name="Recently added", icon="🪄", is_public=0,
                    is_system=False, user_id=7, kobo_sync=False,
                    uuid="uuid-3", last_modified=None,
                    rules={"condition": "AND", "rules": []})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _patch_session(mod, shelf):
    """Stand in for ub.session.query(MagicShelf).get(id) + commit()."""
    committed = {"count": 0}

    class _Query:
        def get(self, _id):
            return shelf

    class _Session:
        def query(self, _model):
            return _Query()

        def commit(self):
            committed["count"] += 1

        def rollback(self):
            pass

    return patch.object(mod, "ub", SimpleNamespace(session=_Session(),
                                                   MagicShelf=object)), committed


# ── feature flag ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_server_features_exposes_magic_shelf_kobo_setting():
    """Without this the SPA cannot tell an inert toggle from a working one."""
    from cps.api import auth as mod
    cfg = SimpleNamespace(config_user_hide_enabled=False, config_public_reg=False,
                          config_anonbrowse=False, config_kobo_sync=True,
                          config_kobo_sync_magic_shelves=True,
                          get_mail_server_configured=lambda: False)
    with patch.object(mod, "config", cfg):
        feats = mod._server_features()
    assert feats["kobo_sync_magic_shelves"] is True

    cfg.config_kobo_sync_magic_shelves = False
    with patch.object(mod, "config", cfg):
        assert mod._server_features()["kobo_sync_magic_shelves"] is False


@pytest.mark.unit
def test_server_features_defaults_off_when_setting_absent():
    """A minimal config object (bootstrap/test paths) must not fault /me."""
    from cps.api import auth as mod
    with patch.object(mod, "config",
                      SimpleNamespace(get_mail_server_configured=lambda: False)):
        assert mod._server_features()["kobo_sync_magic_shelves"] is False


# ── payload surfaces the current mark ────────────────────────────────────────

@pytest.mark.unit
def test_shelf_item_includes_kobo_sync():
    """The shelf list feeds the sidebar; without the field the SPA cannot
    render a correct on/off state after a reload."""
    from cps.api import magicshelves as mod
    with patch.object(mod.magic_shelf, "system_magic_shelf_display_name",
                      lambda s: s.name):
        assert mod._shelf_item(_shelf(kobo_sync=True), 7)["kobo_sync"] is True
        assert mod._shelf_item(_shelf(kobo_sync=False), 7)["kobo_sync"] is False


# ── the toggle endpoint ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_toggle_enables_and_bumps_last_modified():
    """Kobo tag/tombstone payloads carry last_modified as the change stamp —
    a flip that leaves it stale is invisible to an already-synced device."""
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=False)
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": True}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    body = json.loads(resp.get_data())
    assert body == {"id": 3, "kobo_sync": True}
    assert shelf.kobo_sync is True
    assert shelf.last_modified is not None
    assert committed["count"] == 1


@pytest.mark.unit
def test_toggle_disables():
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=True)
    sess_patch, _ = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": False}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert json.loads(resp.get_data())["kobo_sync"] is False
    assert shelf.kobo_sync is False


@pytest.mark.unit
def test_toggle_warns_when_global_magic_shelf_sync_is_off():
    """Mirrors the classic edit route (#359): store the intent, say it's inert."""
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=False)
    sess_patch, _ = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": True}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=False)), \
             patch.object(mod, "_", lambda s: s):  # bare Flask app has no babel
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    body = json.loads(resp.get_data())
    assert shelf.kobo_sync is True
    assert "warning" in body and "Magic Shelves" in body["warning"]


@pytest.mark.unit
def test_toggle_rejects_non_owner():
    """cps/kobo.py only ever syncs shelves owned by the requesting user, so a
    write against someone else's public shelf is a no-op with side effects."""
    from cps.api import magicshelves as mod
    shelf = _shelf(user_id=99, is_public=1)
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": True}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "forbidden"
    assert shelf.kobo_sync is False
    assert committed["count"] == 0


@pytest.mark.unit
def test_toggle_404_for_missing_shelf():
    from cps.api import magicshelves as mod
    sess_patch, _ = _patch_session(mod, None)
    with _ctx("/api/v1/magicshelf/404/kobo-sync", body={"kobo_sync": True}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(404)
    assert resp[1] == 404


@pytest.mark.unit
def test_toggle_403_when_kobo_sync_disabled_server_wide():
    from cps.api import magicshelves as mod
    shelf = _shelf()
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": True}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=False,
                                                         config_kobo_sync_magic_shelves=False)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 403
    assert committed["count"] == 0


@pytest.mark.unit
def test_toggle_requires_kobo_sync_field():
    """An empty body must not be read as "turn it off"."""
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=True)
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={}):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 400
    assert shelf.kobo_sync is True
    assert committed["count"] == 0


# ── SPA source pins ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_spa_shelf_view_renders_the_toggle():
    """Refactor guard: the button must stay gated on BOTH the instance Kobo
    feature and the magic-shelf setting, or it reappears as a dead control."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/MagicShelfView.tsx").read_text()
    assert "useToggleMagicShelfKoboSync" in src
    assert "me?.features?.kobo_sync" in src
    assert "me?.features?.kobo_sync_magic_shelves" in src
    assert "data.is_owner" in src


# ── #866 regression found while wiring #870 ──────────────────────────────────

@pytest.mark.unit
def test_me_payload_carries_kobo_only_shelves_sync():
    """Both shelf views read this off useMe() → /api/v1/auth/me. It was only
    ever emitted by /api/v1/account, so #866's "your Kobo still syncs the whole
    library" warning could never render on either shelf type."""
    from cps.api import auth as mod
    user = SimpleNamespace(kobo_only_shelves_sync=0, name="admin")
    cfg = SimpleNamespace(get_mail_server_configured=lambda: False,
                          config_books_per_page=60, config_random_books=4)
    with patch.object(mod, "config", cfg), \
         patch.object(mod, "serialize_user", lambda u: {}), \
         patch.object(mod, "_instance_name", lambda: "x"), \
         patch.object(mod, "_user_avatar", lambda n: None):
        assert mod._me_payload(user)["kobo_only_shelves_sync"] is False
        user.kobo_only_shelves_sync = 1
        assert mod._me_payload(user)["kobo_only_shelves_sync"] is True
