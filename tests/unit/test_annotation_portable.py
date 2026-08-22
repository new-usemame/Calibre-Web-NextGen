# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 — device-agnostic portable annotation projection.

`to_portable(row)` is the wire shape the pull endpoint returns (the KOReader
plugin maps it to device-native fields). `apply_portable(payload, ...)` is the
push-side upsert: find-or-create by (user_id, annotation_id), populate from the
portable dict, record device_origin_id, and soft-delete on hidden=True.
The caller supplies the protocol's authority for deleting an existing row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub
from cps.progress_syncing.protocols.koreader_annotations import _DELETABLE_SOURCES
from cps.services.annotation_portable import to_portable, apply_portable

pytestmark = pytest.mark.unit


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", s)
    yield s
    s.close()


def _book():
    return SimpleNamespace(id=42, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04")


# --- to_portable -----------------------------------------------------------

def test_to_portable_shape():
    row = ub.Annotation(
        user_id=1, book_id=42, annotation_id="cwn-web-1", source="webreader",
        highlighted_text="hi 中文", note_text="note", highlight_color="green",
        content_id="b3d1b38b-74fd-43b7-a796-996e5a6a8b04!!c1.html",
        start_container_path="span#kobo.4.1", start_offset=0,
        end_container_path="span#kobo.4.2", end_offset=12,
        context_string="ctx", chapter_progress=0.5, hidden=False,
        device_origin_id="dev-7",
    )
    p = to_portable(row)
    assert p["annotation_id"] == "cwn-web-1"
    assert p["highlighted_text"] == "hi 中文"
    assert p["color"] == "green"
    assert p["start_kobospan"] == "kobo.4.1"
    assert p["end_kobospan"] == "kobo.4.2"
    assert p["start_offset"] == 0 and p["end_offset"] == 12
    assert p["content_id"] == "b3d1b38b-74fd-43b7-a796-996e5a6a8b04!!c1.html"
    assert p["source"] == "webreader"
    assert p["hidden"] is False
    assert p["device_origin_id"] == "dev-7"


def test_to_portable_none_safe():
    row = ub.Annotation(user_id=1, book_id=42, annotation_id="x", source="kobo")
    p = to_portable(row)
    assert p["start_kobospan"] is None
    assert p["color"] is None
    assert p["hidden"] is False  # NULL hidden coerced to False


# --- apply_portable --------------------------------------------------------

def test_apply_creates_with_koreader_default(session):
    row, action = apply_portable(
        {"annotation_id": "dev-a", "highlighted_text": "t", "color": "yellow",
         "start_kobospan": "kobo.1.1", "start_offset": 0,
         "end_kobospan": "kobo.1.1", "end_offset": 5,
         "content_id": "b3d1b38b-74fd-43b7-a796-996e5a6a8b04!!c1.html", "device_origin_id": "bm-1"},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert action == "created"
    assert row.source == "koreader"
    assert row.device_origin_id == "bm-1"
    assert row.start_container_path == "span#kobo.1.1"
    assert row.book_id == 42


def test_apply_passthrough_source_kobo(session):
    row, action = apply_portable(
        {"annotation_id": "dev-b", "source": "kobo", "start_kobospan": "kobo.1.1",
         "start_offset": 0, "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert row.source == "kobo"


def test_apply_invalid_source_is_rejected_instead_of_inventing_koreader(session):
    row, action = apply_portable(
        {"annotation_id": "dev-c", "source": "bogus", "start_kobospan": "kobo.1.1",
         "start_offset": 0, "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert row is None
    assert action == "skipped"
    assert session.query(ub.Annotation).count() == 0


def test_portable_boundary_rejects_an_unrecognised_source():
    from cps.services.annotation_portable import validate_portable_payload

    error = validate_portable_payload({"annotation_id": "dev-c", "source": "bogus"})

    assert error == "source must be one of: kobo, koreader, webreader"


def test_apply_portable_sets_origin_only_when_creating(session):
    first = ub.Device(user_id=9, kind="koreader", display_name="First",
                      active=True, created_by="auto")
    second = ub.Device(user_id=9, kind="koreader", display_name="Second",
                       active=True, created_by="auto")
    session.add_all([first, second])
    session.commit()
    row, _ = apply_portable(
        {"annotation_id": "portable-origin", "highlighted_text": "first"},
        user_id=9, book=_book(), session=session, commit=session.commit,
        origin_device_id=first.id,
    )
    assert row.origin_device_id == first.id
    row, _ = apply_portable(
        {"annotation_id": "portable-origin", "highlighted_text": "updated"},
        user_id=9, book=_book(), session=session, commit=session.commit,
        origin_device_id=second.id,
    )
    assert row.origin_device_id == first.id


def test_apply_updates_existing(session):
    apply_portable(
        {"annotation_id": "dev-d", "color": "yellow", "note_text": "v1",
         "start_kobospan": "kobo.1.1", "start_offset": 0,
         "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    row, action = apply_portable(
        {"annotation_id": "dev-d", "color": "red", "note_text": "v2",
         "start_kobospan": "kobo.1.1", "start_offset": 0,
         "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert action == "updated"
    # The portable wire speaks names; the column stores the canonical hex
    # (F-5769c9). Red is the web reader's own colour — Kobo has none.
    assert row.highlight_color == "#D9534F"
    assert row.note_text == "v2"
    assert session.query(ub.Annotation).filter_by(user_id=9, annotation_id="dev-d").count() == 1


def test_apply_hidden_soft_deletes(session):
    apply_portable(
        {"annotation_id": "dev-e", "start_kobospan": "kobo.1.1", "start_offset": 0,
         "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    row, action = apply_portable(
        {"annotation_id": "dev-e", "hidden": True},
        user_id=9, book=_book(), session=session, commit=session.commit,
        deletable_sources=_DELETABLE_SOURCES,
    )
    assert action == "deleted"
    assert row.hidden is True


def test_apply_missing_id_skipped(session):
    row, action = apply_portable(
        {"highlighted_text": "no id"},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert row is None
    assert action == "skipped"


def test_apply_wrong_type_skipped(session):
    row, action = apply_portable(
        "not-an-object", user_id=9, book=_book(), session=session,
        commit=session.commit,
    )
    assert row is None and action == "skipped"


@pytest.mark.parametrize("content_id", [
    "../unbounded-client-shape",
    "x" * 2049,
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa!!chapter.xhtml",
])
def test_portable_boundary_rejects_invalid_or_wrong_book_content_id(content_id):
    from cps.services.annotation_portable import validate_portable_payload
    error = validate_portable_payload(
        {"annotation_id": "unsafe", "content_id": content_id},
        book_uuid=_book().uuid,
    )
    assert error is not None
    assert "content_id" in error


def test_apply_duplicate_is_suppressed(session):
    payload = {
        "annotation_id": "same", "highlighted_text": "text",
        "position_type": "koreader_xpointer",
        "start_xpointer": "/body/DocFragment[1]", "end_xpointer": "/body/DocFragment[2]",
    }
    _, first = apply_portable(payload, user_id=9, book=_book(), session=session, commit=session.commit)
    _, second = apply_portable(payload, user_id=9, book=_book(), session=session, commit=session.commit)
    assert (first, second) == ("created", "skipped")
    assert session.query(ub.Annotation).count() == 1


def test_same_annotation_id_is_scoped_by_book(session):
    payload = {"annotation_id": "local-1", "highlighted_text": "text"}
    apply_portable(payload, user_id=9, book=_book(), session=session, commit=session.commit)
    other = SimpleNamespace(id=43, uuid="other")
    apply_portable(payload, user_id=9, book=other, session=session, commit=session.commit)
    assert session.query(ub.Annotation).filter_by(user_id=9, annotation_id="local-1").count() == 2


def test_stale_complete_list_retry_cannot_resurrect_tombstone(session):
    book = _book()
    payload = {"annotation_id": "deleted", "highlighted_text": "original"}
    row, _ = apply_portable(payload, user_id=9, book=book, session=session, commit=session.commit)
    row.hidden = True
    session.commit()

    row, action = apply_portable(
        {"annotation_id": "deleted", "highlighted_text": "stale", "hidden": False},
        user_id=9, book=book, session=session, commit=session.commit,
    )
    assert action == "skipped"
    assert row.hidden is True
    assert row.highlighted_text == "original"
