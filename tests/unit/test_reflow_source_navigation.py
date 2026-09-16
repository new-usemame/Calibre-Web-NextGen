# SPDX-License-Identifier: GPL-3.0-or-later
"""A reader can navigate to actual PDF page starts, including sampled gaps."""

import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub

pytestmark = pytest.mark.unit
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


@pytest.mark.parametrize("selected", [(0, 1, 2), (0, 2)])
def test_source_page_navigation_resolves_to_real_starts_without_renumbering(tmp_path, selected):
    with pymupdf.open() as doc:
        for words in ("Arrival beside the river.", "The middle passage continues.",
                      "A final journey into the hills."):
            page = doc.new_page(width=500, height=700)
            page.insert_text((50, 110), words, fontsize=12)
        book = assemble.deterministic_book(doc)
    fragments = {pno: build_epub.page_fragment(book, pno) for pno in selected}
    # The marker must still resolve when the first source page spans chapters.
    fragments[0] += "<h2>A new section</h2><p>Its text follows.</p>"
    target = tmp_path / "navigation.epub"
    build_epub.build(book, str(target), page_html=fragments,
                     metadata={"title": "Source navigation contract", "language": "en"})
    with zipfile.ZipFile(target) as archive:
        nav = ET.fromstring(archive.read("OEBPS/nav.xhtml"))
        page_lists = [node for node in nav.iter(XHTML + "nav")
                      if node.get(EPUB_TYPE) == "page-list"]
        assert len(page_lists) == 1, "PDF page markers are unreachable through the navigation document"
        links = list(page_lists[0].iter(XHTML + "a"))
        assert [node.text for node in links] == ["PDF page %d" % (pno + 1) for pno in selected]
        for pno, link in zip(selected, links):
            filename, ident = link.attrib["href"].split("#")
            assert ident == "pg_%04d" % pno
            chapter = ET.fromstring(archive.read("OEBPS/" + filename))
            markers = [node for node in chapter.iter() if node.get("id") == ident]
            assert len(markers) == 1
            assert markers[0].get(EPUB_TYPE) == "pagebreak"

        # Nickel ignores the standard page-list. An ordinary, visible spine
        # document must offer the same destinations through its normal ToC.
        toc = next(node for node in nav.iter(XHTML + "nav")
                   if node.get(EPUB_TYPE) == "toc")
        index_entry = next((node for node in toc.iter(XHTML + "a")
                            if node.text == "Source PDF pages"), None)
        assert index_entry is not None, "Readers ignoring page-list have no source-page index"
        index_href = index_entry.get("href")
        source_index = ET.fromstring(archive.read("OEBPS/" + index_href))
        assert not any(node.get("hidden") or node.get("aria-hidden") == "true"
                       for node in source_index.iter())
        index_links = list(source_index.iter(XHTML + "a"))
        assert [(node.text, node.get("href")) for node in index_links] == [
            (node.text, node.get("href")) for node in links]
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
        ns = "{http://www.idpf.org/2007/opf}"
        item = next(node for node in opf.iter(ns + "item")
                    if node.get("href") == index_href)
        spine = list(opf.iter(ns + "itemref"))
        assert spine[-1].get("idref") == item.get("id"), "The index must not interrupt the book"
