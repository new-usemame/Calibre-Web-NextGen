# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-ingest must normalize a KEPUB before it enters the library (#1715).

Three ingest routes all reach ``add_book_to_library`` with a KEPUB that nothing
has normalized: kepubify's own output, a file already in the target format
(``is_target_format`` at ingest_processor.py), and a format the user told CWA not
to convert. The repair task cannot cover for it -- it is one-shot per
``REPAIR_VERSION`` -- so such a book keeps its fragment-anchored TOC targets
permanently, and a Kobo files every highlight in those chapters under an id no
``ContentType=9`` row carries.

The normalization is the first thing ``add_book_to_library`` does, so these tests
drive the real method with a stub ``self`` and assert on the file. The calibredb
work further down is not stubbed; it is expected to fail on the stub and is
irrelevant to what is being pinned here.
"""
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402

CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles></container>')
OPF = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="i">'
       '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>t</dc:title></metadata>'
       '<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
       '<item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
       '<spine toc="ncx"><itemref idref="c1"/></spine></package>')
NCX = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
       '<navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>'
       '<content src="chapter.xhtml#top"/></navPoint></navMap></ncx>')
CHAPTER = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
           '<body><div id="top">x</div></body></html>')

SPLIT_NCX = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
             '<navPoint id="n1"><navLabel><text>One</text></navLabel>'
             '<content src="chapter.xhtml#one"/></navPoint>'
             '<navPoint id="n2"><navLabel><text>Two</text></navLabel>'
             '<content src="chapter.xhtml#two"/></navPoint></navMap></ncx>')
SPLIT_CHAPTER = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                 '<div id="book-columns"><div id="book-inner">'
                 '<section id="one"><span class="koboSpan" id="kobo.1.1">one</span></section>'
                 '<section id="two"><span class="koboSpan" id="kobo.2.1">two</span></section>'
                 '</div></div></body></html>')


def _make_kepub(path, *, splittable=False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/toc.ncx", SPLIT_NCX if splittable else NCX)
        archive.writestr("OEBPS/chapter.xhtml", SPLIT_CHAPTER if splittable else CHAPTER)
    return str(path)


def _ncx_sources(path):
    from lxml import etree
    with zipfile.ZipFile(path) as archive:
        doc = etree.fromstring(archive.read("OEBPS/toc.ncx"))
    return [e.get("src") for e in doc.iter("{*}content")]


def _run_add(book_path):
    """Call the real method far enough to cover the normalization step."""
    stub = SimpleNamespace(
        target_format="kepub",
        is_kindle_epub_fixer=False,
        tmp_conversion_dir=os.path.dirname(book_path),
        cwa_settings={},
        metadata_db="/nonexistent/metadata.db",
    )
    try:
        ingest_processor.NewBookProcessor.add_book_to_library(stub, book_path)
    except Exception:
        # Everything past normalization needs a real calibredb; not under test.
        pass


def test_ingested_kepub_has_its_redundant_fragment_stripped(tmp_path):
    book = _make_kepub(tmp_path / "incoming.kepub")
    assert _ncx_sources(book) == ["chapter.xhtml#top"]

    _run_add(book)

    assert _ncx_sources(book) == ["chapter.xhtml"], (
        "an ingested KEPUB must be normalized, or a Kobo files highlights in "
        "this chapter under an id no spine row carries")


def test_ingested_kepub_is_born_with_split_chapter_documents(tmp_path):
    book = _make_kepub(tmp_path / "incoming.kepub", splittable=True)

    _run_add(book)

    assert _ncx_sources(book) == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]


def test_ingest_continues_when_opted_in_split_returns_failure(tmp_path, monkeypatch):
    book = _make_kepub(tmp_path / "incoming.kepub", splittable=True)
    before = Path(book).read_bytes()
    monkeypatch.setattr(
        ingest_processor,
        "_normalize_kepub_package",
        lambda _path, **_kwargs: None,
    )

    _run_add(book)

    assert Path(book).read_bytes() == before


def test_ingested_epub_is_left_alone(tmp_path):
    """Only KEPUBs go through the KEPUB normalizer."""
    book = _make_kepub(tmp_path / "incoming.epub")

    _run_add(book)

    assert _ncx_sources(book) == ["chapter.xhtml#top"], (
        "a non-KEPUB ingest must not be rewritten by the KEPUB normalizer")


def test_normalizer_is_reachable_from_the_ingest_process():
    """The lazy loader must actually bind it; a silent None would no-op forever."""
    ingest_processor._load_optional_cps_modules()
    assert ingest_processor._normalize_kepub_package is not None, (
        "ingest_processor could not import normalize_kepub_package, so every "
        "ingested KEPUB would silently skip normalization")
