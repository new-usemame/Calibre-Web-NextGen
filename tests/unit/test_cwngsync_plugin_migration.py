# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 0 migration contract for the KOReader plugin rename.

KOReader derives a plugin's runtime identity from the ``*.koplugin`` directory
basename.  Installing ``cwngsync.koplugin`` beside the legacy
``cwasync.koplugin`` therefore creates two independently-instantiated plugins;
without an explicit guard both can push the same book and manufacture a sync
conflict.

The migration also crosses two settings scopes.  Credentials and preferences
live in the plugin-owned ``G_reader_settings["cwasync"]`` table, while
``device_id`` is a top-level KOReader setting shared by the sync client.  The
server HMAC-fingerprints that exact id for its device registry, so replacing it
during a cosmetic rename would orphan the registered device and its history.

These tests execute the production migration module under Lua.  The fixture is
shaped like the relevant part of a real ``settings.reader.lua`` table rather
than a list of isolated keys, so accidental table replacement or a move of the
global device id is visible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwngsync.koplugin"
MAIN_LUA = PLUGIN_DIR / "main.lua"
LEGACY_NOTICE_DIR = REPO_ROOT / "koreader" / "legacy" / "cwasync.koplugin"
LEGACY_PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish-cwasync-migration-plugin.sh"
NEW_PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish-cwngsync-plugin.sh"


def _lua() -> str:
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    pytest.fail("a Lua interpreter is required for the cwngsync migration contract")


def _run_lua(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_lua(), "-"],
        input=(
            f'package.path = "{PLUGIN_DIR.as_posix()}/?.lua;" .. package.path\n'
            + source
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _assert_lua_passes(source: str) -> None:
    result = _run_lua(source)
    assert result.returncode == 0, (
        f"Lua migration contract failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "loader_fixture",
    (
        '{ enabled_plugins = { { name = "cwasync" } }, disabled_plugins = {}, loaded_plugins = {} }',
        '{ enabled_plugins = {}, disabled_plugins = { { name = "cwasync" } }, loaded_plugins = {} }',
        '{ enabled_plugins = {}, disabled_plugins = {}, loaded_plugins = { cwasync = {} } }',
    ),
    ids=("discovered-enabled", "discovered-disabled", "instantiated"),
)
def test_legacy_plugin_presence_blocks_cwngsync(loader_fixture: str) -> None:
    """Installed *or* loaded legacy code must stop the new plugin completely."""
    _assert_lua_passes(
        f"""
        local Migration = require("migration")
        local loader = {loader_fixture}
        local allowed, message = Migration.canStart(loader)
        assert(allowed == false, "cwngsync was allowed to start beside cwasync")
        assert(type(message) == "string" and message:match("cwasync%.koplugin"),
            "the refusal must name the legacy directory the user needs to remove")
        assert(message:match("restart KOReader"),
            "the refusal must tell the user that removal requires a restart")
        """
    )


def test_initializer_aborts_before_any_sync_registration_when_legacy_is_present() -> None:
    """The detector must be a startup barrier, not a warning beside live sync."""
    body = MAIN_LUA.read_text(encoding="utf-8")
    guard = body.index("Migration.canStart(")
    abort = body.index("error(Migration.BLOCKED_ERROR", guard)
    settings_load = body.index('readSetting("cwngsync"')
    menu_registration = body.index("registerToMainMenu")

    assert guard < abort < settings_load < menu_registration, (
        "cwngsync must abort initialization immediately after detecting cwasync; "
        "loading settings or registering menus/events first leaves a path where "
        "both plugin identities can become active"
    )


def test_realistic_legacy_settings_migrate_without_changing_device_id() -> None:
    """Plugin preferences move once; the global registry identity stays exact."""
    _assert_lua_passes(
        r'''
        local Migration = require("migration")

        local persisted = {
            device_id = "4f7d6c34-9207-4b45-bf83-8717eb6069f1",
            wifi_enable_action = "turn_on",
            home_dir = "/mnt/onboard/Books",
            cwasync = {
                server = "https://reader.example.test",
                username = "reader",
                password = "saved-basic-auth-password",
                auto_sync = true,
                pages_before_update = 12,
                sync_forward = 2,
                sync_backward = 1,
                sync_annotations = true,
            },
        }
        local writes = {}
        local settings = {}
        function settings:readSetting(key, default)
            local value = persisted[key]
            if value == nil then return default end
            return value
        end
        function settings:saveSetting(key, value)
            persisted[key] = value
            table.insert(writes, key)
        end

        local original_device_id = persisted.device_id
        local migrated, did_migrate = Migration.migrateSettings(settings)

        assert(did_migrate == true, "the legacy settings table was not migrated")
        assert(migrated == persisted.cwngsync,
            "the returned settings are not the table saved for cwngsync")
        assert(persisted.device_id == original_device_id,
            "device_id changed during the plugin rename")
        assert(persisted.cwasync ~= nil,
            "migration must copy, not delete, while an older install may need rollback")
        assert(persisted.cwasync ~= persisted.cwngsync,
            "the new settings key must not alias the legacy table")
        assert(#writes == 1 and writes[1] == "cwngsync",
            "migration may write only the new plugin-scoped settings key")

        for key, value in pairs(persisted.cwasync) do
            assert(persisted.cwngsync[key] == value,
                "legacy setting did not survive migration: " .. key)
        end
        '''
    )


def test_existing_cwngsync_settings_win_over_stale_legacy_settings() -> None:
    """A later start must not overwrite post-migration user changes."""
    _assert_lua_passes(
        r'''
        local Migration = require("migration")
        local persisted = {
            device_id = "stable-device-id",
            cwasync = { server = "https://old.example.test" },
            cwngsync = { server = "https://new.example.test" },
        }
        local writes = 0
        local settings = {}
        function settings:readSetting(key) return persisted[key] end
        function settings:saveSetting(_key, _value) writes = writes + 1 end

        local migrated, did_migrate = Migration.migrateSettings(settings)
        assert(migrated == persisted.cwngsync)
        assert(did_migrate == false)
        assert(writes == 0, "an idempotent migration rewrote current settings")
        assert(persisted.device_id == "stable-device-id")
        '''
    )


def test_absent_legacy_plugin_is_not_a_false_positive() -> None:
    _assert_lua_passes(
        r'''
        local Migration = require("migration")
        local allowed, message = Migration.canStart({
            enabled_plugins = { { name = "statistics" } },
            disabled_plugins = { { name = "wallabag" } },
            loaded_plugins = { kosync = {} },
        })
        assert(allowed == true)
        assert(message == nil)
        '''
    )


def test_final_cwasync_package_is_a_notice_only_plugin() -> None:
    """The final old-identity release must have no path that can still sync."""
    main = (LEGACY_NOTICE_DIR / "main.lua").read_text(encoding="utf-8")
    meta = (LEGACY_NOTICE_DIR / "_meta.lua").read_text(encoding="utf-8")

    assert 'name = "cwasync"' in meta
    assert "cwngsync.koplugin" in main
    assert "Remove" in main and "restart KOReader" in main
    for forbidden in (
        "Spore",
        "api.json",
        "updateProgress",
        "getProgress",
        "syncAnnotations",
        "NetworkMgr",
        "CWNGSyncClient",
    ):
        assert forbidden not in main, (
            f"the final cwasync migration release still contains sync surface: {forbidden}"
        )


def test_legacy_notice_and_new_plugin_versions_stay_in_lockstep() -> None:
    """Both halves of the one-time handoff must name the same CWNG release."""
    import re

    version_re = re.compile(r'version\s*=\s*"([0-9]+(?:\.[0-9]+){2})"')
    new_meta = (PLUGIN_DIR / "_meta.lua").read_text(encoding="utf-8")
    legacy_meta = (LEGACY_NOTICE_DIR / "_meta.lua").read_text(encoding="utf-8")
    new_version = version_re.search(new_meta)
    legacy_version = version_re.search(legacy_meta)

    assert new_version and legacy_version
    assert legacy_version.group(1) == new_version.group(1)


def test_final_legacy_release_has_a_dedicated_one_time_publisher() -> None:
    body = LEGACY_PUBLISH_SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE="$ROOT/koreader/legacy/cwasync.koplugin"' in body
    assert 'TARGET_REPO="new-usemame/cwasync.koplugin"' in body
    assert "cwasync.koplugin.zip" in body
    assert "new-usemame/Calibre-Web-NextGen" in body, (
        "the notice release must be anchored to an already-published CWNG tag"
    )
    assert "--publish" in body, "the one-time public mutation must be explicit"
    assert "gh release upload" not in body, (
        "the legacy notice must not replace the application release's full plugin asset"
    )


def test_new_publisher_removes_legacy_tree_after_repository_rename() -> None:
    body = NEW_PUBLISH_SCRIPT.read_text(encoding="utf-8")
    assert 'rm -rf "$tmp/repo/cwasync.koplugin"' in body
    assert 'git -C "$tmp/repo" add -A -- cwasync.koplugin' in body
