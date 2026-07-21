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


def _fake_user(**kw):
    """A user shaped enough for the real ``serialize_user`` (roles are callables
    there). Deliberately NOT a stub of serialize_user itself — see
    test_serialize_user_carries_kobo_only_shelves_sync."""
    defaults = dict(id=1, name="admin", locale="en", theme=0,
                    ui_font_body=None, ui_font_display=None,
                    kobo_only_shelves_sync=0, view_settings={},
                    sidebar_view=0, sidebar_order=None)
    defaults.update(kw)
    user = SimpleNamespace(**defaults)
    for role in ("admin", "upload", "edit", "download", "delete_books",
                 "edit_shelfs", "viewer", "passwd", "anonymous"):
        setattr(user, "role_" + role, lambda: False)
    return user


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
def test_serialize_user_carries_kobo_only_shelves_sync():
    """Both shelf views gate #866's "your Kobo still syncs the whole library"
    notice on this field, read off useMe() → /api/v1/auth/me. ``_me_payload``
    sources it from ``serialize_user`` (added in #1008), so this pins the real
    provider — stubbing ``serialize_user`` out would only test the stub."""
    from cps.api import serializers as mod
    for stored, expected in ((0, False), (1, True)):
        user = _fake_user(kobo_only_shelves_sync=stored)
        assert mod.serialize_user(user)["kobo_only_shelves_sync"] is expected


@pytest.mark.unit
def test_me_payload_carries_kobo_only_shelves_sync_without_restating_it():
    """Guards the contract end-to-end *and* guards against re-adding the
    redundant re-assignment this PR originally carried: `_me_payload` must get
    the field from serialize_user, not set it a second time itself."""
    from cps.api import auth as mod
    src = inspect.getsource(mod._me_payload)
    assert 'payload["kobo_only_shelves_sync"]' not in src, \
        "serialize_user already provides this; re-setting it is dead code"
    user = _fake_user(kobo_only_shelves_sync=1)
    cfg = SimpleNamespace(get_mail_server_configured=lambda: False,
                          config_books_per_page=60, config_random_books=4,
                          config_public_reg=False, config_anonbrowse=False,
                          config_kobo_sync=True, config_kobo_sync_magic_shelves=True)
    with patch.object(mod, "config", cfg), \
         patch.object(mod, "_instance_name", lambda: "x"), \
         patch.object(mod, "_user_avatar", lambda n: None):
        assert mod._me_payload(user)["kobo_only_shelves_sync"] is True


# ── request-body validation (a JSON boolean, not Python truthiness) ──────────

@pytest.mark.unit
@pytest.mark.parametrize("body", [{"kobo_sync": "false"}, {"kobo_sync": "0"},
                                  {"kobo_sync": []}, {"kobo_sync": 1},
                                  {"kobo_sync": None}])
def test_toggle_rejects_non_boolean_kobo_sync(body):
    """bool("false") is True — coercing would perform the opposite write."""
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=False)
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body=body):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 400
    assert shelf.kobo_sync is False
    assert committed["count"] == 0


@pytest.mark.unit
def test_toggle_rejects_non_object_body():
    """A top-level scalar body used to raise TypeError on the membership test
    and surface as a 500 instead of a 400."""
    from cps.api import magicshelves as mod
    shelf = _shelf(kobo_sync=False)
    sess_patch, committed = _patch_session(mod, shelf)
    with _ctx("/api/v1/magicshelf/3/kobo-sync", body=42):
        with sess_patch, \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 400
    assert committed["count"] == 0


@pytest.mark.unit
def test_toggle_rolls_back_and_hides_driver_error_on_commit_failure():
    """ub.session is a long-lived global session: a commit that fails without a
    rollback poisons every later request. The driver text must not be echoed."""
    from cps.api import magicshelves as mod
    from sqlalchemy.exc import IntegrityError
    shelf = _shelf(kobo_sync=False)
    rolled = {"count": 0}

    class _Sess:
        def query(self, _m): return self
        def get(self, _i): return shelf
        def commit(self): raise IntegrityError("INSERT", {}, Exception("secret schema detail"))
        def rollback(self): rolled["count"] += 1

    with _ctx("/api/v1/magicshelf/3/kobo-sync", body={"kobo_sync": True}):
        with patch.object(mod, "ub", SimpleNamespace(session=_Sess(), MagicShelf=object)), \
             patch.object(mod, "current_user", SimpleNamespace(id=7, is_authenticated=True)), \
             patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True,
                                                         config_kobo_sync_magic_shelves=True)):
            resp = inspect.unwrap(mod.set_magic_shelf_kobo_sync)(3)
    assert resp[1] == 500
    assert rolled["count"] == 1, "IntegrityError must still roll back"
    assert "secret schema detail" not in resp[0].get_data(as_text=True)
