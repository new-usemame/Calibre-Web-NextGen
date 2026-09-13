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

        assert {"marker_apostrophe", "marker_degree", "marker_residue"} <= kinds
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
