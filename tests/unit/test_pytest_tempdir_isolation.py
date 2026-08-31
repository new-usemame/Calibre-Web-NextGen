# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every pytest process gets its own tempdir, so the machine-global singletons
in scripts/ cannot be contended by anything outside this process."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.conftest import _isolate_pytest_tempdir

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_tempdir_is_private_and_both_channels_agree(monkeypatch, tmp_path):
    """Half-application is the failure mode this pins.

    ``gettempdir()`` caches into ``tempfile.tempdir``, so setting only the
    environment variable leaves this process on the shared path and setting only
    the module global leaves subprocesses on it.  Either alone fixes half the
    contention and reads as "the fixture does not quite work".
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    root = _isolate_pytest_tempdir()

    assert str(os.getpid()) in root, root
    assert tempfile.gettempdir() == root, "this process still resolves the shared tempdir"
    assert os.environ["TMPDIR"] == root, "subprocesses still inherit the shared tempdir"
    assert Path(root).is_dir(), "the private tempdir was never created"


@pytest.mark.unit
def test_it_applies_without_xdist(monkeypatch, tmp_path):
    """The measured failure was a SERIAL run losing to an outside lock holder.

    An earlier version returned early when PYTEST_XDIST_WORKER was absent, which
    skipped exactly the case that was reproduced: 12 failed, 57 passed serially
    with the lock held by an unrelated process.
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    root = _isolate_pytest_tempdir()

    assert root is not None, "a serial run is not exempt: the lock is machine-global"
    assert tempfile.gettempdir() == root


@pytest.mark.unit
def test_the_key_is_the_process_not_the_worker_id(monkeypatch, tmp_path):
    """Two concurrent sessions both have a ``gw0``; they do not share a PID."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    root = _isolate_pytest_tempdir()

    assert "gw0" not in Path(root).name, (
        "keying on the worker id makes two concurrent sessions collide on gw0"
    )
    assert Path(root).name == str(os.getpid())


@pytest.mark.unit
def test_the_singletons_this_protects_still_key_on_gettempdir():
    """If a guard stops deriving its path from gettempdir(), this fixture stops
    covering it -- and the coverage loss would otherwise be silent."""
    guarded = (
        "scripts/ingest_processor.py",
        "scripts/cover_enforcer.py",
        "scripts/convert_library.py",
        "scripts/kindle_epub_fixer.py",
    )
    missing = [
        rel for rel in guarded
        if "tempfile.gettempdir()" not in (REPO / rel).read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"these no longer key a lock on gettempdir(): {missing} -- either they "
        f"moved to a safe path (drop them here) or to another shared one (this "
        f"fixture no longer protects them)"
    )
