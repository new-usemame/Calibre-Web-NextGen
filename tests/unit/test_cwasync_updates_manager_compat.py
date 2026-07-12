# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for fork issue #400 — Updates Manager compatibility.

KOReader's Updates Manager plugin (advokatb/updatesmanager.koplugin) reads the
``version`` field from a plugin's ``_meta.lua`` and compares it against the
GitHub release tag of the configured repository. Two invariants make our
cwasync plugin distributable through it:

* ``_meta.lua`` MUST declare a ``version`` field. Without it Updates Manager
  shows the installed version as "unknown" and flags a (false) update on every
  check, even when the user already runs the latest build.
* The ``_meta.lua`` version and the ``main.lua`` version (shown in the
  plugin's own About dialog) must stay in lockstep — two version strings that
  drift produce contradictory answers to "what version am I running?".

The release-tag anchor itself is pinned by ``EXPECTED_PLUGIN_VERSION`` in
``test_kosync_plugin_no_book_handling.py``. Dedicated-repository publishing is
handled by ``scripts/publish-cwasync-plugin.sh`` so an unchanged plugin no
longer appears as a new update on every CWNG application release.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwasync.koplugin"
META_LUA = PLUGIN_DIR / "_meta.lua"
MAIN_LUA = PLUGIN_DIR / "main.lua"
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish-cwasync-plugin.sh"

VERSION_RE = re.compile(r'version\s*=\s*"([^"]+)"')


def _meta_version() -> str | None:
    match = VERSION_RE.search(META_LUA.read_text())
    return match.group(1) if match else None


def _main_version() -> str | None:
    match = VERSION_RE.search(MAIN_LUA.read_text())
    return match.group(1) if match else None


def test_meta_lua_declares_version():
    assert _meta_version() is not None, (
        "_meta.lua must declare a `version = \"...\"` field — Updates Manager "
        "reads the installed version from _meta.lua, and without it every "
        "update check reports 'unknown' plus a false update notification"
    )


def test_meta_and_main_versions_match():
    assert _meta_version() == _main_version(), (
        f"_meta.lua version ({_meta_version()}) and main.lua version "
        f"({_main_version()}) must stay in lockstep — bump both when a "
        "release touches the plugin directory"
    )


def test_version_is_release_tag_shaped():
    version = _meta_version()
    assert version and re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"plugin version {version!r} must be the CWNG release tag without the "
        "leading 'v' (e.g. 4.0.162) so Updates Manager's semantic-version "
        "comparison against release tags works"
    )


def test_dedicated_publish_script_pins_release_and_zip_contract():
    assert PUBLISH_SCRIPT.exists()
    body = PUBLISH_SCRIPT.read_text()
    assert "new-usemame/cwasync.koplugin" in body
    assert 'gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen' in body
    assert "cwasync.koplugin.zip" in body
    assert "--publish" in body, "publishing must require an explicit opt-in"


def test_monorepo_no_longer_publishes_plugin_on_every_app_release():
    old_workflow = REPO_ROOT / ".github" / "workflows" / "plugin-release-asset.yml"
    assert not old_workflow.exists(), (
        "an app-release-triggered plugin workflow makes Updates Manager report "
        "unchanged plugins as updates; dedicated releases must be intentional"
    )
