# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Book-level assembly: joining what the page breaks split, and proving nothing was
lost while doing it.

DIAGNOSIS B measured 270 blocked paragraph joins in one book, 268 of them blocked
purely because footnote elements sat between the two halves in the element stream.
The reader sees a sentence stop mid-clause at every page turn.
"""

import pytest

from cps.services.reflow import assemble
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


def _paras(book):
    return [el.text for el in book.elements if el.kind == "p"]


# ----------------------------------------------------------- page-turn joins (defect B)

def test_a_sentence_broken_at_a_page_turn_is_one_paragraph():
    """The tail ends mid-clause on `Firmicus` and the next page opens lowercase."""
    book = _book(F.defect_b_pages)

    joined = [p for p in _paras(book) if "oftentimes Firmicus is more expansive" in p]

    assert joined, _paras(book)


def test_no_orphan_fragment_is_left_behind_by_the_join():
    """A half-join leaves the second half standing as its own paragraph, which reads
    as a new paragraph that begins in lowercase."""
    book = _book(F.defect_b_pages)

    assert not any(p.startswith("is more expansive") for p in _paras(book))


def test_footnotes_between_the_two_halves_do_not_block_the_join():
    """This is the measured cause: the previous converter required the previous
    element to be a paragraph, and the intervening footnote elements made it a note.
    The join must still happen with three notes printed between the halves."""
    book = _book(F.defect_b_pages)

    assert [n.num for n in book.notes] == [160, 161, 166]
    assert len([p for p in _paras(book) if "oftentimes Firmicus is more expansive" in p]) == 1


def test_a_finished_sentence_is_not_joined_across_the_page_turn():
    """The control: the previous page ends on a full stop. The next page opens
    lowercase, which is exactly the signal the join looks for, and it must still
    refuse — otherwise every page turn fuses two paragraphs."""
    book = _book(F.defect_b_pages, F.defect_c_page)

    assert any(p.endswith("between Anubio and Firmicus.") for p in _paras(book))
    assert any(p.startswith("sometime prior to the second century") for p in _paras(book))


# ---------------------------------------------------------------------- line joining

def test_a_line_break_hyphen_is_repaired_but_a_printed_compound_is_kept():
    """`conjunc-` + `tion` is a typesetter's break; `Sun-` + `Moon` is a real
    compound. Getting this wrong produces either `conjunc- tion` or `SunMoon`."""
    book = _book(F.hyphenated_page)

    text = " ".join(_paras(book))

    assert "conjunction" in text
    assert "Sun-Moon" in text


# ------------------------------------------------------------------ nothing is lost

def test_no_printed_word_disappears_without_being_accounted_for():
    """SPEC §3: the assembled output's tokens are the source's tokens. Furniture is
    removed on purpose and is therefore listed, not dropped on the floor."""
    book = _book(F.defect_a_page, F.defect_b_pages, F.defect_c_page,
                 F.hyphenated_page, F.numbered_bibliography_page)

    report = book.conservation

    assert report.ok, (report.missing, report.added)


def test_the_conservation_check_can_actually_fail():
    """An invariant that cannot report a violation is decoration. Remove one real
    paragraph and the check has to notice."""
    book = _book(F.defect_a_page, F.defect_b_pages)
    survivors = [el for el in book.elements if el.kind != "p"]

    report = assemble.check_conservation(book.source_words, survivors, book.notes,
                                         book.furniture)

    assert not report.ok
    assert report.missing


class TestOcrDamagedNoteNumbers(object):
    """The note's own number can be damaged exactly like the marker that points at it.

    MEASURED on the acceptance book: 39 pages carry note numbers that do not ascend —
    ``[8, 9, 1, 11, 12, 13]``, ``[96, 97, 98, 99, 1, 101]``, ``[277, 278, 279, 288,
    281]``. Each is a printed number the OCR truncated or substituted a digit in.
    Repairing one is only allowed when the page itself proves the answer: exactly one
    number fits between its neighbours, and the damaged text is one edit away from it.
    """

    def test_a_note_number_the_ocr_broke_is_repaired_from_its_neighbours(self):
        book = _book(F.broken_note_number_page)

        assert sorted(n.num for n in book.notes) == [9, 10, 11]
        assert [n.num for n in book.notes if n.marked] == [10]
        assert any(r.kind == "note_number" for r in book.repairs), book.repairs

    def test_the_repair_says_what_it_changed_and_why(self):
        book = _book(F.broken_note_number_page)
        repair = [r for r in book.repairs if r.kind == "note_number"][0]

        assert "1" in repair.detail and "10" in repair.detail, repair.detail

    def test_a_note_number_is_left_alone_when_more_than_one_number_would_fit(self):
        """The control. Between 9 and 13 the broken ``1`` could be 10, 11 or 12, and
        nothing on the page decides between them. A converter that picks one is
        inventing a citation."""
        book = _book(F.ambiguous_note_number_page)

        assert sorted(n.num for n in book.notes) == [1, 9, 13]
        assert not any(r.kind == "note_number" for r in book.repairs)
        assert "note_marker_mismatch" in book.page_reasons(0)

    def test_two_notes_damaged_in_a_row_are_read_off_the_gap_they_leave(self):
        """MEASURED: page 115 came back as ``117, 18, 1, 120``. One number at a time
        cannot be read -- 18 is two numbers away from anything -- but the pair is,
        because the gap holds exactly two numbers and the body marks both."""
        book = _book(F.two_damaged_note_numbers_page)

        assert sorted(n.num for n in book.notes) == [117, 118, 119, 120]
        assert sorted(n.num for n in book.notes if n.marked) == [118, 119]

    def test_a_number_that_reads_too_high_is_corrected_by_the_notes_that_follow_it(self):
        """MEASURED: page 117 came back as ``27, 28, 29, 38, 39, 32, 33, 34``.
        Reading left to right trusts 38 and 39 and then blames every number after
        them, which renumbers four correct citations to fix two damaged ones."""
        book = _book(F.note_number_read_too_high_page)

        assert sorted(n.num for n in book.notes) == [27, 28, 29, 30, 31, 32, 33, 34]
        assert sorted(n.num for n in book.notes if n.marked) == [30, 31]

    def test_a_note_damaged_at_the_top_of_a_page_is_read_from_the_page_before(self):
        """MEASURED: note 115 came back as ``1`` at the head of its own zone. On that
        page alone it has no predecessor to fail to ascend from, so the damage is
        invisible and the marker pointing at it binds to a neighbour instead -- a
        wrong citation, printed."""
        book = _book(F.damaged_first_note_pages)

        assert sorted(n.num for n in book.notes) == [113, 114, 115, 116, 117]
        assert 115 in [n.num for n in book.notes if n.marked]

    def test_notes_that_restart_each_chapter_are_not_renumbered(self):
        """The control. A book that numbers each chapter's notes from 1 is not a
        book whose numbers stopped ascending."""
        book = _book(F.restarting_note_numbers_pages)

        assert sorted(n.num for n in book.notes) == [1, 1, 2, 2, 3, 3]
        assert not any(r.kind == "note_number" for r in book.repairs), book.repairs


def test_a_word_broken_across_two_lines_of_a_footnote_is_put_back_together():
    """MEASURED on the acceptance book: five words survive the body text and are lost
    inside footnotes -- ``non-standard``, ``hour-priests``, ``astrologer-bashing`` --
    because a note's text was assembled by joining its lines and the body's was not.
    The conservation check is what found them, which is the whole reason it counts
    the side channels rather than forgiving them."""
    book = _book(F.hyphenated_note_page)
    note = [n for n in book.notes if n.num == 31][0]

    assert "non-standard" in note.text or "nonstandard" in note.text, note.text
    assert "non- standard" not in note.text
    assert book.conservation.ok, (book.conservation.missing, book.conservation.added)


class TestMarkersTheScannerReadAsLetters(object):
    """The dominant damage in the acceptance book, and the one that cost the most.

    MEASURED over pages 100-129: 21 of 26 model answers were refused by the word
    gate, and on 17 of them every difference was the same thing — the deterministic
    text carried ``Hephaestio.s°`` where the page prints ``Hephaestio`` and a
    superscript 50, and the model, which can see the page, wrote the 50. The gate
    was right to refuse an answer that does not match its source; the source was
    wrong. A number the page prints as a note, left unreferenced, standing as a
    short run of look-alike glyphs tight against the word it marks, is that note.
    """

    def test_a_marker_the_scanner_turned_into_letters_is_read_back(self):
        book = _book(F.glyph_marker_page)

        assert "Hephaestio.[50]" in assemble.page_source_text(book, 0)
        assert "s°" not in assemble.page_source_text(book, 0)

    def test_the_note_it_points_at_stops_being_an_orphan(self):
        book = _book(F.glyph_marker_page)

        assert [n.marked for n in book.notes if n.num == 50] == [True]

    def test_the_repair_is_recorded_with_what_it_read_and_what_it_consumed(self):
        book = _book(F.glyph_marker_page)
        repair = [r for r in book.repairs if r.kind == "marker_glyphs"]

        assert repair, book.repairs
        assert "50" in repair[0].detail and "s°" in repair[0].detail

    def test_the_page_still_accounts_for_every_printed_word(self):
        """The repair consumes letters, so the conservation check has to be told
        which ones rather than quietly forgiving a category of loss."""
        book = _book(F.glyph_marker_page)

        assert book.conservation.ok, (book.conservation.missing,
                                      book.conservation.added)

    def test_letters_stay_letters_when_the_page_prints_no_note_they_could_be(self):
        """The control. Without a printed, unreferenced note to match, there is no
        evidence this is a number at all."""
        book = _book(F.glyph_marker_without_its_note_page)

        assert "Hephaestio.s°" in assemble.page_source_text(book, 0)
        assert "note_marker_mismatch" in book.page_reasons(0)

    def test_a_marker_split_between_a_digit_and_a_letter_is_put_back_together(self):
        book = _book(F.split_glyph_marker_page)

        assert "above.[45]" in assemble.page_source_text(book, 0)
        assert [n.marked for n in book.notes if n.num == 45] == [True]

    def test_the_punctuation_in_front_of_a_resolved_marker_goes_with_it(self):
        """``fourth.'°`` + ``3`` is one marker, not a marker with two quotation
        marks in front of it."""
        book = _book(F.glyph_prefix_marker_page)
        text = assemble.page_source_text(book, 0)

        assert "fourth.[103]" in text, text
        assert "'°" not in text

    def test_nothing_is_repaired_when_two_of_the_page_s_notes_would_fit(self):
        """The control. Both 100 and 111 are one edit from what the scanner
        returned, and the page does not say which."""
        book = _book(F.ambiguous_glyph_marker_page)
        text = assemble.page_source_text(book, 0)

        assert "century.\"°" in text, text
        assert not any(r.kind == "marker_glyphs" for r in book.repairs)

    def test_a_printed_degree_is_not_a_marker(self):
        """The control that matters most: the acceptance book prints 207 real
        degree tokens. A marker is set tight against the word it marks; ``16°``
        stands after a space and is part of the sentence."""
        book = _book(F.degree_control_page)
        text = assemble.page_source_text(book, 0)

        assert "16° of Aries" in text, text
        assert "45° of Leo" in text, text

    def test_a_marker_whose_note_is_missing_is_not_bound_to_its_neighbour(self):
        """The control for the near tier. MEASURED on page 104: the body marks 59
        and the note zone holds 58, 60, 61 -- 59's text was swept into 58's. ``59``
        is one digit from ``58``, so a converter that reads every unmatched digit run
        as damage prints a citation the book does not make. The page is missing a
        note, and saying so is the answer."""
        book = _book(F.marker_for_a_missing_note_page)
        text = assemble.page_source_text(book, 0)

        assert "fourth century.59" in text, text
        assert not any(r.kind == "marker_glyphs" for r in book.repairs), book.repairs
        assert 58 not in [n.num for n in book.notes if n.marked]
