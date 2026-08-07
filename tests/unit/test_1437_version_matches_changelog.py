# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #1437 — the checked-in VERSION must not drift
behind the newest released CHANGELOG section.

Reported by @chloeroform: ``git reset --hard v4.1.32 && cat VERSION`` printed
``4.1.31``. The tag froze a VERSION file a release out of date, and so did
v4.1.31 before it.

Why it matters, and to whom. In the Docker image the version is stamped by the
build (``ENV CWA_INSTALLED_VERSION=${VERSION}`` from the release workflow's
build arg), so a container reports its tag correctly and none of this is
visible there. Everyone else reads it through packaging:
``pyproject.toml`` resolves ``version = {file = ["VERSION"]}``, and
``cps.constants._get_version()`` reads that back out with
``importlib.metadata.version("calibre-web-automated")``. So a source or pip
install off the v4.1.32 tag reports ``v4.1.31``, which the updater compares
against the newest published release and turns into a permanent "update
available" nag on a checkout that is already current — the same symptom
#1108 produced, arrived at from the other direction.

The drift was already preventable. ``scripts/preflight-release-tag.sh``
(outside this repo, in the autopilot workspace) carries exactly this check and
would have refused the v4.1.32 tag; it was committed ~18h before that tag and
simply never ran, because its only caller is a sentence of prose in LOOP.md.
A gate whose invocation depends on somebody remembering to invoke it is
documentation. This module is the enforcing copy: it runs in CI on every pull
request, so it goes red on the *pre-tag bookkeeping PR* — which is upstream of
the tag, and therefore the last place the fix is still free. Tags are immutable
and published releases are never retracted, so after the tag there is no repair,
only the next version.

The invariant is deliberately anchored to an INDEPENDENT artifact. Asserting
VERSION against a constant restated in this file would compare a value to
itself and stay green through the exact drift it exists to catch. CHANGELOG.md
is maintained by a separate step of the release train (the ``[Unreleased]``
roll), so the two can only agree when both halves of the bookkeeping actually
happened. Git tags would be an equally independent source, but they are
repo-global rather than branch-local and are not reliably fetched in CI — the
same reason test_changelog_tag_sections.py keeps a committed fallback ledger.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSION_FILE = _REPO_ROOT / "VERSION"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# Same shape scripts/preflight-release-tag.sh matches, and the same shape
# test_changelog_tag_sections.py already treats as a released section. An
# undated "## [Unreleased]" deliberately does not match: it is the staging area
# for the *next* release, so it must not drag VERSION forward with it.
_RELEASE_HEADING = re.compile(
    r"^## \[v(\d+\.\d+\.\d+)\]\s+[-–]\s+\d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)


def _released_versions() -> list[str]:
    """Every dated release section, newest first (file order is newest first)."""
    return _RELEASE_HEADING.findall(_CHANGELOG.read_text(encoding="utf-8"))


def _version_file() -> str:
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


def test_version_file_matches_the_newest_released_changelog_section():
    """The bug #1437 reports, stated as an invariant.

    Red on the v4.1.32 tree (VERSION 4.1.31 vs newest section v4.1.32); green
    once the bookkeeping bumps both together.
    """
    released = _released_versions()
    newest = released[0]
    assert _version_file() == newest, (
        f"VERSION reads {_version_file()!r} but the newest released section in "
        f"CHANGELOG.md is v{newest}. A release rolled its changelog without "
        "bumping VERSION, so a source/pip install off that tag will report the "
        "previous version and nag about an update it already has (#1437). Bump "
        "VERSION in the same commit as the changelog roll."
    )


def test_the_changelog_scan_actually_finds_releases():
    """A regex that silently matched nothing would hold the test above green.

    The assertion is `VERSION == released[0]`, so a parse returning an empty
    list raises IndexError rather than passing — but a regex that drifted to
    match only *one* stale heading would still look fine. Pin that the scan
    sees the real, long release history.
    """
    released = _released_versions()
    assert len(released) > 20, (
        f"only {len(released)} release sections parsed out of CHANGELOG.md; the "
        "heading regex has drifted from the format the file actually uses"
    )


def test_released_sections_are_ordered_newest_first():
    """`released[0]` is only "newest" if the file is ordered, so pin the order.

    Keep-a-Changelog puts the newest release directly under [Unreleased]. If
    that ever inverted, the invariant above would quietly start comparing
    VERSION against the *oldest* release and pass for years.
    """
    released = [tuple(map(int, v.split("."))) for v in _released_versions()]
    assert released == sorted(released, reverse=True), (
        "CHANGELOG.md release sections are not in descending version order"
    )


def test_version_file_is_a_bare_pep440_release():
    """No leading 'v', no whitespace, three numeric components.

    setuptools reads this file straight through ``dynamic = {file = ...}`` and
    rejects anything that is not a PEP 440 version, which would break the wheel
    build rather than merely misreport. ``preflight-release-tag.sh`` also
    compares it against the bare form of the tag, so a stray 'v' here would
    make that gate refuse a correct release.
    """
    raw = _VERSION_FILE.read_text(encoding="utf-8")
    assert raw == raw.strip() + "\n", (
        "VERSION must hold exactly one line with a trailing newline; "
        f"got {raw!r}"
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", raw.strip()), (
        f"VERSION must be a bare X.Y.Z release, got {raw.strip()!r}"
    )


def test_pyproject_still_sources_the_version_from_this_file():
    """The invariant only protects users while packaging reads this file.

    If the dynamic-version pointer moved, VERSION would become decorative and
    this whole module would be guarding nothing. #1231 pinned the same line for
    its own reasons; pin it here too, so that removing that test does not
    silently take this one's meaning with it.
    """
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = {file = ["VERSION"]}' in pyproject
