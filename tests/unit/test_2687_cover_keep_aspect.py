# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The enforcer can keep a new cover's aspect ratio in an EPUB (#2687).

``ebook-polish -c`` writes the generated titlepage SVG with
``preserveAspectRatio="none"``, so readers that render it stretch the cover to
the screen. With ``cover_keep_aspect_ratio`` on, the cover goes in through
Calibre's ``set_cover(..., options={"keep_aspect": True})`` instead, which
writes ``xMidYMid meet``.

The first test runs the real enforcer method against real Calibre tools on a
real EPUB and reads the titlepage back out of the file; it is skipped where
Calibre is not installed. The second pins the wiring in the fast lane.
"""
import io
import os
import shutil
import subprocess
import types
import zipfile
from pathlib import Path

import pytest

from tests.unit.test_1372_kepub_metadata_enforcement import _bare_enforcer, _load_module

pytestmark = pytest.mark.unit

MAC_CALIBRE = "/Applications/calibre.app/Contents/MacOS"


@pytest.fixture(scope="module")
def enforcer_module():
    return _load_module()


def _calibre_path():
    path = os.environ.get("PATH", "")
    if os.path.isdir(MAC_CALIBRE):
        path = MAC_CALIBRE + os.pathsep + path
    if shutil.which("ebook-polish", path=path) and shutil.which("calibre-debug", path=path):
        return path
    return None


def _minimal_epub(target):
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Aspect Fixture</dc:title><dc:creator>Fixture Author</dc:creator>
    <dc:language>en</dc:language><dc:identifier id="uid">urn:uuid:2687</dc:identifier>
  </metadata>
  <manifest>
    <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:2687"/></head>
  <docTitle><text>Aspect Fixture</text></docTitle>
  <navMap><navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>
  <content src="chapter.xhtml"/></navPoint></navMap>
</ncx>"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>
<body><h1>One</h1><p>Hello.</p></body></html>"""
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
        z.writestr("content.opf", opf, zipfile.ZIP_DEFLATED)
        z.writestr("toc.ncx", ncx, zipfile.ZIP_DEFLATED)
        z.writestr("chapter.xhtml", chapter, zipfile.ZIP_DEFLATED)


def _jpeg(target, size=(600, 900)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG")
    target.write_bytes(buf.getvalue())


def _book_dir(tmp_path):
    book_dir = tmp_path / "Fixture Author" / "Aspect Fixture (7)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "Aspect Fixture - Fixture Author.epub"
    _minimal_epub(epub)
    _jpeg(book_dir / "cover.jpg")
    opf = tmp_path / "new_metadata.opf"
    opf.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Aspect Fixture</dc:title><dc:creator>Fixture Author</dc:creator>"
        "<dc:language>en</dc:language></metadata></package>",
        encoding="utf-8",
    )
    return book_dir, epub, opf


def _stub_side_effects(module, monkeypatch, opf):
    class _FakeBook:
        def __init__(self, bd, file_path):
            self.file_path = file_path
            self.file_format = Path(file_path).suffix.replace(".", "")
            self.cover_path = str(Path(bd) / "cover.jpg")
            self.old_metadata_path = str(Path(bd) / "metadata.opf")
            self.new_metadata_path = str(opf)
            self.book_id = "7"
            self.title_author = "Aspect Fixture - Fixture Author"
            self.book_title = "Aspect Fixture"
            self.author_name = "Fixture Author"

    monkeypatch.setattr(module, "Book", _FakeBook)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    for name in ("replace_old_metadata", "empty_metadata_temp",
                 "_recalculate_checksum_after_modification",
                 "_restat_format_size_after_modification",
                 "generate_missing_cover_if_enabled"):
        monkeypatch.setattr(module.Enforcer, name, lambda self, *a, **k: None)
    monkeypatch.setattr(module.Enforcer, "_reset_book_dir_ownership",
                        staticmethod(lambda *a: None))


def _titlepage_aspect(epub):
    with zipfile.ZipFile(epub) as z:
        pages = [n for n in z.namelist() if n.endswith("titlepage.xhtml")]
        assert len(pages) == 1, z.namelist()
        page = z.read(pages[0]).decode("utf-8")
    start = page.index('preserveAspectRatio="') + len('preserveAspectRatio="')
    return page[start:page.index('"', start)], page


@pytest.mark.parametrize("keep, expected", [(True, "xMidYMid meet"), (False, "none")])
def test_enforced_epub_cover_page_aspect_follows_the_setting(
        enforcer_module, tmp_path, monkeypatch, keep, expected):
    path = _calibre_path()
    if path is None:
        pytest.skip("Calibre (ebook-polish, calibre-debug) not installed")
    monkeypatch.setenv("PATH", path)
    book_dir, epub, opf = _book_dir(tmp_path)
    _stub_side_effects(enforcer_module, monkeypatch, opf)

    inst = _bare_enforcer(enforcer_module)
    inst.cwa_settings = {"cover_keep_aspect_ratio": 1 if keep else 0}
    inst.calibre_env = dict(os.environ)
    inst.enforce_cover(str(book_dir))

    aspect, page = _titlepage_aspect(epub)
    assert aspect == expected
    # The cover page must show the cover that was supplied (600x900), not
    # some leftover image.
    assert 'viewBox="0 0 600 900"' in page


def test_keep_aspect_moves_the_cover_out_of_ebook_polish(enforcer_module, tmp_path, monkeypatch):
    book_dir, epub, opf = _book_dir(tmp_path)
    _stub_side_effects(enforcer_module, monkeypatch, opf)
    calls = []

    def _fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(enforcer_module.subprocess, "run", _fake_run)
    inst = _bare_enforcer(enforcer_module)
    inst.cwa_settings = {"cover_keep_aspect_ratio": 1}
    inst.enforce_cover(str(book_dir))

    assert len(calls) == 2, calls
    polish, cover = calls
    assert polish[0] == "ebook-polish" and "-c" not in polish and "-o" in polish
    assert cover[0] == "calibre-debug" and cover[2].endswith("calibre_set_cover.py")
    assert cover[-2:] == [str(epub), str(book_dir / "cover.jpg")]
    assert Path(cover[2]).is_file()
