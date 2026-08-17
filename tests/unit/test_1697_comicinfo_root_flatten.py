# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #1697: comic auto-ingest can't read
ComicInfo.xml when it's nested instead of sitting at the archive root.

Root cause: comicapi's ``ComicArchive.has_cix()`` does
``self.ci_xml_filename in self.archiver.get_filename_list()`` - an exact
match against the bare string ``"ComicInfo.xml"``. A zip's file list keeps
full internal paths, so this only ever matches a true root-level entry.
Some real-world scan-group .cbz releases package ``ComicInfo.xml`` one
folder down alongside the pages (confirmed against a live MyAnonamouse
release, "Amazing Fantasy 015 - Facsimile Edition (1962)") - for those,
``has_cix()`` silently returns False and the book gets no metadata from a
file that genuinely carries some.

This matches the documented ComicInfo.xml spec (root-only) and the same
behavior in ComicTagger (comicapi's own parent project) and Komga - the
file is technically out of spec, not comicapi being wrong. So the fix
isn't to make detection more lenient (that would make CWA quietly diverge
from every other reader); it's to fix the file. ``cps/comic.py``'s new
``flatten_comicinfo_to_root()`` rewrites a copy of a .cbz with a misplaced
ComicInfo.xml moved to the root - the same manual fix the ComicTagger
community already recommends for this exact situation, just automated.
Zip-based archives only (.cbz); .cbr/.cb7/.cbt aren't zip containers and
there's no free Python RAR writer, so those are left untouched.

Gated behind the new ``comic_flatten_comicinfo`` ingest setting (default
off) - it rewrites a file, however narrowly scoped, so it's opt-in.
``scripts/ingest_processor.py`` only ever calls it on the *staged copy*
made by ``add_book_to_library`` (after ``shutil.copy2``), never the
original - the source file (which may still be hardlinked elsewhere, e.g.
a seeding torrent or another app's own library copy) is never touched.
"""

import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import cps  # noqa: E402,F401  (registers application MIME types)
from cps import comic  # noqa: E402
import ingest_processor  # noqa: E402

_COMIC_INFO = """<?xml version="1.0"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Series>Test Fixture Series</Series>
  <Number>7</Number>
  <Publisher>Fixture Comics</Publisher>
</ComicInfo>
"""


def _build_cbz(path, entries):
    """entries: dict of {archive-internal-path: bytes-or-str}."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_nested_comicinfo_is_moved_to_root(tmp_path):
    """The reporter's exact case: ComicInfo.xml one folder down, alongside
    the pages, must end up at the true archive root - RED on main, where
    this function doesn't exist."""
    path = tmp_path / "nested.cbz"
    _build_cbz(path, {
        "Some Series (2020)/page001.jpg": b"not a real jpeg, just needs to exist",
        "Some Series (2020)/ComicInfo.xml": _COMIC_INFO,
    })

    changed = comic.flatten_comicinfo_to_root(str(path))

    assert changed is True
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "ComicInfo.xml" in names
        assert "Some Series (2020)/ComicInfo.xml" not in names
        assert "Some Series (2020)/page001.jpg" in names  # untouched
        assert zf.read("ComicInfo.xml").decode() == _COMIC_INFO


def test_root_comicinfo_is_left_alone(tmp_path):
    """Already correct -> no-op, and the archive isn't touched at all
    (checked via mtime, not just the return value)."""
    path = tmp_path / "already_root.cbz"
    _build_cbz(path, {
        "page001.jpg": b"page",
        "ComicInfo.xml": _COMIC_INFO,
    })
    mtime_before = path.stat().st_mtime_ns

    changed = comic.flatten_comicinfo_to_root(str(path))

    assert changed is False
    assert path.stat().st_mtime_ns == mtime_before


def test_no_comicinfo_at_all_is_a_noop(tmp_path):
    path = tmp_path / "untagged.cbz"
    _build_cbz(path, {"page001.jpg": b"page"})

    assert comic.flatten_comicinfo_to_root(str(path)) is False


def test_non_zip_archive_is_a_noop(tmp_path):
    """.cbr/.cb7/.cbt aren't zip containers; must degrade to a no-op
    rather than raising on a file zipfile can't open."""
    path = tmp_path / "not_a_zip.cbr"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"not really a rar either")

    assert comic.flatten_comicinfo_to_root(str(path)) is False


def test_flattened_archive_is_valid_and_complete(tmp_path):
    """The rewrite must not corrupt or drop any other entry - checked with
    zipfile's own integrity check, not just namelist membership."""
    path = tmp_path / "multi_page.cbz"
    entries = {f"Sub/page{i:03d}.jpg": f"page {i}".encode() for i in range(1, 6)}
    entries["Sub/ComicInfo.xml"] = _COMIC_INFO
    _build_cbz(path, entries)

    comic.flatten_comicinfo_to_root(str(path))

    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        assert len(zf.namelist()) == len(entries)
        for i in range(1, 6):
            assert zf.read(f"Sub/page{i:03d}.jpg") == f"page {i}".encode()


def test_flattening_twice_is_idempotent(tmp_path):
    path = tmp_path / "nested.cbz"
    _build_cbz(path, {
        "Sub/page001.jpg": b"page",
        "Sub/ComicInfo.xml": _COMIC_INFO,
    })

    first = comic.flatten_comicinfo_to_root(str(path))
    second = comic.flatten_comicinfo_to_root(str(path))

    assert first is True
    assert second is False


def test_flattening_makes_comicapi_find_the_metadata(tmp_path):
    """End-to-end: comicapi's own has_metadata/read_metadata must actually
    pick up the fixed archive, not just our namelist check."""
    comicapi = pytest.importorskip("comicapi.comicarchive")
    path = tmp_path / "nested.cbz"
    _build_cbz(path, {
        "Sub/page001.jpg": b"page",
        "Sub/ComicInfo.xml": _COMIC_INFO,
    })

    archive = comicapi.ComicArchive(str(path))
    assert archive.has_metadata(comicapi.MetaDataStyle.CIX) is False  # RED without the fix

    comic.flatten_comicinfo_to_root(str(path))

    archive = comicapi.ComicArchive(str(path))  # fresh instance, no cached state
    assert archive.has_metadata(comicapi.MetaDataStyle.CIX) is True
    md = archive.read_metadata(comicapi.MetaDataStyle.CIX)
    assert md.series == "Test Fixture Series"
    assert md.publisher == "Fixture Comics"


def test_ingest_setting_defaults_off():
    """A file-rewriting feature, however narrowly scoped, should be opt-in
    - source-pin against the schema default drifting to on."""
    schema_path = REPO_ROOT / "scripts" / "cwa_schema.sql"
    schema = schema_path.read_text()
    assert "comic_flatten_comicinfo SMALLINT DEFAULT 0 NOT NULL" in schema


def test_ingest_only_flattens_staged_copy_not_the_original():
    """Source-pin: the flatten call must happen after staging (the
    shutil.copy2 that isolates the working copy from the original,
    possibly-hardlinked-elsewhere file), not before."""
    import inspect

    src = inspect.getsource(ingest_processor.NewBookProcessor.add_book_to_library)
    stage_idx = src.index("shutil.copy2(source_path, staged_path)")
    flatten_idx = src.index("comic.flatten_comicinfo_to_root(str(staged_path))")
    assert flatten_idx > stage_idx, (
        "flatten_comicinfo_to_root must run on the staged copy, after "
        "shutil.copy2 - never on the original source file"
    )
    # And it must be gated on the setting, not unconditional.
    window = src[max(0, flatten_idx - 200):flatten_idx]
    assert "is_comic_flatten_comicinfo" in window


def test_ingest_setting_read_from_cwa_settings():
    import inspect

    src = inspect.getsource(ingest_processor.NewBookProcessor.__init__)
    assert "self.is_comic_flatten_comicinfo = self.cwa_settings.get('comic_flatten_comicinfo'" in src


def test_missing_attribute_on_a_bare_processor_does_not_raise():
    """tests/unit/test_ingest_batch_dirty.py builds NewBookProcessor via
    object.__new__() and sets only a handful of attributes by hand,
    bypassing __init__ entirely - a pattern already in use elsewhere in
    this suite, not something to work around. The comic-flatten check must
    degrade to "disabled" via getattr, not raise AttributeError, when
    is_comic_flatten_comicinfo was never set. Caught live: an early
    version of this fix used a bare self.is_comic_flatten_comicinfo access
    and broke exactly that test file's full-suite run (passed every time
    in isolation, only failed as part of the complete suite - the
    difference was never test order, it was this file's own bug)."""
    import inspect

    src = inspect.getsource(ingest_processor.NewBookProcessor.add_book_to_library)
    assert 'getattr(self, "is_comic_flatten_comicinfo", False)' in src, (
        "must use getattr with a default, not a bare attribute access, so "
        "a processor built without __init__ (object.__new__, as an "
        "existing test in this suite does) doesn't raise"
    )
