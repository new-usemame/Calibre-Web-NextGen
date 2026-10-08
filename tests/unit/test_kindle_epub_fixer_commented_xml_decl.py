# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #2506: no comment imitating an XML declaration
(``<!--?xml version="1.0" encoding="utf-8"?-->``) may survive the fixer.

The stored EPUB stays well-formed with such a comment in it, but kepubify 4.0.4
rewrites every one of them into a real declaration, wherever it sits (measured
on the reporter's book: after a real declaration, after an ordinary comment,
stacked, and mid-body all gave a KEPUB chapter that is not well-formed XML).
On a Kobo that book opens with every page blank.
"""

import re
import zipfile
from pathlib import Path
from xml.dom import minidom

import pytest

pytestmark = pytest.mark.unit

CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Commented declaration</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="uid">urn:uuid:0d4e1f2a-0000-4000-8000-0000000cwng2</dc:identifier>
  </metadata>
  <manifest>
    <item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="text"/>
  </spine>
</package>
"""

BODY = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head>'
    "<body><p>Hello</p></body></html>\n"
)
FAKE_DECL = '<!--?xml version="1.0" encoding="utf-8"?-->'
DECL_RE = re.compile(r"<\?xml\b")


class StubCwaDb:
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


def build_epub(path: Path, chapter: str) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/text.xhtml", chapter.encode("utf-8"))
    return path


def fixed_chapter(fixer_module, tmp_path, chapter):
    book = build_epub(tmp_path / "book.epub", chapter)
    fixer_module.EPUBFixer().process(str(book), str(book))
    with zipfile.ZipFile(book) as zf:
        return zf.read("OEBPS/text.xhtml").decode("utf-8")


REAL_DECL = '<?xml version="1.0" encoding="utf-8"?>'


@pytest.mark.parametrize(
    "chapter",
    [
        pytest.param(FAKE_DECL + "<!DOCTYPE html>\n" + BODY, id="leading"),
        pytest.param(FAKE_DECL + "\n<!DOCTYPE html>\n" + BODY, id="leading-newline"),
        pytest.param("  \n" + FAKE_DECL + "<!DOCTYPE html>\n" + BODY, id="leading-whitespace"),
        pytest.param('<!--?xml version="1.0"?--><!DOCTYPE html>\n' + BODY, id="no-encoding"),
        # The layout an earlier fixer stored, so re-running the fixer repairs it.
        pytest.param(REAL_DECL + "\n" + FAKE_DECL + "<!DOCTYPE html>\n" + BODY, id="after-real"),
        pytest.param(FAKE_DECL + FAKE_DECL + "<!DOCTYPE html>\n" + BODY, id="stacked"),
        pytest.param("<!-- gen -->\n" + FAKE_DECL + "<!DOCTYPE html>\n" + BODY, id="after-comment"),
        pytest.param(REAL_DECL + "\n" + BODY.replace("<body>", FAKE_DECL + "<body>"), id="mid-body"),
    ],
)
def test_no_commented_declaration_survives(fixer_module, tmp_path, chapter):
    text = fixed_chapter(fixer_module, tmp_path, chapter)

    assert "<!--?xml" not in text
    assert len(DECL_RE.findall(text)) == 1
    assert text.startswith(REAL_DECL)
    assert "<p>Hello</p>" in text


def test_html_chapter_loses_the_comment_too(fixer_module, tmp_path):
    book = tmp_path / "book.epub"
    with zipfile.ZipFile(book, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF.replace("text.xhtml", "text.html"))
        zf.writestr("OEBPS/text.html", FAKE_DECL + "<!DOCTYPE html>\n" + BODY)
    fixer_module.EPUBFixer().process(str(book), str(book))
    with zipfile.ZipFile(book) as zf:
        text = zf.read("OEBPS/text.html").decode("utf-8")

    assert "<!--?xml" not in text
    assert "<p>Hello</p>" in text


def test_second_pass_changes_nothing(fixer_module, tmp_path):
    chapter = "<!-- gen -->\n" + FAKE_DECL + FAKE_DECL + "<!DOCTYPE html>\n" + BODY
    once = fixed_chapter(fixer_module, tmp_path, chapter)
    twice = fixed_chapter(fixer_module, tmp_path, once)

    assert twice == once


def test_processing_instruction_in_a_comment_is_left_alone(fixer_module, tmp_path):
    chapter = REAL_DECL + '\n<!--?xml-stylesheet href="a.css"?-->\n' + BODY
    text = fixed_chapter(fixer_module, tmp_path, chapter)

    assert '<!--?xml-stylesheet href="a.css"?-->' in text


def test_converter_style_rewrite_would_stay_well_formed(fixer_module, tmp_path):
    """kepubify rewrites a leading comment of this shape into a declaration, so
    nothing declaration-shaped may survive as a comment after the fixer."""
    text = fixed_chapter(fixer_module, tmp_path, FAKE_DECL + "<!DOCTYPE html>\n" + BODY)
    simulated = text.replace("<!--?xml", "<?xml").replace("?-->", "?>")
    minidom.parseString(simulated.encode("utf-8"))


def test_real_declaration_plus_ordinary_comment_is_untouched(fixer_module, tmp_path):
    chapter = '<?xml version="1.0" encoding="utf-8"?>\n<!-- keep me -->\n' + BODY
    text = fixed_chapter(fixer_module, tmp_path, chapter)

    assert "<!-- keep me -->" in text
    assert len(DECL_RE.findall(text)) == 1


def test_ordinary_leading_comment_still_gets_a_declaration(fixer_module, tmp_path):
    text = fixed_chapter(fixer_module, tmp_path, "<!-- generated -->\n" + BODY)

    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<!-- generated -->" in text
