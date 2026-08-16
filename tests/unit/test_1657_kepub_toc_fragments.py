# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Synthetic-archive coverage for fragment-anchored EPUB TOCs."""

import logging
from types import SimpleNamespace
import zipfile

import pytest


CONTAINER_XML = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NCX_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint><content src="chapter.xhtml#one"/></navPoint>
    <navPoint><content src="chapter.xhtml"/></navPoint>
    <navPoint><content src="chapter.xhtml#two"/></navPoint>
  </navMap>
</ncx>
"""

NCX_WITHOUT_FRAGMENTS = NCX_WITH_FRAGMENTS.replace(
    b"chapter.xhtml#one", b"chapter-one.xhtml"
).replace(
    b"chapter.xhtml#two", b"chapter-two.xhtml"
)

NAV_WITH_FRAGMENTS = b"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter.xhtml#one">One</a></li>
        <li><a href="chapter.xhtml">Whole document</a></li>
        <li><a href="#local-position">Local position</a></li>
      </ol>
    </nav>
    <nav epub:type="landmarks">
      <a href="chapter.xhtml#not-a-toc-entry">Body</a>
    </nav>
  </body>
</html>
"""


def _matching_dual_tocs(target_count):
    ncx_targets = "\n".join(
        '<navPoint><content src="chapters/chapter.xhtml#anchor-{0:03d}"/></navPoint>'.format(index)
        for index in range(target_count)
    )
    nav_targets = "\n".join(
        '<li><a href="../chapters/chapter.xhtml#anchor-{0:03d}">{0}</a></li>'.format(index)
        for index in range(target_count)
    )
    ncx = (
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        + ncx_targets
        + '</navMap></ncx>'
    ).encode()
    nav = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="toc"><ol>'
        + nav_targets
        + '</ol></nav></body></html>'
    ).encode()
    return ncx, nav


def _opf(*manifest_items, version="3.0", spine_toc=""):
    items = "\n".join(manifest_items)
    toc_attribute = f' toc="{spine_toc}"' if spine_toc else ""
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <manifest>{items}</manifest>
  <spine{toc_attribute}/>
</package>
""".encode()


def _write_epub(path, opf, members=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OPS/book.opf", opf)
        for name, content in members:
            archive.writestr(name, content)
    return path


def _count(path):
    from cps.services.kepub_package_normalizer import count_fragment_anchored_toc_targets

    return count_fragment_anchored_toc_targets(path)


@pytest.mark.unit
def test_ncx_only_toc_counts_fragment_anchored_targets(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_toc_without_fragments_reports_zero(tmp_path):
    package = _write_epub(
        tmp_path / "ncx-clean.epub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            version="2.0",
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", NCX_WITHOUT_FRAGMENTS)],
    )

    assert _count(package) == 0


@pytest.mark.unit
def test_nav_only_toc_counts_fragments_but_not_landmarks(tmp_path):
    package = _write_epub(
        tmp_path / "nav-fragments.epub",
        _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
        [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
    )

    assert _count(package) == 2


@pytest.mark.unit
def test_matching_ncx_and_nav_targets_are_counted_once_per_package(tmp_path):
    ncx, nav = _matching_dual_tocs(42)
    package = _write_epub(
        tmp_path / "dual-toc-fragments.kepub",
        _opf(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            spine_toc="ncx",
        ),
        [("OPS/toc.ncx", ncx), ("OPS/nav/toc.xhtml", nav)],
    )

    assert _count(package) == 42


@pytest.mark.unit
@pytest.mark.parametrize("toc_state", ["absent", "malformed"])
def test_absent_or_malformed_toc_never_raises(tmp_path, toc_state):
    if toc_state == "absent":
        opf = _opf('<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>')
        members = []
    else:
        opf = _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        members = [("OPS/nav.xhtml", b"<html><nav")]
    package = _write_epub(tmp_path / f"{toc_state}.epub", opf, members)

    assert _count(package) == 0


@pytest.mark.unit
def test_conversion_diagnostic_names_book_and_fragment_count(tmp_path, caplog, monkeypatch):
    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "affected"
    (tmp_path / "affected.epub").write_bytes(b"source")
    book = SimpleNamespace(
        id=42,
        title="Synthetic Fragment Book",
        path="Synthetic/Fragment Book",
        data=[SimpleNamespace(name="affected")],
    )

    class Query:
        def filter(self, *_args):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, *_args):
            return Query()

        def merge(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class LocalDB:
        def __init__(self, **_kwargs):
            self.session = Session()

        def get_book(self, _book_id):
            return book

        def get_book_format(self, *_args):
            return None

    def convert_package(*_args):
        _write_epub(
            tmp_path / "affected.kepub",
            _opf('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
            [("OPS/nav.xhtml", NAV_WITH_FRAGMENTS)],
        )
        return 0, None

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.helper, "mark_book_modified", lambda *_args, **_kwargs: None)
    task = convert.TaskConvert(
        str(book_path), 42, "convert",
        {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None,
    )
    monkeypatch.setattr(task, "_convert_kepubify", convert_package)
    monkeypatch.setattr(task, "_handleSuccess", lambda: None)

    with caplog.at_level(logging.WARNING):
        assert task._convert_ebook_format() == "affected.kepub"

    message = caplog.text
    assert "Synthetic Fragment Book" in message
    assert "42" in message
    assert "2 fragment-anchored TOC targets" in message
    assert "highlights" in message.lower()
