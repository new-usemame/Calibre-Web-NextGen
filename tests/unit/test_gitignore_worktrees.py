"""A git worktree created inside the checkout must be ignored.

`git worktree add .worktrees/<name>` run from inside the checkout leaves an
untracked directory behind. That makes `git status --porcelain` non-empty, and
the autopilot preflight treats a dirty checkout as a reason to stop — so one
stray worktree fails every tick until a human notices. The failure reads as a
legitimate "someone left work in the tree" warning rather than a wedge, which
is what makes it expensive.

Ignoring it is also strictly safer than leaving it visible: while the directory
is untracked, a plain `git clean -fd` in the checkout would delete a live
worktree along with any uncommitted work inside it.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# `.git` is a directory in a normal clone and a file in a linked worktree.
pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(), reason="not a git checkout"
)


def _check_ignore(path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_worktrees_directory_is_ignored():
    assert _check_ignore(".worktrees/").returncode == 0, (
        ".worktrees/ is not ignored. A worktree created inside the checkout "
        "makes `git status --porcelain` non-empty, which wedges the autopilot "
        "preflight's clean-tree gate on every tick."
    )


def test_files_inside_a_worktree_are_ignored_too():
    """Ignoring the directory must cover its contents, not just its name."""
    assert _check_ignore(".worktrees/example-branch/cps/admin.py").returncode == 0, (
        "a file inside .worktrees/ is still visible to git status; the ignore "
        "rule must cover the directory's contents"
    )
