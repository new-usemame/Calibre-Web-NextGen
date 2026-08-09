#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Debouncing dispatcher for metadata change-log events (fork #802).

Both watchers in the ``metadata-change-detector`` s6 service — the inotify
watcher and the polling fallback (``watch_fallback.py``) — feed change-log
*filenames* (one per line) into this dispatcher on stdin. It coalesces the
burst of duplicate / near-simultaneous events that a single logical metadata
save produces into **at most one enforcement pass per file**, then invokes
``scripts/cover_enforcer.py --log <file>`` once for each settled file.

Why this exists (fork #802)
---------------------------
The previous detector piped the raw watcher output straight into a
``while read`` loop that ran the enforcer **once per raw event**, with no
de-duplication. A single metadata save can produce several inotify
notifications for one change log (write + rename + chmod, and repeated
``close_write`` notifications on some overlay / bind-mount filesystems),
because the change-log filename is second-granular
(``{YYYYmmddHHMMSS}-{book_id}.json``) and identical events therefore share a
name. The result: one save spawned ~7 enforcer processes; the first consumed
and deleted the change log, and the remaining six logged a spurious
``WARNING: Log file '...' not found after 3 attempts`` followed by a
``Skipping processing`` line — six times (the reporter's symptom).

Notably the polling fallback already de-duplicated per file (its
``FIRED_SENTINEL`` guard), while the inotify path did not — an asymmetry.
This dispatcher unifies **both** paths behind one testable debounce so one
logical save is one enforcement pass regardless of watcher backend.

Design
------
Single-threaded and deterministic so it is unit-testable without real timers
or subprocesses (see ``ChangeLogDispatcher`` — inject ``dispatch`` + ``clock``):

* ``observe(filename)`` records an inbound event, filtered to real change-log
  files (``.json``; temp / hidden / swap / other extensions ignored). A late
  duplicate of a filename dispatched within ``cooldown`` seconds is dropped.
* ``flush_ready()`` dispatches any pending filename that has been quiet for at
  least ``debounce`` seconds — collapsing an event burst into one call.
* ``next_timeout()`` tells the stdin ``select`` loop how long it may block.

Enforcement remains **serialized** (the dispatch callback runs the enforcer
synchronously), preserving the previous one-at-a-time processing order.
"""

from __future__ import annotations

import argparse
import os
import select
import subprocess
import sys
import time

import app_paths
from typing import Callable, Dict, Optional, Sequence

# Defaults chosen so a single save's event burst (sub-second) collapses to one
# pass, while a genuine *second* save of the same book a moment later — which
# gets its own change-log filename once the wall-clock second ticks over — is
# still processed. Overridable via env for operators on unusually slow storage.
DEFAULT_DEBOUNCE = float(os.environ.get("CWA_DETECTOR_DEBOUNCE", "1.0"))
DEFAULT_COOLDOWN = float(os.environ.get("CWA_DETECTOR_COOLDOWN", "15.0"))
VALID_SUFFIXES = (".json",)


class ChangeLogDispatcher:
    """Coalesce duplicate change-log events into one enforcement pass each."""

    def __init__(
        self,
        dispatch: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        debounce: float = DEFAULT_DEBOUNCE,
        cooldown: float = DEFAULT_COOLDOWN,
        valid_suffixes: Sequence[str] = VALID_SUFFIXES,
    ) -> None:
        self._dispatch = dispatch
        self._clock = clock
        self._debounce = debounce
        self._cooldown = cooldown
        self._valid_suffixes = tuple(valid_suffixes)
        # filename -> monotonic time of the most recent event seen
        self._pending: Dict[str, float] = {}
        # filename -> monotonic time we last dispatched it (dup-suppression)
        self._recent: Dict[str, float] = {}

    def _accept(self, filename: str) -> bool:
        """True if ``filename`` is a real change-log file we should enforce.

        Ignores empty names, dotfiles (editor swap/temp like ``.foo.swp`` and
        atomic-write temporaries), and anything not ending in a valid change-log
        suffix. This is stricter than the old ``--exclude '\\.swp$'`` and means a
        non-conforming name never reaches the enforcer's timestamp parser.
        """
        if not filename:
            return False
        if filename.startswith("."):
            return False
        return filename.endswith(self._valid_suffixes)

    def observe(self, filename: str, now: Optional[float] = None) -> None:
        """Record an inbound watcher event for ``filename``."""
        now = self._clock() if now is None else now
        if not self._accept(filename):
            return
        done_at = self._recent.get(filename)
        if done_at is not None and (now - done_at) < self._cooldown:
            # We already dispatched this exact filename very recently; the
            # enforcer consumed+deleted it. A late duplicate notification must
            # not spawn a second (doomed) enforcement pass.
            return
        self._pending[filename] = now

    def flush_ready(self, now: Optional[float] = None) -> None:
        """Dispatch pending filenames quiet for >= ``debounce`` seconds."""
        now = self._clock() if now is None else now
        ready = [f for f, seen in self._pending.items() if (now - seen) >= self._debounce]
        for filename in ready:
            self._pending.pop(filename, None)
            self._recent[filename] = now
            self._dispatch(filename)
        # Expire stale dup-suppression records so a legitimate later re-use of a
        # name (well past the cooldown) is honoured.
        for filename in [f for f, t in self._recent.items() if (now - t) >= self._cooldown]:
            self._recent.pop(filename, None)

    def next_timeout(self) -> Optional[float]:
        """Seconds until the earliest pending filename is ready, or None if idle."""
        if not self._pending:
            return None
        now = self._clock()
        return max(0.0, min((seen + self._debounce) - now for seen in self._pending.values()))

    def drain(self, now: Optional[float] = None) -> None:
        """Dispatch everything still pending, ignoring the debounce window.

        Used at shutdown (stdin closed) so a change log that arrived in the last
        debounce window is not silently dropped.
        """
        now = self._clock() if now is None else now
        for filename in list(self._pending.keys()):
            self._pending.pop(filename, None)
            self._recent[filename] = now
            self._dispatch(filename)


def _default_dispatch(watch_folder: str, enforcer: str) -> Callable[[str], None]:
    """Build the production dispatch callback: log + run the enforcer once."""

    def _run(filename: str) -> None:
        # Preserve the historical log line so existing log-scrapers keep working.
        print(f"[metadata-change-detector] New file detected: {filename}", flush=True)
        subprocess.run(
            ["python3", enforcer, "--log", filename],
            check=False,
        )

    return _run


def run(stdin, dispatcher: ChangeLogDispatcher, poll_interval: float = 0.25) -> int:
    """Read filenames (one per line) from ``stdin`` and drive ``dispatcher``.

    Blocks in ``select`` until either input arrives or the earliest pending
    filename is due, so it is idle-cheap yet timely. Returns 0 on clean EOF.
    """
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError):
        fd = None

    while True:
        timeout = dispatcher.next_timeout()
        if timeout is None:
            timeout = poll_interval if fd is None else None

        if fd is not None:
            try:
                readable, _, _ = select.select([fd], [], [], timeout)
            except (OSError, ValueError):
                readable = [fd]
            if not readable:
                dispatcher.flush_ready()
                continue

        line = stdin.readline()
        if line == "":  # EOF
            dispatcher.flush_ready()
            dispatcher.drain()
            return 0
        filename = line.strip()
        dispatcher.observe(filename)
        dispatcher.flush_ready()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metadata-change-dispatch",
        description="Debounce metadata change-log events into one enforcement pass each (fork #802).",
    )
    parser.add_argument("--watch-folder", required=True, help="Directory the change logs live in (informational).")
    parser.add_argument(
        "--enforcer",
        default=str(app_paths.script_path("cover_enforcer.py")),
        help="Path to cover_enforcer.py.",
    )
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE, help="Quiet-window seconds before dispatch.")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN, help="Seconds to suppress duplicate filenames after dispatch.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    dispatcher = ChangeLogDispatcher(
        dispatch=_default_dispatch(args.watch_folder, args.enforcer),
        debounce=args.debounce,
        cooldown=args.cooldown,
    )
    return run(sys.stdin, dispatcher)


if __name__ == "__main__":
    raise SystemExit(main())
