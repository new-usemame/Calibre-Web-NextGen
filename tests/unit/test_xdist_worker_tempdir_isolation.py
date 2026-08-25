# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Each xdist worker gets its own tempdir, so temp-dir-keyed singletons in
scripts/ cannot collide across workers and surface as test failures."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from tests.conftest import _isolate_worker_tempdir

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_worker_tempdir_is_private_and_both_channels_agree(monkeypatch, tmp_path):
    """Half-application is the failure mode this pins.

    ``gettempdir()`` caches into ``tempfile.tempdir``, so setting only the
    environment variable leaves this process on the shared path and setting only
    the module global leaves subprocesses on it.  Either alone fixes half the
    collisions and reads as "the fixture does not quite work".
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    root = _isolate_worker_tempdir()

    assert root is not None
    assert "gw7" in root, root
    assert tempfile.gettempdir() == root, "this process still resolves the shared tempdir"
    assert os.environ["TMPDIR"] == root, "subprocesses still inherit the shared tempdir"
    assert Path(root).is_dir(), "the private tempdir was never created"


@pytest.mark.unit
def test_two_workers_get_different_tempdirs(monkeypatch, tmp_path):
    seen = []
    for worker in ("gw0", "gw1"):
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        seen.append(_isolate_worker_tempdir())
    assert seen[0] != seen[1], seen


@pytest.mark.unit
def test_serial_runs_keep_the_system_tempdir(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert _isolate_worker_tempdir() is None
    assert tempfile.gettempdir() == str(tmp_path), "a serial run must not be redirected"


@pytest.mark.unit
def test_the_singletons_this_protects_still_key_on_gettempdir():
    """If a guard stops deriving its path from gettempdir(), this fixture stops
    covering it -- and the coverage loss would otherwise be silent."""
    expected = {
        "scripts/ingest_processor.py": r"tempfile\.gettempdir\(\)",
        "scripts/cover_enforcer.py": r"tempfile\.gettempdir\(\)",
        "scripts/convert_library.py": r"tempfile\.gettempdir\(\)",
        "scripts/kindle_epub_fixer.py": r"tempfile\.gettempdir\(\)",
    }
    missing = [
        rel for rel, pattern in expected.items()
        if not re.search(pattern, (REPO / rel).read_text(encoding="utf-8"))
    ]
    assert not missing, (
        f"these no longer key a lock on gettempdir(): {missing} -- either they "
        f"moved to a safe path (drop them here) or they moved to another shared "
        f"one (this fixture no longer protects them)"
    )
