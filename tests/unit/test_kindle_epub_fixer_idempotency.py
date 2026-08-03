# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #1304 — the Epub Fixer reported the same
"fixes" on every run and processed Calibre's trash folder.

Three defects, one root cause: the fixer decided what it had *done* from
labels and bookkeeping rather than from the bytes.

1. ``_decode_text_entry`` compared the *detected encoding name* to the target
   name. A pure-ASCII stylesheet detects as ``ascii``, target is ``utf-8``, so
   it logged "Converted page_styles.css from ascii to utf-8" — but ASCII bytes
   re-encode to byte-identical UTF-8, so nothing changed, and it said so again
   on the next run, forever.
2. ``process()`` backed up and rewrote every EPUB unconditionally, so a library
   sweep churned mtimes/checksums for every book and grew the backup folder
   without bound even when nothing needed fixing.
3. ``get_all_epubs_in_library`` walked hidden directories, so it processed
   ``.caltrash`` — books the user had deleted.
"""

import hashlib
import os
import warnings
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Idempotency Fixture</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">urn:uuid:0d4e1f2a-0000-4000-8000-00000000cwng</dc:identifier>
  </metadata>
  <manifest>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="page_styles.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="text"/>
  </spine>
</package>
"""

TEXT_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter One</title></head>
  <body><p>Plain ASCII text with nothing wrong with it.</p></body>
</html>
"""

# Pure ASCII, and (like every stylesheet) it carries no XML declaration and no
# meta charset — so encoding detection falls through to chardet/charset-normalizer
# and comes back with "ascii". This is the exact file the reporter named.
PAGE_STYLES_CSS = """.calibre { display: block; font-size: 1em; margin: 0 5pt; }
.calibre1 { display: block; margin: 1em 0; }
"""


def build_epub(path: Path, css: bytes = None) -> Path:
    """Write a minimal, already-correct EPUB. A fixer with nothing to do should
    report no fixes and leave it alone."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", CONTENT_OPF)
        zf.writestr("OEBPS/text.xhtml", TEXT_XHTML)
        zf.writestr("OEBPS/page_styles.css", PAGE_STYLES_CSS.encode("ascii") if css is None else css)
    return path


def epub_payload(path: Path) -> dict:
    """Entry name -> bytes. Compares what a reader would actually get, ignoring
    zip container noise like entry timestamps."""
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


class StubCwaDb:
    """Stands in for CWA_DB so the fixer needs no /config/cwa.db."""

    def __init__(self, settings=None):
        self.cwa_settings = {
            "auto_backup_epub_fixes": True,
            "kindle_epub_fixer_aggressive": 0,
            **(settings or {}),
        }
        self.entries = []

    def epub_fixer_add_entry(self, *args):
        self.entries.append(args)


@pytest.fixture()
def fixer_module(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    import kindle_epub_fixer

    monkeypatch.setattr(kindle_epub_fixer, "CWA_DB", StubCwaDb)
    # Keep backups inside tmp_path so we can assert on them without touching /config.
    backup_dir = tmp_path / "fixed_originals"
    backup_dir.mkdir()
    monkeypatch.setattr(
        kindle_epub_fixer.EPUBFixer,
        "backup_original_file",
        lambda self, epub_path: _record_backup(self, backup_dir, epub_path),
    )
    kindle_epub_fixer._test_backup_dir = backup_dir
    return kindle_epub_fixer


def _record_backup(fixer, backup_dir, epub_path):
    import shutil

    if fixer.cwa_settings["auto_backup_epub_fixes"]:
        shutil.copy2(epub_path, backup_dir / os.path.basename(str(epub_path)))


def backups_taken(fixer_module):
    return sorted(p.name for p in fixer_module._test_backup_dir.iterdir())


# --------------------------------------------------------------------------
# Defect 1 — the phantom "Converted <file> from ascii to utf-8"
# --------------------------------------------------------------------------


def test_ascii_stylesheet_is_not_reported_as_an_encoding_conversion(fixer_module, tmp_path):
    """The reporter's exact symptom: an ASCII stylesheet logged as converted."""
    book = build_epub(tmp_path / "book.epub")

    problems = fixer_module.EPUBFixer().process(str(book), str(book))

    phantom = [p for p in problems if "page_styles.css" in p and "ascii" in p]
    assert not phantom, (
        "ASCII re-encodes to byte-identical UTF-8, so this conversion changed "
        f"nothing and must not be reported as a fix. Got: {phantom}"
    )
    assert problems == [], f"a correct EPUB should need no fixes, got {problems}"


def test_a_real_encoding_conversion_is_still_reported(fixer_module, tmp_path):
    """Guard: silencing the no-op must not silence genuine conversions."""
    latin1_css = ".calibre { font-family: 'Crème Café'; }\n".encode("latin-1")
    book = build_epub(tmp_path / "latin1.epub", css=latin1_css)

    problems = fixer_module.EPUBFixer().process(str(book), str(book))

    assert any("page_styles.css" in p and "to utf-8" in p for p in problems), (
        f"a latin-1 stylesheet genuinely changes bytes and must still be fixed, got {problems}"
    )
    payload = epub_payload(book)
    assert payload["OEBPS/page_styles.css"].decode("utf-8") == latin1_css.decode("latin-1")


# --------------------------------------------------------------------------
# Defect 2 — unconditional rewrite + backup of every book, every run
# --------------------------------------------------------------------------


def test_clean_epub_is_left_untouched_on_disk(fixer_module, tmp_path):
    book = build_epub(tmp_path / "book.epub")
    # Pin the mtime into the past so "was it rewritten?" is a deterministic
    # question. Re-zipping stamps entries with the current time, so without
    # this the check can pass by coincidence when the write lands in the same
    # second the fixture was built.
    past = 1_500_000_000
    os.utime(book, (past, past))
    before = hashlib.sha256(book.read_bytes()).hexdigest()

    fixer_module.EPUBFixer().process(str(book), str(book))

    assert int(os.stat(book).st_mtime) == past, (
        "an EPUB with nothing to fix was rewritten; a library sweep would churn "
        "the mtime of every book and re-sync them all to every device"
    )
    assert hashlib.sha256(book.read_bytes()).hexdigest() == before


def test_clean_epub_is_not_backed_up(fixer_module, tmp_path):
    book = build_epub(tmp_path / "book.epub")

    fixer_module.EPUBFixer().process(str(book), str(book))

    assert backups_taken(fixer_module) == [], (
        "a book that was never modified must not be copied into "
        "fixed_originals; otherwise the backup folder grows without bound"
    )


def test_a_clean_book_reports_nothing_on_every_run(fixer_module, tmp_path):
    """The headline symptom: 'no matter how many times I run the Epub Fixer,
    I get fixes like this'. An ASCII stylesheet re-reports forever because
    nothing about it ever changes."""
    book = build_epub(tmp_path / "book.epub")

    runs = [fixer_module.EPUBFixer().process(str(book), str(book)) for _ in range(3)]

    assert runs == [[], [], []], f"the same fixes were reported on every run: {runs}"


def test_a_fixed_book_converges_after_the_first_run(fixer_module, tmp_path):
    """A book that genuinely needed fixing must go quiet once it is fixed."""
    latin1_css = ".calibre { font-family: 'Crème'; }\n".encode("latin-1")
    book = build_epub(tmp_path / "book.epub", css=latin1_css)

    first = fixer_module.EPUBFixer().process(str(book), str(book))
    assert first, "the seeded latin-1 stylesheet should be fixed on the first run"

    second = fixer_module.EPUBFixer().process(str(book), str(book))
    third = fixer_module.EPUBFixer().process(str(book), str(book))

    assert second == [], f"second run still reported fixes: {second}"
    assert third == [], f"third run still reported fixes: {third}"
    assert backups_taken(fixer_module) == ["book.epub"], (
        "only the run that actually fixed something should have taken a backup"
    )


def test_a_book_that_needs_fixing_is_backed_up_and_written(fixer_module, tmp_path):
    """Guard: skipping no-op writes must not skip real ones."""
    latin1_css = ".calibre { font-family: 'Crème'; }\n".encode("latin-1")
    book = build_epub(tmp_path / "book.epub", css=latin1_css)
    before = epub_payload(book)

    problems = fixer_module.EPUBFixer().process(str(book), str(book))

    assert problems
    assert backups_taken(fixer_module) == ["book.epub"]
    assert epub_payload(book) != before, "the fix was reported but never written to disk"


def test_separate_output_path_is_always_written(fixer_module, tmp_path):
    """Guard for the ingest path: ingest_processor passes a different output
    directory, so the file must be produced even when nothing needed fixing."""
    book = build_epub(tmp_path / "book.epub")
    out_dir = tmp_path / "conversion"
    out_dir.mkdir()

    fixer_module.EPUBFixer().process(str(book), str(out_dir) + os.sep)

    written = out_dir / "book.epub"
    assert written.exists(), "ingest would lose the book if a no-op skipped the write"
    assert epub_payload(written) == epub_payload(book)


def test_checksum_is_not_recalculated_when_nothing_changed(fixer_module, tmp_path, monkeypatch):
    book = build_epub(tmp_path / "book.epub")
    calls = []
    monkeypatch.setattr(
        fixer_module.EPUBFixer,
        "_recalculate_checksum_after_modification",
        lambda self, *a: calls.append(a),
    )
    monkeypatch.setattr(
        fixer_module.EPUBFixer,
        "_extract_book_info_from_path",
        lambda self, path: (42, "epub"),
    )

    fixer_module.EPUBFixer().process(str(book), str(book))

    assert calls == [], "the file was not modified, so its checksum cannot have changed"


# --------------------------------------------------------------------------
# Skipping the write must never skip a real repair (cross-family review, Terra)
# --------------------------------------------------------------------------


def test_duplicate_archive_entries_are_still_repaired(fixer_module, tmp_path):
    """Reading is keyed by name, so duplicates collapse and every surviving
    payload matches — but write_epub emits one entry per name, so rewriting is
    a genuine repair. Comparing name *sets* would call this file unchanged."""
    book = tmp_path / "dupes.epub"
    build_epub(book)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on the duplicate name
        with zipfile.ZipFile(book, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("OEBPS/page_styles.css", PAGE_STYLES_CSS.encode("ascii"))

    with zipfile.ZipFile(book, "r") as zf:
        assert len(zf.namelist()) != len(set(zf.namelist())), "fixture is not duplicated"

    fixer_module.EPUBFixer().process(str(book), str(book))

    with zipfile.ZipFile(book, "r") as zf:
        names = zf.namelist()
    assert len(names) == len(set(names)), (
        "the duplicate entry survived; the archive was left malformed"
    )


def test_symlinked_output_path_is_treated_as_writing_in_place(fixer_module, tmp_path):
    """A symlink spells the same book two ways. Comparing absolute paths would
    call them different destinations and rewrite the file anyway, defeating the
    idempotency this whole change exists to provide."""
    book = build_epub(tmp_path / "book.epub")
    past = 1_500_000_000
    os.utime(book, (past, past))
    link = tmp_path / "linked.epub"
    link.symlink_to(book)

    fixer_module.EPUBFixer().process(str(book), str(link))

    assert int(os.stat(book).st_mtime) == past, (
        "a symlinked output path aliased the input and rewrote it"
    )
    assert backups_taken(fixer_module) == []


def test_misplaced_mimetype_is_repaired_and_reported(fixer_module, tmp_path):
    """EPUB requires mimetype first and stored. write_epub enforces that, so the
    summary must not say 'No issues found' immediately before rewriting."""
    book = tmp_path / "badlayout.epub"
    with zipfile.ZipFile(book, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", CONTENT_OPF)
        zf.writestr("OEBPS/text.xhtml", TEXT_XHTML)
        zf.writestr("OEBPS/page_styles.css", PAGE_STYLES_CSS.encode("ascii"))
        zf.writestr("mimetype", b"application/epub+zip")  # last, and deflated

    problems = fixer_module.EPUBFixer().process(str(book), str(book))

    assert any("layout" in p for p in problems), (
        f"a malformed archive layout was repaired but never reported: {problems}"
    )
    with zipfile.ZipFile(book, "r") as zf:
        first = zf.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED

    assert fixer_module.EPUBFixer().process(str(book), str(book)) == [], (
        "the repaired archive must go quiet on the next run"
    )


# --------------------------------------------------------------------------
# Defect 3 — walking Calibre's trash
# --------------------------------------------------------------------------


def test_library_walk_skips_caltrash_and_other_hidden_dirs(fixer_module, tmp_path, monkeypatch):
    library = tmp_path / "calibre-library"
    (library / "Alice" / "Wonderland (1)").mkdir(parents=True)
    (library / "Alice" / "Wonderland (1)" / "Wonderland.epub").write_bytes(b"x")
    (library / ".caltrash" / "b" / "2").mkdir(parents=True)
    (library / ".caltrash" / "b" / "2" / "Deleted.epub").write_bytes(b"x")
    (library / ".calnotes").mkdir()
    (library / ".calnotes" / "Note.epub").write_bytes(b"x")

    monkeypatch.setattr(fixer_module, "get_library_location", lambda: str(library) + os.sep)

    found = fixer_module.get_all_epubs_in_library()

    assert [os.path.basename(f) for f in found] == ["Wonderland.epub"], (
        f"hidden Calibre directories must not be processed, got {found}"
    )


# --------------------------------------------------------------------------
# Defect 4 — the hidden-directory prune is wider than Calibre's own folders
# --------------------------------------------------------------------------
#
# The prune was written as "skip anything starting with a dot", but the library
# layout takes its directory names from the book's author and title through
# ``get_valid_filename``, which PRESERVES a leading dot — ".NET Core in Action"
# comes back unchanged. So the prune silently dropped real books from the sweep,
# with no log line and no error, which is worse than the false-positive
# reporting this file exists to fix: those books simply stop being repaired.


def test_library_walk_keeps_books_whose_folder_starts_with_a_dot(
        fixer_module, tmp_path, monkeypatch):
    """A dot is legal in an author/title directory; only Calibre's own folders
    are bookkeeping."""
    library = tmp_path / "calibre-library"
    (library / "Chris Sainty" / ".NET Core in Action (42)").mkdir(parents=True)
    (library / "Chris Sainty" / ".NET Core in Action (42)" / "dotnet.epub").write_bytes(b"x")
    (library / ".hack__SIGN" / "Volume 1 (7)").mkdir(parents=True)
    (library / ".hack__SIGN" / "Volume 1 (7)" / "hack.epub").write_bytes(b"x")
    (library / ".caltrash" / "b" / "2").mkdir(parents=True)
    (library / ".caltrash" / "b" / "2" / "Deleted.epub").write_bytes(b"x")

    monkeypatch.setattr(fixer_module, "get_library_location",
                        lambda: str(library) + os.sep)

    found = sorted(os.path.basename(f) for f in fixer_module.get_all_epubs_in_library())

    assert found == ["dotnet.epub", "hack.epub"], (
        f"a book whose folder begins with a dot must still be swept; "
        f"only Calibre's own bookkeeping folders are skipped, got {found}"
    )


# --------------------------------------------------------------------------
# Defect 5 — a "preserved" language is reported as a fix on every run
# --------------------------------------------------------------------------
#
# The #1304 symptom in its original form: a line appended to ``fixed_problems``
# on a path whose OPF write is then declined, so the run reports "1 issues
# fixed" and, one line later, "No changes needed, leaving file untouched."
# ``und`` is what Calibre writes for a book with no language set, so this is an
# ordinary library, not a contrived one.


LANG_OPF = CONTENT_OPF.replace(
    "<dc:language>en</dc:language>", "<dc:language>und</dc:language>")


def _build_epub_with_language(path: Path, opf: str) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/text.xhtml", TEXT_XHTML)
        zf.writestr("OEBPS/page_styles.css", PAGE_STYLES_CSS.encode("ascii"))
    return path


def test_a_language_that_is_only_preserved_is_not_reported_as_a_fix(
        fixer_module, tmp_path):
    """Reporting a fix the writer then declines to make is the whole #1304 bug."""
    book = _build_epub_with_language(tmp_path / "und.epub", LANG_OPF)
    before = epub_payload(book)

    for run in range(1, 4):
        fixer = fixer_module.EPUBFixer()
        fixer.process(input_path=str(book))
        assert fixer.fixed_problems == [], (
            f"run {run} reported {fixer.fixed_problems!r} but wrote nothing")

    assert epub_payload(book) == before
