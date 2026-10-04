# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A KOSync client that knows only words and a percentage lands on the same words.

Some reading clients flatten a book to its words and hold no engine locator.
They push ``position_kind: "percentage"`` with an ``anchor`` (the word at
their place plus a few words either side) and ask for ``anchor`` on GET.
Driven through the real KOSync routes and the web reader's resume read, over
a real SQLite app database and the Metamorphosis EPUB, with positions a
Kindle recorded (``tests/fixtures/koreader_xpointer``).

What must hold:
* a client's anchor reaches KOReader and the web reader as the exact place,
  and every other reader's exact place reaches the client as words;
* only a place provably in the library EPUB becomes words or an XPointer;
  anything else stays the percentage it always was;
* which position wins (furthest, same-device rewind) does not change.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cps import calibre_db, constants, ub
from cps.progress_syncing.models import KOSyncProgress
from cps.services import koreader_xpointer as kx
from tests.unit.test_koreader_exact_positions import (  # noqa: F401  (fixture)
    BOOK_ID, OTHER_FILE, _cfi_page_text, _drain_resume_workers, _page, _web_cfi, world)
from tests.unit.test_koreader_xpointer import _html_book, _squash

pytestmark = pytest.mark.unit

KINDS = "?position_kinds=locator,percentage,anchor"


@pytest.fixture(autouse=True)
def _a_reader_who_may_open_the_book(request, monkeypatch):
    """The world's user can see book 221 and read it in the browser.

    The visibility lookup answers for the user it is asked about, so a
    caller that forgets to pass the user, or asks about another one, is
    told the book does not exist.
    """
    if "world" not in request.fixturenames:
        return
    world = request.getfixturevalue("world")
    world.user.role = constants.ROLE_VIEWER
    world.session.commit()
    world.can_see = {BOOK_ID}
    book = SimpleNamespace(
        id=BOOK_ID, title="Metamorphosis", path="Franz Kafka/Metamorphosis (221)",
        data=[SimpleNamespace(format="EPUB", name="Metamorphosis - Franz Kafka")])

    def get_filtered_book(book_id, *args, user=None, **kw):
        if user is not world.user or book_id not in world.can_see:
            return None
        return book
    monkeypatch.setattr(calibre_db, "get_filtered_book", get_filtered_book, raising=False)


def _client(world):
    import importlib
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    import flask
    app = flask.Flask(__name__)
    app.secret_key = "test"  # device identities are keyed by it
    app.register_blueprint(kosync.kosync)
    return app.test_client()


def _words(text, n):
    return " ".join(text.split()[:n])


def _anchor_for_page(n, *, typographic=True):
    """The anchor a word-based client would send for Kindle page ``n``.

    Built from the words KOReader showed, as another tokenizer would see them:
    straight quotes for curly ones, and a soft hyphen inside a word.
    """
    _xpointer, shown = _page(n)
    words = shown.split()
    after = " ".join(words[1:9])
    if typographic:
        after = after.replace("’", "'").replace("“", '"').replace("”", '"')
        after = after.replace("glowering", "glow­ering")
    return {"text": words[0], "before": "", "after": after}


def _kindle_fraction(n):
    """How far into the book the Kindle was on page ``n`` (of its 116)."""
    return n / 116


def _push(client, body, status=200):
    payload = {"device": "WordReader (Phone)", "device_id": "phone-1",
               "position_kind": "percentage", **body}
    response = client.put("/kosync/syncs/progress", json=payload)
    assert response.status_code == status, response.get_json()
    _drain_resume_workers()  # the web reader's conversion runs off the request
    return response.get_json()


def _solid(epub, xpointer):
    return kx.solid_index_of_xpointer(epub, xpointer)


# ---------------------------------------------------------------------------
# words -> every other reader
# ---------------------------------------------------------------------------


def test_the_kindle_and_the_browser_open_at_the_clients_words(world):
    client = _client(world)
    page_xpointer, shown = _page(60)
    _push(client, {"document": world.digest, "percentage": 0.51,
                   "anchor": _anchor_for_page(60)})

    body = world.koreader_pull(world.digest)
    assert body["position_kind"] == "locator"
    assert _solid(world.epub, body["progress"]) == _solid(world.epub, page_xpointer)
    assert body["percentage"] == pytest.approx(_kindle_fraction(60), abs=0.02)

    resume = world.web_resume()
    assert _cfi_page_text(world.epub, resume["cfi"]).startswith(_squash(shown)[:80])


def test_an_older_plugin_also_gets_the_place_the_words_name(world):
    # The located row is an ordinary locator, so a plugin that predates
    # percentage-only rows receives it too, instead of nothing.
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.51,
                   "anchor": _anchor_for_page(60)})

    body = world.koreader_pull(world.digest, advertise=False)

    assert _solid(world.epub, body["progress"]) == _solid(world.epub, _page(60)[0])


LICENCE = {"text": "Project", "before": "",
           "after": "Gutenberg License included with this eBook or online"}


def test_repeated_words_are_placed_by_the_percentage_only_when_it_decides(world):
    # The Project Gutenberg licence names itself in the same words at the
    # front of the book and at its end.
    from cps.services import text_anchor
    front = text_anchor.locate(world.epub, LICENCE, 0.0)
    back = text_anchor.locate(world.epub, LICENCE, 100.0)
    assert front and back and front != back
    # Halfway between them the percentage says nothing: no place is invented.
    total = len(text_anchor._book(world.epub).text)
    middle = sum(text_anchor._book(world.epub).text.find(text_anchor.fold(
        LICENCE["text"] + LICENCE["after"]), start) for start in (0, total // 2))
    assert text_anchor.locate(world.epub, LICENCE, middle / 2 / total * 100) is None
    assert text_anchor.locate(world.epub, LICENCE, None) is None


def test_ambiguous_words_stay_a_percentage_on_the_wire(world):
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.5, "anchor": {
        "text": "Gregor", "before": "", "after": ""}})

    stored = world.session.query(KOSyncProgress).one()
    assert stored.progress == "cwng:percentage"
    body = world.koreader_pull(world.digest)
    assert body["position_kind"] == "percentage" and body["progress"] is None
    assert body["percentage"] == pytest.approx(0.5)
    assert "cfi" not in world.web_resume()


def test_words_not_in_the_book_stay_a_percentage(world):
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.4, "anchor": {
        "text": "Nowhere", "after": "in this book do these exact words appear together"}})

    body = world.koreader_pull(world.digest)
    assert body["position_kind"] == "percentage"
    assert "anchor" not in _client(world).get(
        f"/kosync/syncs/progress/{world.digest}{KINDS}").get_json()


def test_a_percentage_push_without_an_anchor_is_shared_as_a_percentage(world):
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.3})

    body = world.koreader_pull(world.digest)
    assert (body["position_kind"], body["progress"]) == ("percentage", None)
    assert world.web_resume()["percentage"] == pytest.approx(30.0)
    assert world.koreader_pull(world.digest, advertise=False) == {
        "position_kinds_available": ["percentage"]}


# ---------------------------------------------------------------------------
# every other reader -> words
# ---------------------------------------------------------------------------


def test_a_kindle_page_comes_back_as_its_words(world):
    page_xpointer, shown = _page(60)
    world.koreader_push(page_xpointer, 0.5143, world.digest)

    body = _client(world).get(f"/kosync/syncs/progress/{world.digest}{KINDS}").get_json()

    anchor = body["anchor"]
    assert anchor["text"] == "his"
    assert _squash(anchor["text"] + anchor["after"]).startswith(_squash(_words(shown, 9)))
    assert anchor["before"].endswith("shouted")
    assert len(anchor["before"].split()) == 8 and len(anchor["after"].split()) == 8


def test_the_web_readers_place_comes_back_as_its_words(world):
    _xpointer, cfi = _web_cfi(61)
    world.web_save(cfi, 52.0)

    body = _client(world).get(f"/kosync/syncs/progress/{OTHER_FILE}{KINDS}").get_json()

    # The client holds its own copy (another digest): it still gets the words,
    # which are the same in every copy, though never the library XPointer.
    assert body["position_kind"] == "percentage" and body["progress"] is None
    assert _squash(body["anchor"]["text"] + body["anchor"]["after"]).startswith(
        _squash(_words(_page(61)[1], 9)))


def test_a_kindle_page_in_some_other_file_is_not_turned_into_words(world):
    # Its XPointer addresses a file the server does not have; the words the
    # library EPUB holds at that path could be anywhere in that file.
    world.koreader_push(_page(60)[0], 0.5143, OTHER_FILE)

    body = _client(world).get(f"/kosync/syncs/progress/{world.digest}{KINDS}").get_json()

    assert "anchor" not in body
    assert body["percentage"] == pytest.approx(0.5143)


def test_the_clients_own_words_round_trip(world):
    client = _client(world)
    sent = _anchor_for_page(61, typographic=False)
    _push(client, {"document": OTHER_FILE, "percentage": 0.52, "anchor": sent})

    body = client.get(f"/kosync/syncs/progress/{OTHER_FILE}{KINDS}").get_json()

    assert body["anchor"]["text"] == sent["text"]
    assert body["anchor"]["after"] == sent["after"]


# ---------------------------------------------------------------------------
# who wins is unchanged
# ---------------------------------------------------------------------------


def test_a_client_behind_another_device_does_not_pull_it_back(world):
    client = _client(world)
    world.koreader_push(_page(61)[0], 0.52, world.digest)
    _push(client, {"document": world.digest, "percentage": 0.10,
                   "anchor": _anchor_for_page(10)})

    body = world.koreader_pull(world.digest)
    assert body["progress"] == _page(61)[0]
    assert body["percentage"] == pytest.approx(0.52)


def test_a_client_counting_more_words_does_not_overtake_a_kindle_ahead_of_it(world):
    # The client counts the front matter too, so the sentence the Kindle
    # calls 51% is 58% to it. Its place is two pages behind the Kindle's:
    # it must not win on its own larger figure.
    client = _client(world)
    world.koreader_push(_page(62)[0], _kindle_fraction(62), world.digest)
    _push(client, {"document": world.digest, "percentage": 0.58,
                   "anchor": _anchor_for_page(60)})

    body = world.koreader_pull(world.digest)
    assert body["progress"] == _page(62)[0]


def test_a_place_the_client_has_not_finished_is_not_made_finished(world):
    from cps.services import text_anchor
    member, solid = kx.spine_solid_texts(world.epub)[-1]
    near_end = kx.xpointer_at_solid_index(world.epub, member, len(solid) - 40, solid)
    anchor = text_anchor.anchor_at(world.epub, near_end)
    found = text_anchor.place(world.epub, anchor, 98.9)
    assert found and found[1] >= 99.0  # these words are in the book's last 1%
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.989, "anchor": anchor})

    body = world.koreader_pull(world.digest)
    assert body["percentage"] < 0.99
    assert world.session.query(ub.ReadBook).one().read_status != ub.ReadBook.STATUS_FINISHED


def test_a_client_that_finished_the_story_finishes_the_book(world):
    # The story ends where the licence pages begin, well short of 100% of
    # the file's text; the client, which does not count those pages, is done.
    from cps.services import text_anchor
    last_words = {"text": "body.", "before": "get up and stretch out her young", "after": ""}
    found = text_anchor.place(world.epub, last_words, 100.0)
    assert found and found[1] < 95.0
    _push(_client(world), {"document": world.digest, "percentage": 1.0, "anchor": last_words})

    assert world.session.query(ub.ReadBook).one().read_status == ub.ReadBook.STATUS_FINISHED
    assert world.koreader_pull(world.digest)["percentage"] == pytest.approx(1.0)


def test_the_same_client_may_turn_back(world):
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.52,
                   "anchor": _anchor_for_page(61)})
    _push(client, {"document": world.digest, "percentage": 0.51,
                   "anchor": _anchor_for_page(60)})

    body = world.koreader_pull(world.digest)
    assert _solid(world.epub, body["progress"]) == _solid(world.epub, _page(60)[0])
    assert body["percentage"] == pytest.approx(_kindle_fraction(60), abs=0.02)


def test_an_unplaced_percentage_level_with_the_kindle_does_not_erase_its_place(world):
    # The client echoes the percentage it pulled without words (it holds
    # another copy, say): the Kindle's exact place must survive the tie.
    page_xpointer, _shown = _page(60)
    world.koreader_push(page_xpointer, 0.5143, world.digest)
    _push(_client(world), {"document": world.digest, "percentage": 0.5143})

    body = world.koreader_pull(world.digest)
    assert body["position_kind"] == "locator" and body["progress"] == page_xpointer


# ---------------------------------------------------------------------------
# the words are the book's content: only a reader of the book gets them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["no reading role", "book not visible to them"])
def test_someone_who_may_not_read_the_book_neither_places_nor_receives_words(world, who):
    if who == "no reading role":
        world.user.role = constants.ROLE_UPLOAD
        world.session.commit()
    else:
        world.can_see = set()
    client = _client(world)

    _push(client, {"document": world.digest, "percentage": 0.51,
                   "anchor": _anchor_for_page(60)})
    assert world.session.query(KOSyncProgress).one().progress == "cwng:percentage"

    world.koreader_push(_page(61)[0], 0.52, world.digest)
    assert "anchor" not in client.get(
        f"/kosync/syncs/progress/{world.digest}{KINDS}").get_json()


def test_the_download_role_alone_is_enough_to_get_the_words(world):
    world.user.role = constants.ROLE_DOWNLOAD
    world.session.commit()
    world.koreader_push(_page(60)[0], 0.5143, world.digest)

    body = _client(world).get(f"/kosync/syncs/progress/{world.digest}{KINDS}").get_json()

    assert body["anchor"]["text"] == "his"


# ---------------------------------------------------------------------------
# what the server serves, it must accept back
# ---------------------------------------------------------------------------


def test_text_without_spaces_between_words_gets_no_anchor(tmp_path):
    from cps.services import text_anchor
    epub = _html_book(tmp_path, ["<p>" + "変身" * 300 + "</p>"])
    member, solid = kx.spine_solid_texts(epub)[0]
    xpointer = kx.xpointer_at_solid_index(epub, member, 300, solid)

    assert xpointer and text_anchor.anchor_at(epub, xpointer) is None


def test_very_long_words_around_the_place_are_trimmed_to_what_a_push_allows(tmp_path):
    from cps.services import text_anchor
    long_words = " ".join(f"{chr(97 + i)}" * 150 for i in range(8))
    epub = _html_book(tmp_path, [f"<p>{long_words} here {long_words}</p>"])
    member, solid = kx.spine_solid_texts(epub)[0]
    xpointer = kx.xpointer_at_solid_index(epub, member, solid.index("here"), solid)

    anchor = text_anchor.anchor_at(epub, xpointer)

    assert anchor["text"] == "here"
    assert text_anchor.parse_anchor(anchor) == anchor
    # The words nearest the place are the ones kept.
    assert anchor["before"].endswith("h" * 150) and anchor["after"].startswith("a" * 150)


def test_words_that_repeat_more_often_than_are_counted_are_not_placed(world, monkeypatch):
    from cps.services import text_anchor
    # The licence's words occur twice; counting only that many cannot show
    # there is no third copy nearer, so the percentage no longer decides.
    assert text_anchor.locate(world.epub, LICENCE, 0.0)
    monkeypatch.setattr(text_anchor, "_HIT_LIMIT", 2)
    assert text_anchor.locate(world.epub, LICENCE, 0.0) is None


# ---------------------------------------------------------------------------
# the request contract
# ---------------------------------------------------------------------------


def test_auth_names_the_capabilities_a_client_may_rely_on(world):
    body = _client(world).get("/kosync/users/auth").get_json()

    assert body["authorized"] == "OK"
    assert {"percentage_push", "anchor", "book_id_document"} <= set(body["capabilities"])


@pytest.mark.parametrize("body", [
    {"position_kind": "percentage", "progress": "/body/DocFragment[3]", "percentage": 0.5},
    {"position_kind": "percentage", "percentage": 0.5, "anchor": {"text": ""}},
    {"position_kind": "percentage", "percentage": 0.5, "anchor": {"text": "x" * 201}},
    {"position_kind": "percentage", "percentage": 0.5, "anchor": ["his", "sister"]},
    {"position_kind": "percentage", "percentage": True},
    {"position_kind": "percentage", "percentage": float("nan")},
    {"position_kind": "percentage", "percentage": "inf"},
    {"position_kind": "words", "percentage": 0.5},
    {"progress": "/body/DocFragment[4]/body/div/p[27]/text().352", "percentage": 0.5,
     "anchor": {"text": "his"}},
])
def test_a_malformed_push_changes_nothing(world, body):
    client = _client(world)
    _push(client, {"document": world.digest, "percentage": 0.2})
    before = [(r.document, r.progress, r.percentage) for r in
              world.session.query(KOSyncProgress).all()]

    response = client.put("/kosync/syncs/progress", json={
        "document": world.digest, "device": "WordReader", "device_id": "phone-1", **body})

    assert response.status_code == 400, response.get_json()
    # Refused as an invalid field, not by the database tripping over it.
    assert response.get_json()["error"] == 2003, response.get_json()
    world.session.expire_all()
    assert [(r.document, r.progress, r.percentage) for r in
            world.session.query(KOSyncProgress).all()] == before


# ---------------------------------------------------------------------------
# a decimal book id as the document
# ---------------------------------------------------------------------------


@pytest.fixture
def by_book_id(world, monkeypatch):
    """Only the digest of the library file names the book; ids resolve by policy."""
    import importlib
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    monkeypatch.setattr(kosync, "enrich_response_with_book_info",
                        lambda response, document: (response, None, None, None, None))
    return world


def test_a_push_under_the_book_id_reaches_the_kobo_and_the_read_status(by_book_id):
    client = _client(by_book_id)

    body = _push(client, {"document": str(BOOK_ID), "percentage": 0.51,
                          "anchor": _anchor_for_page(60)})

    assert body["calibre_book_id"] == BOOK_ID
    bookmark = by_book_id.session.query(ub.KoboBookmark).one()
    assert bookmark.progress_percent == pytest.approx(_kindle_fraction(60) * 100, abs=2)
    read = by_book_id.session.query(ub.ReadBook).one()
    assert read.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    pulled = client.get(f"/kosync/syncs/progress/{BOOK_ID}{KINDS}").get_json()
    assert pulled["anchor"]["text"] == "his"


def test_a_book_id_the_user_cannot_open_names_no_book(by_book_id):
    client = _client(by_book_id)

    body = _push(client, {"document": "999", "percentage": 0.5})

    assert "calibre_book_id" not in body and "calibre_book_title" not in body
    assert by_book_id.session.query(ub.KoboBookmark).count() == 0
    assert by_book_id.session.query(ub.ReadBook).count() == 0


# ---------------------------------------------------------------------------
# the fold every client must reproduce (published in KOSYNC-TEXT-ANCHOR-DESIGN)
# ---------------------------------------------------------------------------

FOLD_VECTORS = [
    ("Café", "cafe"),
    ("Café", "cafe"),            # decomposed accent
    ("CAFÉ", "cafe"),
    ("glow­ering", "glowering"),  # soft hyphen
    ("zero​width", "zerowidth"),
    ("“Gregor!”", '"gregor!"'),
    ("it’s", "it's"),
    ("self—aware", "self-aware"),
    ("a b c\n\td", "abcd"),       # every kind of whitespace
    ("ﬁne", "fine"),              # ligature
    ("Straße", "straße"),              # lowercase, not casefold
    ("\u00c6SIR", "\u00e6sir"),
    ("zero\u200bwidth\ufeff", "zerowidth"),
    ("\u201cGregor\u2014come here!\u201d", '"gregor-comehere!"'),
    ("it\u2019s \u2018so\u2019", "it's'so'"),
    ("\ufb01ne\u00a0\u2163", "fineiv"),       # compatibility forms
    ("\u2032", "\u2032"),                       # primes are not quotes
]


@pytest.mark.parametrize("raw, folded", FOLD_VECTORS)
def test_the_published_fold(raw, folded):
    from cps.services.text_anchor import fold
    assert fold(raw) == folded


def test_words_in_another_case_still_find_their_place(world):
    from cps.services import text_anchor
    page_xpointer, shown = _page(60)
    words = shown.split()
    anchor = {"text": words[0], "after": " ".join(words[1:9]).upper()}

    assert _solid(world.epub, text_anchor.locate(world.epub, anchor, 51.0)) == \
        _solid(world.epub, page_xpointer)
