# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep every canonical-era published tag represented in CHANGELOG.md."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
CANONICAL_CHANGELOG_FIRST_RELEASE = "v4.0.147"

VERSION = re.compile(r"v\d+\.\d+\.\d+")
RELEASE_HEADING = re.compile(
    r"^## \[(v\d+\.\d+\.\d+)\]\s+[-–]\s+\d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)
WHATS_NEW_VERSION = re.compile(
    r"^\s*version:\s*'(v\d+\.\d+\.\d+)',\s*$",
    re.MULTILINE,
)


def _version_tuple(version: str) -> tuple[int, int, int]:
    return tuple(map(int, version.removeprefix("v").split(".")))


def _missing_sections(published: set[str], headings: set[str]) -> list[str]:
    """Return canonical-era published versions absent from the changelog."""
    boundary = _version_tuple(CANONICAL_CHANGELOG_FIRST_RELEASE)
    covered_tags = {
        version for version in published if _version_tuple(version) >= boundary
    }
    return sorted(covered_tags - headings, key=_version_tuple)


def _published_versions(root: Path = ROOT) -> tuple[set[str], str]:
    """Use tags reachable from this checkout, or its committed release ledger."""
    result = subprocess.run(
        ["git", "tag", "--list", "v*", "--merged", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    tags = {tag for tag in result.stdout.splitlines() if VERSION.fullmatch(tag)}
    if result.returncode == 0 and tags:
        return tags, "git tags reachable from HEAD"

    whats_new = root / "frontend" / "src" / "data" / "whatsNew.ts"
    assert whats_new.is_file(), (
        "cannot determine published releases: no semver git tags are available "
        f"and the committed ledger is missing at {whats_new}"
    )
    versions = set(WHATS_NEW_VERSION.findall(whats_new.read_text(encoding="utf-8")))
    assert versions, (
        "cannot determine published releases: no semver git tags are available "
        "and the committed What's New ledger contains no release versions"
    )
    return versions, "committed What's New ledger (git tags unavailable)"


def test_every_published_version_has_a_changelog_section():
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = set(RELEASE_HEADING.findall(text))
    assert headings, "CHANGELOG.md contains no dated semver release sections"
    published, source = _published_versions()
    missing = _missing_sections(published, headings)
    assert not missing, (
        f"published release(s) from {source}, at or after the canonical "
        f"CHANGELOG boundary {CANONICAL_CHANGELOG_FIRST_RELEASE}, have no "
        "matching dated section: " + ", ".join(missing)
    )


def test_detector_rejects_the_real_missing_tag_section_shape():
    """Pin the correspondence detector to the v4.1.27 regression."""
    headings_after_revert = {"v4.1.26", "v4.1.25"}
    assert _missing_sections({"v4.1.27"}, headings_after_revert) == ["v4.1.27"]


def test_reachability_filter_keeps_ancestor_tags_and_excludes_later_tags(tmp_path):
    """Non-vacuity: filtering out a future tag must retain the branch's own tag."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--quiet")
    marker = repo / "marker"
    marker.write_text("reachable\n", encoding="utf-8")
    git("add", "marker")
    git(
        "-c",
        "user.name=changelog-test",
        "-c",
        "user.email=changelog-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "reachable release",
    )
    reachable_commit = git("rev-parse", "HEAD").stdout.strip()
    git("tag", "v4.1.38")

    marker.write_text("future\n", encoding="utf-8")
    git("add", "marker")
    git(
        "-c",
        "user.name=changelog-test",
        "-c",
        "user.email=changelog-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "future release",
    )
    git("tag", "v4.1.40")
    git("switch", "--quiet", "--detach", reachable_commit)

    assert set(git("tag", "--list", "v*").stdout.splitlines()) == {
        "v4.1.38",
        "v4.1.40",
    }
    versions, source = _published_versions(root=repo)
    assert versions == {"v4.1.38"}
    assert source == "git tags reachable from HEAD"
    assert _missing_sections(versions, set()) == ["v4.1.38"]


def test_tagless_checkout_uses_committed_release_ledger(monkeypatch):
    """A source archive or offline shallow clone remains strict, never skips."""

    class NoTags:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: NoTags())
    versions, source = _published_versions()
    assert "v4.1.27" in versions
    assert source == "committed What's New ledger (git tags unavailable)"


def test_failed_reachability_query_uses_committed_release_ledger(monkeypatch):
    """A failed Git query also stays strict through the branch-local ledger."""

    class FailedQuery:
        returncode = 128
        stdout = "v4.1.40\n"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FailedQuery())
    versions, source = _published_versions()
    assert "v4.1.27" in versions
    assert source == "committed What's New ledger (git tags unavailable)"


def test_at_most_one_version_section_is_untagged():
    """A release is sectioned first, then tagged. Only the in-flight one may lack a tag.

    The sibling test above checks tag -> section: every published tag has a
    changelog section. Nothing checked the REVERSE, and the reverse is where work
    gets stranded.

    OBSERVED 2026-08-03: `## [v4.1.29] - 2026-08-03` was written with five
    user-facing entries and `[Unreleased]` emptied, but the tag was deliberately
    held (a refuter round found a Kobo read-state blocker). That window is normal
    and short. What is NOT normal is a second release being prepped on top of an
    abandoned one: the first section then claims a release that never existed, and
    its entries are announced nowhere, because the next release's notes are built
    from `[Unreleased]` — which the abandoned prep already emptied.

    Deliberately clock-free. An age-based rule would need a wall-clock and would
    red every PR during a legitimately long hold, which is how a guard gets muted.
    "At most one in flight" cannot fire on the normal window and cannot flake.
    The operational question — *this* release has been held too long — belongs in
    the autopilot floor, not in a repo unit test.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = set(RELEASE_HEADING.findall(text))
    assert headings, "CHANGELOG.md contains no dated semver release sections"
    published, source = _published_versions()
    boundary = _version_tuple(CANONICAL_CHANGELOG_FIRST_RELEASE)

    untagged = sorted(
        (v for v in headings if _version_tuple(v) >= boundary and v not in published),
        key=_version_tuple,
    )
    assert len(untagged) <= 1, (
        "more than one changelog release section has no published tag "
        f"(source: {source}): {untagged}.\n"
        "A sectioned-but-never-tagged release strands its entries: [Unreleased] was "
        "emptied when it was cut, so the next release's notes cannot mention them. "
        "Either tag the older one or fold its entries back into [Unreleased]."
    )


def test_the_untagged_detector_catches_two_abandoned_preps():
    """Non-vacuity: the assertion above is worthless if it cannot count past one."""
    published = {"v4.1.28"}
    headings = {"v4.1.28", "v4.1.29", "v4.1.30"}
    boundary = _version_tuple(CANONICAL_CHANGELOG_FIRST_RELEASE)
    untagged = sorted(
        (v for v in headings if _version_tuple(v) >= boundary and v not in published),
        key=_version_tuple,
    )
    assert untagged == ["v4.1.29", "v4.1.30"], untagged
    assert len(untagged) > 1, "the shape the guard must reject is not being detected"

    # And the normal in-flight window must stay green, or the guard blocks releases.
    one_in_flight = {"v4.1.28", "v4.1.29"}
    still = [v for v in one_in_flight if v not in published]
    assert len(still) <= 1, "a single in-flight prep must not trip the guard"
