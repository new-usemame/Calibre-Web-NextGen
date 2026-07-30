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

import pytest

# CI selects with -m "smoke or unit". Without this marker every test in this
# file is collected and then silently deselected, so the guards below have never
# gated a pull request — an unmarked guard is decoration.
pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwasync.koplugin"
META_LUA = PLUGIN_DIR / "_meta.lua"
MAIN_LUA = PLUGIN_DIR / "main.lua"
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish-cwasync-plugin.sh"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "plugin-release-publish.yml"

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
    """The per-app-release *asset* workflow must stay gone.

    ``plugin-release-publish.yml`` does not resurrect it: that workflow pushes
    to the dedicated repository only when the plugin actually changed (see
    ``test_publish_is_skipped_when_the_plugin_did_not_change``), so an unchanged
    plugin still never surfaces as an update.
    """
    old_workflow = REPO_ROOT / ".github" / "workflows" / "plugin-release-asset.yml"
    assert not old_workflow.exists(), (
        "an app-release-triggered plugin workflow makes Updates Manager report "
        "unchanged plugins as updates; dedicated releases must be intentional"
    )


def test_publishing_is_automated_on_release_rather_than_a_remembered_step():
    """fork #400: a manual publish step is a skipped publish step.

    v4.1.12, v4.1.13 and v4.1.14 all shipped while the dedicated repository sat
    on the v4.1.11 plugin, because publishing was prose in notes/org/LEDGER.md
    that nobody re-ran. Updates Manager reads the dedicated repository, so three
    plugin-changing fixes never reached a single device.
    """
    assert RELEASE_WORKFLOW.exists(), (
        "publishing the plugin must be triggered by the release itself, not "
        "left as a step someone has to remember"
    )
    body = RELEASE_WORKFLOW.read_text()
    assert "release:" in body and "published" in body, (
        "the workflow must fire when a CWNG release is published"
    )
    assert "publish-cwasync-plugin.sh" in body, (
        "the workflow must reuse the publish script rather than reimplementing "
        "the release recipe — one source of truth for how a plugin ships"
    )
    assert "--auto" in body, "the workflow must use the skip-when-unowed mode"
    assert "secrets.GH_PAT" in body, (
        "GITHUB_TOKEN is scoped to this repository and cannot push to "
        "new-usemame/cwasync.koplugin; the cross-repo PAT is required"
    )


def test_publish_is_skipped_when_the_plugin_did_not_change():
    body = PUBLISH_SCRIPT.read_text()
    assert "--auto" in body, (
        "the release workflow runs on every app tag, most of which do not touch "
        "the plugin; it needs a mode that exits 0 instead of failing"
    )
    assert re.search(r"AUTO == 1", body), "--auto must actually branch on skip"


def test_publish_path_attaches_the_zip_to_the_application_release():
    """fork #1253: the application-release download must not depend on a human.

    v4.1.16 publicly restored ``cwasync.koplugin.zip`` on the application release
    after it went missing, but as a one-off manual upload. v4.1.17 through
    v4.1.25 then shipped no asset at all, so a device pointed at the application
    repository saw v4.1.16 as the newest release carrying one and answered "no new
    release available" indefinitely while newer plugins existed.
    """
    body = PUBLISH_SCRIPT.read_text()
    assert (
        'gh release upload "$TAG" "$tmp/repo/cwasync.koplugin.zip"' in body
        and "--repo new-usemame/Calibre-Web-NextGen" in body
    ), (
        "a plugin-changing release must attach the validated zip to the "
        "application release too; leaving it to a manual step is what stranded "
        "devices for nine consecutive releases"
    )
    assert "--clobber" in body, (
        "the upload must be idempotent — without --clobber a re-run of the "
        "release workflow fails on an already-present asset"
    )


# The invariant "the application asset rides the *plugin changed* decision and
# nothing weaker" is NOT pinned here. It was, by comparing the character offsets
# of "Nothing owed" and the upload command — and that pin broke the moment the
# upload also became reachable from a helper function defined earlier in the file,
# on a change that was correct. Offsets model line order, not control flow, so
# they answer a different question than the one that matters.
#
# It now lives in tests/unit/test_1253_plugin_release_asset_publish_paths.py,
# which executes this script against recording fakes for gh and git and asserts
# which releases each invocation actually mutates — dry run, unchanged plugin
# under --auto and --publish, version mismatch, missing application release,
# happy path, failed-upload retry, and a re-run after a complete publish.


def test_update_manager_repository_is_documented_where_users_look():
    """fork #1253: an update path documented only in an issue thread is undocumented.

    The reporter's complaint was literally "there's no documentation for setting
    up Updates Manager with Calibre-Web NextGen". The dedicated repository was
    named only in comments on fork #400, while README sends users to /kosync for
    install instructions and that page described nothing but the manual
    download-and-copy route.
    """
    kosync_page = REPO_ROOT / "cps" / "templates" / "kosync_plugin.html"
    readme = REPO_ROOT / "README.md"
    for surface in (kosync_page, readme):
        text = surface.read_text()
        assert "new-usemame/cwasync.koplugin" in text, (
            f"{surface.name} must name the plugin's own repository — an update "
            "manager pointed at the application repository only sees releases "
            "that happened to carry an asset"
        )
        assert "Updates Manager" in text or "updatesmanager" in text, (
            f"{surface.name} must name the update manager the instructions are for"
        )


def test_owed_check_runs_before_the_version_check():
    """The ordering IS the fix — reversing it re-breaks fork #400.

    Version-first means every release where the plugin did not change dies on a
    lockstep mismatch (plugin still declares the tag it last shipped under), so
    the automation goes red on ordinary releases and gets muted or ignored.

    Owed-first means an unchanged plugin exits quietly, and a plugin that DID
    change but was never version-bumped fails loudly and actionably — which is
    the case that silently stranded users for three releases.
    """
    body = PUBLISH_SCRIPT.read_text()
    owed_check = body.index("publish_owed")
    version_check = body.index("expected_version=${TAG#v}")
    assert owed_check < version_check, (
        "the owed-check must precede the version check: an unchanged plugin has "
        "no business failing a release, and a changed one must never be skipped"
    )
