# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A malformed content location must never destroy the user's highlight.

Regression guard for the #1531 fallout: ``_upsert_annotation`` began validating
the derived ``content_id`` and returning ``None`` when the check failed, which
discarded the entire annotation -- text, note, colour and span anchors -- over a
locator field that is nullable and recomputable.  On the household instance that
silently dropped 95 Kobo highlights in two days.

``content_id`` is ``nullable=True`` (cps/ub.py).  It is derived data.  The
highlighted text is the irreplaceable part and it is device-local for sideloaded
books, so dropping it loses data that exists nowhere else.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.annotation_sync import (
    dispatch_annotation_sync,
    reset_registry_for_testing,
)

BOOK_UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry_for_testing()
    yield
    reset_registry_for_testing()


@pytest.fixture
def patched_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="u", email="u@e.com", role=0, password="x")
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda: session.commit())
    yield session, user
    session.close()


def _book():
    """A book WITH a uuid -- the existing dispatcher fixture has none, which is
    exactly why the content-id gate was dead code in every test and shipped."""
    return SimpleNamespace(id=347, title="Flatland", uuid=BOOK_UUID)


def _payload(chapter_filename):
    return {
        "id": "1e0f4b1a-0000-4000-8000-000000000001",
        "highlightedText": "Be patient, for the world is broad and wide.",
        "highlightColor": "yellow",
        "noteText": "irreplaceable user note",
        "location": {"span": {
            "chapterFilename": chapter_filename,
            "chapterProgress": 0.5,
            "startPath": "span#kobo\\.5\\.1",
            "endPath": "span#kobo\\.5\\.3",
            "startChar": 0,
            "endChar": 44,
        }},
    }


# Shapes a real Kobo can emit that the #1531 grammar rejects outright.
@pytest.mark.parametrize("chapter_filename", [
    "/OPS/chapter-006.xml",                                    # absolute
    "./OPS/chapter-006.xml",                                   # dot segment
    "OPS//chapter-006.xml",                                    # empty segment
    "OPS\\chapter-006.xml",                                    # backslash
    "file:///mnt/onboard/Flatland.kepub.epub#(6)OPS/chapter-006.xml",  # device ContentID
])
def test_bad_content_location_never_discards_the_annotation(
    patched_session, chapter_filename,
):
    session, user = patched_session

    dispatch_annotation_sync([_payload(chapter_filename)], _book(), user)

    rows = session.query(ub.Annotation).all()
    assert len(rows) == 1, (
        f"highlight was DESTROYED for chapterFilename={chapter_filename!r}; "
        "a derived locator must never cost the user their annotation"
    )
    row = rows[0]
    assert row.highlighted_text == "Be patient, for the world is broad and wide."
    assert row.note_text == "irreplaceable user note"
    assert row.highlight_color == "yellow"
    # The span anchors survive too -- they are what re-render the highlight.
    assert row.start_container_path == "span#kobo\\.5\\.1"
    assert row.end_container_path == "span#kobo\\.5\\.3"


def test_valid_content_location_still_normalizes(patched_session):
    """The happy path must keep working -- this is not a licence to stop validating."""
    session, user = patched_session

    dispatch_annotation_sync([_payload("OPS/chapter-006.xml")], _book(), user)

    row = session.query(ub.Annotation).one()
    assert row.content_id == f"{BOOK_UUID}!!OPS/chapter-006.xml"


def test_a_newly_invalid_location_preserves_the_last_known_valid_locator(
    patched_session,
):
    """A malformed replacement does not prove the validated stored locator is
    wrong. Preserve the last-known-valid value; the current backfill normalizes
    non-NULL locators and cannot reconstruct one that was cleared.
    """
    session, user = patched_session
    book = _book()

    good = _payload("OPS/chapter-006.xml")
    good["clientLastModifiedUtc"] = "2026-08-15T10:00:00Z"
    dispatch_annotation_sync([good], book, user)
    row = session.query(ub.Annotation).one()
    assert row.content_id == f"{BOOK_UUID}!!OPS/chapter-006.xml"

    moved = _payload("OPS/../../outside.xml")   # escapes the container -> unusable
    moved["clientLastModifiedUtc"] = "2026-08-15T11:00:00Z"
    moved["highlightedText"] = "same highlight, relocated"
    dispatch_annotation_sync([moved], book, user)

    row = session.query(ub.Annotation).one()
    assert row.content_id == f"{BOOK_UUID}!!OPS/chapter-006.xml"
    assert row.highlighted_text == "same highlight, relocated", "the highlight survives"


def test_a_new_annotation_with_an_invalid_location_is_stored_with_null_content_id(
    patched_session,
):
    session, user = patched_session

    dispatch_annotation_sync([_payload("OPS/../../outside.xml")], _book(), user)

    row = session.query(ub.Annotation).one()
    assert row.content_id is None
    assert row.highlighted_text == "Be patient, for the world is broad and wide."


def test_an_update_with_no_location_at_all_leaves_the_stored_one_alone(patched_session):
    """The other half of the distinction: silence is not a retraction."""
    session, user = patched_session
    book = _book()

    good = _payload("OPS/chapter-006.xml")
    good["clientLastModifiedUtc"] = "2026-08-15T10:00:00Z"
    dispatch_annotation_sync([good], book, user)

    quiet = {
        "id": good["id"],
        "highlightedText": "text only, no location block",
        "clientLastModifiedUtc": "2026-08-15T11:00:00Z",
    }
    dispatch_annotation_sync([quiet], book, user)

    row = session.query(ub.Annotation).one()
    assert row.content_id == f"{BOOK_UUID}!!OPS/chapter-006.xml"
