# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The metadata-change-detector service starts as root and the dispatcher used
to run ``cover_enforcer.py`` as root too. On a book library on a network share
(NFS with root squash, say) root cannot write, and calibredb fails opening the
library with ``PermissionError`` on its case-sensitivity probe file, so every
metadata edit logged ``Failed to enforce metadata ... exit status 1``.

The enforcer now runs as the app user, like the ingest service and the web
app. These tests cover the decision (when to drop) and the config directory it
gets, behaviourally, with the uid and the helper lookup injected.
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import metadata_change_dispatch as dispatch  # noqa: E402

HELPER_PRESENT = lambda name: "/usr/local/bin/" + name  # noqa: E731
HELPER_ABSENT = lambda name: None  # noqa: E731
ENFORCER = "/app/calibre-web-automated/scripts/cover_enforcer.py"
BARE = ["python3", ENFORCER, "--log", "20261009095953-502.json"]


def test_root_runs_the_enforcer_through_the_privilege_drop_helper():
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=0, which=HELPER_PRESENT
    )
    assert cmd == ["cwa-as-abc"] + BARE


def test_non_root_runs_the_enforcer_as_it_is():
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=1000, which=HELPER_PRESENT
    )
    assert cmd == BARE


def test_without_the_helper_the_enforcer_still_runs():
    """Outside the image there is no helper; the command must not break."""
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=0, which=HELPER_ABSENT
    )
    assert cmd == BARE


def test_root_private_config_dir_is_swapped_for_the_app_users_when_dropping():
    env = dispatch.enforcer_env(
        {"CALIBRE_CONFIG_DIRECTORY": "/tmp/cwa-calibre-config-0", "PATH": "/bin"},
        euid=0, which=HELPER_PRESENT,
    )
    assert env["CALIBRE_CONFIG_DIRECTORY"] == "/config/.config/calibre-runtime"
    assert env["PATH"] == "/bin"


def test_an_operator_chosen_config_dir_is_left_alone():
    env = dispatch.enforcer_env(
        {"CALIBRE_CONFIG_DIRECTORY": "/srv/calibre-config"},
        euid=0, which=HELPER_PRESENT,
    )
    assert env["CALIBRE_CONFIG_DIRECTORY"] == "/srv/calibre-config"


@pytest.mark.parametrize(
    "euid, which",
    [(1000, HELPER_PRESENT), (0, HELPER_ABSENT)],
    ids=["not-root", "no-helper"],
)
def test_config_dir_is_untouched_when_nothing_is_dropped(euid, which):
    env = dispatch.enforcer_env(
        {"CALIBRE_CONFIG_DIRECTORY": "/tmp/cwa-calibre-config-0"},
        euid=euid, which=which,
    )
    assert env["CALIBRE_CONFIG_DIRECTORY"] == "/tmp/cwa-calibre-config-0"


def test_default_dispatch_uses_the_dropped_command_and_env(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    monkeypatch.setattr(dispatch.os, "geteuid", lambda: 0)
    monkeypatch.setattr(dispatch.shutil, "which", HELPER_PRESENT)
    monkeypatch.setenv("CALIBRE_CONFIG_DIRECTORY", "/tmp/cwa-calibre-config-0")

    dispatch._default_dispatch("/config/metadata_change_logs", ENFORCER)("x-502.json")

    assert seen["cmd"] == ["cwa-as-abc", "python3", ENFORCER, "--log", "x-502.json"]
    assert seen["kwargs"]["env"]["CALIBRE_CONFIG_DIRECTORY"] == "/config/.config/calibre-runtime"
    assert seen["kwargs"]["check"] is False
