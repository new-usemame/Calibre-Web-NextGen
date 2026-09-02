# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #1690: auto-ingest never reads embedded
ComicInfo.xml/CBI metadata, even though the manual web-upload path does.

Root cause: ``cps/uploader.py``'s ``process()`` (backed by
``cps/comic.py``'s ``get_comic_info()`` / ``comicapi``) is the reader that
already extracts title/series/issue/authors from a comic's embedded
``ComicInfo.xml`` for the manual upload form. ``scripts/ingest_processor.py``'s
``NewBookProcessor.add_book_to_library`` never called it: for the ``text=True``
branch it ran a bare ``calibredb add <file>`` with no metadata flags, so a
tagged comic lost its tags on auto-ingest, landed as
title=<filename>/author=Unknown, and the subsequent external metadata-provider
lookups usually couldn't confidently match a bare filename either.

The sibling audiobook branch of the same function already got this right —
it calls ``audiobook.get_audio_file_info()`` and passes the result to
``calibredb add`` as explicit ``--title``/``--authors``/``--series``/etc.
flags. The fix (``NewBookProcessor._comic_calibredb_metadata_args``) gives
the comic path the same treatment, reusing ``cps.uploader.process(...,
strict=True)`` so an ingest-time comic gets no metadata guesses beyond what
the file itself actually carries — a comic with no embedded tags falls
through to the pre-existing filename/web-lookup behavior, unchanged.
"""

import io
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
import ingest_processor  # noqa: E402

# Minimal valid 10x10 JPEG — comicapi needs at least one real image entry to
# recognise the archive as a comic before it will look for ComicInfo.xml.
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb00430008060607060508"
    "0707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720"
    "222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909"
    "090c0b0c180d0d1832211c213232323232323232323232323232323232323232"
    "323232323232323232323232323232323232323232323232323232323232ffc0"
    "001108000a000a03012200021101031101ffc4001f0000010501010101010100"
    "000000000000000102030405060708090a0bffc400b510000201030302040305"
    "0504040000017d01020300041105122131410613516107227114328191a10823"
    "42b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
    "434445464748494a535455565758595a636465666768696a737475767778797a"
    "838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7"
    "b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
    "f2f3f4f5f6f7f8f9faffc4001f01000301010101010101010100000000000001"
    "02030405060708090a0bffc400b5110002010204040304070504040001027700"
    "0102031104052131061241510761711322328108144291a1b1c109233352f015"
    "6272d10a162434e125f11718191a262728292a35363738393a43444546474849"
    "4a535455565758595a636465666768696a737475767778797a82838485868788"
    "898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4"
    "c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9"
    "faffda000c03010002110311003f00f7fa28a2803fffd9"
)

_COMIC_INFO_TEMPLATE = """<?xml version="1.0"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Series>{series}</Series>
  <Number>{number}</Number>
  <Title>{title}</Title>
  <Publisher>{publisher}</Publisher>
  <LanguageISO>en</LanguageISO>
  <Writer>{writer}</Writer>
</ComicInfo>
"""


def _build_cbz(path: Path, comic_info: str | None) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("page001.jpg", _TINY_JPEG)
        if comic_info is not None:
            zf.writestr("ComicInfo.xml", comic_info)


@pytest.fixture
def tagged_cbz(tmp_path) -> Path:
    """A .cbz with a valid, root-level ComicInfo.xml — the case the manual
    upload path has always handled correctly."""
    path = tmp_path / "tagged.cbz"
    xml = _COMIC_INFO_TEMPLATE.format(
        series="Test Fixture Series",
        number="7",
        title="The Test Issue",
        publisher="Fixture Comics",
        writer="Jane Fixture",
    )
    _build_cbz(path, xml)
    return path


@pytest.fixture
def untagged_cbz(tmp_path) -> Path:
    """A .cbz with no ComicInfo.xml at all."""
    path = tmp_path / "untagged.cbz"
    _build_cbz(path, None)
    return path


class _FakeProcessor:
    """Stand-in for NewBookProcessor exposing only what the method under
    test needs — avoids constructing the full ingest object (DB paths,
    settings, etc.) that __init__ requires."""

    _COMIC_INGEST_EXTENSIONS = ingest_processor.NewBookProcessor._COMIC_INGEST_EXTENSIONS
    _comic_calibredb_metadata_args = ingest_processor.NewBookProcessor._comic_calibredb_metadata_args


@pytest.fixture(autouse=True)
def _load_cps_modules():
    """The lazily-loaded cps modules (_uploader, _CPS_AVAILABLE, ...) are
    module-level globals populated on first use elsewhere in the real
    service; a bare test process needs the same load call."""
    ingest_processor._load_optional_cps_modules()
    assert ingest_processor._CPS_AVAILABLE, (
        "cps.uploader must load in the test environment for these tests "
        "to exercise the real reader, not a mock of it"
    )


def test_tagged_comic_yields_calibredb_metadata_flags(tagged_cbz):
    """The reporter's exact case: a properly-tagged .cbz must produce
    calibredb flags carrying its real title/authors/series/series-index/
    language — RED on main, which always returns []."""
    args = _FakeProcessor()._comic_calibredb_metadata_args(tagged_cbz)

    assert args == [
        "--title", "The Test Issue",
        "--authors", "Jane Fixture",
        "--series", "Test Fixture Series",
        "--series-index", "7",
        "--languages", "eng",
    ]


def test_untagged_comic_yields_no_flags(untagged_cbz):
    """No ComicInfo.xml -> no flags at all, so add_book_to_library falls
    through to the pre-existing filename/web-lookup behavior unchanged."""
    assert _FakeProcessor()._comic_calibredb_metadata_args(untagged_cbz) == []


def test_non_comic_extension_yields_no_flags(tmp_path):
    """Scope guard: this must never touch non-comic formats (epub already
    gets reasonable metadata from calibredb's own OPF parsing)."""
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"not a real epub, just needs to exist")
    assert _FakeProcessor()._comic_calibredb_metadata_args(epub_path) == []


def test_non_numeric_issue_number_drops_series_index_not_series(tmp_path):
    """A real-world non-numeric issue number ('Annual 1') must not blow up
    calibredb's --series-index (which requires a number) — series itself
    is still applied, just without an index."""
    path = tmp_path / "annual.cbz"
    xml = _COMIC_INFO_TEMPLATE.format(
        series="Some Series", number="Annual 1", title="Annual",
        publisher="", writer="",
    )
    _build_cbz(path, xml)

    args = _FakeProcessor()._comic_calibredb_metadata_args(path)

    assert "--series" in args
    assert args[args.index("--series") + 1] == "Some Series"
    assert "--series-index" not in args


def test_missing_uploader_module_degrades_to_no_flags(tagged_cbz, monkeypatch):
    """If the optional cps import ever fails to load (e.g. a stripped-down
    deployment), the ingest path must degrade to today's behavior instead
    of raising and failing the whole import."""
    monkeypatch.setattr(ingest_processor, "_uploader", None)
    assert _FakeProcessor()._comic_calibredb_metadata_args(tagged_cbz) == []


def test_comic_metadata_args_wired_into_calibre_transaction():
    """Source-pin: add_book_to_library's text=True branch must actually
    call _comic_calibredb_metadata_args and fold the result into the
    Calibre database transaction, or this whole fix is dead code."""
    import inspect

    src = inspect.getsource(ingest_processor.NewBookProcessor.add_book_to_library)
    assert "_comic_calibredb_metadata_args(staged_path)" in src, (
        "add_book_to_library must call _comic_calibredb_metadata_args "
        "for the text-format import path"
    )
    # The result must reach the actual transaction helper, not just be
    # computed and discarded.
    conversion = src.index("metadata_override = self._metadata_args_to_override(comic_meta_args)")
    transaction = src.index(
        "transaction_result = self._run_calibre_transaction(", conversion
    )
    assert conversion < transaction
    assert "metadata_override" in src[transaction:transaction + 320], (
        "the computed comic metadata flags must be passed into the "
        "Calibre database transaction, not just computed and dropped"
    )
