# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned Kobo annotation requests are answered by CWNG on the wire (#1923)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.readingservices as rs
from cps.services import kobo_annotation_authority as authority
from cps.services.kobo_annotation_capture import extract_object_member_value


OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
BOOK_ID = 347
USER_ID = 7


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
    monkeypatch.setattr(
        rs, "current_user",
        SimpleNamespace(id=USER_ID, name="reader", is_authenticated=True),
    )
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    return application


def _annotation(annotation_id, annotation_type="highlight", **overrides):
    values = {
        "user_id": USER_ID,
        "book_id": BOOK_ID,
        "annotation_id": annotation_id,
        "source": "kobo",
        "annotation_type": annotation_type,
        "highlighted_text": "measured passage" if annotation_type == "highlight" else "",
        "highlight_color": "#F6F3B3" if annotation_type == "highlight" else None,
        "note_text": None,
        "content_id": f"{OWNED}!!OEBPS/chapter.xhtml",
        "chapter_progress": 0.25,
        "start_container_path": "p/1",
        "end_container_path": "p/1",
        "start_offset": 2,
        "end_offset": 18,
        "context_string": "surrounding context",
        "client_modified_at": datetime(2026, 8, 28, 5, 0, 0),
        "hidden": False,
        "content_revision": 1,
    }
    values.update(overrides)
    return ub.Annotation(**values)


def _owned(monkeypatch):
    book = SimpleNamespace(id=BOOK_ID, uuid=OWNED, title="Flatland", identifiers=[])
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("owned annotations request contacted Kobo"),
    )
    return book


def test_fallback_field_sets_match_measured_highlight_and_dogear_shapes():
    highlight, exact_highlight, _reason = authority._fallback_object(
        _annotation("highlight-1"), OWNED,
    )
    dogear, exact_dogear, dogear_reason = authority._fallback_object(
        _annotation("dogear-1", "dogear"), OWNED,
    )

    assert exact_highlight is True
    assert set(highlight) == {
        "attachments", "clientLastModifiedUtc", "context", "highlightColor",
        "highlightedText", "id", "location", "type",
    }
    assert set(highlight["location"]["span"]) == {
        "chapterFilename", "chapterProgress", "endChar", "endPath",
        "startChar", "startPath",
    }
    assert exact_dogear is False
    assert dogear_reason == "dogear_chapter_title_unavailable"
    assert set(dogear) == {
        "clientLastModifiedUtc", "context", "highlightedText", "id",
        "location", "type",
    }
    assert set(dogear["location"]["span"]) == {
        "chapterFilename", "chapterProgress", "chapterTitle", "endChar",
        "endPath", "startChar", "startPath",
    }
    assert "attachments" not in dogear
    assert "highlightColor" not in dogear


def test_note_column_fallback_preserves_note_text_and_identity():
    note, faithful, reason = authority._fallback_object(
        _annotation(
            "note-1", "note", highlighted_text="", note_text="Do not lose this note",
            highlight_color=None,
        ),
        OWNED,
    )

    assert faithful is False
    assert reason == "note_wire_shape_unproven"
    assert note["id"] == "note-1"
    assert note["type"] == "note"
    assert note["noteText"] == "Do not lose this note"
    assert note["location"]["span"]["chapterFilename"] == "OEBPS/chapter.xhtml"


def test_owned_patch_ack_has_measured_response_shape(app):
    with app.test_request_context(f"/annotations/{OWNED}", method="PATCH"):
        response = rs._owned_annotation_patch_ack(
            None, SimpleNamespace(id=BOOK_ID), OWNED,
        )

    assert response.status_code == 204
    assert response.get_data() == b""
    assert response.headers["Content-Type"] == "text/html"
    assert response.headers["Content-Length"] == "0"


def test_owned_get_is_full_200_raw_exact_if_none_match_ignored_and_etag_moves(
    app, session, monkeypatch,
):
    _owned(monkeypatch)
    raw = (
        b'{ "type" : "highlight", "id" : "a-raw", '
        b'"clientLastModifiedUtc" : "2026-08-28T05:00:00Z", '
        b'"context" : "raw context", "highlightedText" : "raw text", '
        b'"highlightColor" : "#E8AFCF", "attachments" : {}, '
        b'"location" : {"span":{"chapterFilename":"OEBPS/raw.xhtml",'
        b'"chapterProgress":0.5,"endChar":9,"endPath":"p/2",'
        b'"startChar":1,"startPath":"p/2"}} }'
    )
    raw_row = _annotation("a-raw", highlighted_text="parsed copy", highlight_color="#E8AFCF")
    mapped_row = _annotation("b-mapped")
    legacy_visible_row = _annotation("c-legacy-null", hidden=None)
    hidden_row = _annotation("d-hidden", hidden=True)
    session.add_all([raw_row, mapped_row, legacy_visible_row, hidden_row])
    session.flush()
    raw_location = extract_object_member_value(raw, "location")
    session.add(ub.KoboAnnotationMaterialization(
        annotation_id=raw_row.id,
        raw_annotation_json=raw,
        raw_location_json=raw_location,
        raw_client_modified_utc="2026-08-28T05:00:00Z",
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        materialization_revision=1,
        provenance="kobo_patch",
        attachments_state="empty",
        serveable=False,
    ))
    session.commit()

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
        headers={"If-None-Match": 'W/"CWNG:ignored:1:ignored"'},
    ):
        first = rs.handle_annotations.__wrapped__(OWNED)

    assert first.status_code == 200
    assert first.headers["Content-Type"] == "application/json"
    assert first.get_data().count(raw) == 1
    first_payload = json.loads(first.get_data())
    assert [row["id"] for row in first_payload["annotations"]] == [
        "a-raw", "b-mapped", "c-legacy-null",
    ]
    assert first_payload["nextPageOffsetToken"] is None
    assert re.fullmatch(
        r'W/"CWNG:[0-9a-f-]{36}:1:[0-9a-f]{16}"', first.headers["ETag"],
    )
    first_etag = first.headers["ETag"]

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
        headers={"If-None-Match": first_etag},
    ):
        matched = rs.handle_annotations.__wrapped__(OWNED)

    assert matched.status_code == 200
    assert matched.get_data() == first.get_data()
    assert matched.headers["ETag"] == first_etag

    mapped_row.highlighted_text = "changed set"
    mapped_row.content_revision += 1
    session.commit()
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        changed = rs.handle_annotations.__wrapped__(OWNED)

    assert changed.status_code == 200
    assert changed.headers["ETag"] != first_etag
    assert re.fullmatch(
        r'W/"CWNG:[0-9a-f-]{36}:2:[0-9a-f]{16}"', changed.headers["ETag"],
    )


def test_owned_get_returns_more_than_device_limit_as_one_honest_page(
    app, session, monkeypatch,
):
    _owned(monkeypatch)
    session.add_all([_annotation(f"row-{index:03d}") for index in range(101)])
    session.commit()

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        response = rs.handle_annotations.__wrapped__(OWNED)

    payload = response.get_json()
    assert response.status_code == 200
    assert len(payload["annotations"]) == 101
    assert payload["nextPageOffsetToken"] is None
