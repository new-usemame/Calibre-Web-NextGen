# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Run a crash scenario in a forked child that dies only where the test says.

Reflow's crash-recovery tests fork the test process so that the child inherits
the monkeypatched task and can die at an exact point with ``os._exit``. A forked
child also inherits everything else in the pytest worker, including sqlite3
connections that earlier tests never closed and the garbage collector has not
reached yet. When the child's collector finalizes one that still holds a
transaction or an unfinished statement, SQLite closes it in a process that did
not open it and logs about it; on macOS that log call (os_log, not safe after
``fork()``) can kill the child with SIGSEGV before the scenario has run. It
needs a leaked connection, so a whole-suite run, and macOS refreshing its log
settings at that moment, so it is intermittent: measured, two of the six Reflow
fork tests in one whole-suite run, and two of eighteen forks in a probe that
never crashed again in three more runs.

:func:`run_forked` collects garbage in the parent first, so a leaked connection
is closed by the process that opened it, and never leaves a child behind.
"""

import gc
import multiprocessing


def run_forked(target, timeout, *args):
    """Run ``target(*args)`` in a forked child and return the child's exit code.

    A child still running after ``timeout`` seconds is killed and reported with
    ``TimeoutError``, instead of outliving the test that started it.
    """
    gc.collect()
    process = multiprocessing.get_context("fork").Process(target=target, args=args)
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.kill()
        process.join(10)
        raise TimeoutError("the forked child did not finish within %s seconds" % timeout)
    return process.exitcode
