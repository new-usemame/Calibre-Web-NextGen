# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""A reading position must survive the book being re-converted.

MEASURED 2026-09-11 on the household Libra: a PDF re-converted to EPUB changes
the spine file names and the kobo span numbering; the reader's position
(`OEBPS/chap0021.xhtml kobo.156.1`, 15 %) then points at bytes that no longer
exist and the device opens the book at the start. The position has no text of
its own, so its text is captured from the OLD book before the file is replaced
and found again in the NEW one; when the old book is already gone the position
is re-placed at the same fraction of the book.
"""
from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import kobo_annotation_reanchor as reanchor
from cps.services import kobo_position_reanchor as positions

from tests.unit.test_kobo_redownload_harmless import _kepub

USER_ID, DEVICE_ID, BOOK_ID = 3, 1, 567
LOG = logging.getLogger("test")

OLD = [
    ("OEBPS/chap0020.xhtml", [("kobo.1.1", "Chapter twenty opens here. "),
                              ("kobo.2.1", "It goes on for a while. ")]),
    ("OEBPS/chap0021.xhtml", [("kobo.155.1", "The whole-sign house system assigns "),
                              ("kobo.156.1", "each sign to one house, starting from the rising sign. "),
                              ("kobo.157.1", "Later authors introduced quadrant divisions. ")]),
]
# The re-converted book: the same prose lands in a different file under
# different span numbers, and a DIFFERENT chapter now carries the old name.
NEW = [
    ("OEBPS/chap0008.xhtml", [("kobo.1.1", "Front matter that did not exist before. ")]),
    ("OEBPS/chap0009.xhtml", [("kobo.3.1", "The whole-sign house system assigns each sign "),
                              ("kobo.3.2", "to one house, starting from the rising sign. "),
                              ("kobo.4.1", "Later authors introduced quadrant divisions. ")]),
    ("OEBPS/chap0021.xhtml", [("kobo.9.1", "An appendix now wears the old file name. ")]),
]


def _row(**overrides):
    values = {"location_source": "OEBPS/chap0021.xhtml", "location_type": "KoboSpan",
              "location_value": "kobo.156.1", "progress_percent": 15.0}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_a_position_follows_its_text_into_the_re_converted_book(tmp_path):
    _kepub(tmp_path / "old.kepub.epub", OLD)
    _kepub(tmp_path / "new.kepub.epub", NEW)
    old = reanchor.KepubIndex(str(tmp_path / "old.kepub.epub"))
    new = reanchor.KepubIndex(str(tmp_path / "new.kepub.epub"))
    row = _row()

    moved = positions.reanchor_position_rows(old, new, [row], log=LOG)

    assert moved == [row]
    # The span where its prose now starts - NOT the chapter that merely kept
    # the old file name.
    assert (row.location_source, row.location_value) == ("OEBPS/chap0009.xhtml", "kobo.3.1")
    assert row.location_type == "KoboSpan"


def test_a_position_whose_old_book_is_gone_keeps_its_fraction_of_the_book(tmp_path):
    _kepub(tmp_path / "new.kepub.epub", NEW)
    new = reanchor.KepubIndex(str(tmp_path / "new.kepub.epub"))
    # The device position row of a book whose previous KEPUB was deleted: the
    # calibre spine name it names exists nowhere any more.
    row = _row(location_source="Hellenistic Astrology - Chris Brennan.src_split_001.html",
               location_value="kobo.92.1", progress_percent=50.0)

    moved = positions.reanchor_position_rows(None, new, [row], log=LOG)

    assert moved == [row]
    assert row.location_source == "OEBPS/chap0009.xhtml"
    assert row.location_value.startswith("kobo.")


def test_a_position_already_inside_the_new_book_is_left_alone(tmp_path):
    _kepub(tmp_path / "new.kepub.epub", NEW)
    new = reanchor.KepubIndex(str(tmp_path / "new.kepub.epub"))
    row = _row(location_source="OEBPS/chap0009.xhtml", location_value="kobo.4.1")

    assert positions.reanchor_position_rows(None, new, [row], log=LOG) == []
    assert (row.location_source, row.location_value) == ("OEBPS/chap0009.xhtml", "kobo.4.1")


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    database.add(ub.Device(id=DEVICE_ID, user_id=USER_ID, kind="kobo", display_name="Libra",
                           model="Kobo Libra Colour", active=True, created_by="auto"))
    database.add(ub.User(id=USER_ID, name="reader", email="r@example.invalid", role=0,
                         password="x"))
    state = ub.KoboReadingState(user_id=USER_ID, book_id=BOOK_ID)
    state.current_bookmark = ub.KoboBookmark(
        location_source="OEBPS/chap0021.xhtml", location_type="KoboSpan",
        location_value="kobo.156.1", progress_percent=15.0,
        content_source_progress_percent=39.0,
    )
    state.statistics = ub.KoboStatistics()
    database.add(state)
    database.add(ub.ReadBook(user_id=USER_ID, book_id=BOOK_ID,
                             read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    database.add(ub.DeviceReadingPosition(
        device_id=DEVICE_ID, book_id=BOOK_ID, location_source="OEBPS/chap0021.xhtml",
        location_type="KoboSpan", location_value="kobo.156.1", progress_percent=15.0,
        client_modified_at=datetime(2026, 9, 12, 2, 51, 48), rehydrate_needed=False,
    ))
    database.commit()
    yield database
    database.close()
    engine.dispose()


def _library_book(tmp_path, monkeypatch, name="new"):
    lib = tmp_path / "lib" / "Brennan"
    lib.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reanchor.config, "get_book_path", lambda: str(tmp_path / "lib"))
    return SimpleNamespace(
        id=BOOK_ID, uuid="c65e568b-0000-0000-0000-000000000567", path="Brennan",
        timestamp=datetime(2026, 9, 1), title="Hellenistic Astrology",
        data=[SimpleNamespace(format="KEPUB", name=name)],
    )


def test_replacing_a_book_re_anchors_every_reader_and_re_arms_their_devices(
    session, monkeypatch, tmp_path,
):
    _kepub(tmp_path / "old.kepub.epub", OLD)
    book = _library_book(tmp_path, monkeypatch)
    _kepub(tmp_path / "lib" / "Brennan" / "new.kepub", NEW)

    count = positions.reanchor_book_positions(
        book, old_kepub_path=str(tmp_path / "old.kepub.epub"), log=LOG,
    )

    assert count == 2
    bookmark = session.query(ub.KoboBookmark).one()
    assert (bookmark.location_source, bookmark.location_value) == ("OEBPS/chap0009.xhtml", "kobo.3.1")
    latch = session.query(ub.DeviceReadingPosition).one()
    assert (latch.location_source, latch.location_value) == ("OEBPS/chap0009.xhtml", "kobo.3.1")
    assert latch.rehydrate_needed is True


def test_a_moved_position_carries_a_fresh_clock_so_the_device_takes_it(
    session, monkeypatch, tmp_path,
):
    """The replayed reading state must be newer than the device's own copy,
    and a later echo of the old locator must read as older than the repair."""
    _kepub(tmp_path / "old.kepub.epub", OLD)
    book = _library_book(tmp_path, monkeypatch)
    _kepub(tmp_path / "lib" / "Brennan" / "new.kepub", NEW)
    device_clock = datetime(2026, 9, 12, 2, 51, 48)
    state = session.query(ub.KoboReadingState).one()
    state.current_bookmark.last_modified = device_clock
    state.last_modified = state.priority_timestamp = device_clock
    session.commit()

    positions.reanchor_book_positions(
        book, old_kepub_path=str(tmp_path / "old.kepub.epub"), log=LOG,
    )

    session.expire_all()
    state = session.query(ub.KoboReadingState).one()
    assert state.current_bookmark.last_modified > device_clock
    assert state.last_modified > device_clock
    assert state.priority_timestamp > device_clock
    latch = session.query(ub.DeviceReadingPosition).one()
    assert latch.server_modified_at > device_clock


def test_serving_a_reading_state_whose_chapter_vanished_re_places_it_by_fraction(
    session, monkeypatch, tmp_path,
):
    from cps import kobo
    book = _library_book(tmp_path, monkeypatch)
    _kepub(tmp_path / "lib" / "Brennan" / "new.kepub",
           [("OEBPS/a.xhtml", [("kobo.1.1", "x" * 100)]),
            ("OEBPS/b.xhtml", [("kobo.2.1", "y" * 100)]),
            ("OEBPS/c.xhtml", [("kobo.3.1", "z" * 100)])])
    state = session.query(ub.KoboReadingState).one()

    response = kobo.get_kobo_reading_state_response(book, state)

    assert response["CurrentBookmark"]["Location"]["Source"] == "OEBPS/a.xhtml"
    assert session.query(ub.KoboBookmark).one().location_source == "OEBPS/a.xhtml"
    # The wire percent is the device's own number and is not rewritten.
    assert response["CurrentBookmark"]["ProgressPercent"] == 15


def test_a_highlight_whose_chapter_name_was_reused_is_re_anchored_when_forced(tmp_path):
    from tests.unit.test_kobo_redownload_harmless import OWNED, _highlight
    _kepub(tmp_path / "new.kepub.epub", NEW)
    new = reanchor.KepubIndex(str(tmp_path / "new.kepub.epub"))
    row = _highlight("h1", "quadrant divisions",
                     content_id=f"{OWNED}!!OEBPS/chap0021.xhtml",
                     start_container_path="span#kobo\\.157\\.1", end_container_path="span#kobo\\.157\\.1")

    assert reanchor.reanchor_rows(new, [row], OWNED, log=LOG) == []  # the name still exists
    assert reanchor.reanchor_rows(new, [row], OWNED, log=LOG, force=True) == [row]
    assert row.content_id.endswith("!!OEBPS/chap0009.xhtml")
    assert row.start_container_path == "span#kobo\\.4\\.1"


def test_a_position_on_a_footnote_paragraph_lands_in_the_body_prose_that_follows(tmp_path):
    """v3 output set page-foot notes as body paragraphs; v4 moves them into
    endnote asides at the end of the chapter document. A position saved on such
    a paragraph must follow the reading page, not the note."""
    _kepub(tmp_path / "old.kepub.epub", [
        ("OEBPS/chap0021.xhtml", [
            ("kobo.155.1", "Antiochus wrote in the first century CE. "),
            ("kobo.156.1", "1 His arguments were originally outlined in Pingree, Antiochus and Rhetorius. "),
            ("kobo.157.1", "The next page continues the discussion of Antiochus and his summary. "),
        ]),
    ])
    with zipfile.ZipFile(tmp_path / "new.kepub.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<manifest><item id="c0" href="chap0021.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c0"/></spine></package>')
        z.writestr("OEBPS/chap0021.xhtml",
                   '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                   '<p><span class="koboSpan" id="kobo.1.1">Antiochus wrote in the first century CE. </span></p>'
                   '<p><span class="koboSpan" id="kobo.2.1">The next page continues the discussion of Antiochus and his summary. </span></p>'
                   '<section class="footnotes" epub:type="footnotes"><aside epub:type="footnote" id="fn-1">'
                   '<p><span class="koboSpan" id="kobo.156.1">1 His arguments were originally outlined in Pingree, Antiochus and Rhetorius. </span></p>'
                   '</aside></section></body></html>')
    old = reanchor.KepubIndex(str(tmp_path / "old.kepub.epub"))
    new = reanchor.KepubIndex(str(tmp_path / "new.kepub.epub"))
    row = _row()  # OEBPS/chap0021.xhtml kobo.156.1 - the name AND the span id survive

    assert positions.reanchor_position_rows(old, new, [row], log=LOG) == [row]
    assert (row.location_source, row.location_value) == ("OEBPS/chap0021.xhtml", "kobo.2.1")
