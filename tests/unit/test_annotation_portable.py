# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 — device-agnostic portable annotation projection.

`to_portable(row)` is the wire shape the pull endpoint returns (the KOReader
plugin maps it to device-native fields). `apply_portable(payload, ...)` is the
push-side upsert: find-or-create by (user_id, annotation_id), populate from the
portable dict, record device_origin_id, and soft-delete on hidden=True.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub
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
    return SimpleNamespace(id=42, uuid="bk-uuid")


# --- to_portable -----------------------------------------------------------

def test_to_portable_shape():
    row = ub.Annotation(
        user_id=1, book_id=42, annotation_id="cwn-web-1", source="webreader",
        highlighted_text="hi 中文", note_text="note", highlight_color="green",
        content_id="bk-uuid!!c1.html",
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
    assert p["content_id"] == "bk-uuid!!c1.html"
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
         "content_id": "bk-uuid!!c1.html", "device_origin_id": "bm-1"},
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


def test_apply_invalid_source_coerced(session):
    row, _ = apply_portable(
        {"annotation_id": "dev-c", "source": "bogus", "start_kobospan": "kobo.1.1",
         "start_offset": 0, "end_kobospan": "kobo.1.1", "end_offset": 3},
        user_id=9, book=_book(), session=session, commit=session.commit,
    )
    assert row.source == "koreader"


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
    assert row.highlight_color == "red"
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
