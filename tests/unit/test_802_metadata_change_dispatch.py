# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #802: the metadata-change-detector spawned one
enforcer per raw inotify event, so a single metadata save (which emits several
notifications for one second-granular change-log file) fanned out into ~7
enforcement passes — the first consumed+deleted the log and the other six logged
``WARNING: Log file '...' not found after 3 attempts`` + ``Skipping processing``.

Two layers are covered *behaviourally* (not source-pins):

1. ``ChangeLogDispatcher`` coalesces an event burst into one enforcement pass
   per file (the root-cause fix — the detector now debounces both the inotify
   and polling paths behind one dispatcher).
2. ``cover_enforcer.read_log`` quiets the benign already-consumed case to a
   single INFO line — no alarming WARNING, no second "Skipping" line — so even a
   duplicate that escapes the dispatcher can't produce spam (defense in depth).

A light source-pin also guards the s6 ``run`` wiring so it can't silently revert
to the per-event dispatch that caused the bug.
"""

import io
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from metadata_change_dispatch import ChangeLogDispatcher  # noqa: E402
from tests.fixtures.service_sources import service_source


def _recording_dispatcher(**kwargs):
    calls = []
    disp = ChangeLogDispatcher(dispatch=calls.append, debounce=1.0, cooldown=15.0, **kwargs)
    return disp, calls


# --------------------------------------------------------------------------- #
# Layer 1: dispatcher debounce/dedup — the root-cause fix
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_identical_event_burst_dispatches_once():
    """7 identical events for one save -> exactly ONE enforcement pass.

    This is the reporter's exact scenario. Pre-fix the detector ran the enforcer
    once per event (7x); the dispatcher collapses the burst.
    """
    disp, calls = _recording_dispatcher()
    name = "20260711011124-18095.json"
    # A burst of 7 notifications arriving within ~0.12s (sub-second, one save).
    for i in range(7):
        disp.observe(name, now=i * 0.02)
    # Still inside the debounce window -> nothing dispatched yet.
    disp.flush_ready(now=0.5)
    assert calls == []
    # After the quiet window elapses -> exactly one dispatch.
    disp.flush_ready(now=1.5)
    assert calls == [name]
    # Later flushes must not re-dispatch.
    disp.flush_ready(now=5.0)
    assert calls == [name]


@pytest.mark.unit
def test_two_distinct_files_dispatch_twice():
    """Two genuinely different change logs each get their own pass."""
    disp, calls = _recording_dispatcher()
    disp.observe("20260711011124-1.json", now=0.0)
    disp.observe("20260711011130-2.json", now=0.1)
    disp.flush_ready(now=1.5)
    assert sorted(calls) == ["20260711011124-1.json", "20260711011130-2.json"]


@pytest.mark.unit
def test_late_duplicate_after_dispatch_is_suppressed():
    """A duplicate notification arriving after the pass ran does not re-fire.

    (The enforcer already consumed+deleted the log, so a second pass would be the
    doomed one that produced the warning spam.)
    """
    disp, calls = _recording_dispatcher()
    name = "20260711011124-18095.json"
    disp.observe(name, now=0.0)
    disp.flush_ready(now=1.5)
    assert calls == [name]
    # Late duplicate within the cooldown -> ignored.
    disp.observe(name, now=3.0)
    disp.flush_ready(now=4.5)
    assert calls == [name]


@pytest.mark.unit
def test_same_name_reused_after_cooldown_is_processed():
    """A genuinely new change log that happens to reuse a name (well past the
    cooldown) is still enforced — dedup must not be permanent."""
    disp, calls = _recording_dispatcher()
    name = "20260711011124-18095.json"
    disp.observe(name, now=0.0)
    disp.flush_ready(now=1.5)
    disp.observe(name, now=30.0)  # > cooldown (15s)
    disp.flush_ready(now=31.5)
    assert calls == [name, name]


@pytest.mark.unit
@pytest.mark.parametrize("ignored", [".20260711011124-1.json.swp", ".hidden.json", "notalog.txt", "20260711-1.tmp", ""])
def test_non_changelog_events_are_ignored(ignored):
    """Editor swap/temp files, hidden files and non-.json events never reach the
    enforcer's timestamp parser."""
    disp, calls = _recording_dispatcher()
    disp.observe(ignored, now=0.0)
    disp.flush_ready(now=2.0)
    assert calls == []


@pytest.mark.unit
def test_drain_flushes_pending_on_eof():
    """A log that arrived in the last debounce window is not dropped at shutdown."""
    disp, calls = _recording_dispatcher()
    disp.observe("20260711011124-7.json", now=0.0)
    disp.drain(now=0.1)  # EOF before debounce elapsed
    assert calls == ["20260711011124-7.json"]


# --------------------------------------------------------------------------- #
# Layer 2: enforcer quiets the benign already-consumed case (defense in depth)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_enforcer_missing_log_logs_single_info_not_warning_spam(capsys, tmp_path):
    """read_log() on an already-consumed log -> ONE calm INFO line.

    Pre-fix this printed 'WARNING: Log file ... not found after 3 attempts' and
    the caller added a second 'Skipping processing' line. Behavioural red/green.
    """
    import cover_enforcer

    # read_log(auto=False, log_path=...) touches no instance state on the
    # not-found path, so a bare object stands in for `self` (avoids the
    # container-only CWA_DB/app.db construction in Enforcer.__init__).
    dummy = types.SimpleNamespace()
    missing = tmp_path / "20260711011124-18095.json"  # valid name, does not exist

    result = cover_enforcer.Enforcer.read_log(dummy, auto=False, log_path=str(missing))
    assert result is None

    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "not found after" not in out
    assert "Skipping processing" not in out
    assert "INFO" in out
    assert "already processed" in out
    # Exactly one non-empty line of output.
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1


# --------------------------------------------------------------------------- #
# Wiring guard: the s6 detector must dispatch through the debouncer
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_detector_run_script_dispatches_through_debouncer():
    run = service_source("metadata-change-detector")
    # Both watcher backends must feed the dispatcher.
    assert "metadata_change_dispatch.py" in run
    # The bug was invoking the enforcer once per raw event inline; that direct
    # per-event call must be gone from the detector.
    assert "cover_enforcer.py" not in run
