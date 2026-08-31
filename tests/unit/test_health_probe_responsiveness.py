# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The container's own /health request must not freeze the gevent hub.

Fork issue #1799 reproduced a self-inflicted restart loop: Docker requests
``/health`` every 30 seconds, while that route performs unpatched blocking
SQLite and subprocess work on the request greenlet.  A writer holding
``metadata.db`` can therefore freeze every request until Docker kills the
container it was trying to measure.

This test uses the production gevent WSGI server, real loopback HTTP, and a
real SQLite exclusive writer lock.  A second client requests an unrelated
route shortly after the health request.  Both responses must remain bounded;
greenlet-only or Flask-test-client coverage would not prove the HTTP server
keeps accepting concurrent work.
"""

from __future__ import annotations

import http.client
import logging
import sqlite3
import threading
import time

import pytest
from flask import Flask


pytestmark = pytest.mark.unit
gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")
pywsgi = pytest.importorskip("gevent.pywsgi")

_HEALTH_LATENCY_LIMIT = 1.0
_CONCURRENT_LATENCY_LIMIT = 0.5
_SAFETY_UNLOCK_SECONDS = 2.0


def _http_get(port: int, path: str, results: dict[str, tuple[int, float]]) -> None:
    started = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        results[path] = (response.status, time.monotonic() - started)
    finally:
        connection.close()


def test_locked_metadata_probe_returns_promptly_without_stalling_another_request(
    monkeypatch, tmp_path
):
    from cps import config
    from cps import web as web_module

    metadata_db = tmp_path / "metadata.db"
    setup = sqlite3.connect(metadata_db)
    setup.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    setup.execute("INSERT INTO books (title) VALUES ('probe fixture')")
    setup.commit()
    setup.close()

    monkeypatch.setattr(web_module, "cwa_get_library_location", lambda: str(tmp_path))
    monkeypatch.setattr(config, "db_configured", True, raising=False)
    monkeypatch.setattr(
        web_module,
        "_check_s6_service_status",
        lambda: {name: "unknown" for name in web_module._CRITICAL_LONGRUNS},
    )

    # Prime the post-fix known-good cache before creating contention.  On the
    # pre-fix implementation this is simply an ordinary successful probe.
    assert web_module._probe_metadata_db() is True

    writer = sqlite3.connect(metadata_db, check_same_thread=False)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("UPDATE books SET title = 'writer still working' WHERE id = 1")

    unlock_fired = threading.Event()
    probe_entered = threading.Event()
    probe_entered_while_locked = threading.Event()

    def safety_unlock() -> None:
        writer.rollback()
        unlock_fired.set()

    safety_timer = threading.Timer(_SAFETY_UNLOCK_SECONDS, safety_unlock)
    real_sqlite_connect = sqlite3.connect

    def connect_and_arm_safety_unlock(*args, **kwargs):
        # Arm only after /health has entered the real DB probe. Starting this
        # timer before the client thread lets an overloaded CI worker unlock
        # the fixture before the buggy request ever reaches SQLite.
        if not probe_entered.is_set():
            if not unlock_fired.is_set():
                probe_entered_while_locked.set()
            probe_entered.set()
            safety_timer.start()
        return real_sqlite_connect(*args, **kwargs)

    monkeypatch.setattr(web_module.sqlite3, "connect", connect_and_arm_safety_unlock)

    app = Flask(__name__)
    app.register_blueprint(web_module.web)

    @app.get("/health-probe-concurrent")
    def concurrent_request():
        return {"ok": True}

    server = pywsgi.WSGIServer(("127.0.0.1", 0), app, log=None, error_log=None)
    server.start()

    results: dict[str, tuple[int, float]] = {}

    def delayed_health_request() -> None:
        # Model an overloaded runner that does not schedule the client until
        # after the old two-second pre-client timer would already have fired.
        # Request timing itself begins in _http_get after this scheduling lag.
        time.sleep(_SAFETY_UNLOCK_SECONDS + 0.25)
        _http_get(server.server_port, "/health", results)

    health_client = threading.Thread(
        target=delayed_health_request,
        daemon=True,
    )

    def request_concurrently() -> None:
        assert probe_entered.wait(timeout=4), "health probe never reached sqlite3.connect"
        time.sleep(0.1)
        _http_get(server.server_port, "/health-probe-concurrent", results)

    concurrent_client = threading.Thread(target=request_concurrently, daemon=True)
    safety_timer_alive = False

    try:
        health_client.start()
        concurrent_client.start()

        deadline = time.monotonic() + 5
        while (
            health_client.is_alive() or concurrent_client.is_alive()
        ) and time.monotonic() < deadline:
            gevent.sleep(0.01)

        health_client.join(timeout=0.1)
        concurrent_client.join(timeout=0.1)
    finally:
        safety_timer.cancel()
        if safety_timer.ident is not None:
            safety_timer.join(timeout=2)
        safety_timer_alive = safety_timer.is_alive()
        if not unlock_fired.is_set():
            writer.rollback()
        writer.close()
        server.stop(timeout=1)

    assert not safety_timer_alive, "safety unlock timer survived test teardown"
    assert not health_client.is_alive(), (
        "/health did not return within the five-second test bound"
    )
    assert not concurrent_client.is_alive(), (
        "the concurrent request did not return within the test bound"
    )
    assert probe_entered.is_set(), "the safety unlock was not armed by the real DB probe"
    assert probe_entered_while_locked.is_set(), (
        "the writer unlocked before /health entered SQLite; the red test can pass "
        "without exercising lock contention"
    )

    health_status, health_elapsed = results["/health"]
    concurrent_status, concurrent_elapsed = results["/health-probe-concurrent"]
    print(
        f"locked metadata.db: /health={health_elapsed:.3f}s, "
        f"concurrent request={concurrent_elapsed:.3f}s"
    )
    assert health_status == 200
    assert concurrent_status == 200
    violations = []
    if health_elapsed >= _HEALTH_LATENCY_LIMIT:
        violations.append(
            "/health waited on SQLite instead of using a bounded stale-known-good result"
        )
    if concurrent_elapsed >= _CONCURRENT_LATENCY_LIMIT:
        violations.append(
            "blocking health work ran on the gevent request thread and froze the whole app"
        )
    assert not violations, (
        f"health={health_elapsed:.3f}s (limit {_HEALTH_LATENCY_LIMIT:.1f}s), "
        f"concurrent={concurrent_elapsed:.3f}s (limit {_CONCURRENT_LATENCY_LIMIT:.1f}s): "
        + "; ".join(violations)
    )


def test_corrupt_metadata_db_is_still_reported_unhealthy(monkeypatch, tmp_path):
    """The lock-only stale fallback must never turn a broken DB into 200."""
    from cps import config
    from cps import web as web_module

    (tmp_path / "metadata.db").write_bytes(b"this is not a sqlite database")
    monkeypatch.setattr(web_module, "cwa_get_library_location", lambda: str(tmp_path))
    monkeypatch.setattr(config, "db_configured", True, raising=False)
    monkeypatch.setattr(
        web_module,
        "_check_s6_service_status",
        lambda: {name: "unknown" for name in web_module._CRITICAL_LONGRUNS},
    )

    assert web_module._probe_metadata_db() is False

    app = Flask(__name__)
    app.register_blueprint(web_module.web)
    with app.test_request_context("/health"):
        _body, status = web_module.health_check()
    assert status == 503


def test_writer_lock_fallback_expires_instead_of_masking_a_deadlock(
    monkeypatch, tmp_path
):
    from cps import web as web_module

    metadata_db = tmp_path / "metadata.db"
    setup = sqlite3.connect(metadata_db)
    setup.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    setup.commit()
    setup.close()

    monkeypatch.setattr(web_module, "cwa_get_library_location", lambda: str(tmp_path))
    stale_at = time.monotonic() - web_module._METADATA_DB_LOCK_STALE_GRACE_SECONDS - 1
    monkeypatch.setattr(
        web_module,
        "_metadata_db_last_good",
        (web_module._metadata_db_identity(metadata_db.resolve()), stale_at),
    )

    writer = sqlite3.connect(metadata_db)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO books DEFAULT VALUES")
        assert web_module._probe_metadata_db() is False
    finally:
        writer.rollback()
        writer.close()


def test_stale_lock_fallback_is_visible_in_operator_logs(monkeypatch, tmp_path, caplog):
    from cps import web as web_module

    metadata_db = tmp_path / "metadata.db"
    setup = sqlite3.connect(metadata_db)
    setup.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    setup.commit()
    setup.close()

    monkeypatch.setattr(web_module, "cwa_get_library_location", lambda: str(tmp_path))
    monkeypatch.setattr(web_module, "_metadata_db_last_good", (None, 0.0))
    assert web_module._probe_metadata_db() is True

    writer = sqlite3.connect(metadata_db)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO books DEFAULT VALUES")
        with caplog.at_level(logging.WARNING, logger="cps.web"):
            assert web_module._probe_metadata_db() is True
    finally:
        writer.rollback()
        writer.close()

    assert "stale known-good" in caplog.text
    assert "corruption" in caplog.text


def test_stale_cache_is_keyed_to_database_identity_not_symlink_spelling(
    monkeypatch, tmp_path
):
    from cps import web as web_module

    library_a = tmp_path / "library-a"
    library_b = tmp_path / "library-b"
    library_a.mkdir()
    library_b.mkdir()
    for library in (library_a, library_b):
        connection = sqlite3.connect(library / "metadata.db")
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()

    active_library = tmp_path / "active-library"
    active_library.symlink_to(library_a, target_is_directory=True)
    monkeypatch.setattr(
        web_module,
        "cwa_get_library_location",
        lambda: str(active_library),
    )
    monkeypatch.setattr(web_module, "_metadata_db_last_good", (None, 0.0))
    assert web_module._probe_metadata_db() is True

    active_library.unlink()
    active_library.symlink_to(library_b, target_is_directory=True)
    writer = sqlite3.connect(library_b / "metadata.db")
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO books DEFAULT VALUES")
        assert web_module._probe_metadata_db() is False
    finally:
        writer.rollback()
        writer.close()


def _call_health(app, web_module, results):
    with app.test_request_context("/health"):
        results.append(web_module.health_check())


def _response_status(response) -> int:
    return response[1] if isinstance(response, tuple) else 200


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        gevent.sleep(0.01)
    assert predicate(), "condition did not become true within the test deadline"


def test_single_flight_releases_gate_if_offloader_fails_before_worker_starts(
    monkeypatch,
):
    from cps import web as web_module

    gate = threading.Lock()
    probe_called = False
    submitted = []

    def reject_before_start(callable_):
        submitted.append(callable_)
        raise RuntimeError("offload submission failed")

    def probe():
        nonlocal probe_called
        probe_called = True

    monkeypatch.setattr(web_module, "_run_blocking", reject_before_start)

    with pytest.raises(RuntimeError, match="offload submission failed"):
        web_module._run_single_flight_health_probe(gate, probe, False, "test")

    assert probe_called is False
    assert gate.locked() is False, "a rejected offload permanently retained the gate"
    assert submitted[0]() is False, "an abandoned queued callable still ran its probe"
    assert probe_called is False
    assert gate.locked() is False, "an abandoned callable double-released the gate"


def test_single_flight_does_not_double_release_after_worker_runs(monkeypatch):
    from cps import web as web_module

    gate = threading.Lock()

    def run_then_raise(callable_):
        assert callable_() is True
        raise RuntimeError("offloader failed after worker completion")

    monkeypatch.setattr(web_module, "_run_blocking", run_then_raise)

    with pytest.raises(RuntimeError, match="offloader failed after worker completion"):
        web_module._run_single_flight_health_probe(gate, lambda: True, False, "test")

    assert gate.locked() is False


def test_duplicate_probe_warning_is_once_per_in_flight_probe(monkeypatch):
    from cps import web as web_module

    gate = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    warnings = []

    monkeypatch.setattr(web_module, "_run_blocking", lambda callable_: callable_())
    monkeypatch.setattr(
        web_module.log,
        "warning",
        lambda *args, **_kwargs: warnings.append(args),
    )

    def wedged_probe():
        started.set()
        assert release.wait(timeout=2), "test did not release the probe"
        return True

    def start_flight():
        started.clear()
        release.clear()
        owner = threading.Thread(
            target=web_module._run_single_flight_health_probe,
            args=(gate, wedged_probe, False, "test"),
            daemon=True,
        )
        owner.start()
        assert started.wait(timeout=2), "probe did not start"
        return owner

    first = start_flight()
    try:
        for _ in range(50):
            assert web_module._run_single_flight_health_probe(
                gate, lambda: True, False, "test"
            ) is False
        assert len(warnings) == 1, "duplicates emitted one warning per request"
    finally:
        release.set()
        first.join(timeout=2)
    assert not first.is_alive()

    second = start_flight()
    try:
        assert web_module._run_single_flight_health_probe(
            gate, lambda: True, False, "test"
        ) is False
        assert len(warnings) == 2, "a later wedged flight produced no fresh warning"
    finally:
        release.set()
        second.join(timeout=2)
    assert not second.is_alive()


def test_only_one_health_db_probe_can_remain_in_flight(monkeypatch):
    from cps import config
    from cps import web as web_module

    app = Flask(__name__)
    app.register_blueprint(web_module.web)
    monkeypatch.setattr(config, "db_configured", True, raising=False)
    monkeypatch.setattr(
        web_module, "_HEALTH_DB_PROBE_GATE", threading.Lock(), raising=False
    )
    monkeypatch.setattr(
        web_module, "_HEALTH_S6_PROBE_GATE", threading.Lock(), raising=False
    )

    started = threading.Event()
    release = threading.Event()
    calls = []

    def wedged_db_probe():
        calls.append(threading.get_ident())
        started.set()
        release.wait(timeout=5)
        return True

    monkeypatch.setattr(web_module, "_probe_metadata_db", wedged_db_probe)
    monkeypatch.setattr(
        web_module,
        "_check_s6_service_status",
        lambda: {name: "unknown" for name in web_module._CRITICAL_LONGRUNS},
    )

    first_results = []
    second_results = []
    first = gevent.spawn(_call_health, app, web_module, first_results)
    _wait_until(started.is_set)
    second = gevent.spawn(_call_health, app, web_module, second_results)
    gevent.sleep(0.1)
    calls_before_release = len(calls)
    second_finished_while_first_wedged = second.ready()
    release.set()
    gevent.joinall([first, second], timeout=2)

    assert calls_before_release == 1, "a second DB worker was retained by the next healthcheck"
    assert second_finished_while_first_wedged, "the duplicate DB probe did not fail fast"
    assert _response_status(first_results[0]) == 200
    assert _response_status(second_results[0]) == 503


def test_only_one_health_s6_probe_can_remain_in_flight(monkeypatch):
    from cps import config
    from cps import web as web_module

    app = Flask(__name__)
    app.register_blueprint(web_module.web)
    monkeypatch.setattr(config, "db_configured", True, raising=False)
    monkeypatch.setattr(
        web_module, "_HEALTH_DB_PROBE_GATE", threading.Lock(), raising=False
    )
    monkeypatch.setattr(
        web_module, "_HEALTH_S6_PROBE_GATE", threading.Lock(), raising=False
    )
    monkeypatch.setattr(web_module, "_probe_metadata_db", lambda: True)

    started = threading.Event()
    release = threading.Event()
    calls = []

    def wedged_s6_probe():
        calls.append(threading.get_ident())
        started.set()
        release.wait(timeout=5)
        return {name: "up" for name in web_module._CRITICAL_LONGRUNS}

    monkeypatch.setattr(web_module, "_check_s6_service_status", wedged_s6_probe)

    first_results = []
    second_results = []
    first = gevent.spawn(_call_health, app, web_module, first_results)
    _wait_until(started.is_set)
    second = gevent.spawn(_call_health, app, web_module, second_results)
    gevent.sleep(0.1)
    calls_before_release = len(calls)
    second_finished_while_first_wedged = second.ready()
    release.set()
    gevent.joinall([first, second], timeout=2)

    assert calls_before_release == 1, "a second s6 worker was retained by the next healthcheck"
    assert second_finished_while_first_wedged, "the duplicate s6 probe did not return unknown fast"
    assert _response_status(first_results[0]) == 200
    assert _response_status(second_results[0]) == 200
    second_body = second_results[0][0].get_json()
    assert all(value == "unknown" for value in second_body["services"].values())
