# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests: the Kindle EPUB Fixer must not mojibake UTF-8 text in
entries that carry no XML declaration and no meta charset.

The symptom: a book whose ``package.opf`` had no ``<?xml ...?>`` line and a
non-ASCII character in its metadata came out of ingest with ``—`` turned into
``â€”`` and ``©`` into ``Â©``. The damaged title was then written to
``metadata.db`` and into the on-disk folder name.

Root cause: with no BOM, declaration or meta tag, ``_detect_encoding`` fell
through to a byte-statistics guess. ``charset_normalizer`` was tried first but
its branch always raised (``CharsetMatch`` has no ``.confidence``) and the
error was swallowed, so the guess came from ``chardet``. That dead branch has
since been removed. chardet 5.2.0 (inside
the pinned ``<5.3.0`` range) reports short UTF-8 text with a single multi-byte
character as Windows-1252 / ISO-8859-1 at 0.73 confidence, which clears the 0.7
acceptance threshold, so the bytes were decoded as cp1252 and re-encoded as
UTF-8.

The trigger is narrow, which is why only some books were hit: chardet's UTF-8
confidence grows with every multi-byte character it sees, so a file with exactly
ONE (a lone ``—`` or ``©`` in otherwise ASCII text) is misread as Windows-1252
at 0.73, while two or more are read correctly as UTF-8. The fixtures below
therefore carry exactly one such character; a fixture with two would pass on the
broken code and guard nothing.

UTF-8 is the XML default when nothing declares an encoding, and a byte string
that is valid multi-byte UTF-8 is overwhelmingly unlikely to be anything else,
so that is checked before any statistical guess.
"""

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

# Deliberately NO <?xml ...?> line: this is what the affected books look like.
# The defaults are pure ASCII; each test injects the characters it is about.
OPF_TEMPLATE = """<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>Nate Kenyon</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">urn:uuid:0d4e1f2a-0000-4000-8000-00000000cwng</dc:identifier>
    <dc:rights>{rights}</dc:rights>
  </metadata>
  <manifest>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="text"/>
  </spine>
</package>
"""


def make_opf(title="Ghost Spectres", rights="Copyright 2012 by Christopher Hitchens") -> str:
    return OPF_TEMPLATE.format(title=title, rights=rights)


DECLARED_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter One</title></head>
  <body><p>Plain ASCII text.</p></body>
</html>
"""

# cp1252/Latin-1 misreads produce the first three; a MacRoman misread (what
# chardet guesses for "Café") produces "√".
MOJIBAKE_MARKERS = ("Â", "â€", "Ã", "√")


class StubCwaDb:
    """Stands in for CWA_DB so the fixer needs no /config/cwa.db."""

    def __init__(self, settings=None):
        self.cwa_settings = {
            "auto_backup_epub_fixes": False,
            "kindle_epub_fixer_aggressive": 0,
            **(settings or {}),
        }
        self.entries = []

    def epub_fixer_add_entry(self, *args):
        self.entries.append(args)


@pytest.fixture()
def fixer_module(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    import kindle_epub_fixer

    monkeypatch.setattr(kindle_epub_fixer, "CWA_DB", StubCwaDb)
    return kindle_epub_fixer


def build_epub(path: Path, *, opf: bytes = None, extra: dict = None) -> Path:
    """Write a minimal EPUB. ``extra`` maps entry name -> bytes."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", make_opf().encode("utf-8") if opf is None else opf)
        zf.writestr("OEBPS/text.xhtml", DECLARED_XHTML)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def read_entry(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path, "r") as zf:
        return zf.read(name)


def assert_no_mojibake(text: str, context: str) -> None:
    bad = [m for m in MOJIBAKE_MARKERS if m in text]
    assert not bad, f"{context}: UTF-8 text was decoded as cp1252 and re-encoded ({bad}):\n{text}"


# --------------------------------------------------------------------------
# End to end: the reporter's exact symptom
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, rights",
    [
        # Exactly one multi-byte character each, mirroring the two real books
        # (these two fail on the broken code).
        ("Ghost—Spectres", "Copyright 2012"),  # a lone em dash in the title
        ("Mortality", "Copyright © 2012 by Christopher Hitchens"),  # a lone ©
        # Guards: these were already read correctly at the normal threshold;
        # they pin that they stay so.
        ("Café", "Copyright 2012"),
        ("Gabriel García Márquez", "Copyright © 2012"),
        ("日本語のタイトル", "Copyright 2012"),
        ("“Curly quotes” and ‘apostrophes’", "Copyright 2012"),
    ],
)
def test_undeclared_utf8_opf_keeps_its_characters(fixer_module, tmp_path, title, rights):
    """The reported bug: 'Ghost—Spectres' became 'Ghostâ€”Spectres' and
    '© 2012' became 'Â© 2012' in an OPF with no XML declaration."""
    opf = make_opf(title=title, rights=rights).encode("utf-8")
    book = build_epub(tmp_path / "book.epub", opf=opf)

    fixer_module.EPUBFixer().process(str(book), str(book))

    text = read_entry(book, "OEBPS/content.opf").decode("utf-8")
    assert_no_mojibake(text, "content.opf")
    assert f"<dc:title>{title}</dc:title>" in text
    assert f"<dc:rights>{rights}</dc:rights>" in text


@pytest.mark.parametrize(
    "name, data",
    [
        # A stylesheet never carries a declaration; a pseudo-element 'content'
        # with a typographic character is ordinary CSS.
        ("OEBPS/page_styles.css", 'p.note:before { content: "— "; }\n'),
        # An XHTML chapter with no declaration and no meta charset.
        (
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head>'
            "<body><p>Copyright 2012 — all rights reserved</p></body></html>\n",
        ),
    ],
)
def test_undeclared_utf8_in_other_entries_survives(fixer_module, tmp_path, name, data):
    """Same defect, other entry types: nothing about it is specific to the OPF."""
    book = build_epub(tmp_path / "book.epub", extra={name: data.encode("utf-8")})

    fixer_module.EPUBFixer().process(str(book), str(book))

    text = read_entry(book, name).decode("utf-8")
    assert_no_mojibake(text, name)
    assert "—" in text


def test_aggressive_mode_keeps_undeclared_utf8(fixer_module, monkeypatch, tmp_path):
    """Aggressive mode lowers the acceptance threshold to 0.4, which widened the
    damage: chardet reads an undeclared OPF titled "Café" as MacRoman at about
    0.56, below the normal threshold but above the aggressive one, so the title
    came out as "Caf√©"."""
    monkeypatch.setattr(
        fixer_module, "CWA_DB", lambda: StubCwaDb({"kindle_epub_fixer_aggressive": 1})
    )
    opf = make_opf(title="Café").encode("utf-8")
    book = build_epub(tmp_path / "book.epub", opf=opf)

    fixer = fixer_module.EPUBFixer()
    assert fixer.aggressive_mode
    fixer.process(str(book), str(book))

    text = read_entry(book, "OEBPS/content.opf").decode("utf-8")
    assert_no_mojibake(text, "content.opf")
    assert "<dc:title>Café</dc:title>" in text


# --------------------------------------------------------------------------
# The detector itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Ghost—Spectres", "Copyright © 2012", "García Márquez", "日本語", "“quoted”"],
)
def test_detector_reports_utf8_for_undeclared_multibyte_text(fixer_module, text):
    fixer = fixer_module.EPUBFixer()
    data = f"<dc:title>{text}</dc:title>\n".encode("utf-8")

    encoding, confidence, _source = fixer._detect_encoding(data, "OEBPS/content.opf")

    assert fixer._normalize_encoding_name(encoding) == "utf-8", (
        f"{text!r} is valid UTF-8 and nothing declares otherwise, got {encoding} ({confidence:.2f})"
    )


# --------------------------------------------------------------------------
# Guards: fixing the above must not break genuine non-UTF-8 handling
# --------------------------------------------------------------------------


def test_declared_legacy_encoding_is_still_converted(fixer_module, tmp_path):
    """A file that *declares* ISO-8859-1 must still be decoded as ISO-8859-1."""
    opf = (
        '<?xml version="1.0" encoding="iso-8859-1"?>\n'
        + make_opf(title="José García", rights="Copyright © 2012")
    ).encode("iso-8859-1")
    book = build_epub(tmp_path / "book.epub", opf=opf)

    fixer_module.EPUBFixer().process(str(book), str(book))

    text = read_entry(book, "OEBPS/content.opf").decode("utf-8")
    assert "<dc:title>José García</dc:title>" in text
    assert "Copyright © 2012" in text


def test_undeclared_legacy_bytes_are_never_turned_into_mojibake(fixer_module, tmp_path):
    """Undeclared cp1252 bytes are not valid UTF-8, so detection still runs.
    Whatever it decides, the book must end up correct or untouched, never
    'double-encoded'."""
    opf = make_opf(title="José García").encode("cp1252", "replace")
    book = build_epub(tmp_path / "book.epub", opf=opf)

    fixer_module.EPUBFixer().process(str(book), str(book))

    raw = read_entry(book, "OEBPS/content.opf")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Left as the original single-byte encoding: acceptable, not corrupted.
        assert b"Jos\xe9" in raw
        return
    assert_no_mojibake(text, "content.opf")
    assert "José García" in text
