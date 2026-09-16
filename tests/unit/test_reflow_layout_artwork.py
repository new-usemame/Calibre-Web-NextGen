# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reading order in columns, and artwork that lives inside a page scan.

Two confirmed readiness defects live here, both measured on real shapes from the
downloaded corpus (state/pdf2epub-ai/readiness-20260915):

* a two-column page came back with left and right lines interleaved row by row,
  because a MuPDF block can hold same-baseline lines from both columns and the
  final region sort ordered everything by (y, x);
* a full-page scan's diagrams never became figures at all, because the skeleton
  only kept embedded images that were not full-page -- so book 567's day/night
  sect chart (index 220) and its natal wheels (indexes 485, 515) vanished while
  their OCR'd labels read on as prose.

Every test pairs the defect shape with a control that must NOT change: single
columns, ruled tables, blank leaves.
"""

import io
import re
import zipfile

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
    with zipfile.ZipFile(str(path)) as zf:
        return sorted(name for name in zf.namelist()
                      if "/images/" in name and name.rsplit(".", 1)[-1]
                      in ("jpg", "jpeg", "png"))


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
        day = text.index("Day charts are read first")
        leads = text.index("The sect light leads the day")
        night = text.index("Night charts are read second")
        leaves = text.index("The sect light leaves at night")
        mid = text.index("How the Remaining Planets Behave")
        saturn = text.index("Saturn behaves by day then")
        jupiter = text.index("Jupiter witnesses the day")
        mars = text.index("Mars behaves by night then")
        assert day < leads < night < leaves, text
        assert night < mid < saturn < jupiter < mars, text

    def test_a_paragraph_crossing_the_column_break_is_one_paragraph(self):
        """Column order is a reading order, not a chopping rule: the sentence that
        flows off the bottom of the left column continues at the top of the right."""
        book = _book(F.column_continuity_page)

        paragraphs = [el.text for el in book.elements if el.kind == "p"]
        assert paragraphs == [F.CONTINUITY_TEXT], paragraphs

    def test_a_column_wrap_heals_past_a_below_band_running_head(self):
        """Book 570 page 686, measured: the index entry wraps off the left
        column as 'Chaldean or-' and resumes atop the right as 'der 24-25',
        with the scan's running head between them at 11% of the page height
        -- below the 7.5% furniture band. The head is furniture, not the
        paragraph the entry continues, so the halves join and the wrap heals
        into a word the book prints whole ('the order', left column)."""
        raw = extract.RawPage(
            pno=0, width=403.0, height=606.0,
            blocks=[
                _block(1, [_line("SATURN—SATURN 1257",
                                 166.8, 61.2, 343.0, 67.0, 6.3)]),
                _block(2, [
                    _line("bound lord 129, 133-144, 209-211, 213-214,",
                          62.9, 78.7, 191.0, 85.9, 7.0),
                    _line("216-217, 231-232, 234-235, 550, 1163, Con-",
                          62.2, 88.8, 185.3, 95.3, 7.3),
                    _line("finement, isolation 797-798, 801, danger,",
                          62.6, 97.0, 186.2, 104.9, 8.0),
                    _line("destruction 400, 449, 976, darkness 175,",
                          62.2, 106.3, 171.4, 113.5, 8.7),
                    _line("800, death 370-371, 726, 798, 1141, the order",
                          62.2, 115.9, 190.0, 122.9, 7.0),
                    _line("of the spheres 222, planetary spheres/Chaldean or-",
                          58.8, 124.8, 184.6, 132.7, 11.0),
                ]),
                _block(3, [
                    _line("der 24-25 (fig. 1), 45, 222, 249, 1164, Pre-",
                          215.0, 78.7, 336.7, 86.4, 6.7),
                    _line("dominator 1056 n.14, 1067 n.10, reception",
                          215.0, 88.1, 340.6, 95.8, 8.3),
                    _line("239, rejoicing 89, 91-92, 96-104 (fig. 13),",
                          215.0, 97.2, 336.2, 104.9, 7.0),
                    _line("106-108, 151, 175, 538, 575, retrograde 986,",
                          215.3, 106.6, 340.6, 114.2, 10.7),
                    _line("Sagittarius 158, 181-182, 194, 370, 409,",
                          215.3, 116.2, 342.2, 123.4, 7.0),
                    _line("sect (hairesis) 27, 35, 53, 73-109.",
                          214.3, 125.0, 333.1, 132.7, 8.7),
                ]),
            ])

        book = _raw_book(raw)

        assert "planetary spheres/Chaldean order 24-25 (fig. 1), 45, 222," \
            in _whole_text(book), _whole_text(book)
        assert "SATURN—SATURN 1257" in book.furniture
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_counter_reads_a_reordered_page_in_reading_order(self):
        """Book 569 page 355's shape: the OCR layer hands both columns back as
        one-line blocks that interleave row by row. The reading proves the
        columns and joins each column's wraps; the counter must heal the same
        seams in the same proven order, or it reports the reading's own joins
        as losses (missing 'pect', added 'aspect')."""
        raw = extract.RawPage(
            pno=0, width=472.0, height=688.0,
            blocks=[
                _block(1, [_line("The Moon will not make any applying as­",
                                 93.0, 290.0, 223.0, 297.0, 7.0)]),
                _block(2, [_line("The Sun will not make any applying as­",
                                 253.0, 290.0, 385.0, 297.0, 7.0)]),
                _block(3, [_line("pects before it leaves its sign (Aries).",
                                 93.0, 302.0, 223.0, 309.0, 7.0)]),
                _block(4, [_line("pects for the next join. The Sun’s last as­",
                                 253.0, 302.0, 385.0, 309.0, 7.0)]),
                _block(5, [_line("Moon at 250 Aries made its last aspect to Saturn. Aspects",
                                 93.0, 314.0, 223.0, 321.0, 7.0)]),
                _block(6, [_line("pect while in Aries was a trine to Saturn at",
                                 253.0, 314.0, 385.0, 321.0, 7.0)]),
            ])

        book = _raw_book(raw)

        text = _whole_text(book)
        assert "applying aspects before it leaves its sign" in text, text
        assert "applying aspects for the next join" in text, text
        assert "last aspect while in Aries" in text, text
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_counter_sees_past_furniture_between_half_words(self):
        """Book 566 page 18's shape: a two-up spread whose running head sits
        between the left page's last line ('can’t com-') and the right page's
        first ('municate ...') in the raw block order. The reading removes the
        head and joins the halves; the counter must see past the same head --
        and still count its words."""
        raw = extract.RawPage(
            pno=0, width=842.0, height=595.0,
            blocks=[
                _block(1, [
                    _line("everything we communicate with a body will go",
                          110.0, 100.0, 380.0, 108.0, 9.0),
                    _line("wrong in it when our bodies are sick, we are",
                          110.0, 116.0, 388.0, 124.0, 9.0),
                    _line("unknown and ignored, are weak or incompetent, insecure, and can’t com-",
                          110.0, 530.0, 388.0, 540.0, 9.0),
                ]),
                _block(2, [_line("ASTROLOGY FOR YOURSELF 24",
                                 560.0, 60.0, 736.0, 66.0, 6.3)]),
                _block(3, [
                    _line("municate in the way we want. These functional",
                          457.0, 76.0, 736.0, 85.0, 9.0),
                    _line("conditions of the planets are meant to help us",
                          457.0, 92.0, 730.0, 100.0, 9.0),
                    _line("live full lives, and each one has its own voice.",
                          457.0, 108.0, 728.0, 116.0, 9.0),
                ]),
            ])

        book = _raw_book(raw)

        assert "can’t communicate in the way we want" in _whole_text(book), \
            _whole_text(book)
        assert "ASTROLOGY FOR YOURSELF 24" in book.furniture
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_background_photo_does_not_veto_the_two_pages(self):
        """Book 566 page 20's shape: the facsimile photograph crosses the gutter,
        and the two logical pages must still read left then right while the
        photograph is kept as the page's figure."""
        book = _book(lambda d: F.spread_with_background_photo_page(d, F.solid_png()))

        left = " ".join(line for line, _ in F.TWO_COLUMN_ROWS)
        right = " ".join(line for _, line in F.TWO_COLUMN_ROWS)
        text = re.sub(r"\s+", " ", _whole_text(book)).strip()
        assert text == left + " " + right, text
        assert any(el.kind == "fig" for el in book.elements), "the photo stays"
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_panel_beside_a_diagram_at_ocr_leading_recovers_the_diagram(self):
        """Book 569 page 547's panel: the OCR layer's loose leading read every
        line as its own band, no band was tall enough to measure the channel,
        and the diagram the panel describes never became a figure."""
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
        first_left = text.index("the account is given by Diodorus")
        second_left = text.index("Its second step follows the first")
        first_right = text.index("A different argument starts here")
        second_right = text.index("This evidence belongs with the next")
        assert first_left < second_left < first_right < second_right, text
        assert any(n.num == 24 for n in book.notes), book.notes
        assert not any("Diodorus Siculus" in el.text for el in book.elements)
        markers = [int(run[1]) for el in book.elements for run in el.runs
                   if run[0] in ("sup", "mark") and str(run[1]).isdigit()]
        assert 24 in markers

    def test_a_ruled_table_is_not_read_column_major(self):
        """The control: a ruled grid is a table, not prose columns. Reading it
        column-major destroys every row."""
        book = _book(F.ruled_table_page)

        text = _whole_text(book)
        assert text.index("Day") < text.index("Night") < text.index("Sun") \
               < text.index("Moon"), text

    def test_a_sign_pair_table_keeps_its_pairs_together(self):
        """Book 569 p547's shape: a mirror table is not two lists. Column-major
        would print every 'looks at' away from its 'perceives'; the rows are the
        unit, and no heuristic may guess the table into unrelated lists."""
        book = _book(F.sign_pair_table_page)

        text = _whole_text(book)
        assert text.index("GEMINI looks at LEO") < text.index("perceives"), text
        assert text.index("perceives") < text.index("TAURUS looks at VIRGO"), text
        assert "columns_reordered" not in (book.page_reasons(0) or [])
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_sign_pair_table_keeps_each_aspect_on_its_own_row(self):
        """The real page's third column is the aspect, and the letter-spaced
        lowercase cells make every row 'continue' into the next: the measured
        output printed 'Sextile t a u r u s looks at v ir g o' -- row one's
        aspect glued to row two's signs. Each row is one unit holding its pair
        and its own aspect, in print order, and the prose after the table does
        not fuse with the last row either."""
        book = _book(F.sign_pair_table_page)

        rows = [el.text for el in book.elements
                if "looks at" in (el.text or "") or "perceives" in (el.text or "")]
        assert rows == [
            "GEMINI looks at LEO l e o perceives g e m in i Sextile",
            "TAURUS looks at VIRGO v ir g o perceives t a u r u s Trine",
            "ARIES looks at LIBRA l i b r a perceives a r i e s Opposition",
            "SCORPIO looks at PISCES pis c e s perceives Sc o r pio Trine",
            "SAGITTARIUS looks at AQUARIUS a q u a r iu s perceives "
            "Sa g it t a r iu s Sextile",
        ], rows
        text = _whole_text(book)
        assert text.index("Sextile", text.index("SAGITTARIUS")) \
            < text.index("have looked at the transmission"), text
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
        """Book 569's zodiacal tables: a label column of short lines beside a
        content column is a table, and its rows must survive."""
        book = _book(F.unruled_label_table_page)

        text = _whole_text(book)
        assert text.index("Astronomical features") < text.index("Northern"), text
        assert text.index("Northern") < text.index("Characteristics"), text
        assert text.index("Rulerships") < text.index("domicile Venus"), text
        assert "columns_reordered" not in (book.page_reasons(0) or [])

    def test_a_ragged_single_column_page_is_not_shredded(self):
        """Book 565 page 158's shape: ragged short lines are not a column, and
        a hyphenated pair the book never prints whole stays exactly as printed --
        ``Christian-so``, never an invented ``Christianso``."""
        book = _book(F.ragged_single_column_page)

        text = _whole_text(book)
        assert text.index("Christianity was abolished.") < text.index("Mohammed"), text
        assert "Christian-so" in text
        assert "Christianso" not in text
        assert "columns_reordered" not in (book.page_reasons(0) or [])
        assert book.conservation.ok, book.conservation.to_dict()

    def test_an_ordinary_page_keeps_its_order(self):
        """The single-column control: nothing about a normal page may change."""
        book = _book(F.prose_page)

        text = _whole_text(book)
        assert text.startswith("The astrologers of this period")
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

#: The body size is a book-level measurement, and a chart page on its own is mostly
#: giant lettering -- so the chart fixtures follow an ordinary prose page, the way
#: the real corpus settles the census before the chart arrives.
def _chart_book(builder):
    return _book(F.prose_page, builder)


class TestScanArtwork(object):
    """Diagrams that live only inside a page scan are cropped from the source."""

    def test_a_chart_band_becomes_a_figure(self, tmp_path):
        """Index 220's shape: the sect chart must arrive as a figure crop, not as
        nothing plus its OCR wreckage reading as prose. The ink proof is the
        arbiter for everything around it: blank margins propose and drop."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        names = [name.rsplit("/", 1)[-1] for name in _epub_images(result.path)
                 if "fig_p0001" in name]
        assert names == ["fig_p0001_0.jpg"], names
        assert any(el.kind == "fig" and el.pno == 1 for el in book.elements)

    def test_the_charts_ocr_labels_do_not_read_as_prose(self):
        """'DAY CHART a 9 -5 e' is lettering inside the diagram: it rides with the
        artwork and is accounted there, never duplicated into the reading flow."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))

        assert "DAY CHART" not in _whole_text(book)
        assert book.artwork, "the labels are accounted, not silently discarded"
        assert "DAY CHART" in " ".join(a["text"] for a in book.artwork)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_caption_next_to_the_chart_stays_its_caption(self):
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.art_png(F.CHART_BAND_ART)))

        fragment = build_epub.page_fragment(book, 1)
        assert "<figure>" in fragment
        assert "Figure 7.4 - Sect as a Spectrum" in fragment
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
        assert first_prose.text.startswith("The astrologers of this period")
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_sidebar_wheel_beside_the_prose_is_recovered(self, tmp_path):
        """Index 485's shape: the natal wheel occupies the empty region beside a
        narrow prose column, with its caption printed under the wheel."""
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
        assert names == ["fig_p0001_0.jpg"], names
        fragment = build_epub.page_fragment(book, 1)
        assert "Chart 45 - John F. Kennedy Jr." in fragment
        assert "figcaption" in fragment
        assert "The native was the son of U.S." in _whole_text(book)
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
        """The blank control: source 344 is visibly empty paper. Emitting it as a
        recovered figure is how the baseline reached 13 images with nothing in
        them; blank leaves are left out and the omission is disclosed."""
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

    def test_a_lone_display_line_is_a_chapter_opening_not_a_figure(self, tmp_path):
        """Page 93's shape: 'CHAPTER 4' over white space is a chapter opening.
        The white space above a title is not figure territory, the display
        line must stay in the book's text, and the blank margins around it
        propose and drop -- nothing ships."""
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
        """Page 18's shape: a body-sized date line is the book's prose, not chart
        lettering -- it must stay in the reading flow, not vanish into a blank
        territory that is dropped at build time."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_date_tail_page(d, F.art_png()))
        try:
            book = assemble.deterministic_book(doc)
            result = _build(book, tmp_path, doc)
        finally:
            doc.close()

        assert "November 2016" in _whole_text(book)
        assert not any("November" in a["text"] for a in book.artwork)
        assert [name for name in _epub_images(result.path)
                if "fig_p0001" in name] == []

    def test_a_full_bleed_cover_is_one_figure_not_strips(self, tmp_path):
        """Book 567 index 0's shape after the cover's text is read: a title, an
        author, and the whole page is the design. The full-bleed plate is the
        page itself as one figure, with the text reading over it -- never
        strips measured around the title."""
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
        assert "HELLENISTIC ASTROLOGY" in _whole_text(book)
        assert "Chris Brennan" in _whole_text(book)

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
        assert "TITLE" in _whole_text(book) and "AUTHOR" in _whole_text(book)
        assert 'noise' in ' '.join(item['text'] for item in book.artwork)
        assert book.conservation.ok
        # Confident prose on the same background must not become a plate.
        for word in words: word.confidence = 96
        raw = source._page_from_ocr(result, original)
        skel = skeleton.page_skeleton(raw, style, pixel_probe=Probe())
        assert not any(r.kind == 'figure' and r.image and r.image.full_page
                       for r in skel.regions)

    def test_caption_title_follows_geometry_not_extractor_block_order(self):
        caption = _line('figure 68.', 94, 330, 130, 338, 5.6)
        title = _line('RIGHT- AND LEFT-SIDED ASPECT FIGURES', 94, 341, 230, 347, 6)
        prose = _line('Porphyry explains the separate paragraph.', 250, 342, 450, 351, 9)
        explanation = _line('The explanation continues over several lines.', 94, 355, 235, 362, 9)
        candidate = skeleton.Region(kind='figure', bbox=(84,161,249,341))
        blocks = [(_block(i,[ln]),[ln])
                  for i,ln in enumerate([title,prose,caption,explanation])]
        import types
        rest = skeleton._attach_caption(candidate, blocks, types.SimpleNamespace(body_size=9))
        assert [ln.text for ln in candidate.caption_lines] == [caption.text,title.text]
        assert [ln.text for _,lines in rest for ln in lines] == [prose.text, explanation.text]

    def test_split_scan_caption_keeps_printed_gap_and_qualifies_transcription(self):
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
        assert html.index('ANCIENT') < html.index('modern')
        assert 'reflow-uncertain' in html and 'printed caption' in html
        assert '(?)' in html
        assert book.conservation.ok


    def test_full_italic_caption_stays_with_figure_across_extractor_blocks(self):
        number = _line('figure 68.', 94, 330, 130, 338, 5.6)
        title = _line('RIGHT AND LEFT ASPECT FIGURES', 94, 341, 230, 347, 6)
        caption = [_line('Caption line %d continues the explanation.' % n,
                         94, 353+n*9, 235, 359.5+n*9, 6.5) for n in range(11)]
        for line in caption:
            line.spans[0].flags = extract.FLAG_ITALIC
        right = _line('Porphyry explains the right panel.', 250, 355, 383, 364, 9)
        bottom = _line('The full-width bottom continuation stays prose.', 90, 461, 383, 470, 9)
        lines = [title, right, *caption[5:], bottom, number, *caption[:5]]
        blocks = [(_block(i,[ln]),[ln]) for i,ln in enumerate(lines)]
        candidate = skeleton.Region(kind='figure',bbox=(84,161,249,341))
        import types
        rest, _ = skeleton._absorb_figure_content(blocks,[candidate],
                                                  types.SimpleNamespace(body_size=9))
        assert [ln.text for ln in candidate.caption_lines] == [number.text,title.text] + [ln.text for ln in caption]
        assert [ln.text for _,ls in rest for ln in ls] == [right.text,bottom.text]
        assert candidate.bbox[3] <= number.bbox[1], 'caption must not distort the chart crop'
