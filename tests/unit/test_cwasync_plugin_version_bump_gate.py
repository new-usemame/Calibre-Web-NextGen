# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A KOReader plugin publish owed by a release must declare that release's version.

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
it (that regression is why ``plugin-release-asset.yml`` was deleted). The gate
therefore asks two independent questions:

* plugin substance differs from the newest release tag -> a publish is owed, so
  the declared version must equal the version being cut.
* plugin substance is unchanged -> no source-driven bump is owed. Keep every
  declaration the old gate accepted (at or behind the newest tag), and allow
  exactly one new value: equality with the version being cut.

The version being cut comes from the newest dated ``CHANGELOG.md`` section, not
from git tags. The changelog is the branch-local, independent artifact rolled by
the pre-tag bookkeeping commit, so it exists before the tag and pins the exact
version this commit will ship. A tag-anchored upper bound rejects a legitimate
pre-tag bump as a phantom update because the tag necessarily still names the
previous release.

More importantly, the tag baseline can drift from the publisher's real baseline:
``publish-cwasync-plugin.sh`` compares against the dedicated plugin repository,
not against this repository's newest tag. Once that repository falls behind, a
publish is owed even when our plugin is identical to our newest tag. The old gate
missed that state, the post-release publish rejected the stale declaration, and
that rejection prevented the dedicated repository from advancing -- making the
drift self-perpetuating rather than self-healing. Unit tests cannot query that
repository, so allowing an unchanged plugin to catch up to (but never pass) the
version being cut is the hermetic pre-tag rule that admits the required repair.

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
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_RELPATH = "koreader/plugins/cwasync.koplugin"
PLUGIN = REPO_ROOT / PLUGIN_RELPATH
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r'version\s*=\s*"([0-9]+(?:\.[0-9]+)*)"')
RELEASE_TAG_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
RELEASE_HEADING_RE = re.compile(
    r"^## \[v(\d+\.\d+\.\d+)\]\s+[-–]\s+\d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)


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


def _version_being_cut() -> str:
    """Read the pre-tag release version from the newest dated changelog section."""
    versions = RELEASE_HEADING_RE.findall(CHANGELOG.read_text(encoding="utf-8"))
    assert versions, (
        "CHANGELOG.md has no dated vX.Y.Z release section, so the plugin "
        "version gate cannot determine which release is being cut"
    )
    return versions[0]


def test_meta_and_main_declare_the_same_version() -> None:
    """The publish script reads both files and requires them to agree."""
    meta = _declared_version("_meta.lua")
    main = _declared_version("main.lua")
    assert meta == main, (
        f"_meta.lua declares {meta} but main.lua declares {main}. "
        "publish-cwasync-plugin.sh reads both and refuses a mismatch."
    )


def test_plugin_declaration_matches_version_being_cut() -> None:
    """The declaration must fit the source delta and the release being cut.

    Red before the #1427 bump: the plugin differed from v4.1.31 while declaring
    4.1.25, so the next tag would have reached the publish workflow's hard error
    with the tag already immovable.
    """
    newest_tag = _newest_release_tag()
    release_version = _version_being_cut()
    declared = _declared_version("_meta.lua")

    # Working tree vs the tag, so an uncommitted plugin edit counts as a change.
    if _plugin_substance_changed_since(newest_tag):
        assert declared == release_version, (
            f"The plugin has changed since {newest_tag}, so the next release "
            f"owes the dedicated repository a publish -- but it still declares "
            f"{declared} while the release being cut is {release_version}. "
            f"publish-cwasync-plugin.sh will hard-fail on a version that is not "
            f"the tag, and it runs after the tag is published, which cannot be "
            f"undone. Set the version line in both "
            f"{PLUGIN_RELPATH}/_meta.lua and {PLUGIN_RELPATH}/main.lua to the "
            f"version being cut before tagging it."
        )
    else:
        newest_version = newest_tag[1:]
        assert (
            _version_key(declared) <= _version_key(newest_version)
            or declared == release_version
        ), (
            f"The plugin is identical in substance to the one {newest_tag} "
            f"shipped, yet it declares {declared}, which is neither at or "
            f"behind {newest_version} nor equal to the {release_version} "
            f"release being cut. Updates Manager compares installed against "
            f"latest, so a version no release will ship offers devices an "
            f"update that does not exist."
        )


def test_pretag_bump_to_version_being_cut_is_allowed(monkeypatch) -> None:
    """A legitimate pre-tag bump must not be rejected as a phantom update."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_newest_release_tag", lambda: "v4.1.36")
    monkeypatch.setattr(module, "_declared_version", lambda _filename: "4.1.37")
    monkeypatch.setattr(
        module, "_plugin_substance_changed_since", lambda _tag: False
    )
    monkeypatch.setattr(
        module, "_version_being_cut", lambda: "4.1.37", raising=False
    )

    test_plugin_declaration_matches_version_being_cut()


def test_changed_plugin_without_a_bump_is_rejected(monkeypatch) -> None:
    """Keep the original #1427 failure mode pinned as an executed predicate."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_newest_release_tag", lambda: "v4.1.31")
    monkeypatch.setattr(module, "_version_being_cut", lambda: "4.1.32")
    monkeypatch.setattr(module, "_declared_version", lambda _filename: "4.1.25")
    monkeypatch.setattr(
        module, "_plugin_substance_changed_since", lambda _tag: True
    )

    with pytest.raises(AssertionError, match="still declares 4.1.25"):
        test_plugin_declaration_matches_version_being_cut()


@pytest.mark.parametrize("wrong_version", ["4.1.36.1", "4.1.38"])
def test_plugin_cannot_declare_a_version_no_release_will_ship(
    monkeypatch, wrong_version: str
) -> None:
    """Intermediate and future declarations both remain forbidden."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_newest_release_tag", lambda: "v4.1.36")
    monkeypatch.setattr(module, "_version_being_cut", lambda: "4.1.37")
    monkeypatch.setattr(
        module, "_declared_version", lambda _filename: wrong_version
    )
    monkeypatch.setattr(
        module, "_plugin_substance_changed_since", lambda _tag: False
    )

    with pytest.raises(AssertionError, match="no release will ship"):
        test_plugin_declaration_matches_version_being_cut()
