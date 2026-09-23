# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The forked child of a Reflow crash test dies where the test says, and nowhere else.

The crash-recovery tests in test_reflow_task.py and test_reflow_publication.py
fork the pytest worker (tests/fixtures/forking.py). In one whole-suite run on
macOS two of them died with SIGSEGV instead of at their crash point: the child's
garbage collector finalized a sqlite3 connection an earlier test had leaked, and
SQLite's close logged through os_log, which cannot run after fork().
"""

import gc
import os
import sqlite3
import time

import pytest

from tests.fixtures.forking import run_forked


def test_a_connection_an_earlier_test_leaked_is_closed_by_the_process_that_opened_it(tmp_path):
    """Breaks if the forked child is the process that finalizes it. The SIGSEGV
    itself cannot be forced -- it needs macOS to refresh its log settings at that
    moment -- so this checks the thing that prevents it, on every platform."""
    closed_in = tmp_path / "closed-in"

    class Leaked(sqlite3.Connection):
        def __del__(self):
            with open(closed_in, "a") as handle:
                handle.write("%d\n" % os.getpid())

    def leak():
        # What an earlier test can leave in a worker: a connection nobody closed,
        # still inside a transaction, reachable only from a reference cycle.
        connection = sqlite3.connect(str(tmp_path / "leaked.sqlite"), factory=Leaked)
        connection.execute("create table t(x)")
        connection.commit()
        connection.execute("insert into t values (1)")
        cycle = [connection]
        cycle.append(cycle)

    def scenario():
        gc.collect()                # the child's own work collects garbage
        os._exit(86)                # the crash point the test chose

    gc.disable()                    # nothing has collected it yet when the test forks
    try:
        leak()
        assert run_forked(scenario, 60) == 86
    finally:
        gc.enable()
    gc.collect()
    assert closed_in.read_text().split() == [str(os.getpid())]


def test_a_forked_child_that_outlives_its_time_is_stopped_and_reported(tmp_path):
    """Breaks if a wedged child is left running after the test gives up on it."""
    pid_file = tmp_path / "child.pid"

    def hang():
        pid_file.write_text(str(os.getpid()))
        time.sleep(60)
    with pytest.raises(TimeoutError):
        run_forked(hang, 3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
