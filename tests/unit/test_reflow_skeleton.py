# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Synthetic controls for headings, furniture and damaged note markers."""

import pytest

from cps.services.reflow import assemble, extract, skeleton
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


def _book(*builders):
    """Run the deterministic pass over a document built from the given page builders."""
    doc = F.new_doc()
    for build in builders:
        build(doc)
    try:
        return assemble.deterministic_book(doc)
    finally:
        doc.close()


def _kinds(book, kind):
    return [el for el in book.elements if el.kind == kind]


def _all_text(book):
    return " ".join(el.text for el in book.elements)


def _noterefs(book):
    return [int(run[1]) for el in book.elements for run in el.runs if run[0] == "sup"]


# --------------------------------------------------------------- headings (defect A)

def test_a_run_in_heading_is_recovered_as_a_heading():
    """A run in heading is recovered as a heading."""
    book = _book(F.defect_a_page)

    headings = _kinds(book, "h")

    assert headings, "the bold line above the paragraph is a heading"
    assert "Workshop of Birchwood" in headings[0].text


def test_the_paragraph_under_a_run_in_heading_does_not_still_contain_it():
    """The failure mode is not just a missing heading: the heading text is swallowed
    into the first sentence of the paragraph."""
    book = _book(F.defect_a_page)

    paragraphs = _kinds(book, "p")

    assert paragraphs
    assert not paragraphs[0].text.startswith("Workshop of Birchwood (First Edition AB?)")


def test_a_run_in_heading_comes_before_the_paragraph_it_opens():
    """A run in heading comes before the paragraph it opens."""
    book = _book(F.run_in_heading_with_scan_jitter_page)

    kinds = [el.kind for el in book.elements]
    heads = [i for i, el in enumerate(book.elements)
             if el.kind == "h" and el.text.startswith("Workshop of Birchwood (First")]
    paras = [i for i, el in enumerate(book.elements)
             if el.kind == "p" and el.text.startswith("Workshop of Birchwood was")]

    assert heads and paras, kinds
    assert heads[0] < paras[0], (
        "the heading must open its section, not trail it: %s" % kinds)


def test_numbered_bibliography_entries_are_not_headings():
    """Numbered bold prose entries are heading candidates by weight and
    every one is a correct rejection."""
    book = _book(F.numbered_bibliography_page)

    assert _kinds(book, "h") == []


def test_a_bold_lead_in_that_continues_in_lowercase_is_not_a_heading():
    """A bold lead in that continues in lowercase is not a heading."""
    book = _book(F.lead_in_page)

    assert _kinds(book, "h") == []
    assert "THIS IS A SIMPLE WORKSHOP EXERCISE" in _all_text(book)


# -------------------------------------------------------------------- page furniture

def test_running_heads_and_folios_are_not_body_text():
    """A running title repeated on every page turns into a sentence in the middle of
    the prose if it is read as content."""
    book = _book(F.defect_a_page, F.defect_b_pages, F.defect_c_page)

    body = " ".join(el.text for el in book.elements if el.kind in ("p", "h"))

    assert "CHAPTER 4: THE WORKSHOP INSTRUCTIONS" not in body


def test_dropped_furniture_is_recorded_rather_than_silently_discarded():
    """Anything removed from the text has to be inspectable, or the conversion cannot
    be audited when a real heading goes missing."""
    book = _book(F.defect_a_page, F.defect_b_pages)

    dropped = " ".join(book.furniture)

    assert "CHAPTER 4: THE WORKSHOP INSTRUCTIONS" in dropped


def test_a_running_head_below_the_band_is_still_furniture():
    """A running head below the band is still furniture."""
    book = _book(F.below_band_running_head_page)

    body = " ".join(el.text for el in book.elements if el.kind in ("p", "h"))

    assert "CEDAR - CEDAR" not in body
    assert "CEDAR - CEDAR 1257" in " ".join(book.furniture)
    assert book.conservation.ok, book.conservation.to_dict()


def test_a_below_band_caps_line_without_a_folio_is_content():
    """The below-band rule is for running heads, not for anything small and
    caps: no folio at either edge, no furniture."""
    book = _book(F.below_band_caps_line_without_folio_page)

    assert "CEDAR SUPPORTS THE BLUE TRAY" in _all_text(book)
    assert book.conservation.ok, book.conservation.to_dict()


def test_a_below_band_folio_is_furniture():
    """A below band folio is furniture."""
    book = _book(F.below_band_folio_page)

    body = " ".join(el.text for el in book.elements if el.kind in ("p", "h"))

    assert "516" not in body
    assert "516" in " ".join(book.furniture)
    assert book.conservation.ok, book.conservation.to_dict()


# ------------------------------------------------------------------ notes as a channel

def test_footnotes_are_a_page_side_channel_not_inline_elements():
    """Footnotes are a page side channel not inline elements."""
    book = _book(F.defect_b_pages)

    assert [n.num for n in book.notes if n.pno == 0] == [160, 161, 166]
    assert all(el.kind != "fn" for el in book.elements)


def test_page_indices_are_zero_based_for_elements_and_notes_alike():
    """The previous converter stored 0-based page numbers on paragraphs and 1-based on
    footnotes, so a marker on page N looked up a note on page N-1."""
    book = _book(F.defect_a_page, F.defect_b_pages)

    heading = _kinds(book, "h")[0]
    notes = [n for n in book.notes]

    assert heading.pno == 0
    assert {n.pno for n in notes} == {1}
    assert all(run[2] == 1 for el in book.elements for run in el.runs
               if run[0] == "sup")


# --------------------------------------------------------- damaged markers (defect C)

class TestOcrDamagedMarkers:
    """The OCR layer renders small superscripts wrong in three measured ways. Each
    repair is paired with the printed text it must leave alone."""

    def test_an_apostrophe_standing_in_for_a_leading_one_is_repaired(self):
        """Leading-digit damage: `CE.'` + a `56` span, where the page's note is 156."""
        book = _book(F.defect_c_page)

        assert 156 in _noterefs(book)
        assert "CE.'" not in _all_text(book)

    def test_a_printed_apostrophe_before_a_marker_survives(self):
        """The same shape, but the marker resolves on its own face, so the apostrophe
        is printed punctuation. Both printed forms are here: a possessive, and a
        closing quotation after a full stop — which is character-for-character what
        the C1 damage looks like, and is told apart only by the marker resolving
        without the note window."""
        book = _book(F.apostrophe_control_page)

        assert sorted(_noterefs(book)) == [61, 62]
        assert "carpenters'" in _all_text(book)
        assert "keep this lid closed.'" in _all_text(book)

    def test_a_degree_sign_standing_in_for_a_trailing_zero_is_repaired(self):
        """Trailing-digit damage: `canvas.` + `16` + `° Every blue...`, where the note is 160."""
        book = _book(F.defect_c_page)

        assert 160 in _noterefs(book)
        assert "° Every blue sample cabinet" not in _all_text(book)

    def test_printed_degree_tokens_survive(self):
        """Degree glyphs in ordinary source prose remain literal."""
        book = _book(F.degree_control_page)

        assert '16° on its rim' in _all_text(book)
        assert '45°' in _all_text(book)
        assert _noterefs(book) == [160]

    def test_quote_residue_is_paired_with_the_pages_unmarked_note(self):
        """Quotation-shaped damage: the whole superscript is read as `.'\"` and no digits survive.
        The page's remaining unmarked note is the only candidate."""
        book = _book(F.defect_c_page)

        assert sorted(_noterefs(book)) == [156, 160, 161]

    def test_a_balanced_quotation_is_not_turned_into_a_marker(self):
        """Same punctuation, nothing unmarked to pair it with: it stays punctuation."""
        book = _book(F.balanced_quotation_page)

        assert _noterefs(book) == [77]
        assert 'the blue wooden tray.\'"' in _all_text(book)

    def test_residue_is_left_alone_when_the_counts_disagree(self):
        """Two quotation residues, one unmarked note. Pairing them off binds the note
        to whichever quotation came first — a coin flip printed into the reader's
        book. The page keeps its punctuation and earns a routing reason instead."""
        book = _book(F.ambiguous_residue_page)

        assert _noterefs(book) == []
        assert "note_marker_mismatch" in book.page_reasons(0)
        assert not [r for r in book.repairs if r.kind == "marker_residue"]

    def test_every_repair_is_recorded_with_its_page_and_evidence(self):
        """A silent repair is indistinguishable from a silent corruption."""
        book = _book(F.defect_c_page)

        kinds = {r.kind for r in book.repairs}

        assert {"marker_prefix", "marker_degree", "marker_residue"} <= kinds
        assert all(r.pno == 0 for r in book.repairs)
        assert all(r.detail for r in book.repairs)


def test_a_large_chart_label_is_not_promoted_to_a_heading():
    """Panel keys outrank every chapter title in the book on type size alone.

    This is the case the junk veto exists for: `BOX PANEL o 7 -3 z` is bold, large,
    short and ends in no punctuation, so nothing but "that is not what a heading
    looks like" keeps it out of the table of contents."""
    book = _book(F.chart_label_page)

    assert _kinds(book, "h") == []
    assert "BOX PANEL o 7 -3 z" in _all_text(book)


def test_a_tables_column_heads_do_not_outrank_the_section_they_sit_above():
    """The junk veto's floor is "two real words", and a table header row clears it.

    ``Box Shelf  I' J/ /  0`` is two column names and the wreckage of the glyphs
    under them, set at chapter size. Every existing test of the veto is a line that
    fails on word count or on loose single characters; this one fails on neither,
    so it went into the book as an <h1> -- which is a split level, so it took the
    section printed below it out of its own chapter and stood in the table of
    contents where that section should be.
    """
    book = _book(F.table_column_heads_page)

    heads = [el.text.strip() for el in _kinds(book, "h")]

    assert heads == ["Workshop's Alternative Three-Shelf Drawer Scheme"], heads
    assert "Box Shelf" in _all_text(book), "the row itself must still be in the book"


def test_a_heading_of_initials_and_a_surname_is_still_a_heading():
    """The other side of the same rule, and the reason it counts initials as words.

    A line of stops and single letters is exactly what the veto is looking for, and
    a name set as a heading is made of them.
    """
    book = _book(lambda d: F.section_heading_page(
        d, "R. A. Timber and J. B. S. Cabinet on Woodcraft"))

    heads = [el.text.strip() for el in _kinds(book, "h")]

    assert heads == ["R. A. Timber and J. B. S. Cabinet on Woodcraft"], heads


def test_a_body_size_digit_run_is_not_a_note_marker():
    """A body size digit run is not a note marker."""
    body = extract.Span(text="61", size=11.3, font="Times-Roman", flags=4,
                        bbox=(0, 0, 10, 11))
    marker = extract.Span(text="61", size=8.8, font="Times-Roman", flags=5,
                          bbox=(0, 0, 8, 9))

    assert not skeleton.is_marker_span(body, line_size=11.3)
    assert skeleton.is_marker_span(marker, line_size=11.3)


class TestOcrDamagedFootnoteNumbers(object):
    """Detached and merged superscripts retain the same source note identity."""

    def test_a_footnote_whose_number_merged_into_its_text_is_still_a_footnote(self):
        doc = F.new_doc()
        F.merged_note_number_page(doc)
        try:
            raw = extract.read_pages(doc)
            style = skeleton.book_style(raw)
            skel = skeleton.page_skeleton(raw[0], style)
        finally:
            doc.close()

        assert skel.note_numbers == [24, 25]
        body = " ".join(region.text for region in skel.body_regions)
        assert "Workshop Register" not in body, "the note was left in the body text"

    def test_a_note_zone_line_that_merely_starts_with_a_number_is_not_a_footnote(self):
        """The control. Endnote-style entries, table rows and page-bottom debris all
        begin with digits; only a digit run followed by the start of a sentence is
        a footnote opening."""
        doc = F.new_doc()
        F.numbered_bibliography_page(doc)
        try:
            raw = extract.read_pages(doc)
            style = skeleton.book_style(raw)
            skel = skeleton.page_skeleton(raw[0], style)
        finally:
            doc.close()

        assert skel.note_numbers == []


class TestTheHeadingLadder(object):
    """Repeated source sizes define heading levels; OCR size drift does not."""

    def test_one_off_display_type_does_not_define_a_heading_level(self):
        doc = F.new_doc()
        F.title_page(doc)
        for index in range(4):
            F.chapter_opening_page(doc, "CHAPTER %d" % (index + 1), folio=str(30 + index))
        for index in range(6):
            F.section_heading_page(doc, "The Workshop Period", folio=str(40 + index))
        try:
            raw = extract.read_pages(doc)
            style = skeleton.book_style(raw)
        finally:
            doc.close()

        assert style.ladder, "no ladder at all means every heading is level 1"
        assert max(style.ladder) < 20.0, style.ladder
        assert style.level_for(16.0) == 1, style.ladder
        assert style.level_for(13.0) == 2, style.ladder

    def test_type_larger_than_the_ladder_is_never_demoted_below_it(self):
        """A size above every rung has to be level 1. Falling through to the bottom
        would set a book's largest heading deeper than its smallest."""
        style = skeleton.BookStyle(body_size=11.3, ladder=[15.7, 13.0], page_count=40)

        assert style.level_for(30.0) == 1
        assert style.level_for(15.8) == 1
        assert style.level_for(13.1) == 2
        assert style.level_for(11.9) == 3


def test_a_scanner_page_label_outline_is_not_a_table_of_contents():
    """A scanner page label outline is not a table of contents."""
    labels = [{"level": 1, "title": "Page %d" % (n + 1), "pno": n} for n in range(40)]
    real = [{"level": 1, "title": "Chapter 1: Lettering in the Workshop", "pno": 3},
            {"level": 2, "title": "The Workshop Period", "pno": 11}]

    assert not skeleton.outline_is_useful(labels)
    assert skeleton.outline_is_useful(real)


class TestThePageTheWordsCameFrom(object):
    """The model raster and deterministic words describe the same page content."""

    def _skeleton(self, build):
        doc = F.new_doc()
        build(doc)
        try:
            raw = extract.read_pages(doc, [0])
            style = skeleton.book_style(raw)
            return skeleton.page_skeleton(raw[0], style)
        finally:
            doc.close()

    def test_the_band_the_running_head_sits_in_is_not_in_the_picture(self):
        skel = self._skeleton(F.prose_page)
        head = [line for region in skel.regions if region.kind == "furniture"
                for line in region.lines]
        assert head, "this fixture is supposed to have furniture"

        box = skel.body_box()

        assert box[1] > max(line.bbox[3] for line in head)

    def test_every_line_that_survived_is_still_in_the_picture(self):
        """The crop can only ever remove furniture. A page whose body starts high, or
        whose notes run to the foot, keeps the height it needs -- cutting a line of
        the book out of the image would be a far worse defect than the one this
        fixes, because the model would be asked to place words it cannot see."""
        skel = self._skeleton(F.prose_page)
        kept = [line for region in skel.regions if region.kind != "furniture"
                for line in region.lines]

        box = skel.body_box()

        assert box[1] <= min(line.bbox[1] for line in kept)
        assert box[3] >= max(line.bbox[3] for line in kept)

    def test_a_page_with_no_furniture_is_photographed_whole(self):
        skel = self._skeleton(F.title_page)
        assert not [r for r in skel.regions if r.kind == "furniture"]

        assert skel.body_box() == (0.0, 0.0, skel.width, skel.height)

    def test_furniture_low_enough_to_touch_the_text_does_not_crop_the_text(self):
        """A crafted violation: a folio printed inside the footer band on a page whose
        notes run down into it. Trusting the band alone would cut the last note off
        the picture."""
        doc = F.new_doc()
        page = F.add_page(doc)
        F.add_body_lines(page, F.PROSE_LINES[:6])
        F._put(page, F.LEFT, F.PAGE_H * 0.94, "a note that runs into the foot band")
        F._put(page, F.PAGE_W - 60, F.PAGE_H * 0.96, "121", size=10.2)
        try:
            raw = extract.read_pages(doc, [0])
            skel = skeleton.page_skeleton(raw[0], skeleton.book_style(raw))
        finally:
            doc.close()
        kept = [line for region in skel.regions if region.kind != "furniture"
                for line in region.lines]

        assert skel.body_box()[3] >= max(line.bbox[3] for line in kept)


class TestANoteWhoseOwnNumberTheScannerAte(object):
    """A damaged opening number is recovered only from compatible note evidence."""

    def _regions(self, builder):
        doc = F.new_doc()
        builder(doc)
        try:
            raw = extract.read_pages(doc, [0])
            style = skeleton.book_style(raw, None)
            return skeleton.page_skeleton(raw[0], style)
        finally:
            doc.close()

    def test_a_number_read_as_letters_still_opens_its_note(self):
        skel = self._regions(F.glyph_numbered_note_page)

        assert [r.number for r in skel.note_regions] == [90, 91, 92]

    def test_the_notes_text_is_not_left_standing_in_the_body(self):
        skel = self._regions(F.glyph_numbered_note_page)
        body = " ".join(r.text for r in skel.body_regions)

        assert "Shelfbook" not in body, body

    def test_a_word_in_the_note_zone_is_not_read_as_a_number(self):
        """The control. ``so`` is an s and an o, which are a 5 and a 0."""
        skel = self._regions(F.lowercase_word_in_the_note_zone_page)

        assert 50 not in [r.number for r in skel.note_regions]

    def test_a_degree_in_body_prose_is_not_a_note_number(self):
        """A degree in body prose is not a note number."""
        skel = self._regions(F.degree_in_the_note_zone_page)

        assert [r.number for r in skel.note_regions] == [10]
        body = " ".join(r.text for r in skel.body_regions)
        assert 'Clockwise' in body, body
        assert 'small mark' in body, body

    def test_a_number_on_its_own_line_opens_its_note(self):
        """The own-line shape: full-size digits alone on a line, the note's
        sentence on the next. The note opens, keeps its text, and the marker
        in the body binds it -- the link the reader follows."""
        skel = self._regions(F.own_line_note_number_page)

        assert [r.number for r in skel.note_regions] == [10]
        note = skel.note_regions[0]
        assert "Rackbook" in note.text
        body = " ".join(r.text for r in skel.body_regions)
        assert "Rackbook" not in body, body

    def test_a_bare_number_without_a_sentence_after_is_not_a_note(self):
        skel = self._regions(F.bare_number_is_not_an_own_line_note_page)

        assert skel.note_regions == []


class TestTypeThatIsOnlyBiggerBecauseTheScannerSaidSo(object):
    """A slightly oversized roman body line remains part of its paragraph.

    Heading weight and repeated size evidence distinguish it from a section
    heading, so the EPUB does not split a chapter inside a sentence."""

    @staticmethod
    def _with_a_ladder(page_builder):
        """The page under test, in a book whose section heads define a rung.

        A rung is a rung because the book uses it on more than one page, so the
        drifted line can only be judged against a ladder that exists.
        """
        return _book(
            lambda doc: F.chapter_opening_page(doc, "The Workshop Instructions"),
            lambda doc: F.section_heading_page(doc, "Repairlog", folio="70"),
            lambda doc: F.section_heading_page(doc, 'Blueprint', folio="74"),
            lambda doc: F.section_heading_page(doc, "Blueprint", folio="78"),
            page_builder,
        )

    def test_a_line_the_scanner_measured_high_is_not_a_heading(self):
        book = self._with_a_ladder(F.ocr_size_drift_page)

        assert not [el for el in _kinds(book, "h") if "Archive" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_the_line_stays_in_the_paragraph_it_belongs_to(self):
        """Not losing the words is not enough -- a heading is its own element, so a
        promoted line also breaks the paragraph it was a line of."""
        book = self._with_a_ladder(F.ocr_size_drift_page)

        holding = [el for el in _kinds(book, "p") if "The cabinet maker" in el.text]

        assert holding, [el.text for el in book.elements]
        assert "arranged the colored wooden trays Workbook labels" in holding[0].text

    def test_a_heading_in_the_body_face_at_the_books_own_heading_size_still_reads(self):
        """The control. Nothing here is bold: the only thing that makes it a heading
        is that the book sets its section heads at this size."""
        book = self._with_a_ladder(
            lambda doc: F.roman_heading_on_the_ladder_page(doc, "Workshop of Birchwood"))

        assert [el for el in _kinds(book, "h") if "Workshop of Birchwood" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_bold_run_in_head_below_every_rung_still_reads(self):
        """The other control. A run-in head is set on the body's own leading and can
        be barely larger than the body -- it is the weight that marks it, and defect A
        is exactly the case where the size signal is not there to be had."""
        book = self._with_a_ladder(F.flat_run_in_heading_page)

        assert [el for el in _kinds(book, "h") if "Workshop of Birchwood" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_bold_head_the_scanner_measured_under_the_body_still_reads(self):
        """A bold head the scanner measured under the body still reads."""
        book = self._with_a_ladder(F.run_in_heading_the_scanner_measured_small_page)

        assert [el for el in _kinds(book, "h") if "Workshop of Birchwood" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_the_paragraph_under_an_under_measured_head_does_not_still_contain_it(self):
        """A promoted line has to leave the paragraph it was the first line of, or the
        reader reads the heading twice."""
        book = self._with_a_ladder(F.run_in_heading_the_scanner_measured_small_page)

        holding = [el for el in _kinds(book, "p") if "was a workshop" in el.text]

        assert holding, [el.text for el in book.elements]
        assert not holding[0].text.startswith("Workshop of Birchwood (First Century")

    def test_bold_type_far_below_the_body_is_a_different_rung_and_not_a_head(self):
        """Bold type far below the body is a different rung and not a head."""
        book = self._with_a_ladder(F.bold_line_far_below_the_body_page)

        assert not [el for el in _kinds(book, "h") if "Workshop of Birchwood" in el.text], \
            [el.text for el in _kinds(book, "h")]


class TestASentenceTheScannerSetLargeIsStillASentence(object):
    """Large prose lines remain prose even at the heading size.

    Sentence boundaries and unfinished syntax constrain navigation headings."""

    @staticmethod
    def _with_a_ladder(page_builder):
        return _book(
            lambda doc: F.chapter_opening_page(doc, "The Cabinets"),
            lambda doc: F.section_heading_page(doc, "Joinery", folio="400"),
            lambda doc: F.section_heading_page(doc, "Fasteners", folio="402"),
            page_builder,
        )

    def test_a_line_that_starts_in_the_middle_of_a_sentence_is_not_a_heading(self):
        book = self._with_a_ladder(F.heading_that_starts_mid_sentence_page)

        assert not [el for el in _kinds(book, "h") if "polish" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_line_that_stops_on_a_word_no_title_stops_on_is_not_a_heading(self):
        book = self._with_a_ladder(F.heading_that_stops_on_a_function_word_page)

        assert not [el for el in _kinds(book, "h") if "Woodwork" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_line_that_ends_where_a_sentence_ends_is_not_a_heading(self):
        """A line that ends where a sentence ends is not a heading."""
        book = self._with_a_ladder(F.line_that_ends_in_sentence_punctuation_page)

        assert not [el for el in _kinds(book, "h") if "example was the plan" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_real_heading_may_use_those_words_inside_it(self):
        """The control: the rule is about where the words fall, not that they appear."""
        book = self._with_a_ladder(F.long_heading_with_function_words_page)

        assert [el for el in _kinds(book, "h") if "Three Clean Painted Shelving" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_heading_that_ends_on_an_abbreviation_still_reads(self):
        """The other control: ``(First Edition AB?)`` ends on punctuation and a
        two-letter word, and is the shape of every section head in this book."""
        book = self._with_a_ladder(F.defect_a_page)

        assert [el for el in _kinds(book, "h") if "Workshop of Birchwood" in el.text], \
            [el.text for el in _kinds(book, "h")]
