# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local development instance state must never become committable.

``docker-compose.worktree.yml`` accepts an arbitrary ``CWNG_DEV_STATE`` path.
If that path is inside the checkout, the application writes databases, secrets,
annotation backups, and repaired book copies below ``local-dev/``.  A list of
known rig directory names cannot protect the next rig, so ``local-dev/`` must be
closed by default with explicit exceptions for its repository source files.
"""

from fnmatch import fnmatch
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_LOCAL_DEV_SOURCES = {
    "local-dev/docker-compose.koreader.yml",
    "local-dev/docker-compose.worktree.yml",
    "local-dev/koreader-emulator/Dockerfile",
    "local-dev/koreader-emulator/entrypoint.sh",
    "local-dev/private-e2e-rig.sh",
}
REPRESENTATIVE_STATE_PATHS = (
    "local-dev/.rig-anything/config/app.db",
    "local-dev/whatever-rig/config/.key",
)
INSTANCE_STATE_PATTERNS = (
    "app.db*",
    "*.key",
    "*.json.gz",
    "*.kepub",
    "client_secrets.json",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_arbitrary_local_dev_state_directories_are_ignored():
    for path in REPRESENTATIVE_STATE_PATHS:
        result = _git("check-ignore", "-q", "--no-index", path)
        assert result.returncode == 0, (
            f"{path} is not ignored; every new local-dev rig directory must be "
            f"ignored without another named .gitignore rule (stderr={result.stderr!r})"
        )


@pytest.mark.unit
def test_tracked_local_dev_sources_remain_committable():
    for path in sorted(TRACKED_LOCAL_DEV_SOURCES):
        result = _git("check-ignore", "-q", "--no-index", path)
        assert result.returncode == 1, (
            f"{path} is ignored and could not be added in a fresh checkout "
            f"(returncode={result.returncode}, stderr={result.stderr!r})"
        )


@pytest.mark.unit
def test_only_source_files_are_tracked_under_local_dev():
    result = _git("ls-files", "--", "local-dev")
    assert result.returncode == 0, result.stderr

    tracked = set(result.stdout.splitlines())
    state_files = sorted(
        path
        for path in tracked
        if any(fnmatch(path.rsplit("/", 1)[-1], pattern)
               for pattern in INSTANCE_STATE_PATTERNS)
    )

    assert not state_files, (
        "instance state is already tracked under local-dev: "
        + ", ".join(state_files)
    )
    assert tracked == TRACKED_LOCAL_DEV_SOURCES, (
        "local-dev must contain exactly the explicitly committable source "
        f"files; unexpected tracked paths: {sorted(tracked ^ TRACKED_LOCAL_DEV_SOURCES)}"
    )
