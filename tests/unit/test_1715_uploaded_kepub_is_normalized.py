# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An uploaded KEPUB must be normalized on the way in (#1715).

A KEPUB uploaded directly never passes through ``convert.py``, and the repair
task is one-shot per ``REPAIR_VERSION``, so without normalizing here the package
keeps its fragment-anchored TOC targets forever. A Kobo then derives the chapter
id from the TOC entry verbatim -- fragment included -- and files every highlight
made in that chapter under an id no ``ContentType=9`` row carries, so the marks
are stored and drawn nowhere.

These tests drive the real ``upload_book_formats`` with fakes for its module-level
collaborators, so they exercise the actual code path rather than pinning source.
"""
import os
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

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
    return path


def _ncx_sources(path):
    from lxml import etree
    with zipfile.ZipFile(path) as archive:
        doc = etree.fromstring(archive.read("OEBPS/toc.ncx"))
    return [e.get("src") for e in doc.iter("{*}content")]


class _Upload:
    """Stands in for a werkzeug FileStorage."""
    def __init__(self, source, filename):
        self._source = source
        self.filename = filename

    def save(self, destination):
        with open(self._source, "rb") as src, open(destination, "wb") as dst:
            dst.write(src.read())


def _drive_upload(monkeypatch, tmp_path, filename, *, splittable=False,
                  annotations=0, pre_existing=False):
    """Run the real upload_book_formats with fake collaborators."""
    from cps import editbooks

    library = tmp_path / "library"
    (library / "Author" / "Book (1)").mkdir(parents=True)
    source = _make_kepub(str(tmp_path / "incoming.kepub"), splittable=splittable)

    recorded = {}

    class _Config:
        config_check_extensions = False
        config_upload_formats = "epub,kepub"
        config_rarfile_location = ""
        def get_book_path(self):
            return str(library)

    class _User:
        name = "tester"
        def role_upload(self):
            return True

    class _Session:
        def add(self, row):
            recorded["row"] = row
        def commit(self):
            pass
        def rollback(self):
            pass

    class _CalibreDb:
        session = _Session()
        def get_book_format(self, book_id, fmt):
            return None

    class _Data:
        def __init__(self, book_id, fmt, size, name):
            self.book_id, self.format, self.uncompressed_size, self.name = book_id, fmt, size, name

    class _Book:
        id = 1
        title = "Book"
        path = "Author/Book (1)"

    monkeypatch.setattr(editbooks, "config", _Config(), raising=False)
    monkeypatch.setattr(editbooks, "current_user", _User(), raising=False)
    monkeypatch.setattr(editbooks, "calibre_db", _CalibreDb(), raising=False)
    monkeypatch.setattr(editbooks.db, "Data", _Data, raising=False)
    monkeypatch.setattr(editbooks, "flash", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda text, **k: text, raising=False)
    monkeypatch.setattr(editbooks, "validate_mime_type", lambda *a, **k: True, raising=False)
    # Everything past the write is bookkeeping we are not exercising here.
    monkeypatch.setattr(editbooks, "url_for", lambda *a, **k: "/b/1", raising=False)
    monkeypatch.setattr(editbooks, "escape", lambda v: v, raising=False)
    monkeypatch.setattr(editbooks, "N_", lambda text, **k: text, raising=False)
    monkeypatch.setattr(editbooks.WorkerThread, "add", staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(editbooks, "TaskUpload", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks.uploader, "process", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks, "merge_metadata", lambda *a, **k: None, raising=False)

    # `_book_has_annotations` decides whether this upload may be split. It fails
    # CLOSED, so without a working annotation store every upload would silently
    # take the no-split path and the split assertions below would pass for the
    # wrong reason.
    class _AnnotationQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return object() if annotations else None

    class _UbSession:
        def query(self, _model):
            return _AnnotationQuery()

    monkeypatch.setattr(editbooks.ub, "session", _UbSession(), raising=False)

    if pre_existing:
        # The replacement case needs something already stored, or the
        # "was it already split" question is never even asked.
        stored_dir = library / "Author" / "Book (1)"
        stored_dir.mkdir(parents=True, exist_ok=True)
        (stored_dir / ("Book (1)." + filename.rsplit('.', 1)[-1])).write_bytes(b"old")

    editbooks.upload_book_formats([_Upload(source, filename)], _Book(), 1)

    stored = library / "Author" / "Book (1)" / ("Book (1)." + filename.rsplit(".", 1)[-1])
    return stored, recorded


def test_uploaded_kepub_has_its_redundant_toc_fragment_stripped(monkeypatch, tmp_path):
    stored, _recorded = _drive_upload(monkeypatch, tmp_path, "incoming.kepub")

    assert stored.exists(), "the upload should still be stored"
    assert _ncx_sources(str(stored)) == ["chapter.xhtml"], (
        "the redundant #top fragment must be gone, or a Kobo files highlights in "
        "this chapter under an id no spine row carries")


def test_uploaded_kepub_is_born_with_split_chapter_documents(monkeypatch, tmp_path):
    stored, _recorded = _drive_upload(
        monkeypatch, tmp_path, "incoming.kepub", splittable=True)

    assert _ncx_sources(str(stored)) == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]


def test_upload_continues_when_opted_in_split_returns_failure(monkeypatch, tmp_path):
    from cps import editbooks

    monkeypatch.setattr(
        editbooks,
        "normalize_kepub_package",
        lambda _path, **_kwargs: None,
    )

    stored, _recorded = _drive_upload(
        monkeypatch, tmp_path, "incoming.kepub", splittable=True)

    assert stored.exists()
    assert _ncx_sources(str(stored)) == [
        "chapter.xhtml#one",
        "chapter.xhtml#two",
    ]


def test_recorded_size_matches_the_normalized_file(monkeypatch, tmp_path):
    """Size is measured AFTER normalization -- it rewrites the archive."""
    stored, recorded = _drive_upload(monkeypatch, tmp_path, "incoming.kepub")

    assert "row" in recorded, "a Calibre data row should have been added"
    assert recorded["row"].uncompressed_size == os.path.getsize(str(stored)), (
        "uncompressed_size was measured before normalization rewrote the file")


def test_non_kepub_upload_is_untouched(monkeypatch, tmp_path):
    """An .epub must not be run through the KEPUB normalizer."""
    stored, _recorded = _drive_upload(monkeypatch, tmp_path, "incoming.epub")

    assert stored.exists()
    assert _ncx_sources(str(stored)) == ["chapter.xhtml#top"], (
        "a non-KEPUB upload must be stored byte-for-byte as supplied")


def test_an_uploaded_kepub_is_not_split_when_the_book_already_has_annotations(
        monkeypatch, tmp_path):
    """This route is reached from EDIT BOOK, so the book can be years old.

    Splitting renames spine documents. A Kobo matches its stored Bookmark rows
    by ContentID, so it keeps the rows, rewrites each ContentID to the bare old
    filename, renders nothing, and reports "no annotations" for a book the
    reader had highlighted. Normalization alone never renames a document, so it
    still runs -- only the split is withheld.
    """
    stored, _recorded = _drive_upload(
        monkeypatch, tmp_path, "incoming.kepub", splittable=True, annotations=1)

    assert stored.exists(), "the upload must still be stored"
    assert _ncx_sources(str(stored)) == ["chapter.xhtml#one", "chapter.xhtml#two"], (
        "an annotated book's KEPUB was split; every existing highlight in it "
        "would stop rendering on the device")


def test_the_annotation_check_fails_closed(monkeypatch, tmp_path):
    """If the annotation store cannot be read, do not split.

    The unsafe direction is splitting a book that turns out to have highlights,
    so an unreadable store must produce the pre-split behaviour, not the
    optimistic one.
    """
    from cps import editbooks

    class _ExplodingSession:
        def query(self, _model):
            raise RuntimeError("annotation store unavailable")

    monkeypatch.setattr(editbooks.ub, "session", _ExplodingSession(), raising=False)
    assert editbooks._book_has_annotations(1) is True


def test_the_annotation_check_looks_past_the_uploading_user(monkeypatch):
    """An admin replacing a format must not silently break someone else's highlights.

    Executed rather than pinned to source text: capture the criteria the query is
    actually filtered by and read them, so the test fails if a `user_id` filter
    is added no matter how it is spelled.
    """
    from cps import editbooks, ub

    captured = []

    class _Query:
        def filter(self, *criteria):
            captured.extend(criteria)
            return self

        def first(self):
            return None

    class _Session:
        def query(self, model):
            assert model is ub.Annotation, model
            return _Query()

    monkeypatch.setattr(editbooks.ub, "session", _Session(), raising=False)
    assert editbooks._book_has_annotations(7) is False

    rendered = " ".join(str(criterion) for criterion in captured)
    assert captured, "the check ran no filter at all; it would report every book annotated"
    assert "annotation.book_id" in rendered, rendered
    assert "user_id" not in rendered, (
        "the annotation check is filtered by user; an admin replacing a format "
        "would then split a book another account has highlighted: " + rendered
    )


def test_an_annotated_book_that_was_ALREADY_split_is_split_again(monkeypatch, tmp_path):
    """F-bbd10e: withholding the split is the wrong answer for a book born split.

    Piece naming is deterministic, so re-splitting a replacement built from the
    same source reproduces the exact member names its annotations are anchored
    to. Withholding the split instead DELETES those files. The earlier guard —
    "never split an annotated book" — got this case backwards.
    """
    from cps import editbooks

    # Pretend the package already on disk carries our split pieces in its spine.
    monkeypatch.setattr(editbooks, "_package_was_split_by_us", lambda _p: True,
                        raising=False)

    stored, _recorded = _drive_upload(
        monkeypatch, tmp_path, "incoming.kepub", splittable=True, annotations=1,
        pre_existing=True)

    assert _ncx_sources(str(stored)) == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ], "an already-split annotated book was left unsplit, stranding its anchors"


def test_an_annotated_book_that_was_NOT_split_is_still_left_alone(monkeypatch, tmp_path):
    """The original case, which must not regress while fixing the inversion."""
    from cps import editbooks

    monkeypatch.setattr(editbooks, "_package_was_split_by_us", lambda _p: False,
                        raising=False)

    stored, _recorded = _drive_upload(
        monkeypatch, tmp_path, "incoming.kepub", splittable=True, annotations=1,
        pre_existing=True)

    assert _ncx_sources(str(stored)) == ["chapter.xhtml#one", "chapter.xhtml#two"], (
        "a book that was never split had its spine renamed underneath its "
        "existing highlights"
    )


def test_the_decision_rule_itself(monkeypatch):
    """Both inputs, all four combinations, without driving an upload."""
    from cps import editbooks

    monkeypatch.setattr(editbooks, "_book_has_annotations", lambda _b: False,
                        raising=False)
    assert editbooks._may_split_replacement(1, False) is True
    assert editbooks._may_split_replacement(1, True) is True

    monkeypatch.setattr(editbooks, "_book_has_annotations", lambda _b: True,
                        raising=False)
    assert editbooks._may_split_replacement(1, False) is False
    assert editbooks._may_split_replacement(1, True) is True


def test_upload_comment_describes_the_annotated_replacement_exception():
    """The call-site prose must not contradict the decision directly below it.

    The stale wording said splitting happened only without annotations, hiding
    the already-split exception this suite exists to protect. Pin both the
    removal and the corrected rule because a wrong comment disables review of
    the real branch even while behavioural tests remain green.
    """
    from cps import editbooks

    source = Path(editbooks.__file__).read_text(encoding="utf-8")
    stale = "Splitting is opted into only when the book carries no"
    corrected = (
        "Splitting is withheld for an annotated book unless the stored\n"
        "                # KEPUB was already split by us"
    )

    assert stale not in source
    assert corrected in source
