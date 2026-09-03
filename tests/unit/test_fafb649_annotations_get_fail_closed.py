# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""F-afb649: annotation GET failure paths must never revive Kobo authority."""

from __future__ import annotations

import gzip
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, make_response
from flask.wrappers import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.readingservices as rs


OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
BOOK_ID = 347
USER_ID = 7
SNAPSHOT = (
    b'{"annotations":[{"id":"device-only","type":"highlight"}],'
    b'"nextPageOffsetToken":null}'
)
SNAPSHOT_ETAG = 'W/"CWNG:00000000-0000-0000-0000-000000000001:4:' \
                + hashlib.sha256(SNAPSHOT).hexdigest()[:16] + '"'
STALE_KOBO = b'{"annotations":[],"nextPageOffsetToken":null}'


@pytest.fixture
def app_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    monkeypatch.setattr(
        rs, "current_user",
        SimpleNamespace(id=USER_ID, is_authenticated=True),
    )
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    yield Flask(__name__), database
    database.close()
    engine.dispose()


def _stored_authority(database, *, snapshot=True):
    digest = hashlib.sha256(SNAPSHOT).hexdigest()
    state = ub.KoboAnnotationBookState(
        user_id=USER_ID,
        book_id=BOOK_ID,
        content_id=OWNED,
        authority_status="authoritative",
        authority_revision=4,
        ever_authoritative=True,
        generation_id="00000000-0000-0000-0000-000000000001",
        set_digest=digest,
        current_etag=SNAPSHOT_ETAG,
        opaque_content_status="absent",
        seeded_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    if snapshot:
        state.last_served_body_gzip = gzip.compress(SNAPSHOT, mtime=0)
        state.last_served_body_sha256 = digest
        state.last_served_etag = SNAPSHOT_ETAG
        state.last_served_annotation_count = 1
        state.last_served_authority_revision = 4
        state.last_served_set_digest = digest
        state.last_served_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
    database.add_all([
        state,
        ub.Annotation(
            user_id=USER_ID,
            book_id=BOOK_ID,
            annotation_id="device-only",
            source="kobo",
            annotation_type="highlight",
            highlighted_text="must survive",
            content_id=f"{OWNED}!!OEBPS/chapter.xhtml",
            hidden=False,
        ),
    ])
    database.commit()


def _stale_proxy(monkeypatch):
    calls = []

    def proxy(**_kwargs):
        calls.append(True)
        return make_response(STALE_KOBO, 200, {"ETag": 'W/"kobo-stale"'})

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", proxy)
    return calls


@pytest.mark.unit
def test_lookup_exception_replays_current_snapshot_instead_of_proxying(
    app_session, monkeypatch,
):
    """A metadata.db swap cannot replace stored annotations with Kobo's set."""
    app, database = app_session
    _stored_authority(database)
    proxy_calls = _stale_proxy(monkeypatch)

    def metadata_db_swapped(_entitlement_id):
        raise RuntimeError("metadata.db was swapped mid-request")

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", metadata_db_swapped)

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
        headers={"If-None-Match": SNAPSHOT_ETAG},
    ):
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert proxy_calls == []
    assert response.status_code == 200
    assert response.get_data() == SNAPSHOT
    assert response.headers["ETag"] == SNAPSHOT_ETAG


@pytest.mark.unit
def test_live_unowned_without_durable_evidence_still_proxies(
    app_session, monkeypatch,
):
    app, _database = app_session
    proxy_calls = _stale_proxy(monkeypatch)
    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", lambda _content_id: None)

    with app.test_request_context(
        "/api/v3/content/provably-unowned/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations.__wrapped__("provably-unowned")

    assert proxy_calls == [True]
    assert response.status_code == 200
    assert response.get_data() == STALE_KOBO


@pytest.mark.unit
def test_annotation_only_evidence_prevents_proxy_when_live_lookup_is_unknown(
    app_session, monkeypatch,
):
    app, database = app_session
    database.add(ub.Annotation(
        user_id=USER_ID,
        book_id=BOOK_ID,
        annotation_id="stored-without-ledger",
        source="kobo",
        annotation_type="highlight",
        content_id=f"{OWNED.upper()}!!OEBPS/chapter.xhtml",
        hidden=False,
    ))
    database.commit()
    proxy_calls = _stale_proxy(monkeypatch)

    def metadata_db_swapped(_entitlement_id):
        raise RuntimeError("metadata.db was swapped mid-request")

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", metadata_db_swapped)

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert proxy_calls == []
    assert response.status_code == 503


@pytest.mark.unit
def test_body_read_failure_and_unknown_ownership_returns_503_without_proxy(
    app_session, monkeypatch,
):
    app, database = app_session
    _stored_authority(database, snapshot=False)
    proxy_calls = _stale_proxy(monkeypatch)

    def body_read_failed(_request, *args, **kwargs):
        raise OSError("client disconnected while request body was read")

    def metadata_db_swapped(_entitlement_id):
        raise RuntimeError("metadata.db was swapped mid-request")

    monkeypatch.setattr(Request, "get_data", body_read_failed)
    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", metadata_db_swapped)

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert proxy_calls == []
    assert response.status_code == 503


@pytest.mark.unit
@pytest.mark.parametrize("kobo_sync_enabled", [True, False])
def test_unauthenticated_owned_get_returns_503_without_proxy(
    app_session, monkeypatch, kobo_sync_enabled,
):
    """A lapsed session cannot expose the stale cloud replacement set."""
    app, database = app_session
    _stored_authority(database)
    proxy_calls = _stale_proxy(monkeypatch)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=None, is_authenticated=False),
    )
    monkeypatch.setattr(
        rs.config, "config_kobo_sync", kobo_sync_enabled, raising=False,
    )
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _entitlement_id: SimpleNamespace(id=BOOK_ID, uuid=OWNED),
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations(OWNED)

    assert proxy_calls == []
    assert response.status_code == 503


@pytest.mark.unit
def test_disabled_kobo_sync_still_replays_authenticated_current_snapshot(
    app_session, monkeypatch,
):
    app, database = app_session
    _stored_authority(database)
    proxy_calls = _stale_proxy(monkeypatch)
    monkeypatch.setattr(rs.config, "config_kobo_sync", False, raising=False)

    def metadata_db_swapped(_entitlement_id):
        raise RuntimeError("metadata.db was swapped mid-request")

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", metadata_db_swapped)

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations(OWNED)

    assert proxy_calls == []
    assert response.status_code == 200
    assert response.get_data() == SNAPSHOT
    assert response.headers["ETag"] == SNAPSHOT_ETAG
