# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The normal plugin sync cycle must actually collect wanted books."""

from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.unit
PLUGIN = (
    Path(__file__).resolve().parents[2]
    / "koreader" / "plugins" / "cwngsync.koplugin"
)
MAIN = (PLUGIN / "main.lua").read_text(encoding="utf-8")
CLIENT = (PLUGIN / "CWNGSyncClient.lua").read_text(encoding="utf-8")
COLLECTION_TEST = PLUGIN / "tests" / "delivery_collection_test.lua"


def _function(name):
    start = MAIN.index(f"function CWNGSync:{name}")
    next_function = MAIN.find("\nfunction CWNGSync:", start + 1)
    return MAIN[start:next_function if next_function >= 0 else len(MAIN)]


def _lua():
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        executable = shutil.which(candidate)
        if executable:
            return executable
    pytest.fail("a Lua interpreter is required for the cwngsync delivery contract")


def test_reader_ready_sync_cycle_claims_device_deliveries():
    ready = _function("onReaderReady")

    assert "self:collectDeliveries(false, false)" in ready


def test_collection_uses_atomic_installer_then_acknowledges():
    collect = _function("collectDeliveries")

    assert "Delivery.install" in collect
    assert "self:reportInventory" in collect
    assert "client:claim_delivery" in collect
    assert "client:complete_delivery" in collect
    assert collect.index("self:reportInventory") < collect.index("client:claim_delivery")
    assert collect.index("Delivery.install") < collect.index("client:complete_delivery")


def test_collection_serializes_overlapping_sync_triggers():
    result = subprocess.run(
        [_lua(), COLLECTION_TEST.name],
        cwd=COLLECTION_TEST.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"Lua delivery collection contract failed:\n{result.stdout}\n{result.stderr}"
    )


def test_collection_guard_remains_explicit_in_the_production_function():
    collect = _function("collectDeliveries")

    assert "self.delivery_collection_running" in collect
    assert "collection_owned" in collect


def test_manual_collection_is_available_without_an_open_book():
    menu = _function("addToMainMenu")

    assert "Collect books queued for this device now" in menu
    assert "self:collectDeliveries(true, true)" in menu


def test_streamed_download_reuses_authenticated_channel_credentials():
    download = CLIENT[CLIENT.index("function CWNGSyncClient:download_delivery"):]

    assert '["Authorization"] = "Basic "' in download
    assert 'os.remove(local_path)' in download
