# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Highlights named by their words reach every reader, and come back as words.

A reading client that flattens a book to its words holds no XPointer, CFI or
KoboSpan. It pushes a highlight as a text quote (the passage's words plus a
few words either side, W3C TextQuoteSelector style) to the KOReader
annotation routes, and asks for quotes on pull.

Driven through the real ``/kosync/syncs/annotations`` routes over a real
SQLite app database and the Metamorphosis EPUB, with passages taken from what
a Kindle showed (``tests/fixtures/koreader_xpointer``), re-typed the way
another tokenizer sees them (straight quotes, single spaces).

What must hold:
* a placed quote is the exact words for KOReader and the web reader;
* a quote that cannot be placed is kept, never guessed and never dropped;
* every other reader's highlight reaches the client as its own words, and a
  deletion anywhere reaches it as a tombstone with a newer revision;
* a client deletes only its own highlights, and KOReader only its own;
* placing or naming words is a content read: it needs the right to read.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from cps import calibre_db, ub
from cps.services import koreader_xpointer as kx
from cps.services import text_anchor
from tests.unit.test_koreader_exact_positions import BOOK_ID, _page, world  # noqa: F401
from tests.unit.test_kosync_text_anchor import (  # noqa: F401  (autouse fixture)
    _a_reader_who_may_open_the_book, _client)

annotation_routes = importlib.import_module("cps.progress_syncing.protocols.koreader_annotations")
kosync_routes = importlib.import_module("cps.progress_syncing.protocols.kosync")

pytestmark = pytest.mark.unit

DEVICE = {"device": "WordReader (Phone)", "device_id": "phone-1"}


@pytest.fixture
def client(world, monkeypatch):
    monkeypatch.setattr(annotation_routes, "_require_kosync_enabled", lambda: None)
    monkeypatch.setattr(annotation_routes, "authenticate_user", lambda: world.user)
    monkeypatch.setattr(
        annotation_routes, "get_book_by_checksum",
        lambda d: (BOOK_ID, "EPUB", "Metamorphosis", None, "koreader")
        if d == world.digest else (None, None, None, None, None))
    return _client(world)


def _typed(words):
    """Words as another tokenizer types them: straight quotes, single spaces."""
    return (" ".join(words).replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))


def _quote(n, start=8, length=10, context=8):
    words = _page(n)[1].split()
    assert len(words) >= start + length + context, "fixture page too short"
    return {"prefix": _typed(words[start - context:start]),
            "exact": _typed(words[start:start + length]),
            "suffix": _typed(words[start + length:start + length + context])}


def _push(client, annotations, *, document=str(BOOK_ID), status=200, **extra):
    response = client.put("/kosync/syncs/annotations", json={
        "document": document, "annotations": annotations, **DEVICE, **extra})
    assert response.status_code == status, response.get_json()
    return response.get_json()


def _pull(client, document=str(BOOK_ID), quotes=True):
    url = f"/kosync/syncs/annotations/{document}" + ("?text_quote=1" if quotes else "")
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
    return {row["annotation_id"]: row for row in response.get_json()["annotations"]}


def _highlight(annotation_id, quote, **fields):
    return {"annotation_id": annotation_id, "source": "textquote", "type": "highlight",
            "text_quote": quote, "color": "yellow", "device_origin_id": "phone-1",
            **fields}


def _row(world, annotation_id):
    world.session.expire_all()
    return world.session.query(ub.Annotation).filter_by(annotation_id=annotation_id).one()


def _web_reader_status(world, annotation_id):
    from cps.annotations import _resolve_annotation_anchor
    book = calibre_db.get_book(BOOK_ID)
    return _resolve_annotation_anchor(_row(world, annotation_id), book)


# ---------------------------------------------------------------------------
# client -> every other reader
# ---------------------------------------------------------------------------


def test_a_quoted_highlight_is_the_exact_words_for_koreader_and_the_web_reader(world, client):
    quote = _quote(60)

    result = _push(client, [_highlight("hl-1", quote, percentage=0.51)])

    assert result["resolved"] == ["hl-1"] and result["unresolved"] == []
    assert result["created"] == 1
    # KOReader pulls by its own file's digest and draws only if the words
    # between the two XPointers are the row's highlighted text.
    row = _pull(client, world.digest, quotes=False)["hl-1"]
    passage = kx.passage_between(world.epub, row["start_xpointer"], row["end_xpointer"])
    assert " ".join(passage.split()) == " ".join(row["highlighted_text"].split())
    assert text_anchor.fold(passage) == text_anchor.fold(quote["exact"])
    # The web reader converts the same pair into a CFI over the same words.
    cfi, status = _web_reader_status(world, "hl-1")
    assert status == "ok" and cfi.startswith("epubcfi(")
    assert text_anchor.fold(kx.passage_between(
        world.epub, *kx.cfi_range_to_xpointers(world.epub, cfi))) == text_anchor.fold(quote["exact"])


def test_a_quote_that_is_not_in_the_book_is_kept_and_comes_back_unchanged(world, client):
    quote = {"prefix": "words that appear in", "exact": "no edition of this particular novel",
             "suffix": "at all, anywhere"}

    result = _push(client, [_highlight("hl-lost", quote, note_text="mine")])

    assert result["resolved"] == [] and result["unresolved"] == ["hl-lost"]
    stored = _row(world, "hl-lost")
    assert stored.position_type == "text_quote"
    assert stored.highlighted_text == quote["exact"] and stored.note_text == "mine"
    assert _pull(client)["hl-lost"]["text_quote"] == quote
    # The web reader is told plainly it cannot draw it, not handed a guess.
    assert _web_reader_status(world, "hl-lost") == (None, "unresolved")


def test_a_later_push_that_cannot_be_placed_keeps_the_place_found_earlier(world, client):
    quote = _quote(60)
    _push(client, [_highlight("hl-1", quote)])
    placed = _row(world, "hl-1").start_xpointer

    # The same highlight pushed when the library file cannot be read (here:
    # the user lost the right to read it) must not erase its anchor.
    world.user.role = 0
    world.session.commit()
    result = _push(client, [_highlight("hl-1", quote, note_text="later")])

    assert result["unresolved"] == ["hl-1"]
    stored = _row(world, "hl-1")
    assert stored.position_type == "koreader_xpointer" and stored.start_xpointer == placed
    assert stored.note_text == "later"


def test_a_bookmark_is_a_point_at_its_word(world, client):
    words = _page(30)[1].split()
    quote = {"prefix": _typed(words[0:8]), "exact": _typed(words[8:9]),
             "suffix": _typed(words[9:17])}

    result = _push(client, [{"annotation_id": "bm-1", "source": "textquote", "type": "dogear",
                             "text_quote": quote}])

    assert result["resolved"] == ["bm-1"]
    stored = _row(world, "bm-1")
    assert stored.annotation_type == "dogear" and stored.highlighted_text is None
    assert text_anchor.fold(kx.passage_between(
        world.epub, stored.start_xpointer, stored.end_xpointer)) == text_anchor.fold(words[8])
    assert _pull(client)["bm-1"]["text_quote"] == quote


# ---------------------------------------------------------------------------
# every other reader -> client
# ---------------------------------------------------------------------------


def _koreader_highlight(world, annotation_id, n):
    """A KOReader highlight on page ``n``, as its plugin pushes it."""
    start, end, passage, _ = text_anchor.place_quote(world.epub, _quote(n))
    return {"annotation_id": annotation_id, "source": "koreader",
            "position_type": "koreader_xpointer", "start_xpointer": start,
            "end_xpointer": end, "highlighted_text": passage,
            "device_origin_id": annotation_id}


def _web_highlight(world, annotation_id, n):
    start, end, passage, _ = text_anchor.place_quote(world.epub, _quote(n))
    world.session.add(ub.Annotation(
        user_id=world.user.id, book_id=BOOK_ID, annotation_id=annotation_id,
        source="webreader", highlighted_text=passage, position_type="cfi",
        cfi_range=kx.xpointers_to_cfi_range(world.epub, start, end),
        server_modified_at=datetime.now(timezone.utc)))  # as the reader's create does
    world.session.commit()


def test_other_readers_highlights_reach_the_client_as_their_words(world, client):
    _push(client, [_koreader_highlight(world, "kr-1", 40)], document=world.digest,
          device="KindleBasic5", device_id="kindle-1")
    _web_highlight(world, "web-1", 80)

    pulled = _pull(client)

    for annotation_id, n in (("kr-1", 40), ("web-1", 80)):
        quote = pulled[annotation_id]["text_quote"]
        assert text_anchor.fold(quote["exact"]) == text_anchor.fold(_quote(n)["exact"])
        assert quote["prefix"] and quote["suffix"]
        # What is served is what the client may send back, and lands there.
        assert text_anchor.parse_quote(quote) == quote
        placed = text_anchor.place_quote(world.epub, quote)
        assert (placed[0], placed[1]) == kx.cfi_range_to_xpointers(
            world.epub, _web_reader_status(world, annotation_id)[0])
    # A client that did not ask is not made to wait for the book to be read.
    assert _pull(client, quotes=False)["web-1"]["text_quote"] is None


def test_an_edit_from_another_device_keeps_the_highlights_creator(world, client):
    _push(client, [_highlight("hl-1", _quote(60))])

    _push(client, [_highlight("hl-1", _quote(60), note_text="from the tablet",
                              device_origin_id="tablet-1")],
          device="WordReader (Tablet)", device_id="tablet-1")

    pulled = _pull(client)["hl-1"]
    assert pulled["note_text"] == "from the tablet"
    assert pulled["device_origin_id"] == "phone-1"


def test_a_highlight_another_reader_moved_comes_back_as_its_new_words(world, client):
    _push(client, [_highlight("hl-1", _quote(60))])
    moved = _koreader_highlight(world, "hl-1", 62)

    _push(client, [moved], document=world.digest, device="KindleBasic5",
          device_id="kindle-1")

    quote = _pull(client)["hl-1"]["text_quote"]
    assert text_anchor.fold(quote["exact"]) == text_anchor.fold(moved["highlighted_text"])


@pytest.mark.parametrize("position_type", ["cfi", None])
def test_a_quote_sent_for_another_readers_highlight_never_moves_it(world, client, position_type):
    _web_highlight(world, "web-1", 80)
    row = _row(world, "web-1")
    row.position_type = position_type  # None: a row from before position types
    world.session.commit()
    before = (row.position_type, row.cfi_range, row.highlighted_text, row.start_xpointer)

    for elsewhere in (_quote(82), {"exact": "words no edition of this novel holds"}):
        result = _push(client, [_highlight("web-1", elsewhere, note_text="noted")])
        assert result["resolved"] == ["web-1"] and result["unresolved"] == []

    row = _row(world, "web-1")
    assert (row.position_type, row.cfi_range, row.highlighted_text, row.start_xpointer) == before
    assert row.note_text == "noted"
    assert _web_reader_status(world, "web-1")[1] == "ok"


def test_a_quote_echoed_with_another_readers_anchor_is_not_kept_as_its_words(world, client):
    _push(client, [_koreader_highlight(world, "kr-1", 40)], document=world.digest,
          device="KindleBasic5", device_id="kindle-1")
    echoed = dict(_pull(client)["kr-1"], text_quote=_quote(82), note_text="noted")

    _push(client, [echoed])

    assert text_anchor.fold(_pull(client)["kr-1"]["text_quote"]["exact"]) == \
        text_anchor.fold(_quote(40)["exact"])


def test_an_anchor_into_another_copy_of_the_book_gives_no_words(world, client):
    highlight = _koreader_highlight(world, "kr-elsewhere", 40)
    highlight["highlighted_text"] = "words this place does not hold"
    _push(client, [highlight], document=world.digest, device="KindleBasic5",
          device_id="kindle-1")

    assert _pull(client)["kr-elsewhere"]["text_quote"] is None


def test_a_deletion_anywhere_reaches_the_client_as_a_newer_tombstone(world, client):
    from cps.annotations import delete_annotation
    _web_highlight(world, "web-1", 80)
    before = _pull(client)["web-1"]

    delete_annotation("web-1", user_id=world.user.id, book_id=BOOK_ID,
                      session=world.session, commit=world.session.commit)

    after = _pull(client)["web-1"]
    assert after["hidden"] is True
    assert after["content_revision"] > before["content_revision"]
    assert after["server_modified_at"] > before["server_modified_at"]


def test_a_deletion_koreader_names_reaches_the_client_as_a_newer_tombstone(world, client):
    _push(client, [_highlight("ko-1", _quote(70), source="koreader")],
          document=world.digest, device="KindleBasic5", device_id="kindle-1")
    before = _pull(client)["ko-1"]

    _push(client, [], deleted=["ko-1"],
          document=world.digest, device="KindleBasic5", device_id="kindle-1")

    after = _pull(client)["ko-1"]
    assert after["hidden"] is True
    assert after["content_revision"] > before["content_revision"]
    assert after["server_modified_at"] > before["server_modified_at"]


def test_an_edit_from_any_device_advances_the_revision_and_a_resend_does_not(world, client):
    _push(client, [_highlight("hl-1", _quote(60))])
    first = _pull(client)["hl-1"]

    _push(client, [_highlight("hl-1", _quote(60))])
    assert _pull(client)["hl-1"]["content_revision"] == first["content_revision"]

    _push(client, [{"annotation_id": "hl-1", "note_text": "edited in KOReader"}],
          document=world.digest, device="KindleBasic5", device_id="kindle-1")
    edited = _pull(client)["hl-1"]
    assert edited["note_text"] == "edited in KOReader"
    assert edited["content_revision"] > first["content_revision"]


# ---------------------------------------------------------------------------
# delete authority
# ---------------------------------------------------------------------------


def test_each_client_deletes_only_its_own_highlights(world, client):
    _push(client, [_highlight("hl-1", _quote(60))])
    _push(client, [_koreader_highlight(world, "kr-1", 40)], document=world.digest,
          device="KindleBasic5", device_id="kindle-1")
    _web_highlight(world, "web-1", 80)

    named = _push(client, [], deleted=["kr-1", "web-1"], delete_source="textquote")
    inline = _push(client, [{"annotation_id": "kr-1", "hidden": True}],
                   delete_source="textquote")
    by_koreader = _push(client, [], document=world.digest, deleted=["hl-1"],
                        device="KindleBasic5", device_id="kindle-1")

    assert named["deleted"] == inline["deleted"] == by_koreader["deleted"] == 0
    pulled = _pull(client)
    assert not any(pulled[a]["hidden"] for a in ("hl-1", "kr-1", "web-1"))

    own = _push(client, [], deleted=["hl-1"], delete_source="textquote")
    assert own["deleted"] == 1 and _pull(client)["hl-1"]["hidden"] is True


# ---------------------------------------------------------------------------
# access
# ---------------------------------------------------------------------------


def test_words_are_placed_and_named_only_for_a_reader_allowed_to_read(world, client):
    _web_highlight(world, "web-1", 80)
    world.user.role = 0  # may see the book, may neither read nor download it
    world.session.commit()

    result = _push(client, [_highlight("hl-1", _quote(60))])

    assert result["unresolved"] == ["hl-1"]
    assert _row(world, "hl-1").start_xpointer is None
    assert _pull(client)["web-1"]["text_quote"] is None


def test_a_book_id_names_only_a_book_the_user_may_see(world, client):
    world.can_see = set()

    response = client.get(f"/kosync/syncs/annotations/{BOOK_ID}").get_json()
    pushed = _push(client, [_highlight("hl-1", _quote(60))])

    assert response["book_known"] is False and response["annotations"] == []
    assert pushed["matched"] is False
    assert world.session.query(ub.Annotation).count() == 0


@pytest.mark.parametrize("bad, field", [
    ({"exact": ""}, "text_quote"),
    ({"exact": 7}, "text_quote"),
    ({"exact": "x" * (text_anchor.MAX_QUOTE_CHARS + 1)}, "text_quote"),
    ({"exact": "fine", "prefix": "p" * (text_anchor.MAX_CONTEXT_CHARS + 1)}, "text_quote"),
])
def test_a_malformed_quote_is_refused_with_its_reason(world, client, bad, field):
    response = _push(client, [_highlight("hl-1", bad)], status=400)

    assert response["error"] == "invalid_annotation" and field in response["message"]
    assert world.session.query(ub.Annotation).count() == 0


@pytest.mark.parametrize("percentage", [1.5, -0.1, True, "0.5"])
def test_a_percentage_outside_zero_to_one_is_refused(world, client, percentage):
    response = _push(client, [_highlight("hl-1", _quote(60), percentage=percentage)],
                     status=400)

    assert "percentage" in response["message"]


def test_the_server_says_it_understands_quotes():
    assert "annotations_text_quote" in kosync_routes.SERVER_CAPABILITIES


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_the_quote_column_is_added_to_an_existing_database_once(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE annotation (id INTEGER PRIMARY KEY, note_text TEXT)"))
        conn.execute(text("INSERT INTO annotation (id, note_text) VALUES (1, 'kept')"))

    ub.migrate_annotation_text_quote(engine, None)
    ub.migrate_annotation_text_quote(engine, None)

    with engine.connect() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info(annotation)"))]
        assert columns.count("text_quote") == 1
        assert conn.execute(text("SELECT note_text, text_quote FROM annotation")).one() == (
            "kept", None)


def test_a_stored_quote_is_served_as_sent(world, client):
    quote = _quote(60)
    _push(client, [_highlight("hl-1", quote)])

    assert json.loads(_row(world, "hl-1").text_quote) == quote
    assert _pull(client, quotes=False)["hl-1"]["text_quote"] == quote


def test_a_quote_ending_inside_a_ligature_is_not_widened_to_the_whole_character(tmp_path):
    from tests.unit.test_koreader_xpointer import _html_book
    epub = _html_book(tmp_path, ["<p>The committee reached its ﬁnal decision late that night.</p>"])
    # Folded, U+FB01 is "fi": a quote may end on its "f", which no range can
    # hold without also holding the "i".
    cut = {"prefix": "The committee reached its", "exact": "f", "suffix": "inal decision late"}
    whole = {"prefix": "The committee reached its", "exact": "ﬁnal",
             "suffix": "decision late that night."}

    assert text_anchor.place_quote(epub, cut) is None
    start, end, passage, _ = text_anchor.place_quote(epub, whole)
    assert passage == "ﬁnal"


@pytest.fixture
def kobo_book(tmp_path, monkeypatch):
    """Alice as the library holds it: the EPUB and the KEPUB a Kobo reads."""
    import shutil
    from types import SimpleNamespace
    from cps import config
    fixtures = __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "koreader_xpointer"
    folder = tmp_path / "Lewis Carroll" / "Alice (11)"
    folder.mkdir(parents=True)
    shutil.copy(fixtures / "alice-pg11.epub", folder / "alice.epub")
    shutil.copy(fixtures / "alice-pg11.kepub.epub", folder / "alice.kepub")
    monkeypatch.setattr(config, "get_book_path", lambda: str(tmp_path))
    spans = json.loads((fixtures / "alice-pg11.kobo-spans.json").read_text())
    book = SimpleNamespace(id=11, path="Lewis Carroll/Alice (11)", data=[
        SimpleNamespace(format="EPUB", name="alice"), SimpleNamespace(format="KEPUB", name="alice")])
    first = {}
    for s in spans:  # span ids restart in every document; the title's comes first
        first.setdefault(s["span"], s)
    return SimpleNamespace(book=book, epub=str(folder / "alice.epub"), span=first)


def _kobo_row(span, text_):
    return ub.Annotation(annotation_id="kobo-1", source="kobo", highlighted_text=text_,
                         content_id="uuid!!" + span["source"],
                         start_container_path="span#" + span["span"],
                         end_container_path="span#" + span["span"], hidden=False)


def test_a_kobo_highlight_reaches_the_client_as_its_words(kobo_book):
    span = kobo_book.span["kobo.8.1"]  # the title, "Alice’s Adventures in Wonderland"
    row = _kobo_row(span, "Alice’s Adventures in Wonderland")
    wire = {}

    annotation_routes._add_text_quotes([row], [wire], kobo_book.book)

    quote = wire["text_quote"]
    assert text_anchor.fold(quote["exact"]) == text_anchor.fold(row.highlighted_text)
    assert quote["suffix"].startswith("by Lewis Carroll")
    start, end, _passage, _ = text_anchor.place_quote(kobo_book.epub, quote)
    assert kx.passage_between(kobo_book.epub, start, end) == "Alice’s Adventures in Wonderland"


def test_a_kobo_highlight_whose_words_do_not_start_in_its_span_gives_no_words(kobo_book):
    span = kobo_book.span["kobo.8.1"]
    wire = {}

    annotation_routes._add_text_quotes(
        [_kobo_row(span, "Down the Rabbit-Hole")], [wire], kobo_book.book)

    assert "text_quote" not in wire
