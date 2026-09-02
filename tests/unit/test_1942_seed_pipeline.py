# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""M2 production seeding for owned Kobo annotation authority (#1942)."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g, make_response
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.api.kobo_two_way as kobo_two_way_api
import cps.annotations as annotations_api
import cps.readingservices as rs
from cps.services import kobo_annotation_authority as authority
from cps.services import kobo_annotation_seeding as seeding


OWNED = "053742ff-9094-43b2-8511-c0763c90ffab"
BOOK_ID = 540
USER_ID = 107
DEVICE_A = 2
DEVICE_B = 3


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    yield database
    database.close()
    engine.dispose()


@pytest.fixture
def app(monkeypatch):
    application = Flask(__name__)
    user = SimpleNamespace(
        id=USER_ID,
        name="reader",
        is_authenticated=True,
        is_anonymous=False,
        kobo_two_way_annotation_sync=True,
    )
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(
        rs.config, "config_kobo_two_way_annotation_sync", True, raising=False,
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_stage0.schema_capable",
        lambda _engine: True,
    )
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    authority.reset_skip_log_for_testing()
    return application


def _book(monkeypatch):
    book = SimpleNamespace(
        id=BOOK_ID,
        uuid="{053742FF-9094-43B2-8511-C0763C90FFAB}",
        title="Probe book",
        identifiers=[],
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _value: book)
    return book


def _device(session, device_id, *, active=True):
    row = ub.Device(
        id=device_id,
        public_id=f"00000000-0000-0000-0000-{device_id:012d}",
        user_id=USER_ID,
        kind="kobo",
        display_name=f"Kobo {device_id}",
        active=active,
        created_by="auto",
    )
    session.add(row)
    return row


def _state(session, *, status="unseeded", ever=False, content_id=None):
    row = ub.KoboAnnotationBookState(
        user_id=USER_ID,
        book_id=BOOK_ID,
        content_id=content_id or f"legacy-book:{BOOK_ID}",
        authority_status=status,
        authority_revision=0,
        ever_authoritative=ever,
        generation_id="00000000-0000-0000-0000-000000000001",
        opaque_content_status="unknown",
    )
    session.add(row)
    return row


def _wire_annotation(annotation_id):
    return {
        "id": annotation_id,
        "type": "highlight",
        "highlightedText": f"text {annotation_id}",
        "highlightColor": "#F6F3B3",
        "clientLastModifiedUtc": "2026-08-29T01:00:00.000Z",
        "location": {"span": {
            "chapterFilename": "OEBPS/chapter.xhtml",
            "chapterProgress": 0.25,
            "startPath": "p/1",
            "endPath": "p/1",
            "startChar": 1,
            "endChar": 8,
        }},
        "attachments": {},
    }


def _upstream_response(annotations, *, next_offset=None, etag='W/"kobo-seed"'):
    body = json.dumps(
        {"annotations": annotations, "nextPageOffsetToken": next_offset},
        separators=(",", ":"),
    ).encode()
    return make_response(body, 200, {"Content-Type": "application/json", "ETag": etag})


def _accepted_capture_with_page(
    session, state, device_id, annotations, *, completed_at=None,
):
    completed_at = completed_at or datetime.now(timezone.utc)
    body = json.dumps(
        {"annotations": annotations, "nextPageOffsetToken": None},
        separators=(",", ":"),
    ).encode()
    capture = ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=device_id,
        started_at=completed_at - timedelta(seconds=1),
        started_authority_revision=max(0, (state.authority_revision or 0) - 1),
        completed_at=completed_at,
        annotation_count=len(annotations),
        page_count=1,
        result="accepted",
        seed_kind="upstream_capture",
    )
    session.add(capture)
    session.flush()
    session.add(ub.KoboAnnotationSeedCapturePage(
        seed_capture_id=capture.id,
        page_number=0,
        request_offset_token=None,
        response_body_gzip=gzip.compress(body, mtime=0),
        response_sha256=hashlib.sha256(body).hexdigest(),
        next_offset_token=None,
    ))
    return capture


def _accepted_routing_capture(session, state, device_id):
    now = datetime.now(timezone.utc)
    capture = ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=device_id,
        started_at=now,
        started_authority_revision=state.authority_revision or 0,
        completed_at=now,
        annotation_count=0,
        page_count=0,
        result="accepted",
        seed_kind="routing_only",
    )
    session.add(capture)
    return capture


def _annotation_row(annotation_id, **overrides):
    values = {
        "user_id": USER_ID,
        "book_id": BOOK_ID,
        "annotation_id": annotation_id,
        "source": "kobo",
        "annotation_type": "highlight",
        "highlighted_text": f"text {annotation_id}",
        "highlight_color": "#F6F3B3",
        "content_id": f"{OWNED}!!OEBPS/chapter.xhtml",
        "chapter_progress": 0.25,
        "start_container_path": "p/1",
        "end_container_path": "p/1",
        "start_offset": 1,
        "end_offset": 8,
        "client_modified_at": datetime(2026, 8, 29, 1, 0, 0),
        "content_revision": 1,
        "hidden": False,
    }
    values.update(overrides)
    return ub.Annotation(**values)


def _request(app, content_id=OWNED, *, offset=None, headers=None):
    query = "?limit=100"
    if offset is not None:
        query += f"&pageOffsetToken={offset}"
    return app.test_request_context(
        f"/api/v3/content/{content_id}/annotations{query}",
        method="GET",
        headers=headers or {},
    )


def test_capture_page_persists_reconciles_and_repairs_legacy_content_id(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session, content_id=f"legacy-book:{BOOK_ID}")
    session.commit()
    upstream = _upstream_response([_wire_annotation("seeded-1")])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response is upstream
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    page = session.query(ub.KoboAnnotationSeedCapturePage).one()
    state = session.query(ub.KoboAnnotationBookState).one()
    annotation = session.query(ub.Annotation).one()
    materialization = session.query(ub.KoboAnnotationMaterialization).one()
    assert capture.result == "accepted"
    assert capture.seed_kind == "upstream_capture"
    assert capture.annotation_count == 1
    assert capture.page_count == 1
    assert gzip.decompress(page.response_body_gzip) == upstream.get_data()
    assert page.response_sha256 == hashlib.sha256(upstream.get_data()).hexdigest()
    assert page.response_etag == 'W/"kobo-seed"'
    assert page.request_offset_token is None
    assert page.next_offset_token is None
    assert state.content_id == OWNED
    assert state.authority_status == "authoritative"
    assert state.authority_revision == 1
    assert state.ever_authoritative is True
    assert state.seeded_at is not None
    assert state.opaque_content_status == "absent"
    assert state.opaque_content_source == "wire_attachments_verified"
    assert state.opaque_content_checked_at is not None
    assert annotation.origin_device_id == DEVICE_A
    assert annotation.content_revision == 1
    assert materialization.provenance == "kobo_cloud_seed"
    assert materialization.serveable is True


def test_refuses_promotion_when_visible_count_is_below_capture(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    upstream = _upstream_response([
        _wire_annotation("seeded-a"), _wire_annotation("seeded-b"),
    ])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)
    monkeypatch.setattr(seeding, "_visible_ids", lambda *_a: {"seeded-a"})

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False
    assert state.quarantine_reason == "seed_local_count_below_capture"
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_local_count_below_capture"
    assert capture.annotation_count == 2


def test_refuses_promotion_when_visible_set_exceeds_100(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.add_all([
        ub.Annotation(
            user_id=USER_ID,
            book_id=BOOK_ID,
            annotation_id=f"local-{index:03d}",
            source="webreader",
            hidden=False,
        )
        for index in range(101)
    ])
    session.commit()
    upstream = _upstream_response([])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.quarantine_reason == "seed_local_set_requires_pagination"
    assert capture.result == "rejected"
    assert capture.annotation_count == 0


def test_persists_every_page_but_quarantines_multi_page_capture(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    responses = iter([
        _upstream_response([_wire_annotation("page-1")], next_offset="cursor-2"),
        _upstream_response([_wire_annotation("page-2")]),
    ])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: next(responses))

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app, offset="cursor-2"):
        g.annotation_origin_device_id = DEVICE_A
        second = rs.handle_annotations.__wrapped__(OWNED)

    assert first.status_code == second.status_code == 200
    pages = session.query(ub.KoboAnnotationSeedCapturePage).order_by(
        ub.KoboAnnotationSeedCapturePage.page_number,
    ).all()
    assert [(p.page_number, p.request_offset_token, p.next_offset_token) for p in pages] == [
        (0, None, "cursor-2"), (1, "cursor-2", None),
    ]
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.quarantine_reason == "seed_capture_requires_pagination"
    assert capture.page_count == 2
    assert capture.annotation_count == 2


def test_final_page_processing_is_idempotent(app, session, monkeypatch):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session)
    session.commit()
    response = _upstream_response([_wire_annotation("once")])

    with _request(app):
        capture_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
        assert seeding.record_proxy_response(
            capture_id,
            response=response,
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        ) is True
        assert seeding.record_proxy_response(
            capture_id,
            response=response,
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        ) is False

    session.refresh(state)
    assert state.authority_revision == 1
    assert session.query(ub.KoboAnnotationSeedCapture).count() == 1
    assert session.query(ub.KoboAnnotationSeedCapturePage).count() == 1
    assert session.query(ub.Annotation).count() == 1


def test_ever_authoritative_gate_failure_keeps_get_local_and_patch_byte_exact(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="quarantined", ever=True, content_id=OWNED)
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("sticky local authority resumed Kobo forwarding"),
    )
    monkeypatch.setattr(
        rs.config, "config_kobo_two_way_annotation_sync", False, raising=False,
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json={},
    ):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fetched = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 204
    assert response.get_data() == b""
    assert response.headers["Content-Type"] == "text/html"
    assert response.headers["Content-Length"] == "0"
    assert fetched.status_code == 200
    assert json.loads(fetched.get_data())["annotations"] == []


def test_local_get_ignores_if_none_match_and_never_returns_304(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 1
    session.flush()
    session.add(ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=DEVICE_A,
        completed_at=datetime.now(timezone.utc),
        annotation_count=0,
        page_count=1,
        result="accepted",
        seed_kind="upstream_capture",
    ))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("eligible local GET unexpectedly proxied"),
    )

    with _request(app, headers={"If-None-Match": "*"}):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    assert response.status_code != 304
    assert json.loads(response.get_data()) == {
        "annotations": [], "nextPageOffsetToken": None,
    }
    assert response.headers["ETag"].startswith('W/"CWNG:')


def test_mixed_active_devices_use_routing_only_seed_on_their_next_local_get(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _device(session, DEVICE_B)
    _state(session)
    session.commit()
    upstream = _upstream_response([_wire_annotation("shared")])
    proxy_devices = []

    def proxy(**_kwargs):
        proxy_devices.append(g.annotation_origin_device_id)
        return upstream

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", proxy)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)

    state = session.query(ub.KoboAnnotationBookState).one()
    partial = seeding.seed_coverage(user_id=USER_ID, book_state_id=state.id)
    assert first.status_code == 200
    assert partial == {
        "active_device_count": 2,
        "accepted_device_count": 1,
        "missing_device_count": 1,
        "consistently_local": False,
        "books_partially_seeded": 1,
    }

    with _request(app):
        g.annotation_origin_device_id = DEVICE_B
        second = rs.handle_annotations.__wrapped__(OWNED)

    complete = seeding.seed_coverage(user_id=USER_ID, book_state_id=state.id)
    assert second.status_code == 200
    assert proxy_devices == [DEVICE_A]
    assert complete == {
        "active_device_count": 2,
        "accepted_device_count": 2,
        "missing_device_count": 0,
        "consistently_local": True,
        "books_partially_seeded": 0,
    }
    assert session.query(ub.KoboAnnotationSeedCapture).count() == 2
    device_b_capture = (
        session.query(ub.KoboAnnotationSeedCapture)
        .filter_by(device_id=DEVICE_B)
        .one()
    )
    assert device_b_capture.seed_kind == "routing_only"
    assert device_b_capture.result == "accepted"
    annotation = session.query(ub.Annotation).one()
    assert annotation.origin_device_id == DEVICE_A


def test_new_device_patch_x_then_get_never_proxies_stale_set_without_x(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _device(session, DEVICE_B)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 1
    session.flush()
    _accepted_capture_with_page(
        session, state, DEVICE_A, [],
        completed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("ever-authoritative GET/PATCH contacted Kobo"),
    )
    payload = {"updatedAnnotations": [_wire_annotation("X")]}

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json=payload,
    ):
        g.annotation_origin_device_id = DEVICE_B
        patched = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_B
        fetched = rs.handle_annotations.__wrapped__(OWNED)

    assert patched.status_code == 204
    assert patched.get_data() == b""
    assert fetched.status_code == 200
    assert [row["id"] for row in json.loads(fetched.get_data())["annotations"]] == ["X"]
    device_seed = (
        session.query(ub.KoboAnnotationSeedCapture)
        .filter_by(device_id=DEVICE_B)
        .one()
    )
    assert device_seed.seed_kind == "routing_only"
    assert device_seed.result == "accepted"


def test_authoritative_delete_keeps_paired_get_local_with_newer_tombstone(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 1
    session.flush()
    captured_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    _accepted_capture_with_page(
        session, state, DEVICE_A, [_wire_annotation("victim")],
        completed_at=captured_at,
    )
    session.add(_annotation_row("victim"))
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("post-authority delete contacted stale Kobo"),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"deletedAnnotationIds": ["victim"]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fetched = rs.handle_annotations.__wrapped__(OWNED)

    row = session.query(ub.Annotation).filter_by(annotation_id="victim").one()
    assert patched.status_code == 204
    assert row.hidden is True
    assert row.server_modified_at.replace(tzinfo=timezone.utc) > captured_at
    assert fetched.status_code == 200
    assert json.loads(fetched.get_data())["annotations"] == []


def test_promotion_rejects_equal_count_when_captured_id_is_not_visible(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.add(_annotation_row("local-B", source="webreader"))
    session.commit()
    upstream = _upstream_response([_wire_annotation("captured-A")])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)
    # Simulate the final authoritative membership read losing captured-A while
    # retaining the same cardinality. Promotion must prove identities, not N.
    monkeypatch.setattr(seeding, "_visible_ids", lambda *_a: {"local-B"})

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        rs.handle_annotations.__wrapped__(OWNED)

    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.quarantine_reason == "seed_local_set_missing_captured_id"
    assert capture.result == "rejected"


def test_render_never_serves_equal_count_with_different_visible_id(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(
        session, status="authoritative", ever=False, content_id=OWNED,
    )
    state.authority_revision = 1
    session.flush()
    _accepted_capture_with_page(
        session, state, DEVICE_A, [_wire_annotation("captured-A")],
    )
    session.add(_annotation_row("local-B", source="webreader"))
    session.commit()
    upstream = _upstream_response([_wire_annotation("captured-A")])
    proxy_calls = []
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_k: proxy_calls.append(True) or upstream,
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert proxy_calls == [True]
    assert response is upstream
    assert [row["id"] for row in json.loads(response.get_data())["annotations"]] == [
        "captured-A",
    ]


def test_rejected_stale_generic_capture_never_serves_stale_raw_sidecar(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    stale = _wire_annotation("same-id")
    stale["noteText"] = "stale-kobo-note"
    session.add(_annotation_row(
        "same-id",
        note_text="new-local-note",
        client_modified_at=datetime(2026, 8, 29, 2, 0, 0),
        server_modified_at=datetime(2026, 8, 29, 2, 0, 1),
        content_revision=4,
    ))
    session.commit()
    upstream = _upstream_response([stale])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        rs.handle_annotations.__wrapped__(OWNED)

    row = session.query(ub.Annotation).filter_by(annotation_id="same-id").one()
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert row.note_text == "new-local-note"
    assert session.query(ub.KoboAnnotationMaterialization).count() == 0
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_row_conflict_unresolved"
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False


def test_browser_edit_server_revision_survives_equal_client_clock_capture(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.add(_annotation_row("browser-edited", note_text="old-note"))
    session.commit()
    edited = annotations_api.edit_annotation(
        "browser-edited",
        user_id=USER_ID,
        book_id=BOOK_ID,
        session=session,
        commit=session.commit,
        note="new-browser-note",
    )
    assert edited.content_revision == 2
    assert edited.server_modified_at is not None
    stale = _wire_annotation("browser-edited")
    stale["noteText"] = "old-note"
    response = _upstream_response([stale])

    with _request(app):
        capture_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
        assert seeding.record_proxy_response(
            capture_id,
            response=response,
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        ) is True

    session.refresh(edited)
    assert edited.note_text == "new-browser-note"
    assert edited.content_revision == 2
    assert session.query(ub.KoboAnnotationMaterialization).count() == 0


def test_later_device_capture_rejection_does_not_quarantine_authoritative_book(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    _device(session, DEVICE_B)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 7
    session.flush()
    _accepted_capture_with_page(session, state, DEVICE_A, [])
    session.commit()

    with _request(app):
        capture_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_B,
            request_offset_token=None,
            log=rs.log,
        )
        assert seeding.record_proxy_response(
            capture_id,
            response=_upstream_response(
                [_wire_annotation("page-a")], next_offset="cursor-2",
            ),
            book=book,
            user=rs.current_user,
            device_id=DEVICE_B,
            request_offset_token=None,
            log=rs.log,
        ) is True
        assert seeding.record_proxy_response(
            capture_id,
            response=_upstream_response([_wire_annotation("page-b")]),
            book=book,
            user=rs.current_user,
            device_id=DEVICE_B,
            request_offset_token="cursor-2",
            log=rs.log,
        ) is True

    session.refresh(state)
    capture = session.get(ub.KoboAnnotationSeedCapture, capture_id)
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_capture_requires_pagination"
    assert state.authority_status == "authoritative"
    assert state.quarantine_reason is None


def test_authenticated_quarantine_retry_reopens_book_for_seeding(
    app, session, monkeypatch,
):
    state = _state(session, status="quarantined", ever=False)
    state.authority_revision = 3
    state.quarantine_reason = "seed_local_set_missing_captured_id"
    session.commit()
    monkeypatch.setattr(kobo_two_way_api, "_book_titles", lambda _ids: {})

    monkeypatch.setattr(
        kobo_two_way_api,
        "current_user",
        SimpleNamespace(is_authenticated=False, is_anonymous=True),
    )
    with app.test_request_context(
        "/api/v1/account/kobo-two-way-annotations/books/retry",
        method="POST",
        json={"book_id": BOOK_ID},
    ):
        unauthorized = kobo_two_way_api.retry_quarantined_kobo_two_way_book()
    assert unauthorized[1] == 401

    monkeypatch.setattr(kobo_two_way_api, "current_user", rs.current_user)

    with app.test_request_context(
        "/api/v1/account/kobo-two-way-annotations/books/retry",
        method="POST",
        json={"book_id": BOOK_ID},
    ):
        response = kobo_two_way_api.retry_quarantined_kobo_two_way_book()

    session.refresh(state)
    assert response.status_code == 200
    assert state.authority_status == "unseeded"
    assert state.authority_revision == 4
    assert state.quarantine_reason is None


def test_dead_paginated_cursor_is_superseded_by_new_first_page_capture(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    with _request(app):
        first_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
        seeding.record_proxy_response(
            first_id,
            response=_upstream_response([], next_offset="dead-cursor"),
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
        replacement_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )

    assert replacement_id != first_id
    assert session.get(ub.KoboAnnotationSeedCapture, first_id).result == "failed"
    assert (
        session.get(ub.KoboAnnotationSeedCapture, first_id).failure_reason
        == "seed_capture_superseded"
    )
    assert session.get(ub.KoboAnnotationSeedCapture, replacement_id).result == "pending"
    assert session.query(ub.KoboAnnotationBookState).one().authority_status == "seeding"


def test_expired_pending_capture_restarts_and_releases_owner(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    with _request(app):
        first_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
    first = session.get(ub.KoboAnnotationSeedCapture, first_id)
    first.started_at = datetime.now(timezone.utc) - seeding.PENDING_CAPTURE_TTL - timedelta(seconds=1)
    session.commit()
    with _request(app):
        replacement_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )

    session.refresh(first)
    assert replacement_id != first_id
    assert first.result == "failed"
    assert first.failure_reason == "seed_capture_expired"


def test_sqlite_enforces_one_pending_reconciliation_owner_per_book(session):
    _device(session, DEVICE_A)
    _device(session, DEVICE_B)
    state = _state(session)
    session.commit()
    session.add(ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=DEVICE_A,
        started_authority_revision=0,
        result="pending",
    ))
    session.commit()
    session.add(ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=DEVICE_B,
        started_authority_revision=0,
        result="pending",
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert (
        session.query(ub.KoboAnnotationSeedCapture)
        .filter_by(book_state_id=state.id, result="pending")
        .count()
        == 1
    )


def test_stale_capture_authority_revision_is_rejected_before_reconciliation(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session)
    session.commit()
    with _request(app):
        capture_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
    state.authority_revision = 1
    session.commit()
    with _request(app):
        accepted = seeding.record_proxy_response(
            capture_id,
            response=_upstream_response([_wire_annotation("stale")]),
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )

    capture = session.get(ub.KoboAnnotationSeedCapture, capture_id)
    assert accepted is True
    assert capture.result == "failed"
    assert capture.failure_reason == "seed_authority_revision_changed"
    assert session.query(ub.Annotation).count() == 0


def test_new_device_first_get_commits_routing_only_before_rendering_live_set(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_B)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 4
    session.add(_annotation_row("server-A"))
    session.commit()
    original_render = authority.render_owned_annotations
    proof_seen_before_render = []

    def render_with_precondition(**kwargs):
        capture = (
            session.query(ub.KoboAnnotationSeedCapture)
            .filter_by(device_id=DEVICE_B, result="accepted")
            .one()
        )
        proof_seen_before_render.append(capture.seed_kind)
        return original_render(**kwargs)

    monkeypatch.setattr(authority, "render_owned_annotations", render_with_precondition)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("download-shaped first GET unexpectedly proxied"),
    )

    with _request(app, headers={"If-None-Match": 'W/"A:kobo-manifest"'}):
        g.annotation_origin_device_id = DEVICE_B
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    assert proof_seen_before_render == ["routing_only"]
    assert [row["id"] for row in json.loads(response.get_data())["annotations"]] == [
        "server-A",
    ]


def test_prior_cwng_etag_on_authoritative_book_returns_current_local_set_200_never_proxy(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_B)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 4
    session.add(_annotation_row("server-A"))
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("prior CWNG possession resumed stale Kobo proxy"),
    )
    with _request(
        app,
        headers={"If-None-Match": 'W/"CWNG:old-generation:3:deadbeef"'},
    ):
        g.annotation_origin_device_id = DEVICE_B
        response = rs.handle_annotations.__wrapped__(OWNED)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("device-X")]},
    ):
        g.annotation_origin_device_id = DEVICE_B
        patched = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app, headers={"If-None-Match": response.headers["ETag"]}):
        g.annotation_origin_device_id = DEVICE_B
        refreshed = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    assert response.status_code != 304
    assert response.headers["ETag"].startswith('W/"CWNG:')
    assert [row["id"] for row in json.loads(response.get_data())["annotations"]] == [
        "server-A",
    ]
    assert patched.status_code == 204
    assert patched.get_data() == b""
    assert patched.headers["Content-Type"] == "text/html"
    assert patched.headers["Content-Length"] == "0"
    assert refreshed.status_code == 200
    assert refreshed.status_code != 304
    assert {row["id"] for row in json.loads(refreshed.get_data())["annotations"]} == {
        "server-A", "device-X",
    }
    assert session.query(ub.KoboAnnotationSeedCapture).filter_by(
        device_id=DEVICE_B, seed_kind="routing_only",
    ).count() == 1


def test_dual_live_query_failure_serves_exact_last_complete_snapshot(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 4
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("must-survive"))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("authoritative query failure proxied stale Kobo set"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    first_body = first.get_data()
    first_etag = first.headers["ETag"]
    session.refresh(state)
    assert gzip.decompress(state.last_served_body_gzip) == first_body
    assert state.last_served_body_sha256 == hashlib.sha256(first_body).hexdigest()
    assert state.last_served_authority_revision == state.authority_revision
    assert state.last_served_set_digest == state.set_digest

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("simulated live membership read failure")

    monkeypatch.setattr(authority, "_annotation_rows", fail_query)
    monkeypatch.setattr(authority, "_simple_annotation_rows", fail_query)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        replayed = rs.handle_annotations.__wrapped__(OWNED)

    assert replayed.status_code == 200
    assert replayed.get_data() == first_body
    assert replayed.headers["ETag"] == first_etag
    assert [
        row["id"] for row in json.loads(replayed.get_data())["annotations"]
    ] == ["must-survive"]


def test_readable_membership_failure_rejects_snapshot_after_authority_revision_moves(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 4
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("server-complete"))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("membership recovery proxied stale Kobo set"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    _accepted_capture_with_page(
        session, state, DEVICE_A, [_wire_annotation("captured-but-missing")],
    )
    # Production acceptance advances the book revision. The helper constructs
    # already-accepted evidence directly, so mirror that mutation boundary.
    state.authority_revision += 1
    state.set_digest = None
    state.current_etag = None
    session.commit()

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        recovered = rs.handle_annotations.__wrapped__(OWNED)

    assert recovered.status_code == 503
    assert recovered.get_data() != first.get_data()
    assert recovered.get_data() != b'{"annotations":[],"nextPageOffsetToken":null}'


def test_upgraded_pre_r4_authoritative_row_without_snapshot_first_get_failure_is_terminal_503(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("unreadable-not-empty"))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("terminal authoritative fallback proxied Kobo"),
    )

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("simulated first-render failure")

    monkeypatch.setattr(authority, "_annotation_rows", fail_query)
    monkeypatch.setattr(authority, "_simple_annotation_rows", fail_query)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 503
    assert response.get_data() != b'{"annotations":[],"nextPageOffsetToken":null}'


def test_get_a_patch_b_then_dual_failure_never_replays_pre_patch_snapshot_a(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 4
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("A"))
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("ever-authoritative fallback contacted Kobo"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    session.refresh(state)
    snapshot_revision = state.last_served_authority_revision
    snapshot_digest = state.last_served_set_digest
    assert [row["id"] for row in json.loads(first.get_data())["annotations"]] == ["A"]
    assert snapshot_revision == state.authority_revision
    assert snapshot_digest == state.set_digest

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("B")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)
    session.refresh(state)
    assert patched.status_code == 204
    assert patched.get_data() == b""
    assert patched.headers["Content-Type"] == "text/html"
    assert patched.headers["Content-Length"] == "0"
    assert {row.annotation_id for row in session.query(ub.Annotation).filter_by(
        hidden=False,
    )} == {"A", "B"}
    assert state.authority_revision == snapshot_revision + 1
    assert state.set_digest is None
    assert state.current_etag is None
    assert state.last_served_authority_revision == snapshot_revision
    assert state.last_served_set_digest == snapshot_digest

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("simulated post-PATCH dual live-read failure")

    monkeypatch.setattr(authority, "_annotation_rows", fail_query)
    monkeypatch.setattr(authority, "_simple_annotation_rows", fail_query)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fallback = rs.handle_annotations.__wrapped__(OWNED)

    assert fallback.status_code == 503
    assert fallback.get_data() != first.get_data()


def test_live_render_a_b_with_snapshot_commit_failure_never_returns_old_a(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 4
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("A"))
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("ever-authoritative render contacted Kobo"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("B")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)

    def fail_snapshot_commit(**_kwargs):
        raise RuntimeError("simulated SQLite snapshot commit failure")

    monkeypatch.setattr(authority, "_commit_complete_render", fail_snapshot_commit)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        rendered = rs.handle_annotations.__wrapped__(OWNED)

    assert patched.status_code == 204
    assert rendered.status_code == 200
    assert rendered.get_data() != first.get_data()
    assert {row["id"] for row in json.loads(rendered.get_data())["annotations"]} == {
        "A", "B",
    }
    session.refresh(state)
    assert state.authority_revision > state.last_served_authority_revision
    assert state.set_digest is None


def test_annotation_durable_but_watermark_failed_is_impossible(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        authority, "advance_authoritative_patch_revision", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("failed local authority commit proxied PATCH"),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("persisted-before-failure")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 503
    assert response.status_code != 204
    assert session.query(ub.Annotation).filter_by(
        annotation_id="persisted-before-failure",
    ).one_or_none() is None
    session.refresh(state)
    assert state.authority_revision == 0
    assert state.set_digest is None


def test_forced_combined_commit_failure_rolls_back_annotation_and_watermark(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 6
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.commit()
    outcomes = []
    ticket = SimpleNamespace(
        spool_id="atomic-final-commit",
        mark_dispatch_outcome=lambda status: outcomes.append(status) or True,
    )
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: ticket)

    def fail_checked_commit():
        session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", fail_checked_commit)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("rolled-back-with-watermark")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 503
    assert outcomes == ["dispatch_refused"]
    assert session.query(ub.Annotation).filter_by(
        annotation_id="rolled-back-with-watermark",
    ).one_or_none() is None
    persisted_state = session.query(ub.KoboAnnotationBookState).filter_by(
        user_id=USER_ID, book_id=BOOK_ID,
    ).one()
    assert persisted_state.authority_revision == 6
    assert persisted_state.set_digest is None


def test_successful_mixed_owned_patch_uses_one_checked_request_commit(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 14
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("A"))
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    commit_calls = []

    def checked_commit():
        commit_calls.append(True)
        session.commit()
        return True

    monkeypatch.setattr(ub, "session_commit", checked_commit)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={
            "updatedAnnotations": [_wire_annotation("B")],
            "deletedAnnotationIds": ["A"],
        },
    ):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 204
    assert response.get_data() == b""
    assert commit_calls == [True]
    rows = {
        row.annotation_id: row
        for row in session.query(ub.Annotation).order_by(ub.Annotation.annotation_id)
    }
    assert set(rows) == {"A", "B"}
    assert rows["A"].hidden is True
    assert rows["B"].hidden is False
    session.refresh(state)
    assert state.authority_revision == 15
    assert state.set_digest is None


def test_reviewer_exact_snapshot_a_patch_b_watermark_failure_ordering_rolls_back(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 9
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("A"))
    session.commit()
    outcomes = []
    ticket = SimpleNamespace(
        spool_id="atomic-review-ordering",
        mark_dispatch_outcome=lambda status: outcomes.append(status) or True,
    )
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: ticket)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("ever-authoritative request contacted Kobo"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        snapshot_a = rs.handle_annotations.__wrapped__(OWNED)
    session.refresh(state)
    snapshot_revision = state.authority_revision
    snapshot_digest = state.set_digest
    assert [row["id"] for row in json.loads(snapshot_a.get_data())["annotations"]] == ["A"]

    monkeypatch.setattr(
        authority, "advance_authoritative_patch_revision", lambda *_a, **_k: False,
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("B")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        refused = rs.handle_annotations.__wrapped__(OWNED)

    assert refused.status_code == 503
    assert outcomes == ["dispatch_refused"]
    assert session.query(ub.Annotation).filter_by(annotation_id="B").one_or_none() is None
    session.refresh(state)
    assert state.authority_revision == snapshot_revision
    assert state.set_digest == snapshot_digest

    def fail_query(*_args, **_kwargs):
        raise RuntimeError("simulated dual live-query failure after refused PATCH")

    monkeypatch.setattr(authority, "_annotation_rows", fail_query)
    monkeypatch.setattr(authority, "_simple_annotation_rows", fail_query)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fallback = rs.handle_annotations.__wrapped__(OWNED)

    # The old bytes remain eligible only because B never became durable and
    # Nickel received the established retryable PATCH failure, not a 204.
    assert fallback.status_code == 200
    assert fallback.get_data() == snapshot_a.get_data()
    assert [row.annotation_id for row in session.query(ub.Annotation).filter_by(
        hidden=False,
    )] == ["A"]


def test_partial_batch_persistence_never_leaves_revision_matched_stale_snapshot(
    app, session, monkeypatch,
):
    from cps.services import annotation_sync

    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 12
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("A"))
    session.commit()
    outcomes = []
    ticket = SimpleNamespace(
        spool_id="atomic-partial-batch",
        mark_dispatch_outcome=lambda status: outcomes.append(status) or True,
    )
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: ticket)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        snapshot_a = rs.handle_annotations.__wrapped__(OWNED)
    session.refresh(state)
    snapshot_revision = state.authority_revision
    snapshot_digest = state.set_digest

    original_upsert = annotation_sync._upsert_annotation

    def fail_second_member(db_session, payload, book, user, **kwargs):
        if payload.get("id") == "C":
            raise RuntimeError("forced second-member failure")
        return original_upsert(db_session, payload, book, user, **kwargs)

    monkeypatch.setattr(annotation_sync, "_upsert_annotation", fail_second_member)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("B"), _wire_annotation("C")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        refused = rs.handle_annotations.__wrapped__(OWNED)

    assert refused.status_code == 503
    assert outcomes == ["dispatch_refused"]
    assert session.query(ub.Annotation).filter(
        ub.Annotation.annotation_id.in_(["B", "C"]),
    ).count() == 0
    session.refresh(state)
    assert state.authority_revision == snapshot_revision
    assert state.set_digest == snapshot_digest
    assert state.last_served_authority_revision == snapshot_revision
    assert state.last_served_set_digest == snapshot_digest
    assert json.loads(snapshot_a.get_data())["annotations"][0]["id"] == "A"


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        ("create", {"updatedAnnotations": [_wire_annotation("B")]}),
        ("edit", {"updatedAnnotations": [_wire_annotation("A")]}),
        ("delete", {"deletedAnnotationIds": ["A"]}),
        ("batch_mix", {
            "updatedAnnotations": [_wire_annotation("B")],
            "deletedAnnotationIds": ["A"],
        }),
        ("delete_refusal_mix", {
            "updatedAnnotations": [_wire_annotation("B")],
            "deletedAnnotationIds": ["web-owned"],
        }),
    ],
)
def test_owned_patch_branch_sweep_rolls_back_mutation_and_watermark_together(
    app, session, monkeypatch, case, payload,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 21
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add_all([
        _annotation_row("A", highlighted_text="original A"),
        _annotation_row("web-owned", source="webreader", highlighted_text="web row"),
    ])
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        authority, "advance_authoritative_patch_revision", lambda *_a, **_k: False,
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json=payload,
    ):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 503, case
    rows = {
        row.annotation_id: row
        for row in session.query(ub.Annotation).order_by(ub.Annotation.annotation_id)
    }
    assert set(rows) == {"A", "web-owned"}, case
    assert rows["A"].highlighted_text == "original A", case
    assert rows["A"].hidden is False, case
    assert rows["web-owned"].hidden is False, case
    session.refresh(state)
    assert state.authority_revision == 21, case


def test_corrupt_authoritative_capture_rebuilds_live_proof_never_503_and_recovers(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 2
    session.flush()
    corrupt = _accepted_capture_with_page(
        session, state, DEVICE_A, [_wire_annotation("live-A")],
    )
    session.flush()
    corrupt.pages[0].response_body_gzip = b"not-gzip"
    session.add(_annotation_row("live-A"))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("corrupt historical proof resumed stale proxy"),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    session.refresh(state)
    session.refresh(corrupt)
    assert response.status_code == 200
    assert response.status_code != 503
    assert [row["id"] for row in json.loads(response.get_data())["annotations"]] == [
        "live-A",
    ]
    assert corrupt.result == "failed"
    assert state.authority_status == "authoritative"
    assert state.quarantine_reason == "capture_evidence_rebuilt_live"
    assert session.query(ub.KoboAnnotationSeedCapture).filter_by(
        device_id=DEVICE_A, result="accepted", seed_kind="routing_only",
    ).count() == 1

    monkeypatch.setattr(kobo_two_way_api, "_book_titles", lambda _ids: {})
    monkeypatch.setattr(kobo_two_way_api, "current_user", rs.current_user)
    with app.test_request_context(
        "/api/v1/account/kobo-two-way-annotations/books/retry",
        method="POST",
        json={"book_id": BOOK_ID},
    ):
        recovered = kobo_two_way_api.retry_quarantined_kobo_two_way_book()
    session.refresh(state)
    assert recovered.status_code == 200
    assert state.authority_status == "authoritative"
    assert state.quarantine_reason is None


def test_ever_authoritative_lookup_failure_uses_known_local_evidence_for_get_patch(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 2
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add(_annotation_row("known-local"))
    session.commit()
    monkeypatch.setattr(
        authority, "ever_authoritative",
        lambda *_a: authority.AUTHORITY_LOOKUP_FAILED,
    )
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("ambiguous first lookup swallowed sticky evidence"),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json={},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fetched = rs.handle_annotations.__wrapped__(OWNED)

    assert patched.status_code == 204
    assert patched.headers["Content-Type"] == "text/html"
    assert patched.headers["Content-Length"] == "0"
    assert fetched.status_code == 200
    assert [row["id"] for row in json.loads(fetched.get_data())["annotations"]] == [
        "known-local",
    ]


def test_authority_lookup_failed_refuses_get_but_patch_keeps_status_quo(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session, status="unseeded", ever=False, content_id=OWNED)
    session.commit()
    monkeypatch.setattr(
        authority, "ever_authoritative",
        lambda *_a: authority.AUTHORITY_LOOKUP_FAILED,
    )
    monkeypatch.setattr(
        authority, "authority_evidence_for_route",
        lambda *_a: authority.AUTHORITY_LOOKUP_FAILED,
    )
    monkeypatch.setattr(
        rs.config, "config_kobo_two_way_annotation_sync", False, raising=False,
    )
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    proxy_calls = []

    def proxy(**_kwargs):
        proxy_calls.append(request.method)
        return _upstream_response([])

    from flask import request
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", proxy)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fetched = rs.handle_annotations.__wrapped__(OWNED)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json={},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)

    assert fetched.status_code == 503
    assert patched.status_code == 200
    assert proxy_calls == ["PATCH"]


def test_authoritative_patch_growth_over_100_flags_and_serves_complete_single_page(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(
        session, status="authoritative", ever=True, content_id=OWNED,
    )
    state.authority_revision = 5
    session.flush()
    _accepted_routing_capture(session, state, DEVICE_A)
    session.add_all([_annotation_row(f"row-{index:03d}") for index in range(100)])
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("oversize authoritative set contacted stale Kobo"),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations",
        method="PATCH",
        json={"updatedAnnotations": [_wire_annotation("row-100")]},
    ):
        g.annotation_origin_device_id = DEVICE_A
        patched = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        fetched = rs.handle_annotations.__wrapped__(OWNED)

    session.refresh(state)
    payload = json.loads(fetched.get_data())
    assert patched.status_code == 204
    assert patched.get_data() == b""
    assert patched.headers["Content-Type"] == "text/html"
    assert patched.headers["Content-Length"] == "0"
    assert state.quarantine_reason == "oversize_single_page"
    assert fetched.status_code == 200
    assert len(payload["annotations"]) == 101
    assert {row["id"] for row in payload["annotations"]} == {
        *(f"row-{index:03d}" for index in range(101)),
    }
    assert payload["nextPageOffsetToken"] is None


def test_clock_skewed_kobo_row_uses_server_baseline_and_blocks_initial_promotion(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session, status="unseeded", ever=False, content_id=OWNED)
    future_clock = datetime(2035, 1, 1, 0, 0, 0)
    session.add(_annotation_row(
        "skewed",
        note_text="newer-local",
        client_modified_at=future_clock,
        server_modified_at=datetime(2026, 8, 28, 0, 0, 0),
        content_revision=7,
    ))
    session.commit()
    stale = _wire_annotation("skewed")
    stale["noteText"] = "stale-cloud"
    stale["clientLastModifiedUtc"] = "2035-01-01T00:00:00.000Z"
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: _upstream_response([stale]),
    )

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    row = session.query(ub.Annotation).filter_by(annotation_id="skewed").one()
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    baseline = session.query(ub.KoboAnnotationSeedRowBaseline).one()
    assert response.status_code == 200
    assert row.note_text == "newer-local"
    assert row.content_revision == 7
    assert baseline.annotation_row_id == row.id
    assert baseline.content_revision == 7
    assert baseline.content_sha256
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_row_conflict_unresolved"
    assert capture.reconciliation_conflict_count == 1
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False
    assert state.quarantine_reason == "seed_row_conflict_unresolved"
    assert session.query(ub.KoboAnnotationMaterialization).count() == 0


def test_row_changed_after_page_commit_fails_baseline_cas_and_keeps_local(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session, status="unseeded", ever=False, content_id=OWNED)
    row = _annotation_row("raced")
    session.add(row)
    session.commit()
    original_reconcile = seeding._reconcile_and_promote

    def mutate_then_reconcile(capture_id, **kwargs):
        current = session.query(ub.Annotation).filter_by(annotation_id="raced").one()
        current.note_text = "local-after-page-commit"
        current.content_revision += 1
        current.server_modified_at = datetime.now(timezone.utc)
        session.commit()
        return original_reconcile(capture_id, **kwargs)

    monkeypatch.setattr(seeding, "_reconcile_and_promote", mutate_then_reconcile)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: _upstream_response([_wire_annotation("raced")]),
    )
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        rs.handle_annotations.__wrapped__(OWNED)

    session.refresh(row)
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert row.note_text == "local-after-page-commit"
    assert row.content_revision == 2
    assert capture.reconciliation_conflict_count == 1
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_row_conflict_unresolved"
    state = session.query(ub.KoboAnnotationBookState).one()
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False


def test_inverse_conflict_newer_device_copy_never_promotes_older_local_row(
    app, session, monkeypatch,
):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="unseeded", ever=False, content_id=OWNED)
    session.add(_annotation_row(
        "same-id",
        note_text="older-local",
        client_modified_at=datetime(2026, 8, 20, 1, 0, 0),
        server_modified_at=datetime(2026, 8, 20, 1, 0, 1),
        content_revision=1,
    ))
    session.commit()
    newer_device = _wire_annotation("same-id")
    newer_device["noteText"] = "newer-device"
    newer_device["clientLastModifiedUtc"] = "2026-08-29T01:00:00.000Z"
    proxy_calls = []

    def proxy(**_kwargs):
        proxy_calls.append(True)
        return _upstream_response([newer_device])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", proxy)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        triggering_get = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        next_get = rs.handle_annotations.__wrapped__(OWNED)

    session.refresh(state)
    local = session.query(ub.Annotation).filter_by(annotation_id="same-id").one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    page = session.query(ub.KoboAnnotationSeedCapturePage).one()
    captured_payload = json.loads(gzip.decompress(page.response_body_gzip))
    assert json.loads(triggering_get.get_data())["annotations"][0]["noteText"] == \
        "newer-device"
    assert json.loads(next_get.get_data())["annotations"][0]["noteText"] == \
        "newer-device"
    assert proxy_calls == [True, True]
    assert local.note_text == "older-local"
    assert local.origin_device_id is None
    assert captured_payload["annotations"][0]["noteText"] == "newer-device"
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_row_conflict_unresolved"
    assert capture.reconciliation_conflict_count == 1
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False
    assert state.quarantine_reason == "seed_row_conflict_unresolved"

    monkeypatch.setattr(kobo_two_way_api, "_book_titles", lambda _ids: {})
    monkeypatch.setattr(kobo_two_way_api, "current_user", rs.current_user)
    with app.test_request_context(
        "/api/v1/account/kobo-two-way-annotations/books/retry",
        method="POST",
        json={"book_id": book.id},
    ):
        recovered = kobo_two_way_api.retry_quarantined_kobo_two_way_book()
    session.refresh(state)
    assert recovered.status_code == 200
    assert state.authority_status == "unseeded"
    assert state.quarantine_reason is None


def test_additive_migration_is_idempotent_and_backfills_safety_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pre-m2.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE kobo_annotation_book_state (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL, content_id TEXT NOT NULL,
                authority_status TEXT NOT NULL, authority_revision INTEGER NOT NULL,
                generation_id TEXT, opaque_content_status TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE kobo_annotation_seed_capture (
                id INTEGER PRIMARY KEY, book_state_id INTEGER NOT NULL,
                device_id INTEGER, started_at DATETIME, completed_at DATETIME,
                annotation_count INTEGER, page_count INTEGER, result TEXT NOT NULL,
                failure_reason TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE kobo_annotation_seed_capture_page (
                id INTEGER PRIMARY KEY, seed_capture_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL, response_body_gzip BLOB NOT NULL,
                response_sha256 TEXT NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO kobo_annotation_book_state VALUES "
            "(1,107,540,'legacy-book:540','authoritative',1,'generation','absent')"
        ))
        conn.execute(text(
            "INSERT INTO kobo_annotation_seed_capture "
            "(id,book_state_id,annotation_count,page_count,result) "
            "VALUES (1,1,0,1,'accepted')"
        ))
        conn.execute(text(
            "INSERT INTO kobo_annotation_seed_capture "
            "(id,book_state_id,result) VALUES "
            "(2,1,'pending'),(3,1,'pending')"
        ))

    ub.migrate_kobo_annotation_seed_pipeline(engine, None)
    ub.migrate_kobo_annotation_seed_pipeline(engine, None)

    columns = {
        table: [column["name"] for column in inspect(engine).get_columns(table)]
        for table in (
            "kobo_annotation_book_state", "kobo_annotation_seed_capture",
        )
    }
    assert columns["kobo_annotation_book_state"].count("ever_authoritative") == 1
    for column_name in (
        "last_served_body_gzip",
        "last_served_body_sha256",
        "last_served_etag",
        "last_served_annotation_count",
        "last_served_authority_revision",
        "last_served_set_digest",
        "last_served_at",
    ):
        assert columns["kobo_annotation_book_state"].count(column_name) == 1
    assert columns["kobo_annotation_seed_capture"].count("seed_kind") == 1
    assert columns["kobo_annotation_seed_capture"].count(
        "started_authority_revision",
    ) == 1
    assert columns["kobo_annotation_seed_capture"].count(
        "reconciliation_conflict_count",
    ) == 1
    assert "kobo_annotation_seed_row_baseline" in inspect(engine).get_table_names()
    assert "uq_kasc_pending_book_owner" in {
        row["name"] for row in inspect(engine).get_indexes(
            "kobo_annotation_seed_capture",
        )
    }
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT ever_authoritative FROM kobo_annotation_book_state WHERE id=1"
        )).scalar_one() == 1
        assert conn.execute(text(
            "SELECT seed_kind FROM kobo_annotation_seed_capture WHERE id=1"
        )).scalar_one() == "routing_only"
        assert conn.execute(text(
            "SELECT result FROM kobo_annotation_seed_capture WHERE id=2"
        )).scalar_one() == "failed"
        assert conn.execute(text(
            "SELECT failure_reason FROM kobo_annotation_seed_capture WHERE id=2"
        )).scalar_one() == "seed_capture_superseded"
        assert conn.execute(text(
            "SELECT started_authority_revision "
            "FROM kobo_annotation_seed_capture WHERE id=3"
        )).scalar_one() == 1
    engine.dispose()
