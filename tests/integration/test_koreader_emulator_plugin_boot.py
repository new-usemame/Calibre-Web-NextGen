# Calibre-Web-NextGen — fork of Calibre-Web-Automated
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bundled plugin must load in a real KOReader, and refuse to load beside the old one.

The cwasync -> cwngsync rename changes the plugin's identity, because KOReader's
PluginLoader keys plugins by their ``*.koplugin`` directory basename. Two copies
installed at once therefore both initialise and both push position and highlight
updates for the same book, which is worse than not syncing at all. ``migration.lua``
prevents that by refusing to start while a ``cwasync.koplugin`` is present.

Every other test of that guard calls ``Migration.canStart`` directly with a stub
plugin loader. That proves the decision function, not the wiring: it cannot catch
the guard being dropped from ``init``, ``PluginLoader`` no longer exposing the
discovery lists the guard reads, or an upstream KOReader change to how a raising
``init`` is handled. Those are the failures that ship a double-syncing plugin
while the whole unit suite stays green.

So this boots the real upstream KOReader in the emulator image, with the real
plugin mounted, and reads its log. Both directions are asserted deliberately: a
guard that refuses everything is not a working guard, it is an outage, and only
the positive control tells the two apart.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pytest

from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
EMULATOR_CONTEXT = REPO_ROOT / "local-dev" / "koreader-emulator"
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwngsync.koplugin"
IMAGE = "cwng-koreader-emulator:test"

# KOReader boots, migrates its caches and loads plugins; on a cold container that
# is comfortably under a minute, but a busy CI host is slower than a laptop.
BOOT_TIMEOUT_SECONDS = 120
PLUGIN_DISCOVERED = "cwngsync"
BLOCKED = "cwngsync startup blocked by installed cwasync.koplugin"
INIT_FAILED = "Failed to initialize cwngsync plugin"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


pytestmark.append(
    pytest.mark.skipif(not _docker_available(), reason="Docker is required"),
)


@pytest.fixture(scope="module")
def emulator_image():
    """Build the emulator image once for the module.

    The build downloads the pinned upstream KOReader release, so it needs the
    network on a cold cache; a machine without it should skip rather than fail,
    because that says nothing about the plugin.
    """
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE, str(EMULATOR_CONTEXT)],
        capture_output=True, text=True, timeout=900, check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"KOReader emulator image could not be built:\n{build.stderr[-2000:]}")
    return IMAGE


def _boot_and_read_log(image, tmp_path, *, install_legacy: bool) -> str:
    """Run KOReader once and return everything it logged before we stopped it."""
    home = tmp_path / "home"
    (home / ".config" / "koreader" / "plugins").mkdir(parents=True)

    if install_legacy:
        legacy = home / ".config" / "koreader" / "plugins" / "cwasync.koplugin"
        legacy.mkdir()
        (legacy / "_meta.lua").write_text(
            'return { name = "cwasync", fullname = "Legacy CWA Sync",'
            ' description = "legacy", version = "0.0.0" }\n',
            encoding="utf-8",
        )
        (legacy / "main.lua").write_text(
            'local WidgetContainer = require("ui/widget/container/widgetcontainer")\n'
            'local Legacy = WidgetContainer:extend{ name = "cwasync", is_doc_only = false }\n'
            "function Legacy:init() end\n"
            "return Legacy\n",
            encoding="utf-8",
        )

    name = f"cwng-koreader-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-v", f"{home}:/koreader-home",
            "-v", f"{PLUGIN_DIR}:/opt/koreader/lib/koreader/plugins/cwngsync.koplugin:ro",
            image,
        ],
        capture_output=True, text=True, timeout=120, check=True,
    )
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
        log = ""
        while time.monotonic() < deadline:
            log = subprocess.run(
                ["docker", "logs", name],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout
            # The plugin loader has run once this line appears, so both the
            # blocked and the clean outcome are already decided.
            if PLUGIN_DISCOVERED in log:
                # Give init, which runs after discovery, a moment to log its own
                # outcome; otherwise a pass here only means discovery happened.
                time.sleep(3)
                return subprocess.run(
                    ["docker", "logs", name],
                    capture_output=True, text=True, timeout=30, check=False,
                ).stdout
            # A dead container is not a slow one. Without this the run waits the
            # full timeout and then reports "never reached plugin loading",
            # which describes the symptom of any failure and points at none of
            # them -- an architecture mismatch looks exactly like a slow boot.
            state = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", name],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.split()
            if state and state[0] == "false":
                pytest.fail(
                    f"the KOReader container exited (code {state[-1]}) before "
                    f"loading plugins. Log was:\n{log[-4000:]}"
                )
            time.sleep(2)
        pytest.fail(
            "KOReader never reached plugin loading within "
            f"{BOOT_TIMEOUT_SECONDS}s, and the container is still running. "
            f"Log was:\n{log[-4000:]}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, timeout=60, check=False)


def test_the_plugin_loads_in_a_real_koreader(emulator_image, tmp_path):
    """The positive control: without the legacy plugin, nothing blocks."""
    log = _boot_and_read_log(emulator_image, tmp_path, install_legacy=False)

    assert PLUGIN_DISCOVERED in log, f"plugin was never discovered:\n{log[-4000:]}"
    assert BLOCKED not in log, f"guard fired with no legacy plugin installed:\n{log[-4000:]}"
    assert INIT_FAILED not in log, f"plugin failed to initialise:\n{log[-4000:]}"


def test_the_plugin_refuses_to_start_beside_the_legacy_one(emulator_image, tmp_path):
    """The guard must bite in a real KOReader, not only against a stub loader."""
    log = _boot_and_read_log(emulator_image, tmp_path, install_legacy=True)

    assert BLOCKED in log, (
        "cwngsync started while cwasync.koplugin was installed; both would now "
        f"sync the same book:\n{log[-4000:]}"
    )
    # The warning alone would be satisfied by a plugin that logs and carries on.
    # What makes the guard safe is that no instance is recorded, which KOReader
    # reports as an initialisation failure.
    assert INIT_FAILED in log, (
        f"the guard warned but the plugin still initialised:\n{log[-4000:]}"
    )
