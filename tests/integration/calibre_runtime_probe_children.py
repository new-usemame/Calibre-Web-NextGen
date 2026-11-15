# SPDX-License-Identifier: GPL-3.0-or-later
"""Reap only a probe's explicitly identified, adopted Calibre helpers."""

import os
import signal
import time


def reap_owned_children(pids, timeout=10):
    """Wait for helper cleanup before removing its private filesystem.

    The caller is a Linux subreaper and supplies its current direct children.
    A helper may still be handling EOF after the managed launcher was reaped.
    Timeout or a failed helper remains a failure; cleanup kills only children
    that waitpid still identifies as belonging to this probe.
    """
    pending = set(pids)
    statuses = {}
    deadline = time.monotonic() + timeout
    failure = None
    try:
        while pending:
            for pid in list(pending):
                reaped, status = os.waitpid(pid, os.WNOHANG)
                if reaped:
                    pending.remove(pid)
                    statuses[pid] = os.waitstatus_to_exitcode(status)
            if pending:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned Calibre helpers did not finish: %s" % sorted(pending))
                time.sleep(.01)
        if any(statuses.values()):
            raise RuntimeError("Owned Calibre helper failed: %s" % statuses)
        return statuses
    except BaseException as error:
        failure = error
        raise
    finally:
        # This is failure cleanup, never a successful substitute for a drain.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in list(pending):
                try:
                    reaped, _ = os.waitpid(pid, os.WNOHANG)
                    if reaped:
                        pending.remove(pid)
                    else:
                        os.kill(pid, sig)
                except (ChildProcessError, ProcessLookupError):
                    pending.remove(pid)
            end = time.monotonic() + 1
            while pending and time.monotonic() < end:
                for pid in list(pending):
                    try:
                        reaped, _ = os.waitpid(pid, os.WNOHANG)
                        if reaped:
                            pending.remove(pid)
                    except ChildProcessError:
                        pending.remove(pid)
                if pending:
                    time.sleep(.01)
        if pending:
            message = "Owned helper cleanup could not reap: %s" % sorted(pending)
            if failure is not None:
                failure.add_note(message)
            else:
                raise RuntimeError(message)


def drain_owned_children(children_provider, timeout=10):
    """Reconcile late adoptions on success and on an already failed drain."""
    statuses = {}
    deadline = time.monotonic() + timeout
    try:
        while pids := children_provider():
            statuses.update(reap_owned_children(pids, max(0, deadline - time.monotonic())))
        return statuses
    except BaseException as original:
        # Reaping a failed parent can adopt another helper. Reconcile that
        # new inventory too, without replacing the original timeout/error
        # or treating forced failure cleanup as successful helper completion.
        cleanup_deadline = time.monotonic() + 3
        try:
            while pids := children_provider():
                try:
                    reap_owned_children(pids, timeout=0)
                except (TimeoutError, RuntimeError, ChildProcessError) as cleanup_error:
                    original.add_note("Owned helper failure cleanup: %r" % cleanup_error)
                if time.monotonic() >= cleanup_deadline:
                    remaining = children_provider()
                    if remaining:
                        original.add_note("Owned helpers remain after cleanup budget: %s" % remaining)
                    break
        except BaseException as cleanup_error:
            original.add_note("Owned helper cleanup inventory failed: %r" % cleanup_error)
        raise
