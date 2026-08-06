# SPDX-License-Identifier: GPL-3.0-or-later
"""Package metadata must route a reader to *our* tracker, not upstream's (#1361).

Anyone who inspects the installed distribution -- ``pip show``, a package
index, the About page's dependency view -- follows ``[project.urls]``. When
those pointed at ``crocodilestick/Calibre-Web-Automated`` a user with a bug in
*our* build was sent to a tracker that cannot act on it.

#1298 repointed them. These tests keep them repointed, and pin the one that
was still wrong afterwards: ``Source Code`` pointed at the releases page, so
"take me to the source" landed on a list of tarballs rather than the code.
"""

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

FORK_REPO = "https://github.com/new-usemame/Calibre-Web-NextGen"
UPSTREAMS = ("crocodilestick/Calibre-Web-Automated", "janeczku/calibre-web")


@pytest.fixture(scope="module")
def urls():
    if sys.version_info < (3, 11):  # pragma: no cover - CI runs 3.11+
        pytest.skip("tomllib requires Python 3.11+")
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["urls"]


def test_source_code_points_at_the_repository_not_the_releases_page(urls):
    """The regression #1298 left behind: Source Code -> /releases."""
    assert urls["Source Code"] == FORK_REPO, (
        "'Source Code' must be the repository root. Pointing it at /releases "
        "sends someone looking for the code to a list of tarballs; "
        "'Release Management' already covers the releases page."
    )


def test_no_url_sends_a_reader_to_an_upstream_tracker(urls):
    for key, value in urls.items():
        for upstream in UPSTREAMS:
            assert upstream not in value, (
                f"[project.urls] {key} = {value} points at {upstream}. "
                "A bug in this build must land on this fork's tracker (#1361)."
            )


def test_bug_tracker_is_this_forks_issue_tracker(urls):
    assert urls["Bug Tracker"] == f"{FORK_REPO}/issues"


def test_every_url_is_under_this_fork(urls):
    for key, value in urls.items():
        assert value.startswith(FORK_REPO), f"[project.urls] {key} = {value} leaves the fork"


def test_maintainers_do_not_name_upstream(urls):
    with PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    names = [m.get("name", "") for m in project.get("maintainers", [])]
    assert names, "maintainers must not be empty -- packaging drops the field silently"
    for name in names:
        assert "CrocodileStick" not in name, (
            "maintainers still credits upstream; a packaged build of this fork "
            "must not direct correspondence there (#1361)."
        )
