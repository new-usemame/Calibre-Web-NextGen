# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Synthetic controls for source reading order, columns and interior artwork."""

import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub, extract, skeleton
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


def _book(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    try:
        return assemble.deterministic_book(doc)
    finally:
        doc.close()


def _elements_text(book):
    return [el.text for el in book.elements]


def _whole_text(book):
    return " ".join(_elements_text(book))


def _line(text, x0, y0, x1, y1, size):
    span = extract.Span(text=text, size=size, font="Times-Roman", flags=0,
                        bbox=(x0, y0, x1, y1), origin_y=y1)
    return extract.Line(spans=[span], bbox=(x0, y0, x1, y1))


def _block(number, lines):
    return extract.Block(
        number=number,
        bbox=(min(ln.bbox[0] for ln in lines), min(ln.bbox[1] for ln in lines),
              max(ln.bbox[2] for ln in lines), max(ln.bbox[3] for ln in lines)),
        lines=lines)


def _raw_book(*raw_pages):
    """The deterministic pass over pages whose blocks are already known -- the
    shape a local OCR layer hands back, where each block is one column rather
    than MuPDF's row-interleave of both."""
    pages = list(raw_pages)
    style = skeleton.book_style(pages)
    skeletons = [skeleton.page_skeleton(raw, style) for raw in pages]
    return assemble.assemble(skeletons, style, pages)


def _build(book, tmp_path, doc):
    page_html = {pno: build_epub.page_fragment(book, pno)
                 for pno in sorted(book.pages)}
    return build_epub.build(
        book, str(tmp_path / "out.epub"), page_html=page_html, doc=doc,
        metadata={"title": "t", "authors": ["a"], "language": "en"})


def _doc(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    return doc


def _ink_share(jpeg_bytes):
    """Share of dark pixels in a decoded image: blank paper fails, ink passes."""
    pix = pymupdf.Pixmap(jpeg_bytes)
    data, n = pix.samples, pix.n
    total = pix.width * pix.height
    step = max(1, total // 20000)
    dark = sampled = 0
    for i in range(0, total, step):
        sampled += 1
        if data[i * n] < 200:
            dark += 1
    return dark / float(sampled or 1)


def _epub_images(path):
    """Images actually embedded in the reading chapters, excluding source appendices."""
    with zipfile.ZipFile(str(path)) as zf:
        images = set()
        for name in zf.namelist():
            if not re.fullmatch(r'OEBPS/ch\d+\.xhtml', name):
                continue
            for node in ET.fromstring(zf.read(name)).iter('{http://www.w3.org/1999/xhtml}img'):
                images.add(posixpath.normpath(posixpath.join(posixpath.dirname(name), node.get('src'))))
        assert images <= set(zf.namelist()), 'a reading image has no packaged resource'
        return sorted(images)


def _assert_caption_detail(path, pno):
    """The emitted caption link resolves to its unique, nonblank source image."""
    ns = '{http://www.w3.org/1999/xhtml}'
    href = 'original-p%04d.xhtml#caption_0' % pno
    with zipfile.ZipFile(str(path)) as zf:
        links = [node for name in zf.namelist()
                 if re.fullmatch(r'OEBPS/ch\d+\.xhtml', name)
                 for node in ET.fromstring(zf.read(name)).iter(ns + 'a')
                 if node.get('href') == href]
        assert len(links) == 1
        filename, identifier = href.split('#')
        document = ET.fromstring(zf.read('OEBPS/' + filename))
        targets = [node for node in document.iter() if node.get('id') == identifier]
        assert len(targets) == 1
        section = next(node for node in document.iter(ns + 'section') if targets[0] in node.iter())
        images = list(section.iter(ns + 'img'))
        assert len(images) == 1
        assert _ink_share(zf.read('OEBPS/' + images[0].get('src'))) > 0


# ------------------------------------------------------------------ column order

class TestColumnReadingOrder(object):
    """Two and three prose columns read top-to-bottom, then left-to-right."""

    def test_two_columns_are_not_interleaved(self):
        """The readiness probe: same-baseline lines inside one MuPDF block must
        still read all-left then all-right, with every word preserved."""
        book = _book(F.two_column_page)

        left = " ".join(line for line, _ in F.TWO_COLUMN_ROWS)
        right = " ".join(line for _, line in F.TWO_COLUMN_ROWS)
        assert _whole_text(book) == left + " " + right, _whole_text(book)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_three_columns_read_left_to_right(self):
        book = _book(F.three_column_page)

        text = _whole_text(book)
        order = [text.index(word) for word in
                 ("Alpha", "continues", "Beta", "second.", "Gamma", "third.")]
        assert order == sorted(order), text

    def test_a_spanning_heading_stays_between_its_bands(self):
        """A full-width heading between column bands belongs between the bands --
        not absorbed into a column and not floated to the top of one."""
        book = _book(F.column_bands_page)

        text = _whole_text(book)
        day = text.index('Blue trays are stacked first')
        leads = text.index("The small tray holds blue blocks")
        night = text.index("Red trays are stacked second")
        leaves = text.index("The large tray holds red blocks")
        mid = text.index("How the Remaining Drawers Fit Across the Entire Wooden Workshop Wall")
        cedar = text.index("Blue blocks fit in this tray")
        maple = text.index("Maple supports the shelf")
        birch = text.index("Red blocks fit in that tray")
        assert day < leads < night < leaves, text
        assert night < mid < cedar < maple < birch, text

    def test_a_paragraph_crossing_the_column_break_is_one_paragraph(self):
        """Column order is a reading order, not a chopping rule: the sentence that
        flows off the bottom of the left column continues at the top of the right."""
        book = _book(F.column_continuity_page)

        paragraphs = [el.text for el in book.elements if el.kind == "p"]
        assert paragraphs == [F.CONTINUITY_TEXT], paragraphs

    def test_a_column_wrap_heals_past_a_below_band_running_head(self):
        """A column wrap heals past a below band running head."""
        raw = extract.RawPage(
            pno=0, width=403.0, height=606.0,
            blocks=[
                _block(1, [_line("SHELVES—SHELVES 1257",
                                 166.8, 61.2, 343.0, 67.0, 6.3)]),
                _block(2, [
                    _line("wooden lid 129, 133-144, 209-211, 213-214,",
                          62.9, 78.7, 191.0, 85.9, 7.0),
                    _line("216-217, 231-232, 234-235, 550, 1163, Con-",
                          62.2, 88.8, 185.3, 95.3, 7.3),
                    _line("struction, shelving 797-798, 801, doorway,",
                          62.6, 97.0, 186.2, 104.9, 8.0),
                    _line("drawer labels 400, 449, 976, blue paint 175,",
                          62.2, 106.3, 171.4, 113.5, 8.7),
                    _line("800, trays 370-371, 726, 798, 1141, the order",
                          62.2, 115.9, 190.0, 122.9, 7.0),
                    _line("of the shelves 222, cabinet shelving/Sample or-",
                          58.8, 124.8, 184.6, 132.7, 11.0),
                ]),
                _block(3, [
                    _line("der 24-25 (fig. 1), 45, 222, 249, 1164, Pre-",
                          215.0, 78.7, 336.7, 86.4, 6.7),
                    _line("treatment 1056 n.14, 1067 n.10, blue lining",
                          215.0, 88.1, 340.6, 95.8, 8.3),
                    _line("239, corner joints 89, 91-92, 96-104 (fig. 13),",
                          215.0, 97.2, 336.2, 104.9, 7.0),
                    _line("106-108, 151, 175, 538, 575, drawer slides 986,",
                          215.3, 106.6, 340.6, 114.2, 10.7),
                    _line('Clockwise 158, 181-182, 194, 370, 409,',
                          215.3, 116.2, 342.2, 123.4, 7.0),
                    _line("shelf (woodwork) 27, 35, 53, 73-109.",
                          214.3, 125.0, 333.1, 132.7, 8.7),
                ]),
            ])

        book = _raw_book(raw)

        assert "cabinet shelving/Sample order 24-25 (fig. 1), 45, 222," \
            in _whole_text(book), _whole_text(book)
        assert "SHELVES—SHELVES 1257" in book.furniture
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_counter_reads_a_reordered_page_in_reading_order(self):
        """The counter reads a reordered page in reading order."""
        raw = extract.RawPage(
            pno=0, width=472.0, height=688.0,
            blocks=[
                _block(1, [_line("The gauge will not show any further off­",
                                 93.0, 290.0, 223.0, 297.0, 7.0)]),
                _block(2, [_line("The scale will not show any further off­",
                                 253.0, 290.0, 385.0, 297.0, 7.0)]),
                _block(3, [_line("sets before it reaches its mark (Blue).",
                                 93.0, 302.0, 223.0, 309.0, 7.0)]),
                _block(4, [_line("sets for the next join. The scale’s last off­",
                                 253.0, 302.0, 385.0, 309.0, 7.0)]),
                _block(5, [_line('Gauge at 250 Blue marked the final offset to Dial. Offsets',
                                 93.0, 314.0, 223.0, 321.0, 7.0)]),
                _block(6, [_line("set beside Blue was a turn to Dial at",
                                 253.0, 314.0, 385.0, 321.0, 7.0)]),
            ])

        book = _raw_book(raw)

        text = _whole_text(book)
        assert 'further offsets before it reaches its mark' in text, text
        assert 'further offsets for the next join' in text, text
        assert "last offset beside Blue" in text, text
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_counter_sees_past_furniture_between_half_words(self):
        """The counter sees past furniture between half words."""
        raw = extract.RawPage(
            pno=0, width=842.0, height=595.0,
            blocks=[
                _block(1, [
                    _line("woodwork we varnish on a crate will stay",
                          110.0, 100.0, 380.0, 108.0, 9.0),
                    _line("clean on it while our panels are dry, we can",
                          110.0, 116.0, 388.0, 124.0, 9.0),
                    _line("the cupboard labels were faded, and the old crew must varn-",
                          110.0, 530.0, 388.0, 540.0, 9.0),
                ]),
                _block(2, [_line("WORKSHOP FOR BEGINNERS 24",
                                 560.0, 60.0, 736.0, 66.0, 6.3)]),
                _block(3, [
                    _line("ish the edges of the trays. These painted",
                          457.0, 76.0, 736.0, 85.0, 9.0),
                    _line("instructions for the shelf are meant to show us",
                          457.0, 92.0, 730.0, 100.0, 9.0),
                    _line("which drawer holds the blue wooden blocks.",
                          457.0, 108.0, 728.0, 116.0, 9.0),
                ]),
            ])

        book = _raw_book(raw)

        assert "must varnish the edges of the trays" in _whole_text(book), \
            _whole_text(book)
        assert "WORKSHOP FOR BEGINNERS 24" in book.furniture
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_background_photo_does_not_veto_the_two_pages(self):
        """A background photo does not veto the two pages."""
        book = _book(lambda d: F.spread_with_background_photo_page(d, F.solid_png()))

        left = " ".join(line for line, _ in F.TWO_COLUMN_ROWS)
        right = " ".join(line for _, line in F.TWO_COLUMN_ROWS)
        text = re.sub(r"\s+", " ", _whole_text(book)).strip()
        assert text == left + " " + right, text
        assert any(el.kind == "fig" for el in book.elements), "the photo stays"
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_panel_beside_a_diagram_at_ocr_leading_recovers_the_diagram(self):
        """A panel beside a diagram at ocr leading recovers the diagram."""
        book = _chart_book(lambda d: F.scan_panel_diagram_ocr_leading_page(
            d, F.art_png(F.PANEL_DIAGRAM_ART)))

        figures = [f for f in book.figures if f["pno"] == 1]
        assert figures, "the diagram beside the panel is lost"
        assert any(el.kind == "fig" and el.pno == 1 for el in book.elements)

    def test_notes_stay_a_side_channel_on_a_column_page(self):
        """Columns must not push bottom-zone prose into the notes, and the marker
        still binds its note."""
        book = _book(F.column_notes_page)

        text = _whole_text(book)
        first_left = text.index("the tray is assembled by Register")
        second_left = text.index("Its second step follows the first")
        first_right = text.index("A different argument starts here")
        second_right = text.index("This evidence belongs with the next")
        assert first_left < second_left < first_right < second_right, text
        assert any(n.num == 24 for n in book.notes), book.notes
        assert not any("Workshop Register" in el.text for el in book.elements)
        markers = [int(run[1]) for el in book.elements for run in el.runs
                   if run[0] in ("sup", "mark") and str(run[1]).isdigit()]
        assert 24 in markers

    def test_a_ruled_table_is_not_read_column_major(self):
        """The control: a ruled grid is a table, not prose columns. Reading it
        column-major destroys every row."""
        book = _book(F.ruled_table_page)

        text = _whole_text(book)
        assert text.index("Red") < text.index("White") < text.index("Pine") \
               < text.index("Top"), text

    def test_a_color_pair_table_keeps_its_pairs_together(self):
        """A color pair table keeps its pairs together."""
        book = _book(F.color_pair_table_page)

        text = _whole_text(book)
        assert text.index("ORANGE looks at RED") < text.index('faces'), text
        assert text.index('faces') < text.index("YELLOW looks at PURPLE"), text
        assert "columns_reordered" not in (book.page_reasons(0) or [])
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_color_pair_table_keeps_each_offset_on_its_own_row(self):
        """The constructed table's third column is the offset, and the letter-spaced
        lowercase cells make every row 'continue' into the next: the measured
        output printed 'Painted y e l l o w looks at p ur p le' -- row one's
        offset glued to row two's colors. Each row is one unit holding its pair
        and its own offset, in print order, and the prose after the table does
        not fuse with the last row either."""
        book = _book(F.color_pair_table_page)

        rows = [el.text for el in book.elements
                if "looks at" in (el.text or "") or 'faces' in (el.text or "")]
        assert rows == [
            'ORANGE looks at RED b l u e faces g r e e n Painted',
            'YELLOW looks at PURPLE w h i t e faces y e l l o w Plain',
            'BLACK looks at OLIVE b r o w n faces o r a n g e Unpainted',
            'MAGENTA looks at INDIGO p u r p l e faces s i l v e r Plain',
            'VERMILION looks at CHARCOAL c r i m s o n faces c o p p e r Painted',
        ], rows
        text = _whole_text(book)
        assert text.index("Painted", text.index("VERMILION")) \
            < text.index('A new exercise begins beside the wooden bench'), text
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_table_of_shared_entity_rows_stays_row_objects(self):
        """Held-out fixture A: every aligned row is one project, and the source
        forbids treating the columns as unrelated lists. Both columns carry
        ascending years, so the labelled-sequence proof alone would print all
        four Opened entries and then all four Closed -- but the rows share
        their entity, and that proves the pairing the years cannot."""
        book = _book(F.paired_entity_table_page)

        rows = [el.text for el in book.elements if "Archive" in (el.text or "")]
        assert rows == [
            "Opened 2011 · Cedar Archive Closed 2014 · Cedar Archive",
            "Opened 2013 · Harbor Archive Closed 2017 · Harbor Archive",
            "Opened 2016 · Lantern Archive Closed 2020 · Lantern Archive",
            "Opened 2019 · Orchard Archive Closed 2025 · Orchard Archive",
        ], rows
        assert book.conservation.ok, book.conservation.to_dict()

    def test_independent_dated_columns_are_not_paired_by_row(self):
        """The control, fixture B's shape: 'Departed 2008 · North Expedition'
        beside 'Departed 2012 · South Expedition' shares no entity, so the two
        independent sequences read down their columns, never interleaved."""
        book = _book(F.three_column_page)

        text = _whole_text(book)
        order = [text.index(word) for word in
                 ("Alpha", "continues", "Beta", "second.", "Gamma", "third.")]
        assert order == sorted(order), text
        assert "Alpha opens the first. Alpha continues on. Alpha ends its column." \
            in text, text

    def test_an_unruled_label_table_keeps_its_rows(self):
        """An unruled label table keeps its rows."""
        book = _book(F.unruled_label_table_page)

        text = _whole_text(book)
        assert text.index("Cabinet features") < text.index("Northern"), text
        assert text.index("Northern") < text.index("Configurations"), text
        assert text.index("Attachments") < text.index('drawer Blue'), text
        assert "columns_reordered" not in (book.page_reasons(0) or [])

    def test_a_ragged_single_column_page_is_not_shredded(self):
        """A ragged single column page is not shredded."""
        book = _book(F.ragged_single_column_page)

        text = _whole_text(book)
        assert text.index("The old shelving was removed.") < text.index('The sample'), text
        assert 'wooden-so' in text
        assert 'woodenso' not in text
        assert "columns_reordered" not in (book.page_reasons(0) or [])
        assert book.conservation.ok, book.conservation.to_dict()

    def test_an_ordinary_page_keeps_its_order(self):
        """The single-column control: nothing about a normal page may change."""
        book = _book(F.prose_page)

        text = _whole_text(book)
        assert text.startswith('Every shelf in this imaginary workshop')
        assert "multi_column" not in (book.page_reasons(0) or [])


# ------------------------------------------------------------- ruled scan rows

def _probed_book(*builders):
    """The deterministic pass with the pipeline's pixel probe wired -- the seam
    pipeline.run builds one ScanPixelProbe per page for, where a scan's ruling
    is measured from the page raster rather than from a vector path count."""
    doc = F.new_doc()
    for build in builders:
        build(doc)
    try:
        raw_pages = extract.read_pages(doc)
        style = skeleton.book_style(raw_pages)
        skeletons = [
            skeleton.page_skeleton(
                raw, style,
                pixel_probe=extract.ScanPixelProbe(
                    doc, raw.pno,
                    mask=[ln.bbox for blk in raw.text_blocks
                          for ln in blk.lines]))
            for raw in raw_pages]
        return assemble.assemble(skeletons, style, raw_pages)
    finally:
        doc.close()


class TestRuledScanRows(object):
    """A ruled register read off its raster: the rows are the relationships."""

    def test_a_ruled_register_scan_keeps_each_record_with_its_grant(self):
        """Held-out case A's image-only defect (readiness-20260915): the OCR
        layer survived whole, yet the reading printed all four records and then
        all four grants -- the ruling that pairs them lives in the page raster,
        where the vector ruled-grid veto cannot see it, and the prose cells end
        their sentences, which the mirror-row guard reads as independent
        columns. The ruling is the source's own evidence: each record must be
        followed by its own grant, the way the born-digital page already reads."""
        book = _probed_book(F.scan_ruled_register_page)

        text = _whole_text(book)
        positions = [text.index(anchor) for pair in F.REGISTER_PAIR_ANCHORS
                     for anchor in pair]
        assert positions == sorted(positions), \
            "every record must be followed by its own grant, in row order: " + text
        assert book.conservation.ok, book.conservation.to_dict()

    def test_an_unruled_register_scan_is_not_read_as_rows(self):
        """The pixel gate, not the text shape, decides: the same register wording
        on a raster WITHOUT ruling cannot prove its rows, so the columns keep the
        status-quo reading rather than a guessed table. Aligned prose alone is
        not a table."""
        book = _probed_book(F.scan_unruled_register_page)

        text = _whole_text(book)
        records = [text.index(pair[0]) for pair in F.REGISTER_PAIR_ANCHORS]
        grants = [text.index(pair[1]) for pair in F.REGISTER_PAIR_ANCHORS]
        assert max(records) < min(grants), \
            "no ruling measured: the status-quo column reading keeps: " + text
        assert book.conservation.ok, book.conservation.to_dict()

    def test_independent_columns_on_a_scan_stay_independent(self):
        """The control that must not move (case B's shape): two headed,
        independent prose accounts whose raster carries one deck rule and a
        vertical divider -- no row ruling -- keep reading down their own
        columns; no cross-column pairing is fabricated."""
        book = _probed_book(F.scan_independent_columns_page)

        text = _whole_text(book)
        left = [text.index(anchor) for anchor in F.INDEPENDENT_LEFT_ANCHORS]
        right = [text.index(anchor) for anchor in F.INDEPENDENT_RIGHT_ANCHORS]
        assert left == sorted(left) and right == sorted(right), text
        assert max(left) < min(right), \
            "independent accounts read down their own columns: " + text
        assert book.conservation.ok, book.conservation.to_dict()


# --------------------------------------------------------- artwork in a page scan

#: The body size is a book-level measurement, and a panel page on its own is mostly
#: giant lettering -- so the panel fixtures follow an ordinary prose page, the way
#: ordinary surrounding prose settles the census before the panel arrives.
def _chart_book(builder):
    return _book(F.prose_page, builder)


class TestScanArtwork(object):
    """Diagrams that live only inside a page scan are cropped from the source."""

    def test_a_chart_band_becomes_a_figure(self, tmp_path):
        """A panel band becomes a figure."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        names = [name.rsplit("/", 1)[-1] for name in _epub_images(result.path)
                 if "fig_p0001" in name]
        assert len(names) == 1 and names[0].rsplit(".",1)[0] == "fig_p0001_0", names
        assert any(el.kind == "fig" and el.pno == 1 for el in book.elements)

    def test_the_charts_ocr_labels_do_not_read_as_prose(self):
        """'BOX PANEL o 7 -3 z' is lettering inside the diagram: it rides with the
        artwork and is accounted there, never duplicated into the reading flow."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))

        assert "BOX PANEL" not in _whole_text(book)
        assert book.artwork, "the labels are accounted, not silently discarded"
        assert "BOX PANEL" in " ".join(a["text"] for a in book.artwork)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_caption_next_to_the_chart_stays_its_caption(self, tmp_path):
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        fragment = build_epub.page_fragment(book, 1)
        assert "<figure>" in fragment
        assert "Figure 7.4 - Cabinet Color Samples" not in fragment
        assert "original-p0001.xhtml#caption_0" in fragment
        _assert_caption_detail(result.path, 1)
        assert "figcaption" in fragment

    def test_the_crop_is_the_chart_and_not_the_prose(self):
        """Close-to-body boundary: the crop stops where the prose starts, so no
        body line is amputated into the picture and none is lost from the text."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))

        figure = next(f for f in book.figures if f["pno"] == 1)
        bottom = figure["bbox"][3]
        first_prose = next(el for el in book.elements
                           if el.kind == "p" and el.pno == 1)
        assert bottom <= first_prose.bbox[1] + 2.0, (figure, first_prose.bbox)
        assert first_prose.text.startswith('Every shelf in this imaginary workshop')
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_sidebar_wheel_beside_the_prose_is_recovered(self, tmp_path):
        """A sidebar wheel beside the prose is recovered."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_sidebar_figure_page(
                       d, F.art_png(F.SIDEBAR_WHEEL_ART)))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        names = [name.rsplit("/", 1)[-1] for name in _epub_images(result.path)
                 if "fig_p0001" in name]
        assert len(names) == 1 and names[0].rsplit(".",1)[0] == "fig_p0001_0", names
        fragment = build_epub.page_fragment(book, 1)
        assert "Plate 45 - Example Workshop A" not in fragment
        assert "original-p0001.xhtml#caption_0" in fragment
        _assert_caption_detail(result.path, 1)
        assert "figcaption" in fragment
        assert "The cabinet was made for A.B." in _whole_text(book)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_crop_of_a_real_chart_is_not_blank(self, tmp_path):
        """Source image decode integrity: what lands in the EPUB is the ink."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        names = _epub_images(result.path)
        assert names, "no image reached the EPUB"
        with zipfile.ZipFile(result.path) as zf:
            data = zf.read(names[0])
        assert _ink_share(data) > 0.05, "the recovered figure decoded blank"

    def test_a_blank_scan_leaf_is_not_emitted_as_artwork(self, tmp_path):
        """The synthetic blank control contains only empty paper. Emitting it as a
        recovered figure would add an empty image to the output; blank leaves
        are left out and the omission is disclosed."""
        white = F.solid_png(colour=(255, 255, 255))
        doc = _doc(lambda d: F.image_only_page(d, white))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        assert _epub_images(result.path) == [], _epub_images(result.path)
        assert result.sidecar.get("figures_blank_dropped") == 1, result.sidecar

    def test_a_textless_plate_with_ink_is_kept(self, tmp_path):
        """The other control: a textless page that really is a plate stays."""
        doc = _doc(lambda d: F.image_only_page(d, F.solid_png()))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        assert len(_epub_images(result.path)) == 1, _epub_images(result.path)

    def test_a_vector_diagram_becomes_a_figure_crop(self, tmp_path):
        """Born-digital counterpart: a diagram drawn as vector paths is clustered
        into a figure region and cropped from the page render."""
        doc = _doc(F.vector_diagram_page)
        try:
            book = assemble.deterministic_book(doc)
            figures = [f for f in book.figures if f["pno"] == 0]
            assert len(figures) == 1, book.figures
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        names = _epub_images(result.path)
        assert names, "no image reached the EPUB"
        with zipfile.ZipFile(result.path) as zf:
            assert _ink_share(zf.read(names[0])) > 0.01

    def test_compact_vector_circle_beside_prose_keeps_its_diagram(self, tmp_path):
        """A circle built from a few large paths and thin marks stays visible.

        The printed diagram is beside prose, not an embedded bitmap. Breaks if
        figure discovery counts only paths wide and tall enough to be boxes.
        """
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((162, 322), 'The circle divides into equal sections.', fontsize=11)
        page.insert_text((162, 345), 'The diagram explains the paragraph.', fontsize=11)
        page.insert_text((162, 520), 'The next prose paragraph remains text.', fontsize=11)
        center = pymupdf.Point(378, 432)
        page.draw_circle(center, 61, color=(0, 0, 0), width=.5)
        page.draw_circle(center, 31, color=(0, 0, 0), width=.5)
        for dx, dy in ((61, 0), (0, 61), (53, 31), (53, -31), (31, 53), (31, -53)):
            page.draw_line((center.x - dx, center.y - dy),
                           (center.x + dx, center.y + dy), color=(0, 0, 0), width=.5)
        for offset in range(-7, 8):
            page.draw_line((center.x + offset * 4, 499),
                           (center.x + offset * 4 + 2, 502), color=(0, 0, 0), width=.5)
        try:
            book = assemble.deterministic_book(doc)
            figures = [f for f in book.figures if f['pno'] == 0]
            assert len(figures) == 1, book.figures
            assert any('diagram explains' in e.text for e in book.elements)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()
        names = _epub_images(result.path)
        assert len(names) == 1, names
        with zipfile.ZipFile(result.path) as zf:
            assert _ink_share(zf.read(names[0])) > 0.01

    def test_compact_vector_ignores_long_rule_touching_its_edge(self, tmp_path):
        """A page rule can touch a diagram without owning the intervening prose."""
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        prose = 'Unrelated left column prose remains reflowable.'
        page.insert_text((45, 405), prose, fontsize=9)
        center = pymupdf.Point(378, 432)
        for radius in (61, 31):
            page.draw_circle(center, radius, color=(0, 0, 0), width=.5)
        for dx, dy in ((61, 0), (0, 61), (53, 31), (53, -31), (31, 53), (31, -53)):
            page.draw_line((center.x - dx, center.y - dy),
                           (center.x + dx, center.y + dy), color=(0, 0, 0), width=.5)
        for offset in range(-7, 8):
            page.draw_line((center.x + offset * 4, 499),
                           (center.x + offset * 4 + 2, 502), color=(0, 0, 0), width=.5)
        page.draw_line((40, 500), (570, 500), color=(0, 0, 0), width=.5)
        try:
            book = assemble.deterministic_book(doc)
            figures = [f for f in book.figures if f['pno'] == 0]
            assert len(figures) == 1, book.figures
            assert not pymupdf.Rect(figures[0]['bbox']).intersects(page.search_for(prose)[0])
            assert prose in _whole_text(book)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()
        with zipfile.ZipFile(result.path) as zf:
            chapter = '\n'.join(zf.read(n).decode() for n in zf.namelist()
                                if re.fullmatch(r'OEBPS/ch\d+\.xhtml', n))
            assert prose in chapter
            assert len(_epub_images(result.path)) == 1

    def test_short_vector_rules_beside_prose_do_not_become_a_diagram(self):
        """Dense small marks without a circle are page furniture, not artwork."""
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((162, 322), 'An ordinary paragraph survives.', fontsize=11)
        for offset in range(35):
            y = 360 + offset * 4
            page.draw_line((316, y), (319, y + 2), color=(0, 0, 0), width=.5)
        try:
            book = assemble.deterministic_book(doc)
        finally:
            doc.close()
        assert not book.figures
        assert any('ordinary paragraph' in e.text for e in book.elements)

    def test_a_lone_display_line_is_a_chapter_opening_not_a_figure(self, tmp_path):
        """A lone display line is a chapter opening not a figure."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chapter_opening_page(d, F.art_png()))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        assert "CHAPTER 4" in _whole_text(book)
        assert [name for name in _epub_images(result.path)
                if "fig_p0001" in name] == []

    def test_a_body_sized_date_line_is_not_absorbed_as_artwork(self, tmp_path):
        """A body sized date line is not absorbed as artwork."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_date_tail_page(d, F.art_png()))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        assert "February 2042" in _whole_text(book)
        assert not any("February" in a["text"] for a in book.artwork)
        assert [name for name in _epub_images(result.path)
                if "fig_p0001" in name] == []

    def test_a_full_bleed_cover_is_one_figure_not_strips(self, tmp_path):
        """A full bleed cover is one figure not strips."""
        doc = _doc(F.prose_page,
                   lambda d: F.cover_plate_page(
                       d, F.art_png((8.0, 8.0, 470.0, 660.0))))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        figures = [f for f in book.figures if f["pno"] == 1 and f.get("full_page")]
        assert figures, ("the cover is not one whole figure", book.figures)
        assert [name for name in _epub_images(result.path)
                if "fig_p0001" in name], "the cover image did not ship"
        assert "SYNTHETIC TYPOGRAPHY" in _whole_text(book)
        assert "Test Author" in _whole_text(book)

    def test_a_text_page_with_a_page_raster_is_not_a_plate(self):
        """The control: a page full of prose with a scan behind it is text, not
        a cover -- the full-page raster stays the background it is."""
        doc = _doc(F.prose_page,
                   lambda d: F.cover_plate_page(
                       d, F.art_png((8.0, 8.0, 470.0, 660.0))))
        try:
            for line in F.PROSE_LINES[:10]:
                F._put(doc[1], F.LEFT, 300.0 + F.PROSE_LINES.index(line) * 15.0,
                       line)
            book = assemble.deterministic_book(doc)
        finally:
            doc.close()

        assert not [f for f in book.figures
                    if f["pno"] == 1 and f.get("full_page")], book.figures

    def test_prose_words_are_still_conserved_with_the_artwork_moved(self):
        """The conservation contract, stated: every source word is accounted in
        body, notes, furniture, captions, or preserved artwork -- never silently
        discarded."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))

        report = book.conservation
        assert report.ok, report.to_dict()
        assert report.source_total == report.output_total, report.to_dict()

class TestPlateAndCaptionFidelity:
    def test_noisy_ocr_does_not_split_a_full_bleed_plate(self):
        # Linux OCR can invent many lines over artwork. They are retained in
        # the facsimile's accounting, rather than promoted to reading prose.
        from cps.services.reflow import source
        import types
        words = []
        for i, text in enumerate(['TITLE', 'AUTHOR'] + ['noise'] * 12):
            words.append(types.SimpleNamespace(text=text, confidence=96 if i < 2 else 10,
                                  bbox=(40, 40+i*25, 180, 55+i*25),
                                  block=i, paragraph=1, line=1))
        import types
        original = extract.RawPage(pno=0, width=500, height=700, blocks=[],
            images=[extract.Image(bbox=(0, 0, 500, 700), area_ratio=1.0)])
        result = types.SimpleNamespace(words=words, page_index=0, width=500, height=700)
        raw = source._page_from_ocr(result, original)
        class Probe:
            def coverage(self, rect): return 1.0
        style = skeleton.book_style([raw])
        skel = skeleton.page_skeleton(raw, style, pixel_probe=Probe())
        book = assemble.assemble([skel], style, [raw])
        assert len(book.figures) == 1 and book.figures[0]['full_page']
        assert "noise" not in _whole_text(book)
        assert "TITLE" not in _whole_text(book) and "AUTHOR" not in _whole_text(book)
        # The plate now owns all its OCR lettering; don't repeat recognized
        # titles as additional chapters after the intact original cover.
        lettering = ' '.join(item['text'] for item in book.artwork)
        assert all(token in lettering for token in ['TITLE','AUTHOR','noise'])
        assert book.conservation.ok
        # Confident prose on the same background must not become a plate.
        for word in words: word.confidence = 96
        raw = source._page_from_ocr(result, original)
        skel = skeleton.page_skeleton(raw, style, pixel_probe=Probe())
        assert not any(r.kind == 'figure' and r.image and r.image.full_page
                       for r in skel.regions)

    def test_caption_title_follows_geometry_not_extractor_block_order(self):
        caption = _line('figure 68.', 94, 330, 130, 338, 5.6)
        title = _line('UPPER- AND LOWER-EDGE OFFSET PANELS', 94, 341, 230, 347, 6)
        prose = _line('Directory explains the separate paragraph.', 250, 342, 450, 351, 9)
        explanation = _line('The explanation continues over several lines.', 94, 355, 235, 362, 9)
        candidate = skeleton.Region(kind='figure', bbox=(84,161,249,341))
        blocks = [(_block(i,[ln]),[ln])
                  for i,ln in enumerate([title,prose,caption,explanation])]
        import types
        rest = skeleton._attach_caption(candidate, blocks, types.SimpleNamespace(body_size=9))
        assert [ln.text for ln in candidate.caption_lines] == [caption.text,title.text]
        assert [ln.text for _,lines in rest for ln in lines] == [prose.text, explanation.text]

    def test_split_scan_caption_keeps_printed_gap_and_qualifies_transcription(self, tmp_path):
        caption = _line('figure 9.', 90, 330, 135, 338, 5)
        left = _line('(ANCIENT', 95, 341.3, 140, 347, 5)
        right = _line('modern)', 149, 341, 189, 347, 5.6)
        # Same printed baseline, different bounding tops/faces; a missing glyph
        # can occupy the gap. No invented token may fill it.
        left.spans[0].origin_y = right.spans[0].origin_y = 346
        candidate = skeleton.Region(kind='figure', bbox=(84,161,249,338))
        blocks = [(_block(i,[ln]),[ln]) for i,ln in enumerate([right,caption,left])]
        import types
        rest, art = skeleton._absorb_figure_content(blocks, [candidate],
                                                   types.SimpleNamespace(body_size=9))
        assert not rest
        assert candidate.bbox[3] >= right.bbox[3], 'printed caption was cropped away'
        skel = skeleton.PageSkeleton(pno=0,width=500,height=700,regions=[candidate])
        raw = extract.RawPage(pno=0,width=500,height=700,
                             blocks=[blk for blk,_ in blocks],images=[])
        style = skeleton.book_style([raw])
        book = assemble.assemble([skel],style,[raw])
        html = build_epub.page_fragment(book,0)
        assert 'ANCIENT' not in html and 'modern' not in html
        assert 'original-p0000.xhtml#caption_0' in html
        assert 'transcription uncertain' in html
        assert book.conservation.ok
        with pymupdf.open() as doc:
            page = doc.new_page(width=500, height=700)
            for line in (caption, left, right):
                page.insert_text((line.bbox[0], line.spans[0].origin_y), line.text, fontsize=line.spans[0].size)
            result = _build(book, tmp_path, doc)
        _assert_caption_detail(result.path, 0)


    def test_full_italic_caption_stays_with_figure_across_extractor_blocks(self):
        number = _line('figure 68.', 94, 330, 130, 338, 5.6)
        title = _line('UPPER AND LOWER OFFSET PANELS', 94, 341, 230, 347, 6)
        caption = [_line('Caption line %d continues the explanation.' % n,
                         94, 353+n*9, 235, 359.5+n*9, 6.5) for n in range(11)]
        for line in caption:
            line.spans[0].flags = extract.FLAG_ITALIC
        right = _line('Directory explains the right panel.', 250, 355, 383, 364, 9)
        bottom = _line('The full-width bottom continuation stays prose.', 90, 461, 383, 470, 9)
        lines = [title, right, *caption[5:], bottom, number, *caption[:5]]
        blocks = [(_block(i,[ln]),[ln]) for i,ln in enumerate(lines)]
        candidate = skeleton.Region(kind='figure',bbox=(84,161,249,341))
        import types
        rest, _ = skeleton._absorb_figure_content(blocks,[candidate],
                                                  types.SimpleNamespace(body_size=9))
        assert [ln.text for ln in candidate.caption_lines] == [number.text,title.text] + [ln.text for ln in caption]
        assert [ln.text for _,ls in rest for ln in ls] == [right.text,bottom.text]
        assert candidate.bbox[3] <= number.bbox[1], 'caption must not distort the panel crop'

    def test_scan_caption_uses_source_pixels_without_unverified_primary_text(self, tmp_path):
        number = _line('Figure 3. Original caption.', 50, 330, 190, 338, 6)
        explanation = _line('Unverified symbols appear in this explanation.', 50, 345, 190, 353, 6.5)
        explanation.spans[0].flags = extract.FLAG_ITALIC
        figure = skeleton.Region(kind='figure',bbox=(40,100,210,325),
                                 caption_lines=[number,explanation])
        raw = extract.RawPage(pno=0,width=500,height=700,
                             blocks=[_block(0,[number,explanation])],images=[])
        skel = skeleton.PageSkeleton(pno=0,width=500,height=700,is_scan=True,regions=[figure])
        book = assemble.assemble([skel],skeleton.book_style([raw]),[raw])
        caption=next(el for el in book.pages[0] if el.kind=='caption')
        assert caption.caption_uncertain, 'a native scan layer does not verify the printed glyphs'
        html=build_epub.page_fragment(book,0)
        assert 'Unverified symbols' not in html and 'Figure 3.' not in html
        assert 'original_p0000_caption_0.jpg' in html
        assert 'original-p0000.xhtml#caption_0' in html
        assert book.conservation.ok
        with pymupdf.open() as doc:
            page=doc.new_page(width=500,height=700)
            page.insert_text((50,338), 'Figure 3. Original caption.', fontsize=6)
            output=_build(book,tmp_path,doc)
        assert not build_epub.validate(output.path)
        with zipfile.ZipFile(output.path) as z:
            assert any(z.read(n) for n in z.namelist()
                       if n.startswith('OEBPS/images/original_p0000_caption_0.')
                       and n.endswith(('.jpg','.png')))
            chapters=' '.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'Unverified symbols' not in chapters


@pytest.mark.parametrize('side', ['right', 'left'])
def test_sidebar_chart_survives_subglyph_column_edge_drift(side):
    """A second narrow paragraph must not cut through the source circle merely
    because its extracted line ends protrude by less than a glyph's width."""
    from types import SimpleNamespace
    run=[(50,60+i*15,250,72+i*15) for i in range(8)]
    neighbor=[(70,195+i*15,250.8,207+i*15) for i in range(7)]
    lower=[(50,315,500,327)]
    if side=='left':
        flip=lambda b:(550-b[2],b[1],550-b[0],b[3])
        run,neighbor,lower=([flip(b) for b in group] for group in (run,neighbor,lower))
    rows=[(60,177,run),(195,297,neighbor),(315,327,lower)]
    caption=_line('Plate 3 - Complete source wheel',310 if side=='right' else 60,
                  299,490 if side=='right' else 240,312,10)
    found=[]
    skeleton._side_territory(run,side,rows,50,500,
                            SimpleNamespace(width=550,height=800,
                                text_blocks=[_block(0,[caption])]),450,
                            lambda x0,y0,x1,y1,*a,**kw:found.append((x0,y0,x1,y1)))
    assert len(found)==1
    box=found[0]
    # The original source wheel occupies y70..290. Its lower circumference
    # cannot be inferred from its upper half: all of those pixels must survive.
    assert box[1]<=70 and box[3]>=290, box
    assert box[3]<=315, 'the real full-width continuation must remain prose'
    if side=='right':assert box[0]>=250.8, 'do not crop a sliver of adjacent prose'
    else:assert box[2]<=299.2


@pytest.mark.parametrize('intrusion', [5.0, 100.0])
def test_sidebar_growth_stops_at_real_prose_even_beside_a_thin_gutter(intrusion):
    from types import SimpleNamespace
    run=[(50,60+i*15,250,72+i*15) for i in range(8)]
    crossing=(70,195,250+intrusion,207)
    found=[]
    skeleton._side_territory(run,'right',[(60,177,run),(195,207,[crossing])],
                            50,500,SimpleNamespace(width=550,height=800),450,
                            lambda x0,y0,x1,y1,*a,**kw:found.append((x0,y0,x1,y1)))
    assert found and found[0][3]<=195, 'neighboring prose cannot be swallowed as artwork'


def test_separate_caption_units_do_not_expand_into_one_sidebar_chart():
    from types import SimpleNamespace
    run=[(50,60+i*15,250,72+i*15) for i in range(8)]
    neighbor=[(70,195+i*15,250.8,207+i*15) for i in range(12)]
    captions=[_line('Plate 1 - First independent wheel',300,180,490,192,10),
              _line('Plate 2 - Second independent wheel',300,380,490,392,10)]
    found=[]
    skeleton._side_territory(run,'right',[(60,177,run),(195,372,neighbor)],50,500,
        SimpleNamespace(width=550,height=800,text_blocks=[_block(0,captions)]),450,
        lambda x0,y0,x1,y1,*a,**kw:found.append((x0,y0,x1,y1)))
    assert found and found[0][3]<=195, 'independent caption units must not merge'


def test_caption_above_a_sidebar_run_does_not_reassign_the_previous_chart():
    from types import SimpleNamespace
    run=[(50,320+i*15,250,332+i*15) for i in range(8)]
    above=(70,195,250.8,207)
    caption=_line('Plate 1 - Earlier independent wheel',300,150,490,162,10)
    found=[]
    skeleton._side_territory(run,'right',[(195,207,[above]),(320,437,run)],50,500,
        SimpleNamespace(width=550,height=800,text_blocks=[_block(0,[caption])]),450,
        lambda x0,y0,x1,y1,*a,**kw:found.append((x0,y0,x1,y1)))
    assert found and found[0][0]==250, 'an earlier panel caption is not evidence for this crop'


@pytest.mark.parametrize("caption_count", [2, 3])
def test_stacked_sidebar_charts_are_complete_disjoint_caption_units(caption_count):
    """Overlapping prose seeds cannot duplicate wheels or use short caption width
    as the right edge of wider printed artwork."""
    from types import SimpleNamespace
    doc=pymupdf.open();page=doc.new_page(width=500,height=720)
    for y in (130,330,530):page.draw_circle((350,y),70,width=1.5)
    body=[]
    for start,count,right in [(60,9,230),(205,11,230.6),(395,14,230.3)]:
        body.extend(_line('Ordinary surrounding prose remains in its column.',
                          50,start+i*13,right,start+i*13+11,12) for i in range(count))
    captions=[_line('Plate %d - Unit %s'%(i+1,chr(65+i)),310,205+i*200,375,217+i*200,9)
              for i in range(caption_count)]
    blocks=[(_block(0,body),body),(_block(1,captions),captions)]
    raw=SimpleNamespace(width=500,height=720,text_blocks=[b for b,_ in blocks])
    probe=extract.ScanPixelProbe(doc,0,mask=[ln.bbox for _,ls in blocks for ln in ls])
    try:
        figures=skeleton._scan_figures(blocks,raw,SimpleNamespace(body_size=12),probe)
        remaining,_=skeleton._absorb_figure_content(blocks,figures,SimpleNamespace(body_size=12))
    finally:doc.close()
    figures.sort(key=lambda r:r.bbox[1])
    assert len(figures)==3,[(f.bbox,[l.text for l in f.caption_lines]) for f in figures]
    for i,(figure,y) in enumerate(zip(figures,(130,330,530))):
        x0,y0,x1,y1=figure.bbox
        assert x0<=280 and x1>=420 and y0<=y-70 and y1>=y+70, figure.bbox
        assert [ln.text for ln in figure.caption_lines]==([captions[i].text] if i < caption_count else [])
    assert all(a.bbox[3]<=b.bbox[1] for a,b in zip(figures,figures[1:])), 'duplicate source territory'
    assert [ln for _,lines in remaining for ln in lines]==body


def test_adjacent_column_caption_is_not_a_footnote_continuation():
    """A caption at the same page-bottom height as a real note remains with its
    figure; a continuation in the note's own column remains a note."""
    from types import SimpleNamespace
    prose=_line('Normal body prose establishes the larger face.',50,400,240,413,12)
    note=_line('3 Author, Printed Book, page 4.',50,631,190,641,9)
    continuation=_line('The same source continues here.',50,644,190,654,9)
    caption=_line('Plate 7 - An independent printed unit',280,636,440,646,9)
    raw=extract.RawPage(pno=0,width=500,height=720,
        blocks=[_block(0,[prose]),_block(1,[note,caption,continuation])])
    skel=skeleton.PageSkeleton(pno=0,width=500,height=720)
    body,notes=skeleton._split_off_notes(raw,SimpleNamespace(body_size=12),skel)
    assert len(notes)==1 and notes[0].number==3
    assert [ln.text for ln in notes[0].lines]==[note.text,continuation.text]
    assert caption in [ln for block in body for ln in block.lines]


def test_caption_wording_in_the_note_column_remains_a_note_continuation():
    from types import SimpleNamespace
    note=_line('3 Author, Printed Book, page 4.',50,631,190,641,9)
    continuation=_line('Plate 7 - referenced by this note.',50,644,190,654,9)
    raw=extract.RawPage(pno=0,width=500,height=720,blocks=[_block(0,[note,continuation])])
    skel=skeleton.PageSkeleton(pno=0,width=500,height=720)
    body,notes=skeleton._split_off_notes(raw,SimpleNamespace(body_size=12),skel)
    assert not body
    assert len(notes)==1 and [ln.text for ln in notes[0].lines]==[note.text,continuation.text]


@pytest.mark.parametrize("real_intrusion", [False, True])
def test_a_line_above_a_sidebar_chart_cannot_cut_its_inner_circumference(real_intrusion):
    """A ragged last line above the wheel joins the side-channel prose run but
    does not define the wheel's inward edge farther down the printed page."""
    from types import SimpleNamespace
    doc=pymupdf.open();page=doc.new_page(width=500,height=720)
    page.draw_circle((350,220),70,width=1.5)
    body=[_line('The preceding full-width paragraph ends here.',50,100,320,111,12)]
    body.extend(_line('Adjacent prose in a narrower column.',50,113+i*13,230,124+i*13,12)
                for i in range(15))
    if real_intrusion:
        body.append(_line("Actual prose intrudes into this territory.",50,190,300,201,12))
    caption=_line('Figure 4 - Complete circular diagram',350,310,440,322,9)
    blocks=[(_block(0,body),body),(_block(1,[caption]),[caption])]
    raw=SimpleNamespace(width=500,height=720,text_blocks=[b for b,_ in blocks])
    probe=extract.ScanPixelProbe(doc,0,mask=[ln.bbox for _,ls in blocks for ln in ls])
    try:
        figures=skeleton._scan_figures(blocks,raw,SimpleNamespace(body_size=12),probe)
        skeleton._absorb_figure_content(blocks,figures,SimpleNamespace(body_size=12))
    finally:doc.close()
    assert len(figures)==1
    box=figures[0].bbox
    if real_intrusion:
        assert box[0]>=300, "real prose vetoes inward expansion"
        return
    assert box[0]<=280 and box[2]>=420 and box[1]<=150 and box[3]>=290,box
    assert box[1]>111, 'the preceding line stays outside the figure crop'
    assert [ln.text for ln in figures[0].caption_lines]==[caption.text]


def test_composite_caption_keeps_each_printed_title_with_its_subtitle():
    """A two-wheel row reads each source caption in full before its neighbor."""
    left=_line('Figure 2. Rope',100,240,190,246,6)
    right=_line('Figure 3. Oak',250,239.5,340,245.5,6)
    left_sub=_line('Blue, Crosswise, White',110,249,185,256,7)
    right_sub=_line('Orange, Olive, Mahogany',260,249,335,256,7)
    region=skeleton.Region(kind='figure',reason='scan_figure_band',bbox=(90,50,360,260),
                           caption_lines=[right,left,left_sub,right_sub])
    skeleton._absorb_figure_content([], [region], skeleton.BookStyle(body_size=12))
    assert [ln.text for ln in region.caption_lines]==[left.text,left_sub.text,right.text,right_sub.text]


def test_uncaptioned_original_figure_is_accessible_as_content():
    from lxml import etree
    figure=etree.fromstring(build_epub._figure_html(5,0,''))
    assert figure.find('img').get('alt')=='Original figure from PDF page 6'
    assert figure.find('figcaption').get('class')=='reflow-no-caption'


def test_a_shared_composite_caption_is_not_assigned_to_one_panel():
    left=_line('Figure 2. Rope',100,240,190,246,6)
    right=_line('Figure 3. Oak',250,240,340,246,6)
    shared=_line('An explanation applying to both diagrams',100,249,340,256,7)
    lines=[left,right,shared]
    region=skeleton.Region(kind='figure',reason='scan_figure_band',bbox=(90,50,360,260),caption_lines=lines)
    skeleton._absorb_figure_content([], [region], skeleton.BookStyle(body_size=12))
    assert region.caption_lines==lines

class TestUncertainOCRRegion:
    def test_uncertain_paragraph_uses_pixels_including_confident_neighbors(self, tmp_path):
        from cps.services.reflow import source
        import types
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((30, 100), 'Source words remain visible.', fontsize=12)
        page.insert_text((30, 160), 'Reliable following paragraph.', fontsize=12)
        words = [types.SimpleNamespace(text=text, confidence=confidence,
                    bbox=(30+i*40, 88, 68+i*40, 103), block=1, paragraph=1,line=1)
                 for i,(text,confidence) in enumerate([('misread',96),('uncertain',40),('neighbor',94)])]
        words += [types.SimpleNamespace(text='Reliable following paragraph.',confidence=98,
                    bbox=(30,148,190,163),block=2,paragraph=1,line=1)]
        original = extract.RawPage(pno=0,width=300,height=400,blocks=[],
            images=[extract.Image(bbox=(0,0,300,400),area_ratio=1.0)])
        raw=source._page_from_ocr(types.SimpleNamespace(words=words,page_index=0,width=300,height=400),original)
        style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style)
        book=assemble.assemble([skel],style,[raw])
        try:
            assert 'misread' not in _whole_text(book)
            assert 'neighbor' not in _whole_text(book)
            assert 'Reliable following paragraph.' in _whole_text(book)
            assert book.conservation.ok
            assert any('misread' in a['text'] for a in book.artwork)
            output=tmp_path/'uncertain.epub';build_epub.build(book,str(output),doc=doc)
            assert build_epub.validate(str(output)) == []
            with zipfile.ZipFile(output) as z:
                body=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
                assert 'Source image' in body
                assert 'misread' not in body
                assert any('fig_p0000' in n for n in z.namelist())
        finally:doc.close()

    @pytest.mark.parametrize('font,uncertain,expected', [('ocr',False,False),('ocr',True,True),('Times-Roman',True,False)])
    def test_region_confidence_does_not_replace_native_or_confident_prose(self,font,uncertain,expected):
        lines=[_line('A complete useful paragraph.',30,100,220,114,12)]
        for span in lines[0].spans:span.font=font;span.uncertain=uncertain
        raw=extract.RawPage(pno=0,width=300,height=400,blocks=[_block(0,lines)])
        style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style)
        book=assemble.assemble([skel],style,[raw])
        assert bool([f for f in book.figures if f['found']=='ocr_uncertain_region']) is expected
        assert ('A complete useful paragraph.' in _whole_text(book)) is not expected
        assert book.conservation.ok

    def test_isolated_ocr_punctuation_is_source_pixels_without_a_score_cutoff(self):
        line=_line('*',120,100,128,110,10)
        line.spans[0].font='ocr'  # Even a confident recognition is not proof of a textual ornament.
        raw=extract.RawPage(pno=0,width=300,height=400,blocks=[_block(0,[line])])
        style=skeleton.book_style([raw]);book=assemble.assemble([skeleton.page_skeleton(raw,style)],style,[raw])
        assert '*' not in _whole_text(book)
        assert book.figures and book.artwork[0]['text']=='*'
        assert book.conservation.ok

    def test_uncertain_chart_lettering_stays_in_its_existing_source_figure(self):
        doc=_doc(F.prose_page,lambda d:F.scan_chart_band_page(d,F.art_png(F.CHART_BAND_ART)))
        try:
            raws=extract.read_pages(doc)
            for block in raws[1].text_blocks:
                for line in block.lines:
                    for span in line.spans:
                        span.font='ocr'
                        if span.text.strip()=='c':span.uncertain=True
            style=skeleton.book_style(raws)
            skels=[skeleton.page_skeleton(raw,style,pixel_probe=extract.ScanPixelProbe(doc,raw.pno,
                mask=[ln.bbox for b in raw.text_blocks for ln in b.lines])) for raw in raws]
            book=assemble.assemble(skels,style,raws)
            figures=[f for f in book.figures if f['pno']==1]
            assert len(figures)==1, figures
            assert book.conservation.ok
            assert any('c' in a['text'] for a in book.artwork)
        finally:doc.close()
