# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the legacy KOReader plugin repository notice.

``new-usemame/cwasync.koplugin`` did not redirect when the plugin distribution
moved to ``new-usemame/cwngsync.koplugin``. An update manager left on the old
repository therefore reports "no new release" forever, which looks exactly like
an up-to-date installation. Both user-facing update guides must make that silent
stall and the required manual repository change explicit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_REPOSITORY = "cwasync.koplugin"

UPDATE_GUIDES = (
    pytest.param(
        REPO_ROOT / "README.md",
        "**Keeping the plugin updated.**",
        "### Kobo sync",
        id="readme",
    ),
    pytest.param(
        REPO_ROOT / "cps" / "templates" / "kosync_plugin.html",
        "Keeping the Plugin Up to Date",
        "Accessing the Plugin",
        id="kosync-page",
    ),
)


def _update_manager_guidance(path: Path, start: str, end: str) -> str:
    text = path.read_text()
    start_offset = text.index(start)
    end_offset = text.index(end, start_offset)
    return text[start_offset:end_offset]


@pytest.mark.parametrize(("path", "start", "end"), UPDATE_GUIDES)
def test_legacy_repository_is_named_as_frozen_in_update_guidance(
    path: Path,
    start: str,
    end: str,
):
    guidance = _update_manager_guidance(path, start, end)
    assert LEGACY_REPOSITORY in guidance, (
        f"{path.name}'s update-manager guidance must name the legacy "
        f"{LEGACY_REPOSITORY} repository so users know to repoint manually"
    )
    assert "frozen" in guidance.lower(), (
        f"{path.name}'s update-manager guidance must say the legacy repository "
        "is frozen; otherwise 'no new release' still looks like success"
    )
