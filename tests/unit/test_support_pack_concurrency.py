# SPDX-License-Identifier: GPL-3.0-or-later
"""Building the support pack must not freeze the app, or stack up (#2728).

cps serves requests with gevent and no monkey-patching, so every request
shares one OS thread. Building a pack is seconds of CPU on a Raspberry Pi, and
done on the request greenlet it froze every other request; clicking the button
fifteen times queued fifteen serial builds, the reverse proxy answered 504, and
the app stayed frozen until it was restarted. These tests pin the two fixes:
the build runs off the hub, and only one runs at a time.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from cps import debug_info

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _idle_state():
    debug_info._pack_in_progress = False
    yield
    debug_info._pack_in_progress = False


@pytest.fixture
def fake_pack(monkeypatch):
    """Replace the two halves of the build with controllable stand-ins."""
    calls = {"prepare": [], "assemble": 0}

    def prepare(sanitize):
        calls["prepare"].append(sanitize)
        return {"sanitize": sanitize}

    class _Zip:
        def __init__(self, data):
            self._data = data

        def getvalue(self):
            return self._data

    def assemble(prep):
        calls["assemble"] += 1
        return _Zip(b"PK-fake-" + (b"s" if prep["sanitize"] else b"d"))

    monkeypatch.setattr(debug_info, "_prepare_pack", prepare)
    monkeypatch.setattr(debug_info, "_assemble_pack", assemble)
    return calls


class TestSingleFlight:
    def test_builds_and_releases(self, fake_pack):
        assert debug_info._get_pack(True) == b"PK-fake-s"
        assert debug_info._get_pack(False) == b"PK-fake-d"
        assert fake_pack["prepare"] == [True, False] and debug_info._pack_in_progress is False

    def test_a_second_request_during_a_build_is_refused_not_queued(self, monkeypatch):
        started, release = threading.Event(), threading.Event()
        monkeypatch.setattr(debug_info, "_prepare_pack", lambda sanitize: {"sanitize": sanitize})

        class _Zip:
            def getvalue(self):
                return b"zip"

        def slow_assemble(prep):
            started.set()
            assert release.wait(5)
            return _Zip()

        monkeypatch.setattr(debug_info, "_assemble_pack", slow_assemble)
        first = {}
        worker = threading.Thread(target=lambda: first.update(data=debug_info._get_pack(True)))
        worker.start()
        assert started.wait(5)
        try:
            # Fifteen more clicks while the first build runs: every one is refused at once.
            for _ in range(15):
                began = time.monotonic()
                with pytest.raises(debug_info.PackBusy):
                    debug_info._get_pack(True)
                assert time.monotonic() - began < 0.5
            # The unredacted pack shares the same gate, so it cannot pile on either.
            with pytest.raises(debug_info.PackBusy):
                debug_info._get_pack(False)
        finally:
            release.set()
            worker.join(5)
        assert first["data"] == b"zip"
        assert debug_info._get_pack(True) == b"zip"  # free again afterwards

    def test_a_failed_build_does_not_wedge_the_gate(self, monkeypatch):
        monkeypatch.setattr(debug_info, "_prepare_pack", lambda sanitize: {"sanitize": sanitize})

        def boom(prep):
            raise RuntimeError("disk full")

        monkeypatch.setattr(debug_info, "_assemble_pack", boom)
        with pytest.raises(RuntimeError):
            debug_info._get_pack(True)
        assert debug_info._pack_in_progress is False

    def test_a_failure_while_preparing_does_not_wedge_the_gate(self, monkeypatch):
        def boom(sanitize):
            raise RuntimeError("db gone")

        monkeypatch.setattr(debug_info, "_prepare_pack", boom)
        with pytest.raises(RuntimeError):
            debug_info._get_pack(True)
        assert debug_info._pack_in_progress is False


class TestBusyResponse:
    def test_is_a_429_with_retry_after_and_a_readable_message(self):
        body, status, headers = debug_info._busy_response()
        assert status == 429
        assert headers["Retry-After"] == str(debug_info.PACK_BUSY_RETRY_SECONDS)
        assert headers["Content-Type"] == "application/json"
        payload = json.loads(body)
        assert payload["busy"] is True and payload["success"] is False
        assert "already being prepared" in payload["message"]

    @pytest.mark.parametrize("endpoint", ["send_debug_sanitized", "send_debug"])
    def test_both_endpoints_answer_429_while_busy(self, monkeypatch, endpoint):
        def busy(_sanitize):
            raise debug_info.PackBusy()

        monkeypatch.setattr(debug_info, "_get_pack", busy)
        _body, status, _headers = getattr(debug_info, endpoint)()
        assert status == 429


class TestHubStaysResponsive:
    """The invariant that matters: while a pack builds, other greenlets (other
    people's requests) keep getting scheduled."""

    gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")

    def test_cancelling_waiter_keeps_both_packs_busy_until_worker_finishes(self, monkeypatch):
        gevent = self.gevent
        started, release = threading.Event(), threading.Event()
        monkeypatch.setattr(debug_info, "_prepare_pack", lambda sanitize: {"sanitize": sanitize})

        class _Zip:
            def getvalue(self):
                return b"zip"

        def assemble(prep):
            if not release.is_set():
                assert not started.is_set(), "a second build ran before the first worker exited"
                started.set()
                assert release.wait(5)
            return _Zip()

        monkeypatch.setattr(debug_info, "_assemble_pack", assemble)
        waiting = gevent.spawn(debug_info._get_pack, True)
        try:
            with gevent.Timeout(4):
                while not started.is_set():
                    gevent.sleep(0.01)
            waiting.kill()
            assert debug_info.send_debug_sanitized()[1] == 429
            assert debug_info.send_debug()[1] == 429
        finally:
            release.set()
            waiting.kill()
            with gevent.Timeout(4):
                while debug_info._pack_in_progress:
                    gevent.sleep(0.01)

        assert debug_info._get_pack(True) == b"zip"

    def test_other_requests_are_served_during_the_build(self, monkeypatch):
        gevent = self.gevent
        monkeypatch.setattr(debug_info, "_prepare_pack", lambda sanitize: {"sanitize": sanitize})

        class _Zip:
            def getvalue(self):
                return b"zip"

        def blocking_assemble(prep):
            time.sleep(1.0)  # stands in for seconds of log scrubbing; a real blocking call
            return _Zip()

        monkeypatch.setattr(debug_info, "_assemble_pack", blocking_assemble)

        gaps, stop = [], threading.Event()

        def heartbeat():
            last = time.monotonic()
            while not stop.is_set():
                gevent.sleep(0.02)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        beat = gevent.spawn(heartbeat)
        gevent.sleep(0)
        result = {}
        gevent.joinall([gevent.spawn(lambda: result.update(data=debug_info._get_pack(True)))])
        stop.set()
        gevent.joinall([beat])

        assert result["data"] == b"zip"
        # On the pre-fix code the worst gap equals the whole build (~1.0s). Off the
        # hub it is a scheduler tick. 0.3s sits far from both.
        assert gaps and max(gaps) < 0.3, f"hub stalled for {max(gaps):.2f}s while the pack built"
