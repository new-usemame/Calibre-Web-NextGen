# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A changed KOReader plugin must declare a version ahead of the last release.

``scripts/publish-cwasync-plugin.sh`` already refuses to publish a plugin whose
declared version does not equal the tag it is publishing for. That check is
correct, but it runs from ``plugin-release-publish.yml``, which fires on
``release: published`` — **after** the tag exists. A published tag cannot be
retracted, so discovering the mismatch there costs a whole extra version.

That is not hypothetical. #1427 changed the plugin materially (browser-to-KOReader
progress only works on a device running the new plugin), while ``_meta.lua`` and
``main.lua`` both still declared ``4.1.25`` — six releases behind. The next tag
would have published, the plugin job would have failed, and every device would
have stayed on a plugin that cannot do the thing the release notes announced.

The bump was tracked as prose in a notes file. The publish workflow's own header
says it exists because "a release step that depends on someone remembering it is
a step that gets skipped" — this test applies that same reasoning one level up,
to the bump the publish depends on.

**The invariant, and why it is shaped this way.** A bump is *not* owed on every
release. The plugin deliberately holds its version while it is unchanged, so
Updates Manager does not report a phantom update on app tags that never touched
it (that regression is why ``plugin-release-asset.yml`` was deleted). So the rule
keys off the source, not the calendar:

* plugin identical to what the newest release tag shipped -> no bump owed, and
  the declared version must not have run ahead of that tag either.
* plugin differs -> the next tag owes a publish, so the declared version must
  already be greater than the newest released version.

"Differs" deliberately ignores the two ``version = "..."`` lines themselves.
Those lines live inside the directory being compared, so a naive whole-directory
diff reports "changed" the instant anyone edits the version — which would make
the second rule above unreachable, and would let a version bumped with no
accompanying source change satisfy the first. Comparing substance keeps the two
rules independent of each other.

This compares against the working tree, not ``HEAD``, so an uncommitted plugin
edit is caught in the same run that makes it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_RELPATH = "koreader/plugins/cwasync.koplugin"
PLUGIN = REPO_ROOT / PLUGIN_RELPATH

VERSION_RE = re.compile(r'version\s*=\s*"([0-9]+(?:\.[0-9]+)*)"')
RELEASE_TAG_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(REPO_ROOT)) + args,
        capture_output=True,
        text=True,
        check=False,
    )


def _declared_version(filename: str) -> str:
    text = (PLUGIN / filename).read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    assert match is not None, f"{filename} declares no version = \"...\" line"
    return match.group(1)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _release_tags() -> list[str]:
    result = _git("tag", "--list", "v*")
    assert result.returncode == 0, f"git tag failed: {result.stderr.strip()}"
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if RELEASE_TAG_RE.match(line.strip())
    ]


def _without_version_lines(text: str) -> str:
    """Blank the declared version so it cannot masquerade as a source change."""
    return VERSION_RE.sub('version = "<pinned>"', text)


def _plugin_files_at(tag: str) -> dict[str, str]:
    result = _git("ls-tree", "-r", "--name-only", tag, "--", PLUGIN_RELPATH)
    assert result.returncode == 0, (
        f"git ls-tree failed for {tag}: {result.stderr.strip()}"
    )
    files: dict[str, str] = {}
    for path in result.stdout.split("\n"):
        path = path.strip()
        if not path:
            continue
        blob = _git("show", f"{tag}:{path}")
        assert blob.returncode == 0, f"git show failed for {tag}:{path}"
        files[path] = blob.stdout
    return files


def _plugin_files_now() -> dict[str, str]:
    return {
        str(path.relative_to(REPO_ROOT)): path.read_text(
            encoding="utf-8", errors="replace"
        )
        for path in sorted(PLUGIN.rglob("*"))
        if path.is_file() and path.name != ".DS_Store"
    }


def _plugin_substance_changed_since(tag: str) -> bool:
    at_tag = _plugin_files_at(tag)
    now = _plugin_files_now()
    if set(at_tag) != set(now):
        return True
    return any(
        _without_version_lines(at_tag[path]) != _without_version_lines(now[path])
        for path in at_tag
    )


def _newest_release_tag() -> str:
    tags = _release_tags()
    # Deliberately a hard failure rather than a skip. A skip here would pass
    # silently the day CI stops fetching tags, which is precisely when the gate
    # stops protecting anything -- the failure mode this file exists to close.
    assert tags, (
        "no vX.Y.Z release tags are present in this checkout, so the plugin "
        "version gate cannot run. CI's fast-tests job checks out with "
        "fetch-depth: 0; a local shallow clone needs `git fetch --tags`."
    )
    return max(tags, key=lambda tag: _version_key(tag[1:]))


def test_meta_and_main_declare_the_same_version() -> None:
    """The publish script reads both files and requires them to agree."""
    meta = _declared_version("_meta.lua")
    main = _declared_version("main.lua")
    assert meta == main, (
        f"_meta.lua declares {meta} but main.lua declares {main}. "
        "publish-cwasync-plugin.sh reads both and refuses a mismatch."
    )


def test_changed_plugin_declares_a_version_ahead_of_the_last_release() -> None:
    """A plugin that moved since the last tag must already be bumped past it.

    Red before the #1427 bump: the plugin differed from v4.1.31 while declaring
    4.1.25, so the next tag would have reached the publish workflow's hard error
    with the tag already immovable.
    """
    newest_tag = _newest_release_tag()
    newest_version = newest_tag[1:]
    declared = _declared_version("_meta.lua")

    # Working tree vs the tag, so an uncommitted plugin edit counts as a change.
    if _plugin_substance_changed_since(newest_tag):
        assert _version_key(declared) > _version_key(newest_version), (
            f"The plugin has changed since {newest_tag}, so the next release "
            f"owes the dedicated repository a publish -- but it still declares "
            f"{declared}. publish-cwasync-plugin.sh will hard-fail on a version "
            f"that is not the tag, and it runs after the tag is published, "
            f"which cannot be undone. Bump the version line in both "
            f"{PLUGIN_RELPATH}/_meta.lua and {PLUGIN_RELPATH}/main.lua to the "
            f"next release tag before cutting it."
        )
    else:
        assert _version_key(declared) <= _version_key(newest_version), (
            f"The plugin is identical to the one {newest_tag} shipped, yet it "
            f"declares {declared} -- ahead of the newest release. Updates "
            f"Manager compares installed against latest, so a version that ran "
            f"ahead of its own source offers devices an update that does not "
            f"exist."
        )
