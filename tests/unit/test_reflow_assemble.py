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
