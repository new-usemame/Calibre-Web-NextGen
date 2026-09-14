# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The word-preservation gate is the only thing standing between a reader and a
model that quietly rewrote their book.

Operator rule (DECISIONS.md 4): "don't fuck with the text as much as possible" —
a page whose words changed must ship the deterministic text instead. Every test
here crafts a specific violation and asserts the gate REFUSES it; a gate that
cannot fail is worth nothing, so the crafted-violation cases are the point.

The case-sensitivity cases exist because a measured probe caught a frontier model
silently normalising the printed brand `olmOCR` to `OLMOCR` — output that reads
more "correct" and is wrong about what is printed.
"""

import pytest

from cps.services.reflow import gate

pytestmark = pytest.mark.unit


SRC = (
    "so it should be used with caution.[160] It is in this text that he is called "
    "Serapio of Alexandria. The list is incomplete, with the last page of the "
    "manuscript missing."
)


def _html(body):
    return "<p>%s</p>\n" % body


def test_faithful_structure_edit_passes():
    """Marking a heading and splitting paragraphs changes no words, so it passes.

    Breaks if the gate starts counting markup or whitespace as content."""
    out = (
        '<h3>Serapio of Alexandria</h3>\n'
        '<p>so it should be used with caution.'
        '<a class="noteref" href="#fn_160">160</a> It is in this text that he is '
        'called Serapio of Alexandria. The list is incomplete, with the last page '
        'of the manuscript missing.</p>\n'
    )
    src = SRC.replace("Serapio of Alexandria. The list", "Serapio of Alexandria. The list")
    result = gate.check_word_preservation("Serapio of Alexandria\n" + src, out)

    assert result.verdict == "PASS", result.unexplained


def test_a_dropped_content_word_fails():
    """A model that silently drops a word must be caught.

    Measured: qwen3-vl-8b dropped `refinement.` from a real page and the output
    still looked reasonable to a human reader."""
    out = _html(SRC.replace("[160]", " 160 ").replace("incomplete, ", ""))

    result = gate.check_word_preservation(SRC, out)

    assert result.verdict == "FAIL"
    assert any("incomplete," in " ".join(d.source) for d in result.unexplained)


def test_invented_prose_fails():
    """A model that adds explanatory prose of its own must be caught.

    Measured: glm-5.3-flash added 67 tokens of commentary to one page."""
    out = _html(SRC.replace("[160]", " 160 ") + " This passage describes a manuscript.")

    result = gate.check_word_preservation(SRC, out)

    assert result.verdict == "FAIL"
    assert result.invented_count > 0


def test_case_only_change_fails():
    """`olmOCR` -> `OLMOCR` is a silent substitution, not a formatting nicety."""
    src = "The olmOCR pipeline reads olmOCR output."
    out = _html("The OLMOCR pipeline reads OLMOCR output.")

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "FAIL"
    assert result.case_only_count == 2


def test_ocr_damaged_token_must_be_reproduced_verbatim():
    """R3: the damaged token stays. "Correcting" it is exactly what we forbid."""
    src = "the domicile lords of the lurninaries. Rhetorius cited him"
    out = _html("the domicile lords of the luminaries. Rhetorius cited him")

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "FAIL"


def test_footnote_marker_split_is_an_allowed_edit_and_is_reported():
    """Detaching a marker the OCR fused onto a word is a structural edit we make
    on purpose — allowed, but never silent: it lands in allowed_hits."""
    src = "used with caution.160 It is in this text"
    out = _html('used with caution.<a class="noteref" href="#fn_160">160</a> It is in this text')

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "PASS"
    assert result.allowed_hits, "an intentional edit must still be reported"


def test_an_allowance_cannot_hide_a_lost_word():
    """The allow-list must be a narrow re-tokenisation rule, not a wildcard.

    A re-split that also drops a word is not the allowed edit."""
    src = "used with caution.160 It is in this text"
    out = _html("used with caution. 160 It is in this")

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "FAIL"


def test_gate_is_not_applicable_when_the_page_has_no_source_text():
    """An image-only page has nothing to preserve; claiming PASS there would be an
    instrument reporting on an empty subject."""
    result = gate.check_word_preservation("   ", _html("4 Results"))

    assert result.verdict == "NOT_APPLICABLE"


def test_the_models_trailing_json_contract_line_is_not_page_content():
    """The prompt requires a final JSON line. Counting it as invented prose would
    fail every well-behaved page."""
    out = _html(SRC.replace("[160]", " 160 ")) + '\n{"uncertain":[],"notes":"clean page"}'

    result = gate.check_word_preservation(SRC, out)

    assert result.verdict == "PASS", result.unexplained


class TestMarkerTheScannerAte:
    """A superstitial class-less group would read as unrelated tests; these four are
    one rule seen from four sides.

    MEASURED on the acceptance book, page index 100: the scan prints
    ``King Ammon.[35]`` and the text layer returns ``King Ammon."`` — the whole
    superscript arrives as one quotation mark. 424 of the book's notes lose their
    marker that way. The deterministic pass cannot tell that quotation mark from a
    real one (same font, same glyph id, same advance width: MEASURED), so it leaves
    it alone and routes the page. The model can see the scan. This is the allowance
    that lets it say so — and it is an allowance, so it is bounded and it is
    reported.
    """

    SRC = ('the texts takes the form of a letter from Asclepius addressed to King '
           'Ammon." Modern scholars have long wondered about the name.')

    def _out(self, marker='<a class="noteref" href="#fn_35">35</a>'):
        return _html(self.SRC.replace('Ammon."', "Ammon." + marker))

    def test_a_marker_read_as_a_quotation_mark_may_be_restored(self):
        result = gate.check_word_preservation(self.SRC, self._out(),
                                              recoverable_markers=[35])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits), \
            "a repair nobody can audit is not a repair"

    def test_the_quotation_mark_stays_punctuation_when_no_note_is_missing(self):
        """The default. Every page of dialogue in every book depends on it: 555 of
        this book's ``."`` closings sit on pages with no unmarked note at all."""
        assert gate.check_word_preservation(self.SRC, self._out()).verdict == "FAIL"

    def test_only_a_number_the_page_actually_prints_may_be_restored(self):
        """The model reading ``36`` off a page whose missing note is 35 is a guess,
        and a guess that lands in the reader's book as a link to the wrong source."""
        out = self._out('<a class="noteref" href="#fn_36">36</a>')

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[35]).verdict == "FAIL"

    def test_the_restoration_cannot_carry_a_second_change_with_it(self):
        """The allowance is a single character becoming a single number. Anything
        else riding along with it is what an allow-list is for catching."""
        out = self._out().replace("Modern scholars have long ", "Modern scholars ")

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[35]).verdict == "FAIL"

    def test_an_apostrophe_and_quote_together_are_still_one_marker(self):
        """MEASURED shape C3: the superscript comes back as ``.'"`` — two characters
        for one number. Refusing that would leave the commonest damage unrepairable."""
        src = "the domicile lords of the luminaries.'\" Rhetorius cited him later."
        out = _html(src.replace(".'\"", '.<a class="noteref" href="#fn_161">161</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[161])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_a_whole_word_of_punctuation_is_not_a_marker(self):
        """One or two characters is the measured damage. Three would start letting
        the model delete printed punctuation and call it a repair."""
        src = "the luminaries.'\"' Rhetorius cited him later on in the same chapter."
        out = _html(src.replace(".'\"'", '.<a class="noteref" href="#fn_161">161</a>'))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[161]).verdict == "FAIL"

    def test_a_superscript_the_scanner_read_as_letters_may_also_be_restored(self):
        """MEASURED on page index 110: the text layer returns ``reasons.ms`` where
        the page prints ``reasons.`` with a superscript 105. The scanner does not
        only leave quotation marks behind -- across the flagged readings of thirty
        pages it returned ``ms`` for 105, ``si`` for 51, ``s\u00b0`` for 50 and
        ``.s6`` for 56. A rule that knows only about quotation marks calls the
        commonest repair on this book a word loss."""
        src = "he gives no reasons.ms Later writers repeat the omission."
        out = _html(src.replace("reasons.ms",
                                'reasons.<a class="noteref" href="#fn_105">105</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[105])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_the_letters_may_stand_for_the_stop_as_well_as_the_number(self):
        """MEASURED on page index 106: the page prints ``of it.`` with a superscript
        80, and the text layer returns ``its\u00b0`` -- two characters carrying a full
        stop and a two-digit number between them. Nothing in that token is a
        quotation mark, so the letters are the only reading of the superscript there
        is, and the stop the model supplies is punctuation it could not have taken
        from anywhere else."""
        src = "does not survive, although we do possess a later summary of its\u00b0 In the summary"
        out = _html(src.replace("its\u00b0",
                                'it.<a class="noteref" href="#fn_80">80</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[80])

        assert result.verdict == "PASS", result.unexplained
        assert result.recovered_markers == [80]

    def test_a_short_word_is_not_a_superscript_however_orphaned_the_note_is(self):
        """The hole the letters open, closed. A quotation mark is never a word, so
        replacing a whole one is safe; ``ms`` and ``as`` and ``is`` are words, and a
        model that deletes one and writes an orphaned note number in its place has
        taken a word out of the book."""
        src = "the ms of the text was copied later by an unknown scribe."
        out = _html(src.replace(" ms ", ' <a class="noteref" href="#fn_105">105</a> '))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[105]).verdict == "FAIL"

    def test_letters_inside_a_word_are_not_a_superscript(self):
        """MEASURED on page index 106: the model read the chart label ``Tl la`` as
        ``T11a`` and was refused. A superscript follows the word it annotates; it
        does not live in the middle of one, and a rule that let it would let a model
        respell any word whose digits happen to name an orphaned note."""
        src = "the chart labels Tl la and Ti lb are set in the table."
        out = _html(src.replace("Tl la", "T11a"))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[11]).verdict == "FAIL"

    def test_the_full_stop_the_scanner_ate_with_the_marker_comes_back_with_it(self):
        """MEASURED on page index 104: the page prints ``brief.`` with a superscript
        68 after it, and the text layer returns ``brief"`` -- one straight quote
        standing for the stop and the number together. The model answered ``brief.``
        plus note 68, which is exactly what the page prints, and the gate refused the
        whole page for supplying the stop.

        Both halves of that answer are already allowed on their own: a quotation mark
        that comes back as a full stop is punctuation, and a quotation mark that comes
        back as a note this page never referred to is a recovered marker. Refusing
        them together is an accident of where the tokeniser drew the boundary, and on
        that page it cost the reader a correctly lifted section heading as well.
        """
        src = 'delineations of the decans, although they are somewhat brief" Thrasyllus'
        out = _html(src.replace('brief"',
                                'brief.<a class="noteref" href="#fn_68">68</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[68])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_a_letter_may_not_go_missing_beside_a_recovered_marker(self):
        """The control, and a hole this book walked straight into: ``brief"`` used to
        be allowed to become ``brie68``, because a rule that lets the scanner render a
        superscript as letters will read the ``f`` as one of them. A token holding a
        quotation mark has already explained its superscript. Punctuation may differ
        around the number because punctuation is not a word; the letters may not."""
        src = 'delineations of the decans, although they are somewhat brief" Thrasyllus'

        for answer in ('brie.<a class="noteref" href="#fn_68">68</a>',
                       'brie<a class="noteref" href="#fn_68">68</a>'):
            out = _html(src.replace('brief"', answer))

            assert gate.check_word_preservation(
                src, out, recoverable_markers=[68]).verdict == "FAIL", answer

    def test_a_printed_number_may_not_change_beside_a_recovered_marker(self):
        """The other control, and the one that matters in a book of citations: the
        page numbers, dates and section numbers around the marker are words."""
        src = 'Henceforth Porphyry, Introduction, p. 124." Rhetorius cites it twice.'
        out = _html(src.replace('124."',
                                '125.<a class="noteref" href="#fn_68">68</a>'))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[68]).verdict == "FAIL"

    def test_one_missing_note_cannot_be_spent_twice(self):
        src = 'to King Ammon." Later, and again to King Thoth." The scribe agrees.'
        out = _html(src.replace('Ammon."', 'Ammon.<a class="noteref" href="#fn_35">35</a>')
                       .replace('Thoth."', 'Thoth.<a class="noteref" href="#fn_35">35</a>'))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[35]).verdict == "FAIL"


def test_curly_and_straight_quotes_are_the_same_word():
    """Typographic normalisation is not a content change."""
    src = "he said “no” and left"
    out = _html('he said "no" and left')

    assert gate.check_word_preservation(src, out).verdict == "PASS"


class TestStructuralSchemaGate:
    """G3: the markup contract. A noteref with no aside is a dead link on a Kobo."""

    def test_disallowed_tag_is_rejected(self):
        result = gate.check_structure('<p>text</p><script>alert(1)</script>', ladder=[1, 2, 3])

        assert not result.ok
        assert any("script" in r for r in result.reasons)

    def test_heading_outside_the_skeleton_ladder_is_rejected(self):
        """Probes showed models disagree on absolute heading level for identical
        pages; the deterministic ladder is the authority."""
        result = gate.check_structure("<h4>Serapio of Alexandria</h4>", ladder=[1, 2, 3])

        assert not result.ok

    def test_noteref_without_a_matching_aside_is_rejected(self):
        html = '<p>text<a class="noteref" href="#fn_160">160</a></p>'

        result = gate.check_structure(html, ladder=[1, 2, 3])

        assert not result.ok
        assert any("160" in r for r in result.reasons)

    def test_matched_noteref_and_aside_pass(self):
        html = ('<p>text<a class="noteref" href="#fn_160">160</a></p>'
                '<aside class="footnote" id="fn_160">Beck.</aside>')

        assert gate.check_structure(html, ladder=[1, 2, 3]).ok

    def test_figure_without_a_caption_is_rejected(self):
        result = gate.check_structure("<figure><img src='x.png'/></figure>", ladder=[1])

        assert not result.ok


class TestANoteIsTheNumberItIdentifies(object):
    """Whether the number is printed in the note or only in its id is not a fact
    about the book.

    MEASURED on the acceptance book, one run, one model, two pages: page 100 came
    back as ``<aside id="fn_33">33 Heilen, ...`` and page 103 as
    ``<aside id="fn_58">Cumont, ...``. The deterministic reader hands the model
    ``[58] Cumont, ...`` either way, so the second page was refused for losing a word
    it had not lost -- the number was in the markup, in the one place the word gate
    does not look. The prompt now asks for it in the text, and an answer that leaves
    it in the id is read as though it were there, because a rule a provider can
    quietly decline is not a rule to refuse somebody's book over.
    """

    SOURCE = "text of the page[58]\n\n[58] Cumont, Astrology and Religion, p. 76."
    BODY = '<p>text of the page<a class="noteref" href="#fn_58">58</a></p>'

    def test_a_note_that_prints_its_number_passes(self):
        html = self.BODY + '<aside class="footnote" id="fn_58">58 Cumont, ' \
                           'Astrology and Religion, p. 76.</aside>'
        assert gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_a_note_that_leaves_its_number_in_the_id_passes_too(self):
        html = self.BODY + '<aside class="footnote" id="fn_58">Cumont, ' \
                           'Astrology and Religion, p. 76.</aside>'
        assert gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_a_number_already_printed_is_not_printed_twice(self):
        html = '<aside class="footnote" id="fn_58">58 Cumont.</aside>'
        assert gate.number_the_notes(gate.number_the_notes(html)) == \
            gate.number_the_notes(html)
        assert gate.number_the_notes(html).count("58") == 2      # the id and the text

    def test_the_number_supplied_is_the_note_s_own_and_nothing_else(self):
        """A crafted violation: the aside says one number and is identified by
        another. Reading the id in place of the text would launder the wrong number
        into the page."""
        html = self.BODY + '<aside class="footnote" id="fn_58">99 Cumont, ' \
                           'Astrology and Religion, p. 76.</aside>'
        assert not gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_an_aside_that_is_not_numbered_at_all_is_left_alone(self):
        html = '<aside class="footnote" id="fn_note-a">Cumont.</aside>'
        assert gate.number_the_notes(html) == html


class TestABlockThePageSetInTheWrongPlace(object):
    """Defect A, seen from the gate.

    MEASURED on page 121 of the acceptance book: the text layer returns the section
    head ``Serapio of Alexandria (First Century CE?)`` forty lines below where it is
    printed, after the last footnote marker of the section it opens. The model does
    exactly what SPEC 8.2 asks -- makes it an ``<h2>`` and puts it back -- and a gate
    that compares two sequences reads a relocation as a paragraph lost in one place
    and invented in another.

    Preservation is about words, not positions: nothing is lost, nothing is gained,
    and the run stays whole. What must still be refused is a page whose words were
    rearranged rather than whose blocks were moved.
    """

    HEAD = "Serapio of Alexandria (First Century CE?)"
    BODY = ("Serapio of Alexandria was an astrologer who wrote on inceptional "
            "astrology, although only fragments of his work survive.")
    TAIL = ("There is a long list of definitions attributed to Serapio that was "
            "edited by Cumont in the Catalogus.")

    def _source(self):
        return "%s\n\n%s\n\n%s" % (self.BODY, self.HEAD, self.TAIL)

    def test_a_heading_the_model_put_back_where_it_belongs_is_not_a_lost_paragraph(self):
        out = ("<h2>%s</h2>\n<p>%s</p>\n<p>%s</p>\n" % (self.HEAD, self.BODY, self.TAIL))

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "PASS", (result.missing, result.invented)
        assert any(d.kind == "moved" for d in result.allowed_hits), result.allowed_hits

    def test_the_move_is_recorded_rather_than_quietly_forgiven(self):
        """An allowance nobody can see is an allowance nobody can audit."""
        out = ("<h2>%s</h2>\n<p>%s</p>\n<p>%s</p>\n" % (self.HEAD, self.BODY, self.TAIL))

        result = gate.check_word_preservation(self._source(), out)
        moved = [d for d in result.allowed_hits if d.kind == "moved"]

        assert " ".join(moved[0].source) == self.HEAD, moved[0].source

    def test_a_page_whose_words_were_rearranged_is_still_refused(self):
        """The control that matters. Every word survives and the page is nonsense:
        a bag-of-words gate calls this clean."""
        words = self._source().split()
        out = "<p>%s</p>\n" % " ".join(words[::-1])

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "FAIL"

    def test_a_block_that_moved_and_lost_a_word_on_the_way_is_refused(self):
        out = ("<h2>%s</h2>\n<p>%s</p>\n<p>%s</p>\n"
               % (self.HEAD.replace("Alexandria ", ""), self.BODY, self.TAIL))

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "FAIL"

    def test_a_heading_the_model_invented_is_refused(self):
        out = ("<h2>Teucer of Babylon (First Century BCE)</h2>\n"
               "<p>%s</p>\n<p>%s</p>\n<p>%s</p>\n"
               % (self.BODY, self.HEAD, self.TAIL))

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "FAIL"


class TestOrphanPunctuationIsNotAWord(object):
    """What is left of a superscript after the scanner has destroyed it.

    MEASURED on pages 114 and 115 of the acceptance book: the only difference
    between the model's answer and the page is one orphan quotation mark, the
    wreckage of a note marker whose note this page has already resolved by another
    route. Refusing a perfect page over a stray quote is the gate measuring the
    wrong subject -- the rule is word preservation, and a lone quote is not a word.
    """

    SRC = ("possibly from the same period as the Anthologies of Vettius Valens. \" "
           "The list is incomplete, with the last page missing.")

    def test_a_stray_quote_the_scanner_left_behind_may_be_dropped(self):
        out = _html(self.SRC.replace(' " ', " "))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "PASS", (result.missing, result.invented)
        assert any(d.kind == "punctuation" for d in result.allowed_hits)

    def test_a_stray_quote_turned_into_a_citation_is_refused(self):
        """MEASURED on page 104: the page marks 59 and prints notes 58, 60, 61, so
        the model wrote 59 where the quote was. It may well be right, and the page
        cannot confirm it, so the page is refused rather than printed."""
        out = _html(self.SRC.replace('"', "59"))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "FAIL"

    def test_a_word_next_to_the_quote_still_cannot_be_dropped(self):
        out = _html(self.SRC.replace(' " The list', " The"))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "FAIL"
