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

    app = Flask(__name__)
    app.register_blueprint(web_module.web)

    @app.get("/health-probe-concurrent")
    def concurrent_request():
        return {"ok": True}

    server = pywsgi.WSGIServer(("127.0.0.1", 0), app, log=None, error_log=None)
    server.start()

    results: dict[str, tuple[int, float]] = {}
    unlock_fired = threading.Event()

    def safety_unlock() -> None:
        writer.rollback()
        unlock_fired.set()

    safety_timer = threading.Timer(_SAFETY_UNLOCK_SECONDS, safety_unlock)
    health_client = threading.Thread(
        target=_http_get,
        args=(server.server_port, "/health", results),
        daemon=True,
    )

    def request_concurrently() -> None:
        time.sleep(0.1)
        _http_get(server.server_port, "/health-probe-concurrent", results)

    concurrent_client = threading.Thread(target=request_concurrently, daemon=True)

    try:
        safety_timer.start()
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
        if not unlock_fired.is_set():
            writer.rollback()
        writer.close()
        server.stop(timeout=1)

    assert not health_client.is_alive(), (
        "/health did not return within the five-second test bound"
    )
    assert not concurrent_client.is_alive(), (
        "the concurrent request did not return within the test bound"
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
        (str(metadata_db.resolve()), stale_at),
    )

    writer = sqlite3.connect(metadata_db)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO books DEFAULT VALUES")
        assert web_module._probe_metadata_db() is False
    finally:
        writer.rollback()
        writer.close()
