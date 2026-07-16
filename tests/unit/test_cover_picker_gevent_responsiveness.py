# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Fork #954: "Change cover" must not make the rest of the app unreachable.

The reporter's symptom, verbatim: *"Once you click on 'Change cover', the app
is unreachable, until the operation completes."*

Mechanism: ``cps/server.py`` starts gevent WITHOUT ``monkey.patch_all()``
(verified live: ``monkey.is_module_patched('socket')`` is False in the
shipped container), so every request is a greenlet on ONE OS thread.
``gather_cover_candidates`` fanned its ~16 providers out through a stdlib
``concurrent.futures.ThreadPoolExecutor`` and waited on ``as_completed``.
That wait parks the OS thread on a ``threading.Condition`` the gevent hub
cannot preempt, so no other greenlet runs until the slowest provider
returns. Measured live on cwn-local pre-fix: a book page that answers in
30ms took 11.4s while a cover search ran.

This test drives the real ``gather_cover_candidates`` and asserts the thing
the user notices: other requests keep being served while it runs.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest

gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")

REPO_ROOT = Path(__file__).resolve().parents[2]

_MISSING = object()

# Each fake provider blocks this long, standing in for a real socket read.
_PROVIDER_SECONDS = 1.0
# Pre-fix the worst stall equals the whole fan-out (~1s here, ~12s live).
# Post-fix it is one scheduler tick. 300ms is decisive and not flaky.
_MAX_TOLERABLE_STALL = 0.3


def _load_picker_module():
    cps_pkg = sys.modules.get("cps")
    if cps_pkg is None:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg

    constants = sys.modules.get("cps.constants") or types.ModuleType("cps.constants")
    if not hasattr(constants, "USER_AGENT"):
        constants.USER_AGENT = "Calibre-Web-NextGen-tests"
    sys.modules["cps.constants"] = constants
    cps_pkg.constants = constants

    logger_mod = sys.modules.get("cps.logger") or types.ModuleType("cps.logger")
    if not hasattr(logger_mod, "create"):
        logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
            debug=lambda *a, **k: None, warning=lambda *a, **k: None,
            info=lambda *a, **k: None, error=lambda *a, **k: None,
        )
    sys.modules["cps.logger"] = logger_mod
    cps_pkg.logger = logger_mod

    # Snapshot the real cps.config binding so this stub cannot leak into other
    # test files sharing the xdist worker (see test_cover_picker_service.py).
    _orig_config_sysmod = sys.modules.get("cps.config", _MISSING)
    _orig_pkg_config = getattr(cps_pkg, "config", _MISSING)
    config_mod = sys.modules.get("cps.config") or types.ModuleType("cps.config")
    if not hasattr(config_mod, "get_book_path"):
        config_mod.get_book_path = lambda: "/tmp/library"
    sys.modules["cps.config"] = config_mod
    cps_pkg.config = config_mod

    if "cps.services" not in sys.modules:
        services_pkg = types.ModuleType("cps.services")
        services_pkg.__path__ = [str(REPO_ROOT / "cps" / "services")]
        sys.modules["cps.services"] = services_pkg

    booster_spec = importlib.util.spec_from_file_location(
        "cps.services.cover_booster", REPO_ROOT / "cps" / "services" / "cover_booster.py"
    )
    booster_module = importlib.util.module_from_spec(booster_spec)
    sys.modules["cps.services.cover_booster"] = booster_module
    booster_spec.loader.exec_module(booster_module)

    spec = importlib.util.spec_from_file_location(
        "cps.services.cover_picker", REPO_ROOT / "cps" / "services" / "cover_picker.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.services.cover_picker"] = module
    spec.loader.exec_module(module)

    if _orig_config_sysmod is _MISSING:
        sys.modules.pop("cps.config", None)
    else:
        sys.modules["cps.config"] = _orig_config_sysmod
    if _orig_pkg_config is _MISSING:
        if hasattr(cps_pkg, "config"):
            delattr(cps_pkg, "config")
    else:
        cps_pkg.config = _orig_pkg_config
    return module


picker = _load_picker_module()


def _slow_provider(provider_id):
    """A provider whose ``search`` blocks like a real HTTP call.

    ``time.sleep`` is the honest stand-in: with gevent unpatched neither it
    nor ``requests``' socket read yields to the hub.
    """
    inst = types.SimpleNamespace()
    inst.__id__ = provider_id
    inst.__name__ = provider_id.title()

    def search(*_args, **_kwargs):
        time.sleep(_PROVIDER_SECONDS)
        return []

    inst.search = search
    return inst


def _worst_stall_while(run):
    """Worst delay a co-scheduled greenlet suffers while ``run()`` executes —
    i.e. the longest another user's request would hang."""
    gaps = []
    done = []

    def heartbeat():
        # Sample AFTER each wake and record the gap BEFORE testing the exit
        # condition. The gap that matters is the one spanning the stall, and
        # it is only observable on the wake that follows it — by which time
        # `run()` has already finished and set `done`. Checking `done` first
        # would drop exactly the measurement this test exists to take.
        last = time.monotonic()
        while True:
            gevent.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            if done:
                return

    hb = gevent.spawn(heartbeat)
    gevent.sleep(0)

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


def _gather(providers):
    return picker.gather_cover_candidates(
        providers=providers,
        query="Dracula Bram Stoker",
        static_cover="/static/generic_cover.svg",
        locale="en",
        extract_embedded=None,
        book_isbns=(),
    )


class TestChangeCoverKeepsTheAppReachable:
    def test_gather_cover_candidates_does_not_freeze_other_requests(self):
        """#954. Pre-fix this stalls the hub for the whole provider fan-out."""
        providers = [_slow_provider(f"prov{i}") for i in range(5)]

        worst = _worst_stall_while(lambda: _gather(providers))

        assert worst < _MAX_TOLERABLE_STALL, (
            f"'Change cover' froze the gevent hub for {worst * 1000:.0f}ms. Every "
            f"other user's request hangs for that long (fork #954)."
        )

    def test_gather_cover_candidates_still_queries_providers_in_parallel(self):
        """Responsiveness must not cost throughput: 5 x 1s providers on the
        5-worker pool still finish in ~1s, not 5s."""
        providers = [_slow_provider(f"prov{i}") for i in range(5)]

        started = time.monotonic()
        candidates, statuses = _gather(providers)
        elapsed = time.monotonic() - started

        assert elapsed < _PROVIDER_SECONDS * 2.5, (
            f"provider fan-out took {elapsed:.2f}s — providers are being queried "
            "serially instead of in parallel"
        )
        assert len(statuses) == 5, "every provider must still report a status"

    def test_per_provider_timing_is_still_measured_individually(self):
        """The picker UI shows each provider's duration. A fix that collected
        results only after ALL finished would report the whole fan-out's
        duration for every provider."""
        fast = types.SimpleNamespace()
        fast.__id__ = "fast"
        fast.__name__ = "Fast"
        fast.search = lambda *_a, **_k: []

        providers = [_slow_provider("slow"), fast]
        _candidates, statuses = _gather(providers)

        by_id = {s.id: s for s in statuses}
        assert by_id["fast"].duration_ms < (_PROVIDER_SECONDS * 1000) / 2, (
            f"fast provider reported {by_id['fast'].duration_ms}ms — it was timed "
            "against the slow provider's completion, not its own"
        )
