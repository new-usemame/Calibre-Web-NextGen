# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Page-level reading of a scanned book: what is a heading, what is furniture, and
which footnote does a damaged marker belong to.

Every defect here was measured on a real 698-page scan (DIAGNOSIS.md) and every one
is paired with a control that must NOT fire — a converter that finds headings
everywhere is worse than one that finds none, because the reader's table of contents
is then full of chart labels and bibliography numbers.
"""

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
    """DIAGNOSIS A: a bold sub-heading set on the body's own leading shares its text
    block with the paragraph under it. 28 of them were lost in one book — the reader
    gets no way to navigate to the section."""
    book = _book(F.defect_a_page)

    headings = _kinds(book, "h")

    assert headings, "the bold line above the paragraph is a heading"
    assert "Serapio of Alexandria" in headings[0].text


def test_the_paragraph_under_a_run_in_heading_does_not_still_contain_it():
    """The failure mode is not just a missing heading: the heading text is swallowed
    into the first sentence of the paragraph."""
    book = _book(F.defect_a_page)

    paragraphs = _kinds(book, "p")

    assert paragraphs
    assert not paragraphs[0].text.startswith("Serapio of Alexandria (First Century CE?)")


def test_a_run_in_heading_comes_before_the_paragraph_it_opens():
    """A heading in the wrong place is worse than no heading: the EPUB splits on h1
    and h2, so a section head that sorts *after* its own first paragraph leaves that
    paragraph at the end of the previous chapter and opens the new one mid-thought.

    MEASURED on book 567 page 121 (index 120): heading and paragraph share the text
    block's top, and the block's left edge (53.65) is further left than the heading
    line that opens it (53.94), so ordering the page by position alone puts the body
    first."""
    book = _book(F.run_in_heading_with_scan_jitter_page)

    kinds = [el.kind for el in book.elements]
    heads = [i for i, el in enumerate(book.elements)
             if el.kind == "h" and el.text.startswith("Serapio of Alexandria (First")]
    paras = [i for i, el in enumerate(book.elements)
             if el.kind == "p" and el.text.startswith("Serapio of Alexandria was")]

    assert heads and paras, kinds
    assert heads[0] < paras[0], (
        "the heading must open its section, not trail it: %s" % kinds)


def test_numbered_bibliography_entries_are_not_headings():
    """103 bold-numbered entries in one book are heading candidates by weight and
    every one is a correct rejection."""
    book = _book(F.numbered_bibliography_page)

    assert _kinds(book, "h") == []


def test_a_bold_lead_in_that_continues_in_lowercase_is_not_a_heading():
    """A small-caps opening phrase is part of its own sentence. Loosening the heading
    rule far enough to recover DIAGNOSIS A must not reach this."""
    book = _book(F.lead_in_page)

    assert _kinds(book, "h") == []
    assert "IT IS AN HONOR AND A PRIVILEGE" in _all_text(book)


# -------------------------------------------------------------------- page furniture

def test_running_heads_and_folios_are_not_body_text():
    """A running title repeated on every page turns into a sentence in the middle of
    the prose if it is read as content."""
    book = _book(F.defect_a_page, F.defect_b_pages, F.defect_c_page)

    body = " ".join(el.text for el in book.elements if el.kind in ("p", "h"))

    assert "CHAPTER 4: THE HELLENISTIC ASTROLOGERS" not in body


def test_dropped_furniture_is_recorded_rather_than_silently_discarded():
    """Anything removed from the text has to be inspectable, or the conversion cannot
    be audited when a real heading goes missing."""
    book = _book(F.defect_a_page, F.defect_b_pages)

    dropped = " ".join(book.furniture)

    assert "CHAPTER 4: THE HELLENISTIC ASTROLOGERS" in dropped


# ------------------------------------------------------------------ notes as a channel

def test_footnotes_are_a_page_side_channel_not_inline_elements():
    """DIAGNOSIS B was caused by footnote elements sitting between two halves of a
    paragraph in the linear stream. Notes belong to their page, not to the prose."""
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
        """C1, 49 sites: `CE.'` + a `56` span, where the page's note is 156."""
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
        assert "astrologers'" in _all_text(book)
        assert 'it shall be so.\'' in _all_text(book)

    def test_a_degree_sign_standing_in_for_a_trailing_zero_is_repaired(self):
        """C2, 53 sites: `caution.` + `16` + `° It is...`, where the note is 160."""
        book = _book(F.defect_c_page)

        assert 160 in _noterefs(book)
        assert "° It is in this text" not in _all_text(book)

    def test_printed_degree_tokens_survive(self):
        """207 legitimate degree tokens are printed in the same book."""
        book = _book(F.degree_control_page)

        assert "16° of Aries" in _all_text(book)
        assert "45° of Leo" in _all_text(book)
        assert _noterefs(book) == [160]

    def test_quote_residue_is_paired_with_the_pages_unmarked_note(self):
        """C3, 62 sites: the whole superscript is read as `.'\"` and no digits survive.
        The page's remaining unmarked note is the only candidate."""
        book = _book(F.defect_c_page)

        assert sorted(_noterefs(book)) == [156, 160, 161]

    def test_a_balanced_quotation_is_not_turned_into_a_marker(self):
        """Same punctuation, nothing unmarked to pair it with: it stays punctuation."""
        book = _book(F.balanced_quotation_page)

        assert _noterefs(book) == [77]
        assert "the place of fortune.'\"" in _all_text(book)

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
    """Chart keys outrank every chapter title in the book on type size alone.

    This is the case the junk veto exists for: `DAY CHART a 9 -5 e` is bold, large,
    short and ends in no punctuation, so nothing but "that is not what a heading
    looks like" keeps it out of the table of contents."""
    book = _book(F.chart_label_page)

    assert _kinds(book, "h") == []
    assert "DAY CHART a 9 -5 e" in _all_text(book)


def test_a_tables_column_heads_do_not_outrank_the_section_they_sit_above():
    """The junk veto's floor is "two real words", and a table header row clears it.

    ``Day Night  I' J/ /  0`` is two column names and the wreckage of the glyphs
    under them, set at chapter size. Every existing test of the veto is a line that
    fails on word count or on loose single characters; this one fails on neither,
    so it went into the book as an <h1> -- which is a split level, so it took the
    section printed below it out of its own chapter and stood in the table of
    contents where that section should be.
    """
    book = _book(F.table_column_heads_page)

    heads = [el.text.strip() for el in _kinds(book, "h")]

    assert heads == ["Ptolemy's Alternative Triplicity Ruler Scheme"], heads
    assert "Day Night" in _all_text(book), "the row itself must still be in the book"


def test_a_heading_of_initials_and_a_surname_is_still_a_heading():
    """The other side of the same rule, and the reason it counts initials as words.

    A line of stops and single letters is exactly what the veto is looking for, and
    a name set as a heading is made of them.
    """
    book = _book(lambda d: F.section_heading_page(
        d, "R. A. Fisher and J. B. S. Haldane on Inheritance"))

    heads = [el.text.strip() for el in _kinds(book, "h")]

    assert heads == ["R. A. Fisher and J. B. S. Haldane on Inheritance"], heads


def test_a_body_size_digit_run_is_not_a_note_marker():
    """OCR splits spans at digit boundaries, so a cross-reference number arrives
    looking exactly like an inline marker except for its type size.

    Measured on book 567 (every 7th page, 100 pages): 381 digit-only spans are set
    at marker size and 45 at body size. Only the size test separates them, and a
    wrong call turns `page 61` into a footnote link with the digits swallowed."""
    body = extract.Span(text="61", size=11.3, font="Times-Roman", flags=4,
                        bbox=(0, 0, 10, 11))
    marker = extract.Span(text="61", size=8.8, font="Times-Roman", flags=5,
                          bbox=(0, 0, 8, 9))

    assert not skeleton.is_marker_span(body, line_size=11.3)
    assert skeleton.is_marker_span(marker, line_size=11.3)


class TestOcrDamagedFootnoteNumbers(object):
    """The footnote's number is printed as a raised 5.7pt digit beside 9.5pt text.

    Whether the scanner keeps that digit as its own span is a coin toss: MEASURED on
    the acceptance book, 138 footnotes on 70 of 698 pages open with the number merged
    into the first text span instead. Missing them does not lose the words — it
    leaves the note inline in the body, which is the defect the side channel exists
    to prevent, and it leaves the marker in the text pointing at nothing.
    """

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
        assert "Diodorus Siculus" not in body, "the note was left in the body text"

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
    """A heading level is a level because the book uses it more than once.

    MEASURED on the acceptance book: the four largest type sizes are 47.8, 43.8, 40.9
    and 35.1pt, each on exactly one page, all of them scanner debris on the cover. A
    ladder built from the largest sizes puts every real heading below the bottom rung,
    and the EPUB's table of contents comes out flat.
    """

    def test_one_off_display_type_does_not_define_a_heading_level(self):
        doc = F.new_doc()
        F.title_page(doc)
        for index in range(4):
            F.chapter_opening_page(doc, "CHAPTER %d" % (index + 1), folio=str(30 + index))
        for index in range(6):
            F.section_heading_page(doc, "The Hellenistic Period", folio=str(40 + index))
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
    """MEASURED: book 567's PDF outline has 698 entries, titled ``Page 1`` through
    ``Page 698``. Building the EPUB's navigation from it would produce a table of
    contents with one meaningless entry per page and no chapters at all."""
    labels = [{"level": 1, "title": "Page %d" % (n + 1), "pno": n} for n in range(40)]
    real = [{"level": 1, "title": "Chapter 1: Astrology in Mesopotamia", "pno": 3},
            {"level": 2, "title": "The Hellenistic Period", "pno": 11}]

    assert not skeleton.outline_is_useful(labels)
    assert skeleton.outline_is_useful(real)


class TestThePageTheWordsCameFrom(object):
    """The picture sent to the model and the words sent to the model are one page.

    The deterministic pass strips the running head, the running foot and the folio
    before the text ever leaves the process, and then the old code handed the model
    a photograph of the page with all three still printed on it. That is not a
    prompt problem: it asks a vision model to look at a chapter title and not read
    it, and MEASURED on the acceptance book it lost, putting "Chapter 4: The
    Hellenistic Astrologers" inside the chapter on every page of a 30-page range.
    """

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
    """MEASURED on the acceptance book: the footnote zone of page 109 opens
    ``9° Tarrant, Thrasyllan Platonism, p. 10`` and page 113's opens ``"1 Edited in
    CCAG 5, 4``. Neither line opens a note today, because neither starts with
    something that parses as a number -- so the note's text is emitted as a stray
    paragraph in the middle of the body, the note itself never exists, and the
    marker in the body pointing at it has nothing to bind to. Three defects, one
    unread number.
    """

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

        assert "Tarrant" not in body, body

    def test_a_word_in_the_note_zone_is_not_read_as_a_number(self):
        """The control. ``so`` is an s and an o, which are a 5 and a 0."""
        skel = self._regions(F.lowercase_word_in_the_note_zone_page)

        assert 50 not in [r.number for r in skel.note_regions]


class TestTypeThatIsOnlyBiggerBecauseTheScannerSaidSo(object):
    """DIAGNOSIS A has a mirror image. A converter that reads a drifted measurement as
    a heading puts half a sentence in the reader's table of contents, and -- because
    the EPUB splits its chapters on the top of the ladder -- can split a chapter in
    the middle of a paragraph.

    MEASURED on book 567: over PDF pages 100-145 the deterministic pass emitted two
    such headings, 'Schmidt published an attempt to reconstruct the original
    definitions o' (p112, 12.00pt roman) and "Ptolemy's astrological work was
    apparently originally known as the" (p128, 11.90pt roman), against 20 real ones.
    Both sit below the book's own heading rung; both are set in the body face.
    """

    @staticmethod
    def _with_a_ladder(page_builder):
        """The page under test, in a book whose section heads define a rung.

        A rung is a rung because the book uses it on more than one page, so the
        drifted line can only be judged against a ladder that exists.
        """
        return _book(
            lambda doc: F.chapter_opening_page(doc, "The Hellenistic Astrologers"),
            lambda doc: F.section_heading_page(doc, "Critodemus", folio="70"),
            lambda doc: F.section_heading_page(doc, "Abraham", folio="74"),
            lambda doc: F.section_heading_page(doc, "Zoroaster", folio="78"),
            page_builder,
        )

    def test_a_line_the_scanner_measured_high_is_not_a_heading(self):
        book = self._with_a_ladder(F.ocr_size_drift_page)

        assert not [el for el in _kinds(book, "h") if "Schmidt" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_the_line_stays_in_the_paragraph_it_belongs_to(self):
        """Not losing the words is not enough -- a heading is its own element, so a
        promoted line also breaks the paragraph it was a line of."""
        book = self._with_a_ladder(F.ocr_size_drift_page)

        holding = [el for el in _kinds(book, "p") if "Schmidt published" in el.text]

        assert holding, [el.text for el in book.elements]
        assert "reconstruct the original Antiochus definitions" in holding[0].text

    def test_a_heading_in_the_body_face_at_the_books_own_heading_size_still_reads(self):
        """The control. Nothing here is bold: the only thing that makes it a heading
        is that the book sets its section heads at this size."""
        book = self._with_a_ladder(
            lambda doc: F.roman_heading_on_the_ladder_page(doc, "Serapio of Alexandria"))

        assert [el for el in _kinds(book, "h") if "Serapio of Alexandria" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_bold_run_in_head_below_every_rung_still_reads(self):
        """The other control. A run-in head is set on the body's own leading and can
        be barely larger than the body -- it is the weight that marks it, and defect A
        is exactly the case where the size signal is not there to be had."""
        book = self._with_a_ladder(F.flat_run_in_heading_page)

        assert [el for el in _kinds(book, "h") if "Serapio of Alexandria" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_bold_head_the_scanner_measured_under_the_body_still_reads(self):
        """The wobble the ladder exists for runs both ways. MEASURED on book 567: the
        book's bold section heads come back between 10.8 and 11.5pt against an 11.3pt
        body, one printed rung; taking the ones the scanner rounded up and refusing
        the ones it rounded down loses 38 of them, among them 'Mystery Traditions',
        'The Moon - Selene' and 'Saturn / Kronos, the "Shining One" (Phainon)'."""
        book = self._with_a_ladder(F.run_in_heading_the_scanner_measured_small_page)

        assert [el for el in _kinds(book, "h") if "Serapio of Alexandria" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_the_paragraph_under_an_under_measured_head_does_not_still_contain_it(self):
        """A promoted line has to leave the paragraph it was the first line of, or the
        reader reads the heading twice."""
        book = self._with_a_ladder(F.run_in_heading_the_scanner_measured_small_page)

        holding = [el for el in _kinds(book, "p") if "was an astrologer" in el.text]

        assert holding, [el.text for el in book.elements]
        assert not holding[0].text.startswith("Serapio of Alexandria (First Century")

    def test_bold_type_far_below_the_body_is_a_different_rung_and_not_a_head(self):
        """The floor on the same rule. Book 567 sets its example-chart labels bold at
        about 0.78 of the body -- 'STEVEN SPIELBERG', 'TIGER WOODS', 50 of them -- and
        a weight rule with no floor puts every one of them in the heading ladder."""
        book = self._with_a_ladder(F.bold_line_far_below_the_body_page)

        assert not [el for el in _kinds(book, "h") if "Serapio of Alexandria" in el.text], \
            [el.text for el in _kinds(book, "h")]


class TestASentenceTheScannerSetLargeIsStillASentence(object):
    """Two more measured false headings from book 567, both reaching the book's own
    section-head rung, so the ladder cannot tell them from a heading -- and both
    ``<h2>``, which is a level the EPUB splits its chapters on. A false heading is not
    cosmetic: it breaks a chapter in the middle of a paragraph.
    """

    @staticmethod
    def _with_a_ladder(page_builder):
        return _book(
            lambda doc: F.chapter_opening_page(doc, "The Planets"),
            lambda doc: F.section_heading_page(doc, "Sect", folio="400"),
            lambda doc: F.section_heading_page(doc, "Exaltations", folio="402"),
            page_builder,
        )

    def test_a_line_that_starts_in_the_middle_of_a_sentence_is_not_a_heading(self):
        book = self._with_a_ladder(F.heading_that_starts_mid_sentence_page)

        assert not [el for el in _kinds(book, "h") if "bonify" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_line_that_stops_on_a_word_no_title_stops_on_is_not_a_heading(self):
        book = self._with_a_ladder(F.heading_that_stops_on_a_function_word_page)

        assert not [el for el in _kinds(book, "h") if "Capricorn" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_line_that_ends_where_a_sentence_ends_is_not_a_heading(self):
        """MEASURED on book 567: two body lines the scan set large end in sentence
        punctuation and nothing else about them says prose. This is ``The native was
        the son of U.S.`` (index 485); the other, a house-list line ending in a comma
        (index 367), is caught first by the lowercase line under it."""
        book = self._with_a_ladder(F.line_that_ends_in_sentence_punctuation_page)

        assert not [el for el in _kinds(book, "h") if "native was the son" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_real_heading_may_use_those_words_inside_it(self):
        """The control: the rule is about where the words fall, not that they appear."""
        book = self._with_a_ladder(F.long_heading_with_function_words_page)

        assert [el for el in _kinds(book, "h") if "Three Forms of House" in el.text], \
            [el.text for el in _kinds(book, "h")]

    def test_a_heading_that_ends_on_an_abbreviation_still_reads(self):
        """The other control: ``(First Century CE?)`` ends on punctuation and a
        two-letter word, and is the shape of every section head in this book."""
        book = self._with_a_ladder(F.defect_a_page)

        assert [el for el in _kinds(book, "h") if "Serapio of Alexandria" in el.text], \
            [el.text for el in _kinds(book, "h")]
