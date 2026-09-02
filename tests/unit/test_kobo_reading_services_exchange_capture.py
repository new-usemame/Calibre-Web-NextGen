# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Passive, bounded capture of Kobo Reading Services exchanges."""

from __future__ import annotations

import gzip
import importlib
import json
import stat
import threading
from types import SimpleNamespace

import gevent
import pytest
from flask import Flask, jsonify, request

import cps.readingservices as rs


ACK = "I_UNDERSTAND_THIS_CAPTURES_PRIVATE_READING_DATA"
OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
FOREIGN = "kobo-store-content"


def _module():
    return importlib.import_module("cps.services.kobo_exchange_capture")


def _run_capture_io_inline(monkeypatch, capture):
    """Keep persistence-focused tests independent of the request deadline."""
    monkeypatch.setattr(
        capture,
        "_run_off_hub_bounded",
        lambda _scope, function: function(),
    )


def _enable(monkeypatch, tmp_path, *, run_io_inline=True):
    capture = _module()
    root = tmp_path / "private-captures"
    monkeypatch.setenv(capture.ENABLE_ENV, ACK)
    monkeypatch.setattr(capture, "_capture_root", lambda: root)
    if run_io_inline:
        _run_capture_io_inline(monkeypatch, capture)
    return capture, root


def _records(root):
    paths = sorted(root.glob("exchange-*.json.gz"))
    return [json.loads(gzip.decompress(path.read_bytes())) for path in paths]


def _finish(session, body=b'["device"]'):
    return session.finish(
        status=200,
        headers=[("Content-Type", "application/json"), ("Set-Cookie", "secret")],
        body=body,
    )


@pytest.mark.unit
def test_capture_gate_requires_exact_private_data_acknowledgement(monkeypatch, tmp_path):
    capture = _module()
    root = tmp_path / "must-not-exist"
    monkeypatch.setattr(capture, "_capture_root", lambda: root)
    monkeypatch.delenv(capture.ENABLE_ENV, raising=False)

    assert capture.enabled() is False
    assert capture.begin_capture(
        exchange="checkforchanges", method="POST", path="/api/v3/content/checkforchanges",
        query_string=b"", headers=[], body=b"[]",
    ) is None
    assert not root.exists()

    for accidental in ("1", "true", "yes", ACK.lower(), f" {ACK} "):
        monkeypatch.setenv(capture.ENABLE_ENV, accidental)
        assert capture.enabled() is False

    monkeypatch.setenv(capture.ENABLE_ENV, ACK)
    assert capture.enabled() is True


@pytest.mark.unit
def test_capture_preserves_four_exact_bodies_order_decisions_and_redacts_credentials(
    monkeypatch, tmp_path,
):
    capture, root = _enable(monkeypatch, tmp_path)
    device_request = (
        b'[{"ContentId":"' + OWNED.encode() + b'","etag":"W/\\"0\\""},'
        b'{"ContentId":"' + FOREIGN.encode() + b'","etag":"foreign"}]'
    )
    upstream_request = (
        b'[{"ContentId":"' + FOREIGN.encode() + b'","etag":"foreign"}]'
    )
    upstream_response = b'["kobo-store-content","unexpected-owned"]'
    device_response = b'["kobo-store-content"]\n'
    session = capture.begin_capture(
        exchange="checkforchanges", method="POST", path="/api/v3/content/checkforchanges",
        query_string=b"", headers=[
            ("Authorization", "Bearer top-secret"),
            ("X-Kobo-UserKey", "private-key"),
            ("X-Trace", "kept"),
        ], body=device_request,
    )
    assert session is not None
    session.add_decision(
        stage="device_request", index=0, content_id=OWNED,
        ownership="owned", authority_status="unseeded", action="suppressed",
    )
    session.add_decision(
        stage="device_request", index=1, content_id=FOREIGN,
        ownership="unowned", authority_status=None, action="proxied",
    )
    session.record_upstream_request(
        method="POST", path="/api/v3/content/checkforchanges", query_string=b"",
        headers=[("Cookie", "never"), ("X-Trace", "upstream-kept")],
        body=upstream_request,
    )
    session.record_upstream_response(
        status=200,
        headers=[("Set-Cookie", "never"), ("ETag", 'W/"manifest"')],
        body=upstream_response,
    )

    assert _finish(session, device_response) is True
    [record] = _records(root)
    assert record["device_request"]["body"]["data"] == device_request.decode("utf-8")
    assert record["upstream_request"]["body"]["data"] == upstream_request.decode("utf-8")
    assert record["upstream_response"]["body"]["data"] == upstream_response.decode("utf-8")
    assert record["device_response"]["body"]["data"] == device_response.decode("utf-8")
    assert [decision["content_id"] for decision in record["decisions"]] == [OWNED, FOREIGN]
    assert [decision["authority_status"] for decision in record["decisions"]] == [
        "unseeded", None,
    ]
    assert record["device_request"]["headers"] == [
        ["Authorization", "***REDACTED***"],
        ["X-Kobo-UserKey", "***REDACTED***"],
        ["X-Trace", "kept"],
    ]
    assert record["upstream_request"]["headers"][0] == ["Cookie", "***REDACTED***"]
    assert record["upstream_response"]["headers"][0] == ["Set-Cookie", "***REDACTED***"]
    assert record["device_response"]["headers"][1] == ["Set-Cookie", "***REDACTED***"]
    capture_path = next(root.glob("exchange-*.json.gz"))
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(capture_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_capture_retention_prunes_oldest_by_count(monkeypatch, tmp_path):
    capture, root = _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(capture, "MAX_FILES", 2)
    monkeypatch.setattr(capture, "MAX_TOTAL_BYTES", 1024 * 1024)

    for index in range(4):
        session = capture.begin_capture(
            exchange="annotations_get", method="GET", path=f"/annotations/{index}",
            query_string=b"limit=100", headers=[], body=b"",
        )
        assert session is not None
        assert _finish(session, str(index).encode()) is True

    records = _records(root)
    assert len(records) == 2
    assert [record["device_response"]["body"]["data"] for record in records] == ["2", "3"]
    assert sum(path.stat().st_size for path in root.glob("exchange-*.json.gz")) \
        <= capture.MAX_TOTAL_BYTES
    assert capture._file_count_requires_prune(2, incoming=True) is True
    assert capture._file_count_requires_prune(2, incoming=False) is False
    assert capture._file_count_requires_prune(3, incoming=False) is True


@pytest.mark.unit
def test_oversized_exchange_is_skipped_whole_without_partial_capture(monkeypatch, tmp_path):
    capture, root = _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(capture, "MAX_BODY_BYTES", 8)
    session = capture.begin_capture(
        exchange="annotations_patch", method="PATCH", path="/annotations/book",
        query_string=b"", headers=[], body=b"123456789",
    )

    assert session is None
    assert not list(root.glob("exchange-*.json.gz")) if root.exists() else True


@pytest.mark.unit
def test_capture_io_failure_is_swallowed_and_leaves_no_partial_file(monkeypatch, tmp_path):
    capture, root = _enable(monkeypatch, tmp_path)
    session = capture.begin_capture(
        exchange="annotations_get", method="GET", path="/annotations/book",
        query_string=b"limit=100", headers=[], body=b"",
    )
    assert session is not None
    monkeypatch.setattr(session, "_persist", lambda: (_ for _ in ()).throw(OSError("disk full")))

    assert _finish(session) is False
    assert not list(root.glob("*.tmp"))
    assert not list(root.glob("exchange-*.json.gz"))


def _check_app(monkeypatch, *, capture_begin=None):
    app = Flask(__name__)
    app.secret_key = "capture-test"

    @app.post("/api/v3/content/checkforchanges")
    def check():
        return rs._handle_check_for_changes()

    monkeypatch.setattr(rs, "current_user", SimpleNamespace(id=7, is_authenticated=True))
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda content_id: SimpleNamespace(id=347) if content_id == OWNED else None,
    )
    if capture_begin is not None:
        capture = _module()
        monkeypatch.setattr(capture, "begin_capture", capture_begin)
    return app


@pytest.mark.unit
def test_real_checkforchanges_route_captures_actual_filtered_upstream_and_device_bytes(
    monkeypatch, tmp_path,
):
    capture, root = _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rs, "_capture_authority_status",
        lambda ownership: "unseeded" if ownership is not None else None,
    )

    def _proxy(*, data, capture_session=None):
        capture_session.record_upstream_request(
            method="POST", path=request.path, query_string=request.query_string,
            headers=request.headers.items(), body=data,
        )
        response = jsonify([FOREIGN, OWNED])
        capture_session.record_upstream_response(
            status=response.status_code, headers=response.headers.items(), body=response.get_data(),
        )
        return response

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    app = _check_app(monkeypatch)
    raw = (
        b'[{"ContentId":"' + OWNED.encode() + b'","etag":"W/\\"0\\""},'
        b'{"ContentId":"' + FOREIGN.encode() + b'","etag":"foreign"}]'
    )
    response = app.test_client().post(
        "/api/v3/content/checkforchanges", data=raw,
        headers={"Authorization": "Bearer secret"}, content_type="application/json",
    )

    assert response.status_code == 200
    assert response.get_data() == b'["kobo-store-content"]\n'
    [record] = _records(root)
    assert record["device_request"]["body"]["data"].encode() == raw
    assert json.loads(record["upstream_request"]["body"]["data"]) == [
        {"ContentId": FOREIGN, "etag": "foreign"},
    ]
    assert json.loads(record["upstream_response"]["body"]["data"]) == [FOREIGN, OWNED]
    assert record["device_response"]["body"]["data"].encode() == response.get_data()
    assert [(item["content_id"], item["ownership"], item["authority_status"], item["action"])
            for item in record["decisions"][:2]] == [
        (OWNED, "owned", "unseeded", "suppressed"),
        (FOREIGN, "unowned", None, "proxied"),
    ]
    assert all("secret" not in json.dumps(record).lower() for _ in [0])
    assert capture.enabled() is True


@pytest.mark.unit
def test_capture_enabled_and_capture_failure_leave_response_byte_identical(monkeypatch, tmp_path):
    capture, _root = _enable(monkeypatch, tmp_path)

    def _proxy(*, data, capture_session=None):
        del data, capture_session
        return jsonify([FOREIGN])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    app = _check_app(monkeypatch)
    payload = [{"ContentId": FOREIGN, "etag": "foreign"}]

    monkeypatch.delenv(capture.ENABLE_ENV, raising=False)
    baseline = app.test_client().post("/api/v3/content/checkforchanges", json=payload)
    baseline_triplet = (
        baseline.status_code, baseline.get_data(), list(baseline.headers.items()),
    )

    monkeypatch.setenv(capture.ENABLE_ENV, ACK)
    enabled = app.test_client().post("/api/v3/content/checkforchanges", json=payload)
    assert (enabled.status_code, enabled.get_data(), list(enabled.headers.items())) == baseline_triplet

    monkeypatch.setattr(
        capture, "begin_capture", lambda **_kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    failed = app.test_client().post("/api/v3/content/checkforchanges", json=payload)
    assert (failed.status_code, failed.get_data(), list(failed.headers.items())) == baseline_triplet


@pytest.mark.unit
def test_unauthenticated_patch_401_captures_body_with_explicit_provenance_outside_spool(
    monkeypatch, tmp_path,
):
    capture = _module()
    _run_capture_io_inline(monkeypatch, capture)
    unauthenticated_root = tmp_path / "unauthenticated-exchange-captures"
    recovery_spool_root = tmp_path / "recovery-spool-must-stay-empty"
    monkeypatch.setenv(capture.ENABLE_ENV, ACK)
    monkeypatch.setattr(
        capture, "_unauthenticated_capture_root",
        lambda: unauthenticated_root,
        raising=False,
    )
    from cps.services import kobo_patch_spool
    monkeypatch.setattr(kobo_patch_spool, "_spool_root", lambda: recovery_spool_root)
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=False, id=None),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda: pytest.fail("the refused PATCH must not be sent upstream"),
    )
    raw_body = b'{"updatedAnnotations":[{"id":"annotation-1","noteText":"private"}]}'
    app = Flask(__name__)

    @app.patch("/api/v3/content/<content_id>/annotations")
    @rs.requires_reading_services_auth_and_config
    def annotations(content_id):
        del content_id
        pytest.fail("the unauthenticated PATCH must not reach the handler")

    response = app.test_client().patch(
        f"/api/v3/content/{OWNED}/annotations",
        data=raw_body,
        content_type="application/json",
        headers={"Authorization": "Bearer must-be-redacted"},
    )

    assert response.status_code == 401
    [record] = _records(unauthenticated_root)
    assert record["schema_version"] == 3
    assert record["exchange"] == "annotations_patch_unauthenticated"
    assert record["request_provenance"] == {
        "authentication": "unauthenticated",
        "user_id": None,
    }
    assert record["device_request"]["body"]["data"].encode() == raw_body
    assert dict(record["device_request"]["headers"])["Authorization"] \
        == "***REDACTED***"
    assert record["upstream_request"] is None
    assert record["upstream_response"] is None
    assert record["device_response"]["status"] == 401
    assert record["device_response"]["body"]["data"].encode() == response.get_data()
    assert not recovery_spool_root.exists()


@pytest.mark.unit
def test_unauthenticated_capture_is_off_by_default_and_does_not_read_the_body(
    monkeypatch, tmp_path,
):
    capture = _module()
    unauthenticated_root = tmp_path / "must-not-exist"
    monkeypatch.delenv(capture.ENABLE_ENV, raising=False)
    monkeypatch.setattr(
        capture, "_unauthenticated_capture_root",
        lambda: unauthenticated_root,
        raising=False,
    )
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=False, id=None),
    )
    app = Flask(__name__)

    @app.patch("/api/v3/content/<content_id>/annotations")
    @rs.requires_reading_services_auth_and_config
    def annotations(content_id):
        del content_id
        pytest.fail("the unauthenticated PATCH must not reach the handler")

    monkeypatch.setattr(
        app.request_class,
        "get_data",
        lambda *_args, **_kwargs: pytest.fail(
            "an off diagnostic must not consume an unauthenticated request body"
        ),
    )
    response = app.test_client().patch(
        f"/api/v3/content/{OWNED}/annotations",
        data=b'{"updatedAnnotations":[{"id":"annotation-1"}]}',
        content_type="application/json",
    )

    assert response.status_code == 401
    assert not unauthenticated_root.exists()


@pytest.mark.unit
def test_unauthenticated_capture_churn_cannot_evict_authenticated_diagnostics(
    monkeypatch, tmp_path,
):
    capture, authenticated_root = _enable(monkeypatch, tmp_path)
    unauthenticated_root = tmp_path / "separate-unauthenticated-budget"
    monkeypatch.setattr(
        capture, "_unauthenticated_capture_root", lambda: unauthenticated_root,
    )
    monkeypatch.setattr(capture, "UNAUTHENTICATED_MAX_FILES", 2)
    monkeypatch.setattr(capture, "UNAUTHENTICATED_MAX_TOTAL_BYTES", 1024 * 1024)

    authenticated = capture.begin_capture(
        exchange="annotations_patch", method="PATCH", path="/annotations/book",
        query_string=b"", headers=[], body=b'{"updatedAnnotations":[]}',
        authentication="authenticated", user_id=7,
    )
    assert _finish(authenticated) is True

    for index in range(4):
        unauthenticated = capture.begin_capture(
            exchange="annotations_patch_unauthenticated", method="PATCH",
            path=f"/annotations/untrusted-{index}", query_string=b"",
            headers=[], body=str(index).encode(),
            authentication="unauthenticated", user_id=None,
        )
        assert _finish(unauthenticated) is True

    [authenticated_record] = _records(authenticated_root)
    assert authenticated_record["request_provenance"]["user_id"] == 7
    assert len(_records(unauthenticated_root)) == 2


@pytest.mark.unit
def test_capture_persistence_yields_to_gevent_hub(monkeypatch, tmp_path):
    capture, _root = _enable(monkeypatch, tmp_path, run_io_inline=False)
    monkeypatch.setattr(capture, "REQUEST_IO_TIMEOUT_SECONDS", 5.0)
    session = capture.begin_capture(
        exchange="annotations_patch", method="PATCH", path="/annotations/book",
        query_string=b"", headers=[], body=b'{"updatedAnnotations":[]}',
        authentication="authenticated", user_id=7,
    )
    request_thread = threading.get_ident()
    release_worker = threading.Event()
    worker_threads = []
    events = []

    def native_io_waiting_on_hub():
        worker_threads.append(threading.get_ident())
        assert release_worker.wait(2), "hub greenlet did not release capture worker"
        events.append("persisted")

    def hub_ticker():
        events.append("hub-ran")
        release_worker.set()

    monkeypatch.setattr(session, "_persist", native_io_waiting_on_hub)
    ticker = gevent.spawn(hub_ticker)

    assert _finish(session) is True
    ticker.get(timeout=1)

    assert events == ["hub-ran", "persisted"]
    assert worker_threads != [request_thread]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "request_body", "upstream_body"),
    [
        ("GET", b"", b'{"annotations":[],"nextPageOffsetToken":null}'),
        ("PATCH", b'{"updatedAnnotations":[]}', b'{"accepted":true}'),
    ],
)
def test_annotations_get_and_patch_capture_both_proxy_legs_and_device_response(
    monkeypatch, tmp_path, method, request_body, upstream_body,
):
    _capture, root = _enable(monkeypatch, tmp_path)
    app = Flask(__name__)

    @app.route("/annotations/<content_id>", methods=["GET", "PATCH"])
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    monkeypatch.setattr(rs, "current_user", SimpleNamespace(id=7, is_authenticated=True))
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(rs, "_possible_annotation_ownership", lambda _content_id: {})
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)

    def _proxy(*, data=None, capture_session=None):
        outbound = request.get_data() if data is None else data
        capture_session.record_upstream_request(
            method=request.method, path=request.path, query_string=request.query_string,
            headers=request.headers.items(), body=outbound,
        )
        response = app.response_class(upstream_body, status=207, headers={"X-Upstream": "same"})
        capture_session.record_upstream_response(
            status=207, headers=response.headers.items(), body=upstream_body,
        )
        return response

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    response = app.test_client().open(
        f"/annotations/{OWNED}?limit=100", method=method, data=request_body,
        content_type="application/json", headers={"X-Kobo-UserKey": "secret"},
    )

    assert response.status_code == 207
    assert response.get_data() == upstream_body
    [record] = _records(root)
    assert record["device_request"]["body"]["data"].encode() == request_body
    assert record["upstream_request"]["body"]["data"].encode() == request_body
    assert record["upstream_response"]["body"]["data"].encode() == upstream_body
    assert record["device_response"]["body"]["data"].encode() == upstream_body
    assert "secret" not in json.dumps(record).lower()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "request_body", "expected_status"),
    [
        ("GET", b"", 200),
        ("PATCH", b"{}", 204),
    ],
)
def test_owned_annotations_capture_records_local_answer_without_upstream_leg(
    monkeypatch, tmp_path, method, request_body, expected_status,
):
    _capture, root = _enable(monkeypatch, tmp_path)
    app = Flask(__name__)
    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])

    @app.route("/annotations/<content_id>", methods=["GET", "PATCH"])
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    monkeypatch.setattr(rs, "current_user", SimpleNamespace(id=7, is_authenticated=True))
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "_capture_authority_status", lambda _ownership: "authoritative")
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_args: None)
    monkeypatch.setattr(
        rs, "_persist_owned_patch_atomically", lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "cps.services.kobo_annotation_authority.local_get_is_eligible",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_authority.ever_authoritative",
        lambda *_args, **_kwargs: "never_authoritative",
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_authority.mark_authoritative_oversize",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_authority.advance_authoritative_patch_revision",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("owned request must not create an upstream leg"),
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_authority.render_owned_annotations",
        lambda **_kwargs: (
            b'{"annotations":[],"nextPageOffsetToken":null}',
            'W/"CWNG:00000000-0000-0000-0000-000000000000:1:0000000000000000"',
        ),
    )

    response = app.test_client().open(
        f"/annotations/{OWNED}?limit=100", method=method, data=request_body,
        content_type="application/json",
    )

    assert response.status_code == expected_status
    [record] = _records(root)
    assert record["upstream_request"] is None
    assert record["upstream_response"] is None
    assert record["upstream_error"] is None
    assert record["decisions"] == [{
        "stage": "local_authority",
        "index": 0,
        "content_id": OWNED,
        "ownership": "owned",
        "authority_status": "authoritative",
        "action": "answered_locally",
    }]
    assert record["device_response"]["status"] == expected_status
