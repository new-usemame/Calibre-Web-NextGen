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


# --------------------------------------------------------- artwork in a page scan

#: The body size is a book-level measurement, and a chart page on its own is mostly
#: giant lettering -- so the chart fixtures follow an ordinary prose page, the way
#: the real corpus settles the census before the chart arrives.
def _chart_book(builder):
    return _book(F.prose_page, builder)


class TestScanArtwork(object):
    """Diagrams that live only inside a page scan are cropped from the source."""

    def test_a_chart_band_becomes_a_figure(self):
        """Index 220's shape: the sect chart must arrive as a figure crop, not as
        nothing plus its OCR wreckage reading as prose."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.solid_png()))

        figures = [f for f in book.figures if f["pno"] == 1]
        assert len(figures) == 1, book.figures
        assert any(el.kind == "fig" and el.pno == 1 for el in book.elements)

    def test_the_charts_ocr_labels_do_not_read_as_prose(self):
        """'DAY CHART a 9 -5 e' is lettering inside the diagram: it rides with the
        artwork and is accounted there, never duplicated into the reading flow."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.solid_png()))

        assert "DAY CHART" not in _whole_text(book)
        assert book.artwork, "the labels are accounted, not silently discarded"
        assert "DAY CHART" in " ".join(a["text"] for a in book.artwork)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_caption_next_to_the_chart_stays_its_caption(self):
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.solid_png()))

        fragment = build_epub.page_fragment(book, 1)
        assert "<figure>" in fragment
        assert "Figure 7.4 - Sect as a Spectrum" in fragment
        assert "figcaption" in fragment

    def test_the_crop_is_the_chart_and_not_the_prose(self):
        """Close-to-body boundary: the crop stops where the prose starts, so no
        body line is amputated into the picture and none is lost from the text."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.solid_png()))

        figure = next(f for f in book.figures if f["pno"] == 1)
        bottom = figure["bbox"][3]
        first_prose = next(el for el in book.elements
                           if el.kind == "p" and el.pno == 1)
        assert bottom <= first_prose.bbox[1] + 2.0, (figure, first_prose.bbox)
        assert first_prose.text.startswith("The astrologers of this period")
        assert book.conservation.ok, book.conservation.to_dict()

    def test_a_sidebar_wheel_beside_the_prose_is_recovered(self):
        """Index 485's shape: the natal wheel occupies the empty region beside a
        narrow prose column, with its caption printed under the wheel."""
        book = _chart_book(lambda d: F.scan_sidebar_figure_page(d, F.solid_png()))

        figures = [f for f in book.figures if f["pno"] == 1]
        assert len(figures) == 1, book.figures
        fragment = build_epub.page_fragment(book, 1)
        assert "Chart 45 - John F. Kennedy Jr." in fragment
        assert "figcaption" in fragment
        assert "The native was the son of U.S." in _whole_text(book)
        assert book.conservation.ok, book.conservation.to_dict()

    def test_the_crop_of_a_real_chart_is_not_blank(self, tmp_path):
        """Source image decode integrity: what lands in the EPUB is the ink."""
        doc = _doc(F.prose_page,
                   lambda d: F.scan_chart_band_page(d, F.solid_png()))
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

    def test_a_lone_display_line_is_a_chapter_opening_not_a_figure(self):
        """Page 93's shape: 'CHAPTER 4' over white space is a chapter opening.
        The white space above a title is not figure territory, and the display
        line must stay in the book's text."""
        book = _chart_book(lambda d: F.scan_chapter_opening_page(d, F.solid_png()))

        assert "CHAPTER 4" in _whole_text(book)
        assert not [f for f in book.figures if f["pno"] == 1
                    and f.get("found") != "embedded"], book.figures

    def test_a_body_sized_date_line_is_not_absorbed_as_artwork(self):
        """Page 18's shape: a body-sized date line is the book's prose, not chart
        lettering -- it must stay in the reading flow, not vanish into a blank
        territory that is dropped at build time."""
        book = _chart_book(lambda d: F.scan_date_tail_page(d, F.solid_png()))

        assert "November 2016" in _whole_text(book)
        assert not any("November" in a["text"] for a in book.artwork)
        assert not [f for f in book.figures if f["pno"] == 1
                    and f.get("found") != "embedded"], book.figures

    def test_prose_words_are_still_conserved_with_the_artwork_moved(self):
        """The conservation contract, stated: every source word is accounted in
        body, notes, furniture, captions, or preserved artwork -- never silently
        discarded."""
        book = _chart_book(lambda d: F.scan_chart_band_page(d, F.solid_png()))

        report = book.conservation
        assert report.ok, report.to_dict()
        assert report.source_total == report.output_total, report.to_dict()
