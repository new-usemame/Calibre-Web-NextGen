# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""CrossPoint's Sync Clippings against CWNG.

CrossPoint 2.0.0 calls ``<kosync>/api/v1/clippings/<document>`` before its
progress request, and any failure stops Sync Progress. These tests pin the
contract the firmware checks (src/clippings/ClippingSync.cpp at 42d58da):

  - every layout field comes back exactly as uploaded, because the firmware
    replaces its own record with the server copy on its first pull;
  - one item per page, ``revision > cursor``, a cursor that only grows, and
    integer ``deleted``;
  - a tombstone is never undone, and a clipping goes back only to the reader
    that made it.

Requests go through the real KOReader sync blueprint, real accounts and
devices, and a real library EPUB; ``koreader_library_world`` builds them.
"""

import hashlib
import importlib
import json
import logging
import zipfile
from datetime import timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import app_passwords
from tests.unit.koreader_library_world import LibraryWorld

clippings = importlib.import_module("cps.progress_syncing.protocols.crosspoint_clippings")

pytestmark = pytest.mark.unit

BOOK = 433
DOC = str(BOOK)  # a decimal Calibre book id is a valid document (book_id_document)
URL = "/kosync/api/v1/clippings/" + DOC
FIRMWARE = {"X-CWNG-Device-ID": "xteink-x4-pro", "X-CWNG-Device-Name": "Xteink"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.user = w.add_user("alice")
    w.book = w.add_book(BOOK, "The Let Them Theory")
    yield w
    w.close()


def item(clip_id="a" * 32, **fields):
    full = {"id": clip_id, "spine": 7, "start_page": 12, "end_page": 13, "pages": 40,
            "start_word": 5, "end_word": 22, "words": 30, "para": 96,
            "chapter": "Chapter 8", "text": "So we beat on, boats against the current",
            "created_at": 1762190000, "layout_signature": 2876543210,
            "start_offset": 1200, "end_offset": 1260}
    full.update(fields)
    return full


def device(world, name="Xteink"):
    row = ub.Device(user_id=world.user.id, kind="koreader", display_name=name)
    world.session.add(row)
    world.session.commit()
    return row


def upload(world, *raw, device_id=None):
    items = []
    for one in raw:
        parsed, error = clippings.parse_item(one)
        assert error is None, error
        items.append(parsed)
    with world.app.test_request_context():
        return clippings.apply_items(
            items, user=world.user, book=world.book, document="d" * 32,
            device_id=device_id, session=world.session,
            epub_path=str(world.book_file(BOOK, "EPUB")))


def page(world, cursor=0, limit=50, dev=None, has_header=False, reader_format=True):
    return clippings.build_page(world.session, user_id=world.user.id, book_id=BOOK,
                                device=dev, has_header=has_header, cursor=cursor,
                                limit=limit, reader_format=reader_format)


def annotation(world, clip_id):
    return (world.session.query(ub.Annotation)
            .filter(ub.Annotation.annotation_id == clip_id).one())


# -- the firmware's own rules, ported from ClippingSync.cpp ------------------

def _uint(value, limit):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit


def firmware_decode(wire):
    """ClippingSync.cpp lines 88-131: True when the firmware accepts ``wire``."""
    clip_id = wire.get("id")
    if not isinstance(clip_id, str) or not 1 <= len(clip_id) <= 64:
        return False
    if any(not "!" <= ch <= "~" for ch in clip_id):
        return False
    if not _uint(wire.get("deleted"), 1):
        return False
    if wire["deleted"]:
        return True
    if not _uint(wire.get("revision"), 2 ** 64 - 1):
        return False
    for field in ("spine", "start_page", "end_page", "pages", "start_word", "end_word",
                  "words"):
        if not _uint(wire.get(field), 0xFFFF):
            return False
    for field in ("created_at", "layout_signature"):
        if not _uint(wire.get(field), 0xFFFFFFFF):
            return False
    if wire.get("para") is not None and not _uint(wire["para"], 0xFFFF):
        return False
    text_ = wire.get("text")
    if (not isinstance(text_, str) or not text_ or "\x00" in text_
            or len(text_.encode("utf-8")) > 4096):
        return False
    start, end = wire.get("start_offset"), wire.get("end_offset")
    if (start is None) != (end is None):
        return False
    if start is not None and not (_uint(start, 0xFFFFFFFF) and _uint(end, 0xFFFFFFFF)
                                  and start < end and end != 0xFFFFFFFF):
        return False
    return isinstance(wire.get("chapter"), str)


def firmware_pull(fetch):
    """ClippingSync.cpp lines 192-214: every item, or an AssertionError."""
    cursor, seen = 0, []
    while True:
        body = fetch(cursor)
        assert _uint(body.get("cursor"), 2 ** 64 - 1)
        assert isinstance(body.get("more"), bool)
        assert isinstance(body.get("items"), list) and len(body["items"]) <= 1
        assert body["cursor"] >= cursor
        if body["items"]:
            assert body["cursor"] > cursor
            assert firmware_decode(body["items"][0])
            seen.extend(body["items"])
        assert not (body["more"] and not body["items"])
        cursor = body["cursor"]
        if not body["more"]:
            return seen


# -- parse_item ---------------------------------------------------------------

def test_parse_item_accepts_a_firmware_item_and_each_optional_field_absent():
    parsed, error = clippings.parse_item(item())
    assert error is None and parsed["layout_signature"] == 2876543210
    minimal, error = clippings.parse_item({"id": "x", "spine": 0, "text": "t"})
    assert error is None
    assert minimal == {"id": "x", "deleted": False, "spine": 0, "start_page": 0,
                       "end_page": 0, "pages": 1, "start_word": 0, "end_word": 0,
                       "words": 0, "para": None, "created_at": 0, "layout_signature": 0,
                       "start_offset": None, "end_offset": None, "text": "t",
                       "chapter": ""}
    for value in (1, True):
        assert clippings.parse_item({"id": "x", "deleted": value}) == (
            {"id": "x", "deleted": True}, None)
    assert clippings.parse_item(item(deleted=False))[0]["deleted"] is False
    assert clippings.parse_item(item(start_offset=None, end_offset=None))[1] is None
    assert clippings.parse_item(item(chapter="c" * 100))[0]["chapter"] == "c" * 64


@pytest.mark.parametrize("bad", [
    {"spine": True}, {"words": False}, {"spine": 65536}, {"pages": -1},
    {"created_at": 2 ** 32}, {"layout_signature": 1.5}, {"para": 70000},
    {"id": "a b"}, {"id": "é"}, {"id": ""}, {"id": "a" * 65}, {"id": 7},
    {"text": "é" * 2049}, {"text": "a\x00b"}, {"text": ""}, {"text": None},
    {"start_offset": 5, "end_offset": None}, {"start_offset": None, "end_offset": 5},
    {"start_offset": 9, "end_offset": 9}, {"start_offset": 10, "end_offset": 9},
    {"start_offset": 0, "end_offset": 4294967295}, {"deleted": 2}, {"deleted": "1"},
    {"chapter": 5}, {"text": "\ud800"},
])
def test_parse_item_refuses(bad):
    raw = item(**bad)
    assert clippings.parse_item(raw)[0] is None
    assert clippings.parse_item(raw)[1]


def test_parse_item_refuses_an_item_without_spine_or_text():
    raw = item()
    del raw["spine"]
    assert clippings.parse_item(raw)[0] is None
    raw = item()
    del raw["text"]
    assert clippings.parse_item(raw)[0] is None
    assert clippings.parse_item(["not", "an", "object"])[0] is None


def test_text_of_exactly_4096_bytes_is_accepted():
    assert clippings.parse_item(item(text="é" * 2048))[1] is None


# -- upload, pull, delete -----------------------------------------------------

def test_an_upload_comes_back_exactly(world):
    sent = item()
    assert upload(world, sent)["accepted"] == 1
    got = page(world)["items"]
    assert len(got) == 1
    wire = got[0]
    for field in ("id", "spine", "start_page", "end_page", "pages", "start_word",
                  "end_word", "words", "para", "chapter", "text", "created_at",
                  "layout_signature", "start_offset", "end_offset"):
        assert wire[field] == sent[field], field
    assert wire["deleted"] == 0 and wire["deleted"] is not False
    assert wire["revision"] > 0
    assert firmware_decode(wire)
    assert "note" not in wire and "color" not in wire
    row = annotation(world, sent["id"])
    assert row.source == "crosspoint" and row.annotation_type == "highlight"


def test_a_repeat_upload_keeps_the_revision(world):
    upload(world, item())
    first = page(world)["items"][0]["revision"]
    counts = upload(world, item())
    assert counts["accepted"] == 1 and counts["unchanged"] == 1
    assert page(world)["items"][0]["revision"] == first


def test_a_changed_upload_gets_a_new_revision(world):
    upload(world, item())
    first = page(world)["items"][0]["revision"]
    upload(world, item(layout_signature=1))
    again = page(world)["items"][0]
    assert again["revision"] > first and again["layout_signature"] == 1


def test_a_delete_is_a_tombstone_with_a_greater_revision(world):
    upload(world, item())
    first = page(world)["items"][0]["revision"]
    counts = upload(world, {"id": "a" * 32, "deleted": 1})
    assert counts == {"created": 0, "updated": 0, "deleted": 1, "unchanged": 0,
                      "accepted": 1}
    wire = page(world)["items"][0]
    assert set(wire) == {"id", "revision", "deleted", "updated_at"}
    assert wire["deleted"] == 1 and wire["deleted"] is not True
    assert wire["revision"] > first
    assert annotation(world, "a" * 32).hidden is True


def test_an_upload_of_a_tombstoned_id_keeps_the_tombstone(world):
    upload(world, item())
    upload(world, {"id": "a" * 32, "deleted": 1})
    tomb = page(world)["items"][0]
    counts = upload(world, item())
    assert counts["accepted"] == 1 and counts["unchanged"] == 1
    assert page(world)["items"][0] == tomb
    assert annotation(world, "a" * 32).hidden is True


def test_a_delete_of_an_unknown_id_is_counted_and_makes_no_row(world):
    counts = upload(world, {"id": "f" * 32, "deleted": 1})
    assert counts["accepted"] == 1 and counts["unchanged"] == 1
    assert world.session.query(ub.Annotation).count() == 0


def test_a_clipping_goes_back_only_to_the_reader_that_made_it(world):
    reader_a, reader_b = device(world, "Xteink"), device(world, "Other")
    upload(world, item("a" * 32), device_id=reader_a.id)
    assert annotation(world, "a" * 32).origin_device_id == reader_a.id
    assert len(page(world, dev=reader_a, has_header=True)["items"]) == 1
    assert page(world, dev=reader_b, has_header=True)["items"] == []
    # A header naming no known reader yet sees only clippings of no reader.
    assert page(world, dev=None, has_header=True)["items"] == []

    counts = upload(world, {"id": "a" * 32, "deleted": 1}, device_id=reader_b.id)
    assert counts["accepted"] == 1 and counts["deleted"] == 0
    assert annotation(world, "a" * 32).hidden is False
    counts = upload(world, item("a" * 32, layout_signature=9), device_id=reader_b.id)
    assert counts["unchanged"] == 1
    assert page(world, dev=reader_a, has_header=True)["items"][0]["layout_signature"] \
        == 2876543210


def test_a_headerless_clipping_goes_to_every_reader(world):
    reader = device(world)
    upload(world, item())
    assert len(page(world)["items"]) == 1
    assert len(page(world, dev=reader, has_header=True)["items"]) == 1
    assert len(page(world, dev=None, has_header=True)["items"]) == 1


@pytest.mark.parametrize("with_device", [False, True])
def test_an_id_of_a_kobo_highlight_is_counted_and_left_alone(world, with_device):
    world.session.add(ub.Annotation(
        user_id=world.user.id, annotation_id="k" * 32, book_id=BOOK, source="kobo",
        highlighted_text="kobo words", start_container_path="span#kobo.1.1",
        start_offset=0, end_container_path="span#kobo.1.1", end_offset=4))
    world.session.commit()
    dev = device(world) if with_device else None
    dev_id = dev.id if dev else None
    for raw in (item("k" * 32), {"id": "k" * 32, "deleted": 1}):
        counts = upload(world, raw, device_id=dev_id)
        assert counts["accepted"] == 1 and counts["unchanged"] == 1
    row = annotation(world, "k" * 32)
    assert (row.source, row.highlighted_text, row.hidden) == ("kobo", "kobo words", False)
    assert row.crosspoint_clipping is None


def test_a_web_reader_delete_reaches_the_reader_as_a_tombstone(world):
    from cps.annotations import delete_annotation
    upload(world, item())
    first = page(world)["items"][0]["revision"]
    assert delete_annotation("a" * 32, user_id=world.user.id, book_id=BOOK,
                             session=world.session, commit=world.session.commit)
    wire = page(world)["items"][0]
    assert wire["deleted"] == 1 and wire["revision"] > first
    # Reconciled once, then stable.
    assert page(world)["items"][0]["revision"] == wire["revision"]


def test_paging_one_item_at_a_time_follows_every_firmware_rule(world):
    ids = ["%032x" % n for n in range(1, 6)]
    for n, clip_id in enumerate(ids):
        upload(world, item(clip_id, spine=n))
    upload(world, {"id": ids[1], "deleted": 1}, {"id": ids[3], "deleted": 1})
    pulled = firmware_pull(lambda cursor: page(world, cursor=cursor, limit=1))
    assert [p["id"] for p in pulled] == [ids[0], ids[2], ids[4], ids[1], ids[3]]
    assert [p["deleted"] for p in pulled] == [0, 0, 0, 1, 1]
    revisions = [p["revision"] for p in pulled]
    assert revisions == sorted(set(revisions))
    # A pull from the last cursor is empty and does not move it.
    last = page(world, cursor=revisions[-1], limit=1)
    assert last == {"cursor": revisions[-1], "more": False, "items": [], "tombstones": 0}


def test_revisions_are_per_account_and_never_repeat(world):
    bob = world.add_user("bob")
    with world.app.test_request_context():
        first = clippings.next_revision(world.session, world.user.id)
        assert clippings.next_revision(world.session, bob.id) == 1
        assert clippings.next_revision(world.session, world.user.id) == first + 1
        world.session.commit()
    upload(world, item())
    used = page(world)["items"][0]["revision"]
    row = annotation(world, "a" * 32)
    world.session.delete(row)  # a hard delete takes the sidecar with it
    world.session.commit()
    assert world.session.query(ub.CrossPointClipping).count() == 0
    upload(world, item("b" * 32))
    assert page(world)["items"][0]["revision"] > used


def test_a_long_quote_is_cut_for_placement_and_kept_whole(world):
    long_text = "word " * 812  # 4060 characters: over the quote limit, under 4096 bytes
    upload(world, item(text=long_text))
    assert page(world)["items"][0]["text"] == long_text
    row = annotation(world, "a" * 32)
    assert len(json.loads(row.text_quote)["exact"]) == 4000


def _write_epub(path, paragraphs, between=""):
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        archive.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'))
        archive.writestr("content.opf", (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>t</dc:title></metadata><manifest><item id="p" '
            'href="p.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="p"/></spine></package>'))
        archive.writestr("p.xhtml", (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>%s</body></html>'
            % between.join("<p>%s</p>" % p for p in paragraphs)))


def test_a_quote_found_in_the_book_is_placed(world):
    words = "So we beat on, boats against the current, borne back ceaselessly"
    _write_epub(world.book_file(BOOK, "EPUB"),
                ["It was the best of times.", words, "Call me Ishmael."])
    upload(world, item(text=words))
    row = annotation(world, "a" * 32)
    assert row.position_type == "koreader_xpointer" and row.start_xpointer
    first = page(world)["items"][0]
    assert first["text"] == words
    # The same upload places to the same words again and changes nothing.
    assert upload(world, item(text=words))["unchanged"] == 1
    assert page(world)["items"][0]["revision"] == first["revision"]


_REPEATS = ["I will never forget that day.", "Some other words in between.",
            "And I will never forget this one."]
# Indented source, as most books have: the firmware counts these whitespace-only
# text nodes too (every code point of <body> character data, ChapterHtmlSlimParser).
_BETWEEN = "\n    "


def _visible_offset(paragraph, words):
    """The firmware's start_offset of ``words`` in ``_REPEATS[paragraph]``."""
    return (sum(len(p) for p in _REPEATS[:paragraph]) + len(_BETWEEN) * paragraph
            + _REPEATS[paragraph].index(words))


def test_a_short_repeated_quote_is_placed_by_its_offset(world):
    # Too short for a text search (12 characters folded) and found twice.
    _write_epub(world.book_file(BOOK, "EPUB"), _REPEATS, between=_BETWEEN)
    start = _visible_offset(2, "never forget")
    upload(world, item(text="never forget", spine=0, start_offset=start,
                       end_offset=start + len("never forget")))
    row = annotation(world, "a" * 32)
    assert row.position_type == "koreader_xpointer"
    # The third paragraph, after "And I will " (11 characters).
    assert row.start_xpointer.endswith("/body/p[3]/text().11")
    assert page(world)["items"][0]["text"] == "never forget"


def test_an_offset_that_does_not_match_the_words_places_nothing(world):
    _write_epub(world.book_file(BOOK, "EPUB"), _REPEATS, between=_BETWEEN)
    start = _visible_offset(1, "other words")
    upload(world, item(text="never forget", spine=0, start_offset=start,
                       end_offset=start + 12))
    row = annotation(world, "a" * 32)
    assert row.position_type == "text_quote" and not row.start_xpointer


def test_offsets_and_no_offsets_both_come_back_exactly(world):
    upload(world, item("a" * 32), item("b" * 32, start_offset=None, end_offset=None,
                                      para=None))
    got = {wire["id"]: wire for wire in page(world)["items"]}
    assert (got["a" * 32]["start_offset"], got["a" * 32]["end_offset"]) == (1200, 1260)
    assert (got["b" * 32]["start_offset"], got["b" * 32]["end_offset"],
            got["b" * 32]["para"]) == (None, None, None)


def test_created_at_dates_a_new_highlight(world):
    upload(world, item("a" * 32), item("b" * 32, created_at=0))
    dated = annotation(world, "a" * 32).created_at
    assert dated.replace(tzinfo=timezone.utc).timestamp() == 1762190000
    assert annotation(world, "b" * 32).created_at.year >= 2026


def test_another_client_cannot_claim_or_delete_a_clipping(world):
    """``/kosync/syncs/annotations`` owns koreader and textquote rows only."""
    upload(world, item())
    client = world.app.test_client()
    claim = client.put("/kosync/syncs/annotations", headers=world.basic("alice"), json={
        "document": DOC, "annotations": [], "deleted": ["a" * 32],
        "delete_source": "crosspoint"})
    assert claim.status_code == 400
    create = client.put("/kosync/syncs/annotations", headers=world.basic("alice"), json={
        "document": DOC, "annotations": [{"annotation_id": "c" * 32,
                                          "source": "crosspoint", "highlighted_text": "x"}]})
    assert create.status_code == 400
    plain = client.put("/kosync/syncs/annotations", headers=world.basic("alice"), json={
        "document": DOC, "annotations": [], "deleted": ["a" * 32]})
    assert plain.status_code == 200
    assert annotation(world, "a" * 32).hidden is False


# -- the tables ---------------------------------------------------------------

def test_the_migration_creates_the_tables_and_can_run_again():
    engine = create_engine("sqlite://")
    for table in ub.Base.metadata.sorted_tables:
        if table.name not in ("crosspoint_clipping", "crosspoint_clipping_clock"):
            table.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        ub.migrate_crosspoint_clippings(engine, session)
        ub.migrate_crosspoint_clippings(engine, session)
        inspector = inspect(engine)
        assert {"crosspoint_clipping", "crosspoint_clipping_clock"} <= set(
            inspector.get_table_names())
        uniques = inspector.get_unique_constraints("crosspoint_clipping")
        assert any(set(u["column_names"]) == {"user_id", "sync_revision"} for u in uniques)
        keys = inspector.get_foreign_keys("crosspoint_clipping")
        assert any(k["referred_table"] == "annotation"
                   and k["options"].get("ondelete") == "CASCADE" for k in keys)
    finally:
        session.close()
        engine.dispose()


def test_the_database_cascade_removes_the_sidecar(world):
    upload(world, item())
    world.session.execute(text("PRAGMA foreign_keys=ON"))
    world.session.execute(text("DELETE FROM annotation"))
    world.session.commit()
    assert world.session.query(ub.CrossPointClipping).count() == 0


def test_a_book_purge_removes_the_sidecar(world):
    """Bulk deletes skip the ORM cascade, and SQLite FK cascades are off."""
    from cps.user_book_data import purge_user_book_data
    upload(world, item())
    purge_user_book_data(book_id=BOOK, session=world.session)
    world.session.commit()
    assert world.session.query(ub.Annotation).count() == 0
    assert world.session.query(ub.CrossPointClipping).count() == 0


# -- the routes ---------------------------------------------------------------

def firmware_headers(world, password="secret"):
    """What a CrossPoint clippings client that also sends Basic sends: the
    custom headers, x-auth-* and Basic (as its progress client does)."""
    headers = dict(FIRMWARE)
    headers.update({"x-auth-user": "alice",
                    "x-auth-key": hashlib.md5(password.encode()).hexdigest()})
    headers.update(world.basic("alice", password))
    return headers


def get(world, headers, url=URL, **params):
    params.setdefault("format", "reader")
    return world.app.test_client().get(url, headers=headers, query_string=params)


def put(world, headers, items, url=URL):
    return world.app.test_client().put(url, headers=headers, json={"items": items})


def test_the_probe_of_a_known_book(world):
    response = get(world, firmware_headers(world), cursor=0, limit=1)
    assert response.status_code == 200
    body = response.get_json()
    assert body["sync_version"] == 2 and body["items"] == [] and body["more"] is False
    assert body["cursor"] == 0 and body["book_known"] is True
    assert body["calibre_book_id"] == BOOK and isinstance(body["until"], int)


def test_the_probe_of_an_unknown_book(world):
    response = get(world, firmware_headers(world), url="/kosync/api/v1/clippings/" + "0" * 32,
                   cursor=0, limit=1)
    assert response.status_code == 200
    body = response.get_json()
    assert (body["sync_version"], body["items"], body["book_known"], body["cursor"],
            body["more"]) == (2, [], False, 0, False)


def test_an_upload_to_an_unknown_book_is_accepted_and_dropped(world, caplog):
    with caplog.at_level(logging.WARNING):
        response = put(world, firmware_headers(world), [item(), item("b" * 32)],
                       url="/kosync/api/v1/clippings/" + "0" * 32)
    assert response.status_code == 200
    body = response.get_json()
    assert body["accepted"] == 2 and body["matched"] is False
    assert world.session.query(ub.Annotation).count() == 0
    assert "matched NO book - 2 item(s) were NOT saved" in caplog.text


@pytest.mark.parametrize("count", [0, 51])
def test_a_put_needs_1_to_50_items(world, count):
    response = put(world, firmware_headers(world),
                   [item("%032x" % n) for n in range(count)])
    assert response.status_code == 400
    assert world.session.query(ub.Annotation).count() == 0


def test_a_body_over_2_mib_is_refused(world):
    big = json.dumps({"items": [item(chapter="x" * (2 * 1024 * 1024))]})
    response = world.app.test_client().put(
        URL, headers=dict(firmware_headers(world), **{"Content-Type": "application/json"}),
        data=big)
    assert response.status_code == 400


def test_one_bad_item_refuses_the_batch_and_stores_nothing(world):
    response = put(world, firmware_headers(world),
                   [item("a" * 32), item("b" * 32, spine=True), item("c" * 32)])
    assert response.status_code == 400
    assert "items[1]" in response.get_json()["message"]
    assert world.session.query(ub.Annotation).count() == 0


def test_kosync_off_is_503(world):
    headers = firmware_headers(world)
    world.sync_switch(False)
    assert get(world, headers).status_code == 503
    assert put(world, headers, [item()]).status_code == 503


def test_no_login_is_401(world):
    assert get(world, FIRMWARE).status_code == 401
    assert put(world, FIRMWARE, [item()]).status_code == 401
    assert get(world, firmware_headers(world, "wrong")).status_code == 401


def test_crosspoint_2_0_0_without_basic_is_401(world):
    """CrossPoint 2.0.0 sends only x-auth-* on clipping requests. CWNG checks
    Basic, so these need a firmware build that also sends Basic."""
    headers = {k: v for k, v in firmware_headers(world).items() if k != "Authorization"}
    assert get(world, headers).status_code == 401
    assert put(world, headers, [item()]).status_code == 401


def test_an_app_password_over_basic_works(world):
    _row, cleartext = app_passwords.mint(world.user.id, "Xteink", session=world.session)
    world.session.commit()
    headers = firmware_headers(world, cleartext)
    assert put(world, headers, [item()]).get_json()["accepted"] == 1
    assert len(get(world, headers).get_json()["items"]) == 1


def test_the_firmware_round_trip(world):
    headers = firmware_headers(world)
    assert put(world, headers, [{"id": "f" * 32, "deleted": 1}]).get_json()["accepted"] == 1
    uploaded = put(world, headers, [item()])
    assert uploaded.status_code == 200
    assert uploaded.get_json() == {"document": DOC, "until": uploaded.get_json()["until"],
                                   "accepted": 1, "matched": True, "calibre_book_id": BOOK}
    pulled = firmware_pull(lambda cursor: get(world, headers, cursor=cursor,
                                              limit=1).get_json())
    assert [p["id"] for p in pulled] == ["a" * 32]
    row = annotation(world, "a" * 32)
    reader = world.session.get(ub.Device, row.origin_device_id)
    assert reader.display_name == "Xteink"
    # Another reader of the account does not get it.
    other = dict(headers, **{"X-CWNG-Device-ID": "other-reader"})
    assert get(world, other).get_json()["items"] == []
    # Basic alone, as the progress client of 2.0.0 also sends it.
    basic = dict(FIRMWARE, **world.basic("alice"))
    assert len(get(world, basic).get_json()["items"]) == 1


def test_a_document_hash_resolves_like_the_annotations_route(world, monkeypatch):
    from cps.progress_syncing.protocols import koreader_annotations
    digest = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(koreader_annotations, "get_book_by_checksum",
                        lambda document: (BOOK, "EPUB", "t", "p", "koreader")
                        if document == digest else (None, None, None, None, None))
    url = "/kosync/api/v1/clippings/" + digest
    headers = firmware_headers(world)
    assert put(world, headers, [item()], url=url).get_json()["matched"] is True
    assert get(world, headers, url=url).get_json()["calibre_book_id"] == BOOK
    assert world.session.query(ub.CrossPointClipping).one().document == digest


@pytest.mark.parametrize("cursor", ["-1", "x", "1.5", "", "99999999999999999999"])
def test_a_bad_cursor_is_400(world, cursor):
    assert get(world, firmware_headers(world), cursor=cursor).status_code == 400


@pytest.mark.parametrize("limit,expected", [("0", 50), ("500", 100), ("x", 50),
                                            ("-3", 50), ("7", 7)])
def test_the_limit_is_clamped(world, monkeypatch, limit, expected):
    seen = {}
    real = clippings.build_page

    def spy(*args, **kwargs):
        seen["limit"] = kwargs["limit"]
        return real(*args, **kwargs)

    monkeypatch.setattr(clippings, "build_page", spy)
    assert get(world, firmware_headers(world), limit=limit).status_code == 200
    assert seen["limit"] == expected


def test_without_format_reader_an_item_has_note_and_color(world):
    headers = firmware_headers(world)
    put(world, headers, [item()])
    row = annotation(world, "a" * 32)
    row.note_text, row.highlight_color = "a note", "yellow"
    world.session.commit()
    wire = get(world, headers, format="").get_json()["items"][0]
    assert wire["note"] == "a note" and wire["color"] == "yellow"
    reader = get(world, headers).get_json()["items"][0]
    assert "note" not in reader and "color" not in reader


def test_a_worst_case_reply_fits_the_firmware_buffer(world):
    headers = firmware_headers(world)
    worst = item(text="\x01" * 4096, chapter="\x01" * 64, id="~" * 64,
                 created_at=4294967295, layout_signature=4294967295,
                 start_offset=4294967293, end_offset=4294967294)
    assert put(world, headers, [worst]).get_json()["accepted"] == 1
    response = get(world, headers, cursor=0, limit=1)
    assert len(response.get_data()) < 32768
    assert response.get_json()["items"][0]["text"] == "\x01" * 4096


def test_the_auth_reply_lists_the_capability(world):
    response = world.app.test_client().get("/kosync/users/auth",
                                           headers=firmware_headers(world))
    assert response.status_code == 200
    assert "crosspoint_clippings" in response.get_json()["capabilities"]
