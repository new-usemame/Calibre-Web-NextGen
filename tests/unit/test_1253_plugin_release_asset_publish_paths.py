# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural coverage for fork #1253 — which invocations may touch a release.

``scripts/publish-cwasync-plugin.sh`` decides two things that users feel
directly: whether the KOReader plugin gets published to its dedicated
repository, and whether ``cwasync.koplugin.zip`` gets attached to the
application release (the channel a device configured before the plugin moved
still reads). Getting either wrong is silent — a missing asset looks exactly
like "you are up to date", which is how v4.1.17 through v4.1.25 shipped with no
asset while three plugin fixes went out.

The sibling module ``test_cwasync_updates_manager_compat.py`` pins the script's
*text*. Text pins cannot distinguish "the upload sits inside the changed-plugin
path" from "a differently-spelled upload runs unconditionally", so this module
executes the real script against recording fakes for ``gh`` and ``git`` and
asserts on the commands it actually issued. Every remote mutation the script can
make is observable here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish-cwasync-plugin.sh"
APP_REPO = "new-usemame/Calibre-Web-NextGen"
DEDICATED_REPO = "new-usemame/cwasync.koplugin"
TAG = "v9.9.9"
PLUGIN_VERSION = "9.9.9"

REQUIRED_TOOLS = ("git", "zip", "unzip", "rsync", "bash")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        any(shutil.which(tool) is None for tool in REQUIRED_TOOLS),
        reason="publish script requires git, zip, unzip, rsync and bash on PATH",
    ),
]

GIT_SHIM = r"""#!/usr/bin/env bash
# Records every git call. `clone` is faked (there is no network here) and seeded
# from $SHIPPED_DIR, which is what the dedicated repository is pretending to
# already ship. `push` is faked. Everything else runs for real, so the script's
# owed-check (git add + git diff --cached) is genuinely exercised.
set -uo pipefail
printf 'git %s\n' "$*" >> "$CMDLOG"
for arg in "$@"; do
    if [[ "$arg" == "push" ]]; then
        exit "${FAKE_PUSH_RC:-0}"
    fi
done
if [[ "${1:-}" == "clone" ]]; then
    dest="${!#}"
    mkdir -p "$dest"
    "$REAL_GIT" init --quiet "$dest"
    "$REAL_GIT" -C "$dest" config user.email test@example.invalid
    "$REAL_GIT" -C "$dest" config user.name test
    if [[ -n "${SHIPPED_DIR:-}" && -d "${SHIPPED_DIR:-}" ]]; then
        mkdir -p "$dest/cwasync.koplugin"
        cp -R "$SHIPPED_DIR/." "$dest/cwasync.koplugin/"
    fi
    "$REAL_GIT" -C "$dest" add -A
    "$REAL_GIT" -C "$dest" commit --quiet --allow-empty -m seed
    exit 0
fi
exec "$REAL_GIT" "$@"
"""

GH_SHIM = r"""#!/usr/bin/env bash
# Records every gh call and answers from env knobs. Release existence and asset
# presence are the two facts the script branches on, so they are the two facts
# this fake controls.
set -uo pipefail
printf 'gh %s\n' "$*" >> "$CMDLOG"

repo=""
dir=""
prev=""
wants_json=0
for arg in "$@"; do
    [[ "$prev" == "--repo" ]] && repo="$arg"
    [[ "$prev" == "--dir" ]] && dir="$arg"
    [[ "$arg" == "--json" ]] && wants_json=1
    prev="$arg"
done

if [[ "${1:-}" == "api" ]]; then
    printf 'new-usemame\n'
    exit 0
fi

if [[ "${1:-}" == "release" ]]; then
    case "${2:-}" in
        view)
            if [[ "$repo" == "$APP_REPO" ]]; then
                [[ "${FAKE_APP_RELEASE_EXISTS:-1}" == "1" ]] || exit 1
                if ((wants_json)) && [[ "${FAKE_APP_ASSET_PRESENT:-0}" == "1" ]]; then
                    printf 'cwasync.koplugin.zip\n'
                fi
                exit 0
            fi
            [[ "${FAKE_DEDICATED_RELEASE_EXISTS:-0}" == "1" ]] || exit 1
            exit 0
            ;;
        create)
            exit "${FAKE_CREATE_RC:-0}"
            ;;
        upload)
            if [[ "$repo" == "$APP_REPO" ]]; then
                exit "${FAKE_APP_UPLOAD_RC:-0}"
            fi
            exit 0
            ;;
        download)
            mkdir -p "$dir"
            printf 'fake-zip' > "$dir/cwasync.koplugin.zip"
            exit "${FAKE_DOWNLOAD_RC:-0}"
            ;;
    esac
fi
exit 0
"""


def _plugin_files(version: str, extra: str = "") -> dict[str, str]:
    return {
        "_meta.lua": f'return {{ name = "cwasync", version = "{version}", }}\n{extra}',
        "main.lua": f'local M = {{ version = "{version}" }}\nreturn M\n{extra}',
    }


@pytest.fixture()
def sandbox(tmp_path):
    """A minimal tree the real script can run against, plus recording fakes."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(PUBLISH_SCRIPT, root / "scripts" / PUBLISH_SCRIPT.name)
    source = root / "koreader" / "plugins" / "cwasync.koplugin"
    source.mkdir(parents=True)
    for name, body in _plugin_files(PLUGIN_VERSION).items():
        (source / name).write_text(body)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("git", GIT_SHIM), ("gh", GH_SHIM)):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)

    cmdlog = tmp_path / "cmdlog"
    cmdlog.write_text("")

    class Sandbox:
        def __init__(self):
            self.root = root
            self.source = source
            self.shipped = tmp_path / "shipped"

        def ships(self, files: dict[str, str] | None):
            """What the dedicated repository already has. None => empty repo."""
            if self.shipped.exists():
                shutil.rmtree(self.shipped)
            if files is None:
                return
            self.shipped.mkdir(parents=True)
            for name, body in files.items():
                (self.shipped / name).write_text(body)

        def run(self, *args, **knobs):
            env = dict(os.environ)
            env.update(
                PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
                REAL_GIT=shutil.which("git"),
                CMDLOG=str(cmdlog),
                SHIPPED_DIR=str(self.shipped),
                APP_REPO=APP_REPO,
            )
            env.update({k: str(v) for k, v in knobs.items()})
            cmdlog.write_text("")
            proc = subprocess.run(
                ["bash", str(root / "scripts" / PUBLISH_SCRIPT.name), "--tag", TAG, *args],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            calls = [line for line in cmdlog.read_text().splitlines() if line.strip()]
            return proc, calls

    return Sandbox()


def _app_uploads(calls: list[str]) -> list[str]:
    return [
        c
        for c in calls
        if c.startswith("gh release upload") and f"--repo {APP_REPO}" in c
    ]


def _dedicated_creates(calls: list[str]) -> list[str]:
    return [
        c
        for c in calls
        if c.startswith("gh release create") and f"--repo {DEDICATED_REPO}" in c
    ]


def _pushes(calls: list[str]) -> list[str]:
    return [c for c in calls if " push " in f" {c} "]


# --- no invocation may mutate a release unless the plugin actually changed ----


def test_dry_run_never_touches_either_release(sandbox):
    """The default is a dry run. It must not mutate anything, ever."""
    sandbox.ships(None)  # empty dedicated repo => the plugin is owed
    proc, calls = sandbox.run()
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout
    assert _app_uploads(calls) == [], "a dry run attached an asset to a published release"
    assert _dedicated_creates(calls) == []
    assert _pushes(calls) == []


def test_unchanged_plugin_under_auto_attaches_nothing(sandbox):
    """The common case: an app release that never touched the plugin.

    Attaching a zip here is what made Updates Manager report a plugin update on
    releases that changed no plugin code — the reason the old per-release asset
    workflow was deleted.
    """
    sandbox.ships(_plugin_files(PLUGIN_VERSION))  # identical => nothing owed
    proc, calls = sandbox.run("--auto", FAKE_DEDICATED_RELEASE_EXISTS=0)
    assert proc.returncode == 0, proc.stderr
    assert "Nothing owed" in proc.stdout
    assert _app_uploads(calls) == [], "an unchanged plugin attached an asset"
    assert _dedicated_creates(calls) == []


def test_unchanged_plugin_under_publish_fails_and_attaches_nothing(sandbox):
    sandbox.ships(_plugin_files(PLUGIN_VERSION))
    proc, calls = sandbox.run("--publish", FAKE_DEDICATED_RELEASE_EXISTS=0)
    assert proc.returncode != 0
    assert "unchanged" in proc.stderr
    assert _app_uploads(calls) == []


def test_version_mismatch_attaches_nothing(sandbox):
    """A changed plugin that forgot its version bump must fail before mutating."""
    sandbox.ships(None)
    for name, body in _plugin_files("1.0.0").items():
        (sandbox.source / name).write_text(body)
    proc, calls = sandbox.run("--auto")
    assert proc.returncode != 0
    assert "instead of" in proc.stderr
    assert _app_uploads(calls) == []
    assert _dedicated_creates(calls) == []


def test_missing_application_release_attaches_nothing(sandbox):
    sandbox.ships(None)
    proc, calls = sandbox.run("--auto", FAKE_APP_RELEASE_EXISTS=0)
    assert proc.returncode != 0
    assert "is not published" in proc.stderr
    assert _app_uploads(calls) == []
    assert _dedicated_creates(calls) == []


# --- a changed plugin must reach BOTH streams ---------------------------------


def test_changed_plugin_publishes_and_attaches_to_the_application_release(sandbox):
    """The fix: a plugin-changing release feeds the dedicated repo *and* the app release."""
    sandbox.ships(_plugin_files(PLUGIN_VERSION, extra="-- previously shipped\n"))
    proc, calls = sandbox.run("--auto")
    assert proc.returncode == 0, proc.stderr
    assert len(_dedicated_creates(calls)) == 1, calls
    uploads = _app_uploads(calls)
    assert len(uploads) == 1, f"expected exactly one application upload, got {uploads}"
    assert "cwasync.koplugin.zip" in uploads[0]


# --- the failure that used to strand a release forever ------------------------


def test_failed_application_upload_is_repaired_by_a_rerun(sandbox):
    """fork #1253 finding: a retry used to exit 0 with the asset still missing.

    Once the plugin has been pushed to the dedicated repository the owed-check
    goes quiet, so the second run took the "Nothing owed" exit and reported
    success while the application release stayed assetless. One transient API
    failure was enough to strand it permanently, silently — the same class of
    silent gap this script exists to close.
    """
    shipped_before = _plugin_files(PLUGIN_VERSION, extra="-- previously shipped\n")
    sandbox.ships(shipped_before)

    first, first_calls = sandbox.run("--auto", FAKE_APP_UPLOAD_RC=1)
    assert first.returncode != 0, "a failed application upload must be loud"
    assert len(_dedicated_creates(first_calls)) == 1, "the dedicated release did land"

    # The dedicated repository now ships the current plugin, so the owed-check
    # will find nothing owed on the retry. That is the trap.
    sandbox.ships(_plugin_files(PLUGIN_VERSION))
    second, second_calls = sandbox.run(
        "--auto", FAKE_DEDICATED_RELEASE_EXISTS=1, FAKE_APP_ASSET_PRESENT=0
    )
    assert second.returncode == 0, second.stderr
    uploads = _app_uploads(second_calls)
    assert len(uploads) == 1, (
        "the retry must reconcile the missing application asset; instead it "
        f"issued no upload (calls: {second_calls})"
    )
    assert any(
        c.startswith("gh release download") and f"--repo {DEDICATED_REPO}" in c
        for c in second_calls
    ), "reconcile must copy the asset the dedicated release actually shipped"


def test_primary_path_announces_replacing_an_existing_application_asset(sandbox):
    """Replacing an asset on a published release must not happen quietly.

    The only asset that should already be there is one an earlier run of the same
    tag placed — but the manual v4.1.16 upload proves other things can put one
    there, and `--clobber` will replace it either way.
    """
    sandbox.ships(_plugin_files(PLUGIN_VERSION, extra="-- previously shipped\n"))
    proc, calls = sandbox.run("--auto", FAKE_APP_ASSET_PRESENT=1)
    assert proc.returncode == 0, proc.stderr
    assert "replacing the existing cwasync.koplugin.zip" in proc.stdout, (
        "a publish that overwrites an existing public asset must say so"
    )
    assert len(_app_uploads(calls)) == 1


def test_no_workflow_attaches_a_release_asset_outside_the_publish_script():
    """The guarded script must stay the only thing that can attach the plugin.

    The behavioural tests above prove the *script* confines the upload to a
    genuine plugin change. They say nothing about a workflow doing it directly:
    a `gh release upload` step, or a release-asset action, added to any workflow
    would attach the plugin on every application tag and reproduce the
    false-update problem with the script untouched and every test green.

    The older guard in test_cwasync_updates_manager_compat.py only asserts that
    one specific filename (plugin-release-asset.yml) stays deleted, which a file
    with any other name walks straight past. This pins the behaviour instead of
    the filename.
    """
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows found — the guard would be vacuous"
    offenders = []
    for wf in workflows:
        body = wf.read_text()
        for needle in ("gh release upload", "upload-release-asset", "action-gh-release"):
            if needle in body:
                offenders.append(f"{wf.name}: {needle}")
    assert offenders == [], (
        "release assets must be attached only by scripts/publish-cwasync-plugin.sh, "
        "which attaches the plugin exclusively on releases that changed it; these "
        f"workflows attach assets directly: {offenders}"
    )


def test_rerun_after_a_complete_publish_mutates_nothing(sandbox):
    """Idempotent means idempotent: nothing to fix, so touch nothing."""
    sandbox.ships(_plugin_files(PLUGIN_VERSION))
    proc, calls = sandbox.run(
        "--auto", FAKE_DEDICATED_RELEASE_EXISTS=1, FAKE_APP_ASSET_PRESENT=1
    )
    assert proc.returncode == 0, proc.stderr
    assert "nothing to reconcile" in proc.stdout
    assert _app_uploads(calls) == [], "a complete release was mutated by a re-run"
    assert _dedicated_creates(calls) == []
