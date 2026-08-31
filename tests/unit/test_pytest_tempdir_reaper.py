# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-run temp directory reaper must not eat a running suite's directory.

``_isolate_pytest_tempdir`` gives every run its own ``cwng-pytest/<pid>/`` and
nothing ever removed them, so each pytest invocation leaked a directory; they
reached 8.4 GB on a developer machine before anyone noticed.

The cleanup that fixes that is more dangerous than the leak it replaces: a
reaper that mistakes a live run for a dead one deletes the temp directory out
from under a suite that is currently using it, which surfaces as unrelated,
irreproducible failures somewhere else entirely. So the discriminating cases --
not the happy path -- are what these tests pin.

Reaping deliberately happens at startup rather than at exit, because the runs
that leave the biggest directories behind (OOM, Ctrl-C, a timed-out CI job) are
exactly the ones that never reach an exit hook.
"""

from __future__ import annotations

import os
import time

import pytest

from tests.conftest import _reap_stale_pytest_tempdirs

pytestmark = pytest.mark.unit

STALE = 7 * 3600


def _dir(base, name, *, age_seconds=0):
    path = os.path.join(base, name)
    os.makedirs(path)
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def test_a_dead_owner_with_an_old_directory_is_reaped(tmp_path):
    """The only case that should actually free disk."""
    base = str(tmp_path)
    victim = _dir(base, "999999", age_seconds=STALE)

    _reap_stale_pytest_tempdirs(base)

    assert not os.path.exists(victim)


def test_this_running_process_is_never_reaped(tmp_path):
    """Age alone must not condemn a directory whose owner is still alive.

    A long suite can easily run past the age floor while still using its own
    directory; deleting it mid-run would corrupt that run.
    """
    base = str(tmp_path)
    mine = _dir(base, str(os.getpid()), age_seconds=STALE)

    _reap_stale_pytest_tempdirs(base)

    assert os.path.exists(mine)


def test_a_recently_exited_owner_is_protected_by_the_age_floor(tmp_path):
    """Liveness alone is not enough: pids are recycled and siblings race.

    An xdist worker that exited seconds ago may still have peers writing under
    its tree, so a fresh directory is never reaped even once its owner is gone.
    """
    base = str(tmp_path)
    young = _dir(base, "999998")

    _reap_stale_pytest_tempdirs(base)

    assert os.path.exists(young)


def test_a_live_unrelated_process_is_never_reaped(tmp_path):
    """pid 1 is always alive and never ours; nothing about age may override that."""
    base = str(tmp_path)
    launchd = _dir(base, "1", age_seconds=STALE)

    _reap_stale_pytest_tempdirs(base)

    assert os.path.exists(launchd)


def test_directories_that_are_not_pids_are_left_alone(tmp_path):
    """Anything not named for a pid was put there by something else."""
    base = str(tmp_path)
    stranger = _dir(base, "notapid", age_seconds=STALE)

    _reap_stale_pytest_tempdirs(base)

    assert os.path.exists(stranger)


def test_a_missing_base_directory_is_not_an_error(tmp_path):
    """First run on a clean machine must not fail before the suite starts."""
    _reap_stale_pytest_tempdirs(os.path.join(str(tmp_path), "does-not-exist"))


def test_an_unreadable_entry_never_fails_the_run(tmp_path, monkeypatch):
    """Tidying up is best-effort; it may never take a test run down with it."""
    base = str(tmp_path)
    _dir(base, "999997", age_seconds=STALE)

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(os.path, "getmtime", boom)

    _reap_stale_pytest_tempdirs(base)
