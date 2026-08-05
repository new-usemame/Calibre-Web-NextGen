"""Behavioral regressions for blocking Kobo request work."""

import threading
from types import SimpleNamespace

from flask import Flask, request
from werkzeug.datastructures import Headers

from cps import kobo
from cps.services import parallel


def _run_on_real_thread(job):
    """Exercise the context boundary used by gevent's native thread pool."""
    result = []
    failure = []

    def worker():
        try:
            result.append(job())
        except BaseException as exc:  # surface worker failures to pytest
            failure.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


def test_kobo_store_request_prepares_flask_values_before_offload(monkeypatch):
    """Only requests' blocking I/O runs outside the request greenlet."""
    caller_thread = threading.get_ident()
    observed = {}
    app = Flask(__name__)

    def request_store(**kwargs):
        observed["worker_thread"] = threading.get_ident()
        observed["kwargs"] = kwargs
        return SimpleNamespace(content=b"ok", status_code=200)

    monkeypatch.setattr(parallel, "run_blocking", _run_on_real_thread)
    monkeypatch.setattr(kobo.requests, "request", request_store)

    with app.test_request_context(
        "/kobo/token/v1/library/book?foo=bar",
        method="POST",
        headers={"X-Kobo-Test": "prepared", "Host": "local.test"},
        data=b"reading-state",
    ):
        observed["context_thread"] = threading.get_ident()
        response = kobo.make_request_to_kobo_store()

    assert response.status_code == 200
    assert observed["context_thread"] == caller_thread
    assert observed["worker_thread"] != caller_thread
    assert observed["kwargs"] == {
        "method": "POST",
        "url": "https://storeapi.kobo.com/v1/library/book?foo=bar",
        "headers": Headers([("X-Kobo-Test", "prepared"), ("Content-Length", "13")]),
        "data": b"reading-state",
        "allow_redirects": False,
        "timeout": (2, 10),
    }
