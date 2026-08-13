# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The enforcer's single-instance lock must not be taken at import time.

`cover_enforcer.py` used to acquire its lock in the module body and `sys.exit(2)`
when it already existed. Nine test modules import this module, so a lockfile left
behind by a killed run turned every one of them red with a `SystemExit: 2` raised
from the import statement — on a tree whose code was fine.

The lock is released via `atexit`, which does not run on SIGKILL, and the tick's
LLM phase runs under a `timeout` that kills it routinely. So the leak is not
hypothetical. Production recovers on restart (cwa-init clears /tmp/*.lock), but a
developer's tempdir has no such sweep.
"""

import importlib
import os
import sys
import tempfile

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts")


@pytest.fixture
def enforcer(monkeypatch, tmp_path):
    """Import a fresh cover_enforcer whose lock lives in an isolated tempdir."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.syspath_prepend(SCRIPTS)
    for name in ("cover_enforcer",):
        sys.modules.pop(name, None)
    module = importlib.import_module("cover_enforcer")
    yield module
    sys.modules.pop("cover_enforcer", None)


def test_import_does_not_take_the_lock(enforcer, tmp_path):
    """Importing the module must not create the lockfile."""
    assert not (tmp_path / "cover_enforcer.lock").exists()


def test_import_succeeds_when_a_stale_lock_exists(monkeypatch, tmp_path):
    """The regression: a leftover lock must not kill the import.

    Before the fix this raised SystemExit(2) from the import statement itself.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.syspath_prepend(SCRIPTS)
    (tmp_path / "cover_enforcer.lock").write_text("999999")
    sys.modules.pop("cover_enforcer", None)

    module = importlib.import_module("cover_enforcer")  # must not raise

    assert module is not None
    sys.modules.pop("cover_enforcer", None)


def test_acquire_lock_reclaims_a_lock_owned_by_a_dead_pid(enforcer, tmp_path):
    """A run killed before atexit leaves a lock; the next run must reclaim it."""
    lock = tmp_path / "cover_enforcer.lock"
    lock.write_text("999999")  # a pid that is not running
    monkey_dead = lambda pid: False
    enforcer._pid_is_alive = monkey_dead

    enforcer._acquire_lock_or_exit()

    assert lock.exists()
    assert lock.read_text().strip() == str(os.getpid())


def test_acquire_lock_reclaims_a_legacy_empty_lock(enforcer, tmp_path):
    """Locks written before this change carry no pid; they are still stale."""
    lock = tmp_path / "cover_enforcer.lock"
    lock.write_text("")

    enforcer._acquire_lock_or_exit()

    assert lock.read_text().strip() == str(os.getpid())


def test_acquire_lock_refuses_when_the_owner_is_alive(enforcer, tmp_path):
    """The single-instance guarantee still holds against a running enforcer."""
    lock = tmp_path / "cover_enforcer.lock"
    lock.write_text(str(os.getpid()))  # our own pid is definitionally alive

    with pytest.raises(SystemExit) as exc:
        enforcer._acquire_lock_or_exit()

    assert exc.value.code == 2
    assert lock.read_text().strip() == str(os.getpid())


def test_remove_lock_tolerates_an_already_removed_file(enforcer, tmp_path):
    """atexit must not raise when something else already cleaned up."""
    assert not (tmp_path / "cover_enforcer.lock").exists()

    enforcer.removeLock()  # must not raise
