# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Synthetic regressions for source ordering, marker identity and publication."""

import pytest

from cps.services.reflow import assemble, build_epub, extract
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


# --------------------------------------------------- run-shape invariance at the seam

def _assembled_text(*lines, vocab=None):
    """The assembly seam exactly as `_page_elements` drives it: per-line runs,
    stitched across the line boundaries, tidied, read. A contract about the
    seam itself: the same visible line, however the spans fragment it, must
    come out with the same words and the same intentional spaces."""
    runs = []
    for spans in lines:
        line = extract.Line(
            spans=[extract.Span(text=text, size=11.3, font="n", flags=0,
                                bbox=(0.0, 0.0, 10.0, 10.0))
                   for text in spans],
            bbox=(0.0, 0.0, 10.0, 10.0))
        line_runs = assemble._line_runs(line, 0, {}, set(), [], [])
        runs = line_runs if not runs else assemble.stitch_runs(
            runs, line_runs, vocab=vocab)
    return assemble.plain_text(assemble.tidy(runs))


def test_a_line_reads_the_same_as_a_run_a_word_list_or_formatting_fragments():
    shapes = [
        ["The quick brown fox"],
        ["The ", "quick ", "brown ", "fox"],
        ["The", " ", "qu", "ick", " ", "brown", " ", "fox"],
    ]
    texts = [_assembled_text(shape) for shape in shapes]

    assert texts == ["The quick brown fox"] * 3, texts


def test_leading_trailing_and_whitespace_only_runs_change_nothing():
    shapes = [
        ["The fox"],
        [" ", "The fox"],
        ["The fox", "  "],
        ["The", " ", "fox"],
    ]
    texts = [_assembled_text(shape) for shape in shapes]

    assert texts == ["The fox"] * 4, texts


def test_separate_hyphen_and_apostrophe_runs_stay_with_their_words():
    """A hard hyphen, a soft hyphen and an apostrophe as spans of their own are
    parts of the words they touch, not word boundaries."""
    shapes = [
        ["well-read, don’t stop"],
        ["well", "-", "read, ", "don", "’", "t stop"],
        ["well­", "read,", " don’t ", "stop"],
    ]
    texts = [_assembled_text(shape) for shape in shapes]

    assert texts[0] == "well-read, don’t stop"
    assert texts[1] == texts[0], texts
    assert texts[2] == "well­read, don’t stop", texts


def test_a_real_wrap_heals_and_an_ordinary_boundary_keeps_its_space():
    shapes = [
        [["can’t con-"], ["struct the new box"]],
        [["can’t con", "-"], ["struct the new box"]],
        [["can’t con­"], ["struct the new box"]],
    ]
    texts = [_assembled_text(*shape, vocab={"construct"}) for shape in shapes]
    assert texts == ["can’t construct the new box"] * 3, texts

    boundaries = [
        [["quick"], ["brown fox"]],
        [["quick "], ["brown fox"]],
        [["quick"], [" brown fox"]],
    ]
    texts = [_assembled_text(*shape) for shape in boundaries]
    assert texts == ["quick brown fox"] * 3, texts


# ----------------------------------------------------- damaged note identities

def test_a_repair_over_a_scan_marks_the_note_identity_as_uncertain():
    """A repair over a scan marks the note identity as uncertain."""
    book = _book(lambda d: F.scan_note_identity_page(d, F.solid_png()))

    fragment = build_epub.page_fragment(book, 0)

    assert 'class="reflow-uncertain"' in fragment
    assert "damaged text layer" in fragment
    note = next(n for n in book.notes if n.num == 1)
    assert note.uncertain, "the note's identity leans on the residue pairing"
    assert note.marked, "the pairing still binds marker to note"
    assert note.text.startswith("Register"), "the note's text is kept as read"


def test_the_same_repair_on_a_clean_layer_is_not_marked_uncertain():
    """The control: no scan underneath, nothing damaged -- the same pairing is
    just a repair, with no uncertainty to show."""
    book = _book(F.born_digital_note_identity_page)

    fragment = build_epub.page_fragment(book, 0)

    assert "reflow-uncertain" not in fragment
    note = next(n for n in book.notes if n.num == 1)
    assert not note.uncertain


# ----------------------------------------------------------- page-turn joins (defect B)

def test_a_sentence_broken_at_a_page_turn_is_one_paragraph():
    """The printed sentence crosses a page boundary with a lowercase continuation."""
    book = _book(F.defect_b_pages)

    joined = [p for p in _paras(book) if 'sanded lids are packed neatly' in p]

    assert joined, _paras(book)


def test_no_orphan_fragment_is_left_behind_by_the_join():
    """A half-join leaves the second half standing as its own paragraph, which reads
    as a new paragraph that begins in lowercase."""
    book = _book(F.defect_b_pages)

    assert not any(p.startswith("are packed neatly") for p in _paras(book))


def test_footnotes_between_the_two_halves_do_not_block_the_join():
    """This is the measured cause: the previous converter required the previous
    element to be a paragraph, and the intervening footnote elements made it a note.
    The join must still happen with three notes printed between the halves."""
    book = _book(F.defect_b_pages)

    assert [n.num for n in book.notes] == [160, 161, 166]
    assert len([p for p in _paras(book) if 'sanded lids are packed neatly' in p]) == 1


def test_a_finished_sentence_is_not_joined_across_the_page_turn():
    """The control: the previous page ends on a full stop. The next page opens
    lowercase, which is exactly the signal the join looks for, and it must still
    refuse — otherwise every page turn fuses two paragraphs."""
    book = _book(F.defect_b_pages, F.defect_c_page)

    assert any(p.endswith('with every freshly folded cotton cover inside that sample cabinet.') for p in _paras(book))
    assert any(p.startswith('before samples reached their blue cabinet') for p in _paras(book))


class TestFootnotesThatRunOntoTheNextPage(object):
    """An unnumbered note continuation binds across a page break."""

    def test_the_tail_of_a_note_is_not_spliced_onto_a_sentence(self):
        book = _book(F.runover_footnote_pages)

        assert not any('cupboard needs during appears in' in p for p in _paras(book)), _paras(book)

    def test_the_sentence_the_page_turn_split_is_finished_by_its_own_page(self):
        book = _book(F.runover_footnote_pages)

        assert any('large wooden cupboard needs during the height of the shelves'
                   in p for p in _paras(book)), _paras(book)

    def test_no_orphan_paragraph_is_left_where_the_page_turned(self):
        book = _book(F.runover_footnote_pages)

        assert not any(p.startswith('the height of the shelves') for p in _paras(book))

    def test_the_tail_is_set_as_a_note_of_the_page_that_prints_it(self):
        """It is not moved back to the page its number is on: the model is shown a
        picture of the page the words are printed on, and its answer is measured
        against that page's text."""
        book = _book(F.runover_footnote_pages)

        assert [n.text for n in book.notes if n.pno == 1 and n.num is None] == \
            ['filed under Workshop Plans, 5: 7, with a brass hinge sketched beside it.']

    def test_the_page_the_model_is_shown_still_reads_the_way_it_is_printed(self):
        """The tail stands between the body and the numbered notes, where the page
        sets it, so the words the model is asked for are the words on the page."""
        book = _book(F.runover_footnote_pages)
        text = assemble.page_source_text(book, 1)

        assert 'filed under Workshop Plans, 5: 7' in text
        assert text.index('cupboard needs during') < text.index('filed under Workshop Plans') \
            < text.index("[259]")

    def test_nothing_is_lost_when_the_tail_changes_hands(self):
        book = _book(F.runover_footnote_pages)

        assert book.conservation.ok, (book.conservation.missing, book.conservation.added)

    def test_a_note_that_finished_its_sentence_did_not_run_over(self):
        """The control, and the whole of the evidence: a page's last note either
        stops mid-sentence or it does not. Small type under the body is only a
        note's tail when there is a note for it to be the tail of."""
        book = _book(F.finished_footnote_pages)

        assert not [n for n in book.notes if n.pno == 1 and n.num is None]
        assert any('filed under Workshop Plans, 5: 7, with a brass hinge sketched beside it.' in p for p in _paras(book)), _paras(book)


# ---------------------------------------------------------------------- line joining

def test_a_line_break_hyphen_is_repaired_but_a_printed_compound_is_kept():
    """`work-` + `bench` is a typesetter's break; `Bench-` + `Top` is a real
    compound. Getting this wrong produces either `work- bench` or `BenchTop`."""
    book = _book(F.hyphenated_page)

    text = " ".join(_paras(book))

    assert "workbench" in text
    assert "Bench-Top" in text


def test_a_heading_that_ends_mid_word_is_prose_again():
    """The OCR shape where a bold block of prose reads as a heading: the element
    ends with a hyphen, and a real heading never ends mid-word. Demoted, the
    wrap heals exactly once."""
    book = _book(F.bold_body_block_page)

    headings = [el for el in book.elements if el.kind == "h"]
    text = " ".join(el.text for el in book.elements)

    assert "political exhibit" in text, text
    assert "politi-cal" not in text
    assert book.conservation.ok, book.conservation.to_dict()
    assert not any(h.text.rstrip().endswith("-") for h in headings)


def test_a_hyphenated_word_split_across_spans_is_still_one_word():
    """The same break set as word-per-span runs. The trailing space
    run must not survive between the halves."""
    book = _book(F.hyphenated_multispan_page)

    text = " ".join(_paras(book))

    assert "three-shelf" in text, text
    assert "workpa cket" not in text
    assert book.conservation.ok, book.conservation.to_dict()


def test_a_page_turn_hyphen_keeps_its_hyphen():
    """The source counter never heals across a page turn, so the
    output may not either -- a wrap-break and a printed compound cannot be told
    apart there, and the hyphen the page printed stays."""
    book = _book(F.page_turn_compound_hyphen_pages)

    text = " ".join(_paras(book))

    assert "bench-built" in text, text
    assert "spearbuilt" not in text
    assert book.conservation.ok, book.conservation.to_dict()


def test_a_hyphen_heals_only_into_a_word_of_this_book():
    """Synthetic sentence-break control: ``eighth-is`` is a sentence break (``eighthis``
    is not a word of this book) and must keep its hyphen; ``under-standing``
    wraps ``understanding``, which the book prints whole nearby, and must heal.
    """
    book = _book(F.hyphen_heal_needs_a_real_word_pages)

    text = " ".join(_paras(book))

    assert "eighth-is" in text, text
    assert "eighthis" not in text
    assert "understanding of the layout follows" in text, text
    assert book.conservation.ok, book.conservation.to_dict()


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
    """Repairs require neighboring printed note evidence and preserve uncertainty."""

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
        """Two notes damaged in a row are read off the gap they leave."""
        book = _book(F.two_damaged_note_numbers_page)

        assert sorted(n.num for n in book.notes) == [117, 118, 119, 120]
        assert sorted(n.num for n in book.notes if n.marked) == [118, 119]

    def test_a_number_that_reads_too_high_is_corrected_by_the_notes_that_follow_it(self):
        """A number that reads too high is corrected by the notes that follow it."""
        book = _book(F.note_number_read_too_high_page)

        assert sorted(n.num for n in book.notes) == [27, 28, 29, 30, 31, 32, 33, 34]
        assert sorted(n.num for n in book.notes if n.marked) == [30, 31]

    def test_a_repaired_number_is_remembered_beside_what_the_scan_returned(self):
        """A repaired number is remembered beside what the scan returned."""
        book = _book(F.note_number_read_too_high_page)

        assert book.renumbered_notes(0) == [(38, 30), (39, 31)]

    def test_a_page_whose_numbers_were_never_repaired_remembers_nothing(self):
        """The control. Between 9 and 13 nothing was repaired, so there is no second
        reading to warn anybody about."""
        book = _book(F.ambiguous_note_number_page)

        assert book.renumbered_notes(0) == []

    def test_a_note_damaged_at_the_top_of_a_page_is_read_from_the_page_before(self):
        """A carried note identity uses preceding-page numbering evidence."""
        book = _book(F.damaged_first_note_pages)

        assert sorted(n.num for n in book.notes) == [113, 114, 115, 116, 117]
        assert 115 in [n.num for n in book.notes if n.marked]

    def test_notes_that_restart_each_chapter_are_not_renumbered(self):
        """The control. A book that numbers each chapter's notes from 1 is not a
        book whose numbers stopped ascending."""
        book = _book(F.restarting_note_numbers_pages)

        assert sorted(n.num for n in book.notes) == [1, 1, 2, 2, 3, 3]
        assert not any(r.kind == "note_number" for r in book.repairs), book.repairs


class TestNotesTheScannerSweptAway(object):
    """Missing note identities remain distinguishable from unmarked notes."""

    def test_the_number_the_page_stops_printing_is_named(self):
        book = _book(F.swept_note_page)

        assert book.swept_notes(0) == [59]

    def test_it_is_not_the_question_unmarked_notes_answers(self):
        """The two lists are different damage. 58 and 60 are printed and unreferenced;
        59 is not printed at all, which is why it needs saying separately."""
        book = _book(F.swept_note_page)

        assert book.unmarked_notes(0) == [58, 60]

    def test_the_page_says_so_where_a_model_will_be_told(self):
        book = _book(F.swept_note_page)

        assert "note_number_swept" in book.page_reasons(0)

    def test_a_page_whose_numbering_runs_straight_through_reports_nothing(self):
        """The control. Four notes, no gap, nothing swept."""
        book = _book(F.two_damaged_note_numbers_page)

        assert sorted(n.num for n in book.notes) == [117, 118, 119, 120]
        assert book.swept_notes(0) == []

    def test_two_numbers_missing_at_once_are_not_read_as_one_swept_note(self):
        """One missing number has one place to go; two do not. The page prints 58 and
        61 and nothing on it says where 59 stops and 60 starts, so a converter that
        splits the note there is inventing a citation boundary."""
        book = _book(F.two_swept_notes_page)

        assert book.swept_notes(0) == []

    def test_a_number_the_scan_shortened_is_not_a_number_the_scan_swept(self):
        """A number the scan shortened is not a number the scan swept."""
        book = _book(F.shortened_note_number_page)

        assert sorted(n.num for n in book.notes) == [3, 29, 31], \
            "the fixture stopped reproducing the damage"
        assert book.swept_notes(0) == []

    def test_a_page_where_nothing_else_is_wrong_still_says_it(self):
        """A page where nothing else is wrong still says it."""
        book = _book(F.quietly_swept_note_page)

        assert book.swept_notes(0) == [125]
        assert book.page_reasons(0) == ["note_number_swept"]


def test_a_word_broken_across_two_lines_of_a_footnote_is_put_back_together():
    """A word broken across two lines of a footnote is put back together."""
    book = _book(F.hyphenated_note_page)
    note = [n for n in book.notes if n.num == 31][0]

    assert "non-standard" in note.text or "nonstandard" in note.text, note.text
    assert "non- standard" not in note.text
    assert book.conservation.ok, (book.conservation.missing, book.conservation.added)


class TestMarkersTheScannerReadAsLetters(object):
    """Letter-shaped marker debris is repaired only with compatible note evidence."""

    def test_a_marker_the_scanner_turned_into_letters_is_read_back(self):
        book = _book(F.glyph_marker_page)

        assert 'Checklist.[50]' in assemble.page_source_text(book, 0)
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

        assert 'Checklist.s°' in assemble.page_source_text(book, 0)
        assert "note_marker_mismatch" in book.page_reasons(0)

    def test_a_marker_split_between_a_digit_and_a_letter_is_put_back_together(self):
        book = _book(F.split_glyph_marker_page)

        assert "today.[45]" in assemble.page_source_text(book, 0)
        assert [n.marked for n in book.notes if n.num == 45] == [True]

    def test_the_punctuation_in_front_of_a_resolved_marker_goes_with_it(self):
        """``nearby.'°`` + ``3`` is one marker, not a marker with two quotation
        marks in front of it."""
        book = _book(F.glyph_prefix_marker_page)
        text = assemble.page_source_text(book, 0)

        assert "nearby.[103]" in text, text
        assert "'°" not in text

    def test_nothing_is_repaired_when_two_of_the_page_s_notes_would_fit(self):
        """The control. Both 100 and 111 are one edit from what the scanner
        returned, and the page does not say which."""
        book = _book(F.ambiguous_glyph_marker_page)
        text = assemble.page_source_text(book, 0)

        assert 'cabinet."°' in text, text
        assert not any(r.kind == "marker_glyphs" for r in book.repairs)

    def test_a_printed_degree_is_not_a_marker(self):
        """A printed degree is not a marker."""
        book = _book(F.degree_control_page)
        text = assemble.page_source_text(book, 0)

        assert '16° on its rim' in text, text
        assert '45°' in text, text

    def test_a_marker_whose_note_is_missing_is_not_bound_to_its_neighbour(self):
        """A marker whose note is missing is not bound to its neighbour."""
        book = _book(F.marker_for_a_missing_note_page)
        text = assemble.page_source_text(book, 0)

        assert 'beside that drawer.59' in text, text
        assert not any(r.kind == "marker_glyphs" for r in book.repairs), book.repairs
        assert 58 not in [n.num for n in book.notes if n.marked]

    def test_a_reading_that_breaks_the_page_s_order_of_citation_is_refused(self):
        """A reading that breaks the page s order of citation is refused."""
        book = _book(F.out_of_order_glyph_marker_page)
        text = assemble.page_source_text(book, 0)

        assert "[141]" not in text.split("\n")[0], text
        assert 141 in book.unmarked_notes(0), book.unmarked_notes(0)

    def test_the_residue_is_left_standing_for_the_model_to_read(self):
        """Refusing the reading has to leave the evidence on the page. The model is
        shown the raster, so ``window."'`` is a question it can answer; ``window.[141]``
        is a wrong answer it would have to be believed over."""
        book = _book(F.out_of_order_glyph_marker_page)

        assert 'window."\'' in assemble.page_source_text(book, 0)

    def test_the_reading_that_does_fit_the_page_s_order_is_still_made(self):
        """The control on the same page. ``Handbook.''s`` reads as 115 and 135 is
        the only note it could be; nothing stands in front of it to contradict the
        order, and refusing every recovery would cost the page its one good one."""
        book = _book(F.out_of_order_glyph_marker_page)

        assert "Handbook.[135]" in assemble.page_source_text(book, 0)

    def test_a_page_whose_notes_restart_is_not_held_to_an_ascending_order(self):
        """A page whose notes restart is not held to an ascending order."""
        book = _book(F.chapter_restart_glyph_marker_page)

        assert 'cupboard.[111]' in assemble.page_source_text(book, 0)
        assert [n.marked for n in book.notes if n.num == 111] == [True]


class TestANotesOwnNumberPrintedTwice(object):
    """A note identity is printed once, even when markup also carries its number."""

    def test_the_scan_of_the_number_does_not_open_the_note_it_numbers(self):
        book = _book(F.glyph_note_number_page)
        note = [n for n in book.notes if n.num == 151]

        assert note, [n.num for n in book.notes]
        assert note[0].text.startswith("Ledger"), note[0].text

    def test_the_note_keeps_its_number_and_every_word_of_its_citation(self):
        book = _book(F.glyph_note_number_page)
        note = [n for n in book.notes if n.num == 151][0]

        assert "Fixturebook" in note.text and "p. 445" in note.text

    def test_the_page_shown_to_the_model_prints_the_number_once(self):
        book = _book(F.glyph_note_number_page)
        source = assemble.page_source_text(book, 0)

        assert "[151] Ledger" in source
        assert "Is'" not in source

    def test_the_repair_says_which_characters_it_read_as_the_number(self):
        book = _book(F.glyph_note_number_page)
        repair = [r for r in book.repairs if r.kind == "note_number_glyphs"]

        assert repair, book.repairs
        assert "151" in repair[0].detail and "Is'" in repair[0].detail

    def test_the_page_still_accounts_for_every_printed_word(self):
        """The strip takes letters out of the text layer, so it has to declare them
        the way every other repair here does."""
        book = _book(F.glyph_note_number_page)

        assert book.conservation.ok, (book.conservation.missing,
                                      book.conservation.added)

    def test_wreckage_that_does_not_spell_this_note_s_number_is_left_standing(self):
        """The control. ``1"`` in front of note 105 is two digits short of the number
        and spells 111, which is not this note's number and not a number this page
        prints. There is no evidence of what it was, so it stays where the scanner
        put it and a model gets to look at the page."""
        book = _book(F.split_glyph_note_number_page)
        note = [n for n in book.notes if n.num == 105]

        assert note, [n.num for n in book.notes]
        assert note[0].text.startswith('"'), note[0].text
        assert not [r for r in book.repairs if r.kind == "note_number_glyphs"]

    def test_a_note_that_opens_on_a_word_keeps_the_word(self):
        """The control that bounds the rule. ``I`` reads as a 1 and stands exactly
        where a number would, and it is this note's first word."""
        book = _book(F.note_opening_with_a_letter_word_page)
        note = [n for n in book.notes if n.num == 78]

        assert note, [n.num for n in book.notes]
        assert note[0].text.startswith('I can place'), note[0].text
