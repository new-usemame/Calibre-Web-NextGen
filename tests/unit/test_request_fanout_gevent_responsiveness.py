# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The request-path provider fan-outs must not stall the gevent hub.

Fork issue #954: clicking "Change cover" made the whole app unreachable
until the candidate search finished. ``cps/server.py`` runs gevent WITHOUT
``monkey.patch_all()``, so every greenlet shares one OS thread. A stdlib
``concurrent.futures`` wait (``as_completed``/``Future.result``/the
``Executor`` context-manager exit) parks that thread on a
``threading.Condition``, which the gevent hub cannot preempt — so EVERY
other in-flight HTTP request is frozen for the whole fan-out.

These tests measure the invariant the user actually cares about: while a
fan-out runs, other greenlets (= other people's requests) keep getting
scheduled.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest

gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")

REPO_ROOT = Path(__file__).resolve().parents[2]

# A fan-out job blocks this long. Long enough to dwarf scheduler noise,
# short enough to keep the suite fast.
_JOB_SECONDS = 1.0
# The hub must keep serving other greenlets throughout. On the pre-fix code
# the worst gap equals _JOB_SECONDS (total stall); with a gevent-aware wait
# it is a scheduler tick. 300ms sits far from both, so the test is decisive
# without being flaky on a loaded CI box.
_MAX_TOLERABLE_STALL = 0.3


def _load(mod_name: str, rel_path: str):
    """Load a cps module in isolation (no Flask app init)."""
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

    logger_mod = sys.modules.get("cps.logger") or types.ModuleType("cps.logger")
    if not hasattr(logger_mod, "create"):
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *a, **k: None, warning=lambda *a, **k: None,
            info=lambda *a, **k: None, error=lambda *a, **k: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    spec = importlib.util.spec_from_file_location(mod_name, REPO_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


parallel = _load("cps.services.parallel", "cps/services/parallel.py")


def _worst_stall_while(run) -> float:
    """Run ``run()`` in one greenlet while a heartbeat greenlet tries to stay
    on schedule. Return the worst delay the heartbeat suffered — i.e. the
    longest any other request would have been frozen."""
    gaps = []
    done = []

    def heartbeat():
        # Record the gap BEFORE testing the exit condition: the gap spanning
        # the stall is only observable on the wake after it, when `done` is
        # already set. Checking `done` first silently drops the measurement
        # and the test passes on broken code.
        last = time.monotonic()
        while True:
            gevent.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            if done:
                return

    hb = gevent.spawn(heartbeat)
    gevent.sleep(0)  # let the heartbeat take its first sample

    def runner():
        try:
            run()
        finally:
            done.append(True)

    gevent.joinall([gevent.spawn(runner)])
    hb.join(timeout=1)
    hb.kill(block=True)
    assert gaps, "heartbeat never sampled"
    return max(gaps)


def _blocking_job(value):
    """Stands in for a provider's blocking socket read. ``time.sleep`` is the
    honest stand-in: unpatched gevent cannot preempt it, exactly like a real
    ``requests`` call against Google Books / Amazon."""
    def _job():
        time.sleep(_JOB_SECONDS)
        return value
    return _job


class TestFanOutKeepsHubResponsive:
    def test_fan_out_yields_to_other_greenlets_while_jobs_run(self):
        """The shared helper must not freeze other requests. This is the
        invariant #954 violated."""
        jobs = [(f"p{i}", _blocking_job(f"r{i}")) for i in range(5)]

        def run():
            return list(parallel.fan_out(jobs, max_workers=5))

        worst = _worst_stall_while(run)
        assert worst < _MAX_TOLERABLE_STALL, (
            f"fan_out froze the gevent hub for {worst * 1000:.0f}ms; other users' "
            f"requests stall for the whole fan-out (fork #954)"
        )

    def test_fan_out_runs_jobs_in_parallel_not_serially(self):
        """Yielding to the hub must not cost throughput: 5 x 1s jobs on 5
        workers still finish in ~1s, so the fix does not trade a frozen app
        for a slow one."""
        jobs = [(f"p{i}", _blocking_job(f"r{i}")) for i in range(5)]
        started = time.monotonic()
        results = list(parallel.fan_out(jobs, max_workers=5))
        elapsed = time.monotonic() - started
        assert len(results) == 5
        assert elapsed < _JOB_SECONDS * 2.5, (
            f"fan_out took {elapsed:.2f}s for 5 parallel {_JOB_SECONDS}s jobs — "
            "jobs are not running concurrently"
        )

    def test_fan_out_returns_every_key_and_value(self):
        jobs = [("a", lambda: 1), ("b", lambda: 2)]
        got = {key: res.value for key, res in parallel.fan_out(jobs, max_workers=2)}
        assert got == {"a": 1, "b": 2}

    def test_fan_out_surfaces_exceptions_without_killing_the_batch(self):
        """One failing provider must not lose the others' results — the
        picker reports per-provider status."""
        boom = RuntimeError("provider exploded")

        def _raise():
            raise boom

        jobs = [("ok", lambda: "fine"), ("bad", _raise)]
        out = {key: res for key, res in parallel.fan_out(jobs, max_workers=2)}
        assert out["ok"].value == "fine"
        assert out["ok"].exception is None
        assert out["bad"].exception is boom
        assert out["bad"].value is None

    def test_fan_out_yields_incrementally_as_jobs_complete(self):
        """Callers time each provider individually (duration_ms in the picker's
        per-provider status), so results must arrive as they finish rather
        than all at the end."""
        def quick():
            return "quick"

        def slow():
            time.sleep(_JOB_SECONDS)
            return "slow"

        jobs = [("slow", slow), ("quick", quick)]
        order = []
        started = time.monotonic()
        for key, _res in parallel.fan_out(jobs, max_workers=2):
            order.append((key, time.monotonic() - started))
        assert [k for k, _ in order] == ["quick", "slow"], "results not yielded as completed"
        assert order[0][1] < _JOB_SECONDS / 2, "fast provider was withheld until the slow one finished"

    def test_fan_out_with_no_jobs_is_a_noop(self):
        assert list(parallel.fan_out([], max_workers=4)) == []

    def test_elapsed_is_stamped_when_the_job_finished_not_when_it_is_consumed(self):
        """Regression: the picker shows a per-provider duration, and there are
        more providers (16) than workers (5), so jobs finish in waves. A
        consumer-side clock reading collapses every job in a wave onto one
        identical number — measured live: 10 of 16 providers all reporting
        exactly 5316ms, where the pre-fix build reported 16 distinct times.
        The elapsed value must therefore be taken inside the worker, the
        moment the job returns.
        """
        # 6 jobs, 2 workers => three waves of two. Within a wave the two jobs
        # finish ~together; ACROSS waves they must differ by ~_JOB_SECONDS.
        jobs = [(i, _blocking_job(i)) for i in range(6)]
        results = {key: res for key, res in parallel.fan_out(jobs, max_workers=2)}

        elapsed = sorted(res.elapsed_ms for res in results.values())
        # Wave boundaries must be visible: the last job cannot report the same
        # elapsed as the first.
        assert elapsed[-1] - elapsed[0] > (_JOB_SECONDS * 1000) * 1.5, (
            f"elapsed values {elapsed} are collapsed — jobs from later waves are "
            "reporting the same time as the first wave, so the clock is being read "
            "on the consumer side instead of at job completion"
        )
        assert len({round(e / 100) for e in elapsed}) >= 3, (
            f"expected ~3 distinct completion waves, got {elapsed}"
        )

    def test_elapsed_measures_from_fanout_start_to_job_completion(self):
        """A job that finishes early reports a small elapsed even when a
        sibling runs long — the value is per job, not per batch."""
        def quick():
            return "quick"

        jobs = [("slow", _blocking_job("slow")), ("quick", quick)]
        results = {key: res for key, res in parallel.fan_out(jobs, max_workers=2)}

        assert results["quick"].elapsed_ms < (_JOB_SECONDS * 1000) / 2, (
            f"quick job reported {results['quick'].elapsed_ms}ms — it was timed against "
            "the slow job's completion"
        )
        assert results["slow"].elapsed_ms >= (_JOB_SECONDS * 1000) * 0.8


class TestRequestPathCallersUseTheSharedHelper:
    """Guard: the next person to add a request-path fan-out gets a failing
    test, not a frozen app. Pins the #954 root cause, which was three
    copies of the same stdlib-wait pattern (cover picker, metadata search,
    cover booster) rather than one shared, gevent-aware one."""

    # Modules whose fan-out runs ON the request greenlet.
    REQUEST_PATH_MODULES = [
        "cps/services/cover_picker.py",
        "cps/services/cover_booster.py",
        "cps/search_metadata.py",
    ]

    # Matched against real call/attribute nodes via AST, never raw source text:
    # these modules *document* why they avoid the stdlib primitives, and a
    # substring scan would fire on those comments instead of on real code.
    BANNED_CALLS = {"as_completed", "ThreadPoolExecutor", "wait"}

    @pytest.mark.parametrize("rel_path", REQUEST_PATH_MODULES)
    def test_module_does_not_block_the_hub_with_stdlib_futures(self, rel_path):
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))

        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in self.BANNED_CALLS:
                # concurrent.futures.as_completed(...) / futures.wait(...)
                root = node.value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in ("concurrent", "futures"):
                    offenders.append(f"{node.attr} (line {node.lineno})")
            elif isinstance(node, ast.Name) and node.id in self.BANNED_CALLS:
                # bare ThreadPoolExecutor(...) via `from concurrent.futures import ...`
                offenders.append(f"{node.id} (line {node.lineno})")

        assert not offenders, (
            f"{rel_path} uses stdlib concurrent.futures on the request path: "
            f"{', '.join(offenders)}. Under gevent (no monkey-patch) those waits block "
            "the hub, so every other user's request freezes for the whole fan-out — "
            "fork #954. Use cps.services.parallel.fan_out instead."
        )

    @pytest.mark.parametrize("rel_path", REQUEST_PATH_MODULES)
    def test_module_does_not_import_concurrent_futures(self, rel_path):
        """No import, no temptation — and it keeps the check above honest."""
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("concurrent"), (
                        f"{rel_path} imports {alias.name} (line {node.lineno}); "
                        "use cps.services.parallel.fan_out (fork #954)"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("concurrent"), (
                    f"{rel_path} imports from {node.module} (line {node.lineno}); "
                    "use cps.services.parallel.fan_out (fork #954)"
                )
