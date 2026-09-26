# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""epub.js CFI <-> KOReader XPointer conversion (#324), judged by ground truth.

The XPointer side comes from KOReader itself (``tests/fixtures/koreader_xpointer``,
provenance in ``tests/fixtures/README.md``):

* ``<book>.pages.json`` -- the XPointer KOReader recorded at the top of every
  page of two library EPUBs on a Kindle, with the text that page starts with;
* ``engine-words.json`` -- word ranges KOReader's crengine reports for those
  books and for ``probe.epub``, a book built to hit each crengine text rule;
* ``rig-engine-check.json`` -- crengine's text for the two rig highlights.

The CFI side is judged by ``_EpubJs`` below, a small resolver written from
epub.js's CFI rules and independent of the converter: a CFI is right when the
DOM text it addresses is the text KOReader shows at the XPointer.
"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, config, ub
from cps.services import koreader_xpointer as kx

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "koreader_xpointer"
BOOKS = ("metamorphosis-221", "alice-222")

SHUDDER_CFI = "epubcfi(/6/6!/4/2[pgepubid00002]/10,/1:845,/1:852)"


def _json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _squash(text):
    """What both engines agree on: the characters, not the spacing."""
    return re.sub(r"\s+", "", text).replace("­", "")


class _EpubJs:
    """Text addressed by an epub.js CFI (epubcfi.js 0.3.93 rules).

    Steps below ``!`` walk element children as ``2(n+1)`` and text-node
    children as ``2k+1`` (every text node, blank ones included), from
    ``<body>`` = ``/4``; offsets are UTF-16 units.
    """

    def __init__(self, epub):
        self.archive = zipfile.ZipFile(epub)
        container = self._xml("META-INF/container.xml")
        opf = container.find(".//{*}rootfile").get("full-path")
        package = self._xml(opf)
        manifest = {i.get("id"): i.get("href") for i in package.iterfind(".//{*}manifest/{*}item")}
        self.members = [
            posixpath.normpath(posixpath.join(posixpath.dirname(opf), manifest[ref.get("idref")]))
            for ref in package.iterfind(".//{*}spine/{*}itemref")
        ]
        self.chapters = {}

    def _xml(self, member):
        from lxml import etree
        return etree.fromstring(self.archive.read(member))

    def _chapter(self, spine_step):
        index = spine_step // 2 - 1
        if index not in self.chapters:
            body = self._xml(self.members[index]).find("{*}body")
            texts = []

            def walk(el):
                ordinal = 0
                if el.text:
                    texts.append((el, ordinal, el.text))
                    ordinal += 1
                for child in el:
                    if isinstance(child.tag, str):
                        walk(child)
                    if child.tail:
                        texts.append((el, ordinal, child.tail))
                        ordinal += 1
            walk(body)
            self.chapters[index] = (body, texts)
        return self.chapters[index]

    @staticmethod
    def _steps(path):
        return [(int(n), int(o) if o else None)
                for n, o in re.findall(r"/(\d+)(?:\[[^\]]*\])?(?::(\d+))?", path)]

    def _point(self, spine_step, steps):
        body, texts = self._chapter(spine_step)
        assert steps[0] == (4, None)
        node = body
        for number, offset in steps[1:]:
            if number % 2 == 0:
                node = [c for c in node if isinstance(c.tag, str)][number // 2 - 1]
            else:
                i = next(i for i, (parent, k, _t) in enumerate(texts)
                         if parent is node and k == (number - 1) // 2)
                value, units, cp = texts[i][2], 0, 0
                while units < offset:
                    units += 2 if ord(value[cp]) > 0xFFFF else 1
                    cp += 1
                return i, cp
        return node  # an element

    def point_text(self, cfi):
        spine, path = re.fullmatch(r"epubcfi\(/\d+/(\d+)(?:\[[^\]]*\])?!(.*)\)", cfi).groups()
        _body, texts = self._chapter(int(spine))
        point = self._point(int(spine), self._steps(path))
        if not isinstance(point, tuple):
            return point
        i, cp = point
        return texts[i][2][cp:] + "".join(t for _p, _k, t in texts[i + 1:])

    def range_text(self, cfi):
        spine, common, start, end = re.fullmatch(
            r"epubcfi\(/\d+/(\d+)(?:\[[^\]]*\])?!([^,]*),([^,]*),([^,]*)\)", cfi).groups()
        _body, texts = self._chapter(int(spine))
        (i, a) = self._point(int(spine), self._steps(common + start))
        (j, b) = self._point(int(spine), self._steps(common + end))
        if i == j:
            return texts[i][2][a:b]
        return texts[i][2][a:] + "".join(t for _p, _k, t in texts[i + 1:j]) + texts[j][2][:b]


# ---------------------------------------------------------------------------
# The converter against the device and the engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("book", BOOKS)
def test_every_page_start_the_kindle_recorded_maps_to_the_cfi_of_that_text_and_back(book):
    epub = FIXTURES / f"{book}.epub"
    oracle = _EpubJs(epub)
    samples = _json(f"{book}.pages.json")["samples"]
    failures = []
    for sample in samples:
        xpointer, shown = sample["xpointer"], sample["text"]
        cfi = kx.xpointer_to_cfi(epub, xpointer)
        if cfi is None:
            failures.append((xpointer, "no CFI"))
            continue
        at_cfi = oracle.point_text(cfi)
        if isinstance(at_cfi, str):
            # KOReader's page text starts at the XPointer; the DOM text from
            # the CFI must start with the same characters -- and, since the
            # engines only differ in spacing, a page that opens on a space
            # must map to a CFI that opens on one too.
            same_text = _squash(at_cfi).startswith(_squash(shown)[:80])
            same_edge = (shown[:1].isspace()) == (at_cfi[:1].isspace()) if shown else True
            if not (same_text and same_edge):
                failures.append((xpointer, cfi, shown[:40], at_cfi[:40]))
        elif at_cfi.tag.rsplit("}", 1)[-1] != xpointer.rsplit("/", 1)[-1].split(".")[0]:
            failures.append((xpointer, cfi, "wrong element"))
        back = kx.cfi_to_xpointer(epub, cfi)
        if back != xpointer:
            failures.append((xpointer, cfi, "back", back))
    assert not failures, failures[:5]
    assert len(samples) > 100


@pytest.mark.parametrize("book", BOOKS + ("probe",))
def test_every_engine_word_maps_to_a_cfi_range_framing_that_word_and_back(book):
    epub = FIXTURES / f"{book}.epub"
    oracle = _EpubJs(epub)
    words = _json("engine-words.json")[book]["words"]
    failures = []
    for word in words:
        cfi = kx.xpointers_to_cfi_range(epub, word["s"], word["e"])
        if cfi is None:
            failures.append((word, "no CFI"))
            continue
        framed = re.sub(r"\s+", " ", oracle.range_text(cfi)).replace("­", "")
        back = kx.cfi_range_to_xpointers(epub, cfi)
        if framed != word["t"] or back != (word["s"], word["e"]):
            failures.append((word, cfi, framed, back))
    assert not failures, failures[:5]
    assert len(words) > 100


def test_positions_crengine_does_not_have_are_refused_not_moved():
    epub = FIXTURES / "probe.epub"
    # crengine splits a text node past 8192 source characters; its words
    # there cannot be numbered from the browser's single node.
    for word in _json("engine-words.json")["probe"]["refused"]:
        assert kx.xpointers_to_cfi_range(epub, word["s"], word["e"]) is None, word
    # The line break before <h2> is collapsed space between blocks: epub.js
    # can address it, crengine has no node there.
    assert kx.cfi_to_xpointer(epub, "epubcfi(/6/2!/4/2[c1]/1:0)") is None
    assert kx.cfi_to_xpointer(epub, "epubcfi(/6/2!/4/2[c1]/2/1:0)") == (
        "/body/DocFragment[1]/body/div/h2/text()[1].0")


def _head_voids_engine_words():
    """crengine's words of ``head-voids.epub``, by DocFragment (spine order in
    ``engine/make_head_voids.py``: 1 open.html, 2 closed.html, 3 bodyvoid.html,
    4 badhead.html, 5 open.xhtml)."""
    by_fragment = {}
    for word in _json("engine-words.json")["head-voids"]["words"]:
        number = int(re.match(r"/body/DocFragment\[(\d+)\]", word["s"]).group(1))
        by_fragment.setdefault(number, []).append(word)
    return by_fragment


def test_html_chapter_with_open_head_voids_converts_like_its_self_closed_twin():
    # <meta charset="utf-8"> and <link ...> left open in an .html chapter's
    # head: the browser's HTML parser treats them as void and crengine is
    # lenient, so both readers see the tree of the self-closed twin.
    epub = FIXTURES / "head-voids.epub"
    oracle = _EpubJs(epub)
    words = _head_voids_engine_words()
    twins = {(w["s"], w["e"]) for w in words[2]}
    converted = 0
    for word in words[1]:
        twin_s, twin_e = (word[k].replace("DocFragment[1]", "DocFragment[2]") for k in "se")
        # crengine itself reads the two files as the same tree.
        assert (twin_s, twin_e) in twins, word
        cfi = kx.xpointers_to_cfi_range(epub, word["s"], word["e"])
        twin_cfi = kx.xpointers_to_cfi_range(epub, twin_s, twin_e)
        if "/body/p[3]/" in word["s"]:
            # The verse is white-space: pre-wrap by the stylesheet the open
            # <link> names: the repaired head still brings its CSS along.
            assert cfi is None and twin_cfi is None, word
            continue
        assert twin_cfi is not None, word
        assert cfi == twin_cfi.replace("epubcfi(/6/4!", "epubcfi(/6/2!", 1), word
        framed = re.sub(r"\s+", " ", oracle.range_text(twin_cfi))
        assert framed == word["t"], (word, framed)
        assert kx.cfi_range_to_xpointers(epub, cfi) == (word["s"], word["e"]), word
        point = kx.xpointer_to_cfi(epub, word["s"])
        assert point is not None and kx.cfi_to_xpointer(epub, point) == word["s"], word
        converted += 1
    assert converted > 60


def test_open_voids_are_repaired_only_in_the_head_of_an_html_chapter():
    epub = FIXTURES / "head-voids.epub"
    words = _head_voids_engine_words()
    # 3: an open <br> in the BODY (crengine even puts the following text
    #    inside it); 4: a head still malformed once its voids are closed;
    # 5: an .xhtml member, which the browser parses as XML and cannot render.
    for number in (3, 4, 5):
        assert words[number]
        for word in words[number]:
            assert kx.xpointers_to_cfi_range(epub, word["s"], word["e"]) is None, word
            assert kx.xpointer_to_cfi(epub, word["s"]) is None, word
    for cfi in ("epubcfi(/6/6!/4/2/1:0)", "epubcfi(/6/8!/4/2/1:0)",
                "epubcfi(/6/10!/4/4/1:0)"):
        assert kx.cfi_to_xpointer(epub, cfi) is None, cfi
    # The same body in the .html twins does convert.
    assert kx.cfi_to_xpointer(epub, "epubcfi(/6/2!/4/4/1:0)") == (
        "/body/DocFragment[1]/body/p[1]/text().0")


def test_gutenberg_html_chapters_map_to_the_cfis_chromium_gives_their_words():
    # alice-pg11.epub is today's Project Gutenberg build: every chapter has
    # <a id="…"/> or <div/>, which the browser's HTML parser reads as OPEN
    # tags -- the <a> ends up wrapping the rest of the chapter -- while
    # crengine keeps them empty. Each row pairs crengine's XPointers for a
    # word with the CFI epub.js itself produced for it in Chromium.
    epub = FIXTURES / "alice-pg11.epub"
    words = _json("alice-pg11.browser.json")["words"]
    failures = []
    for word in words:
        cfi = kx.xpointers_to_cfi_range(epub, word["s"], word["e"])
        back = kx.cfi_range_to_xpointers(epub, word["cfi"])
        point = kx.xpointer_to_cfi(epub, word["s"])
        if (cfi != word["cfi"] or back != (word["s"], word["e"])
                or point is None or kx.cfi_to_xpointer(epub, point) != word["s"]):
            failures.append((word, cfi, back, point))
    assert not failures, failures[:3]
    assert len(words) > 500
    # The tree really differs: the browser's paths run through the <a>.
    assert any("[chap" in w["cfi"] or "[pgepubid" in w["cfi"] for w in words)


def _html_book(tmp_path, bodies):
    """An EPUB whose spine is one .html chapter per body."""
    manifest = "".join(f'<item id="c{i}" href="c{i}.html" media-type="application/xhtml+xml"/>'
                       for i in range(len(bodies)))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(bodies)))
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">t</dc:identifier><dc:title>t</dc:title><dc:language>en</dc:language></metadata>'
                   f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>")
        for i, body in enumerate(bodies):
            z.writestr(f"OEBPS/c{i}.html", '<?xml version="1.0" encoding="utf-8"?>\n'
                       '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
                       f"<body>{body}</body></html>")
    return path


def test_chapters_the_browser_reorders_or_swallows_are_refused(tmp_path):
    epub = _html_book(tmp_path, [
        # 1: <div/> swallows what follows; the text is unchanged: modelled.
        '<p>before</p><div/><p>after</p>',
        # 2: <script/> opens a script: the rest of the body becomes its source.
        '<p>before</p><script src="x.js"/><p>after</p>',
        # 3: text inside a table but outside a cell is moved before the table.
        '<div/><table><tr><td>cell</td></tr>loose<tr><td>two</td></tr></table><p>after</p>',
        # 4: the same with nothing self-closed: the <p> moves out before the
        # table, so the <p> after it is one element further on in the browser.
        '<table><tbody><tr><td>cell</td></tr><p>moved</p></tbody></table><p>after</p>',
        # 5: moved text equal to a cell's: the order of strings is unchanged,
        # only which node is which.
        '<div/><table><tbody><tr><td>same</td></tr>same</tbody></table>',
    ])
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[1]/body/p[2]/text().0") == (
        "epubcfi(/6/2!/4/4/2/1:0)")  # <p>after</p> is the swallowing div's child
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[2]/body/p[1]/text().0") is None
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[3]/body/p/text().0") is None
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[4]/body/p/text().0") is None
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[5]/body/table/tbody/tr/td/text().0") is None


def test_without_html5lib_a_restructured_chapter_is_refused_as_before(tmp_path, monkeypatch):
    epub = tmp_path / "copy.epub"  # nothing of it cached yet
    shutil.copy(FIXTURES / "alice-pg11.epub", epub)
    word = _json("alice-pg11.browser.json")["words"][100]
    monkeypatch.setitem(__import__("sys").modules, "html5lib", None)
    assert kx.xpointers_to_cfi_range(epub, word["s"], word["e"]) is None
    assert kx.cfi_range_to_xpointers(epub, word["cfi"]) is None


def test_a_restructured_chapter_above_the_size_cap_is_refused_not_parsed_twice(
        tmp_path, monkeypatch):
    epub = _html_book(tmp_path, ['<p>before</p><div/><p>after</p>'])
    monkeypatch.setattr(kx, "MAX_HTML5_CHAPTER_BYTES", 64)  # the chapter is ~150 bytes
    assert kx.xpointer_to_cfi(epub, "/body/DocFragment[1]/body/p[2]/text().0") is None
    assert kx.cfi_to_xpointer(epub, "epubcfi(/6/2!/4/4/2/1:0)") is None


def test_an_unreadable_chapter_is_parsed_once_not_on_every_request(tmp_path, monkeypatch):
    epub = tmp_path / "copy.epub"  # a fresh path: nothing of it is cached yet
    shutil.copy(FIXTURES / "head-voids.epub", epub)
    loads = []
    real = kx._load_chapter
    monkeypatch.setattr(kx, "_load_chapter", lambda *a: loads.append(a) or real(*a))
    for _ in range(3):
        assert kx.cfi_to_xpointer(epub, "epubcfi(/6/8!/4/2/1:0)") is None
    assert len(loads) == 1


def test_rig_highlights_convert_both_ways_only_when_they_frame_their_text():
    epub = FIXTURES / "metamorphosis-221.epub"
    shudder, alth = _json("rig-web-rows.json")
    engine = {row["text"]: (row["s"], row["e"]) for row in _json("rig-engine-check.json")}

    # KOReader -> web: the Kindle highlight becomes a CFI on the same word.
    cfi = kx.derive_cfi_range(epub, shudder["start_xpointer"], shudder["end_xpointer"],
                              shudder["highlighted_text"])
    assert cfi == SHUDDER_CFI
    assert _EpubJs(epub).range_text(cfi) == "shudder"

    # web -> KOReader: the web highlight becomes the XPointers crengine reads
    # back as "Alth" (rig-engine-check.json).
    assert kx.derive_xpointers(epub, alth["cfi_range"], alth["highlighted_text"]) == engine["Alth"]

    # A position that no longer frames the stored words is not converted.
    assert kx.derive_xpointers(epub, alth["cfi_range"], "Also") is None
    assert kx.derive_cfi_range(epub, shudder["start_xpointer"], shudder["end_xpointer"],
                               "shutter") is None


# ---------------------------------------------------------------------------
# Where the conversions are served
# ---------------------------------------------------------------------------


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Book 221 on disk as the EPUB the Kindle holds, plus a user and a DB."""
    book_dir = tmp_path / "library" / "Franz Kafka" / "Metamorphosis (221)"
    book_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "metamorphosis-221.epub", book_dir / "Metamorphosis - Franz Kafka.epub")
    monkeypatch.setattr(config, "get_book_path", lambda: str(tmp_path / "library"))
    book = SimpleNamespace(
        id=221, uuid="bk-221", title="Metamorphosis", path="Franz Kafka/Metamorphosis (221)",
        data=[SimpleNamespace(format="EPUB", name="Metamorphosis - Franz Kafka")])

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    user = ub.User(name="kr", email="kr@e.com", role=0, password="x")
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda *a, **k: session.commit())
    yield book, user, session
    session.close()
    engine.dispose()


def _row(user, **fields):
    return ub.Annotation(user_id=user.id, book_id=221, **fields)


def test_koreader_pull_gives_a_web_highlight_the_xpointers_of_its_words(library, monkeypatch):
    import importlib
    kosync_routes = importlib.import_module("cps.progress_syncing.protocols.kosync")
    annotation_routes = importlib.import_module(
        "cps.progress_syncing.protocols.koreader_annotations")
    book, user, session = library
    alth = _json("rig-web-rows.json")[1]
    session.add_all([
        _row(user, annotation_id="web-alth", source="webreader", position_type="cfi",
             cfi_range=alth["cfi_range"], highlighted_text="Alth"),
        # Same position, but the stored words are not the ones there now.
        _row(user, annotation_id="web-stale", source="webreader", position_type="cfi",
             cfi_range=alth["cfi_range"], highlighted_text="Also"),
    ])
    session.commit()
    monkeypatch.setattr(kosync_routes, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(kosync_routes, "authenticate_user", lambda: user)
    monkeypatch.setattr(annotation_routes, "_require_kosync_enabled", lambda: None)
    monkeypatch.setattr(annotation_routes, "authenticate_user", lambda: user)
    monkeypatch.setattr(annotation_routes, "get_book_by_checksum",
                        lambda document: (221, "EPUB", book.title, book.path, "koreader"))
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(kosync_routes.kosync)

    response = app.test_client().get("/kosync/syncs/annotations/digest-221")

    assert response.status_code == 200
    pulled = {a["annotation_id"]: a for a in response.get_json()["annotations"]}
    engine = {row["text"]: (row["s"], row["e"]) for row in _json("rig-engine-check.json")}
    web = pulled["web-alth"]
    assert (web["start_xpointer"], web["end_xpointer"]) == engine["Alth"]
    assert web["position_type"] == "cfi" and web["source"] == "webreader"
    assert pulled["web-stale"]["start_xpointer"] is None
    stored = session.query(ub.Annotation).filter_by(annotation_id="web-alth").one()
    assert stored.start_xpointer is None and stored.cfi_range == alth["cfi_range"]


def test_web_reader_gets_and_keeps_a_cfi_for_a_kindle_highlight(library, monkeypatch):
    from cps import annotations as ann
    book, user, session = library
    shudder = _json("rig-web-rows.json")[0]
    for annotation_id, text in (("kindle-shudder", "shudder"), ("kindle-stale", "shutter")):
        session.add(_row(user, annotation_id=annotation_id, source="koreader",
                         position_type="koreader_xpointer", highlighted_text=text,
                         start_xpointer=shudder["start_xpointer"],
                         end_xpointer=shudder["end_xpointer"]))
    session.commit()
    monkeypatch.setattr(ann, "current_user", SimpleNamespace(id=user.id))
    monkeypatch.setattr(ann, "_resolve_book_or_404", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(ann.annotations_bp)

    with app.test_request_context("/annotations/221/data.json"):
        served = ann.annotations_data.__wrapped__(221).get_json()["annotations"]

    by_id = {a["annotation_id"]: a for a in served}
    assert by_id["kindle-shudder"]["cfi_range"] == SHUDDER_CFI
    assert by_id["kindle-shudder"]["anchor_status"] == "ok"
    assert by_id["kindle-stale"]["cfi_range"] is None
    assert by_id["kindle-stale"]["anchor_status"] == "unresolved"
    stored = {r.annotation_id: r for r in session.query(ub.Annotation)}
    assert stored["kindle-shudder"].cfi_range == SHUDDER_CFI
    assert stored["kindle-shudder"].position_type == "koreader_xpointer"
    assert stored["kindle-stale"].cfi_range is None
