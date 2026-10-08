# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #2506: a chapter that opens with a *commented-out* XML
declaration (``<!--?xml version="1.0" encoding="utf-8"?-->``) must not end up
with a real declaration followed by that comment.

The fixer's "has a declaration" check only matches a leading ``<?xml``, so it
prepended a real one and left the comment behind. The stored EPUB is still
well-formed (a comment may follow the declaration), but kepubify turns the
comment into a second real declaration, which is not well-formed XML, so every
page of the KEPUB renders blank on a Kobo.
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


@pytest.mark.parametrize(
    "prefix",
    [
        FAKE_DECL,
        FAKE_DECL + "\n",
        "\ufeff" + FAKE_DECL,
        "  \n" + FAKE_DECL,
        '<!--?xml version="1.0"?-->',
    ],
)
def test_commented_declaration_is_not_left_behind(fixer_module, tmp_path, prefix):
    text = fixed_chapter(fixer_module, tmp_path, prefix + "<!DOCTYPE html>\n" + BODY)

    assert "<!--?xml" not in text
    assert len(DECL_RE.findall(text)) == 1
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<!DOCTYPE html>" in text


def test_commented_declaration_after_real_one_is_removed(fixer_module, tmp_path):
    """The layout in the #2506 report: a real declaration and then the fake one.
    A book stored by the old fixer looks like this, so re-running the fixer has
    to repair it rather than skip it because a declaration is already there."""
    chapter = '<?xml version="1.0" encoding="utf-8"?>\n' + FAKE_DECL + "<!DOCTYPE html>\n" + BODY
    text = fixed_chapter(fixer_module, tmp_path, chapter)

    assert "<!--?xml" not in text
    assert len(DECL_RE.findall(text)) == 1
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<!DOCTYPE html>" in text


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
