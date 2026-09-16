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


class TestMarkupSafetyGate:
    """G3's attribute half. The word gate compares text and cannot see an
    attribute at all: a fragment whose every word is the page's own could carry a
    remote URL or an event handler straight into the reader's webview. Tags are
    parsed, not regexed, and every attribute and URL is checked against the markup
    contract the pipeline itself writes."""

    def test_an_image_from_the_network_is_refused(self):
        html = ('<p>text of the page</p>'
                '<img src="https://example.invalid/reflow-pixel.gif" alt=""/>')
        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert not result.ok
        assert any("src" in reason for reason in result.reasons), result.reasons

    def test_an_event_handler_is_refused(self):
        html = ('<p>text of the page</p>'
                '<img src="images/fig_p0000_0.jpg" alt="" '
                'onerror="fetch(\'https://example.invalid/event\')"/>')
        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert not result.ok
        assert any("onerror" in reason for reason in result.reasons), result.reasons

    def test_a_javascript_href_is_refused(self):
        html = '<p>text <a href="javascript:alert(1)">of the page</a></p>'
        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert not result.ok
        assert any("href" in reason for reason in result.reasons), result.reasons

    def test_a_data_url_is_refused(self):
        html = ('<p>text of the page</p>'
                '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt=""/>')
        assert not gate.check_structure(html, ladder=[1, 2, 3], headings=[]).ok

    def test_a_style_attribute_is_refused(self):
        """CSS is network access too: url() in a style loads from anywhere."""
        html = '<p style="background-image: url(https://example.invalid)">text</p>'
        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert not result.ok
        assert any("style" in reason for reason in result.reasons), result.reasons

    def test_an_attribute_the_contract_never_writes_is_refused(self):
        html = '<p formaction="https://example.invalid">text of the page</p>'
        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert not result.ok
        assert any("formaction" in reason for reason in result.reasons), result.reasons

    def test_a_processing_instruction_is_refused(self):
        html = '<p>text of the page</p><?xml-stylesheet href="https://example.invalid"?>'
        assert not gate.check_structure(html, ladder=[1, 2, 3], headings=[]).ok

    def test_the_markup_the_pipeline_writes_itself_is_accepted(self):
        """The control: internal note links, an uncertainty mark, a figure reference
        and a table are the contract, not casualties. Refusing these would be the
        gate failing the pages it exists to let through."""
        html = ('<p>text<a class="noteref" epub:type="noteref" id="fnref_47" '
                'href="#fn_47"><sup>47</sup></a> of the page<sup '
                'class="noteref-unresolved">12</sup> and '
                '<span class="reflow-uncertain" title="likely: well">wel1</span></p>'
                '<figure><img src="images/fig_p0000_0.jpg" alt=""/>'
                '<figcaption class="reflow-no-caption"></figcaption></figure>'
                '<table><thead><tr><th colspan="2">t</th></tr></thead>'
                '<tbody><tr><td>a</td><td>b</td></tr></tbody></table>'
                '<aside class="footnote" epub:type="footnote" id="fn_47">'
                '<p><a href="#fnref_47">47</a> Valens, Anthology.</p></aside>')

        result = gate.check_structure(html, ladder=[1, 2, 3], headings=[])

        assert result.ok, result.reasons


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


class TestTheModelDoesNotDecideWhatAHeadingIs(object):
    """G3, heading agreement. What is a heading is a fact about the page.

    The deterministic reader settles it with the book's type ladder, the geometry of
    a run-in head and ``skeleton.acceptable_heading``, all of them measured on the
    book in hand; the model is told the answer in its prompt and is judged on having
    used it. Left to decide for itself, MEASURED on page index 102 of the acceptance
    book in the run of record, it made ``<h2>`` headings of items 6, 7 and 8 of a
    numbered list running on from the previous page -- and because
    ``build_epub.SPLIT_LEVELS`` starts a chapter at every h1 and h2, that one page
    would have put three sentences of a list into the reader's table of contents as
    three chapters.

    ``headings=None`` means the caller is not declaring them and only the ladder is
    checked; ``headings=[]`` means this page prints none, which is a fact, and any
    heading in the answer is invented.
    """

    HEAD = "Serapio of Alexandria (First Century CE?)"
    ITEM = ("6. Valens associates some timing methods with Nechepso that involve "
            "planetary periods and ascensional times.")

    def test_the_headings_the_page_prints_are_clean(self):
        html = "<h2>%s</h2><p>%s</p>" % (self.HEAD, self.ITEM)

        assert gate.check_structure(html, ladder=[1, 2, 3],
                                    headings=[(2, self.HEAD)]).ok

    def test_a_line_the_page_sets_as_body_may_not_come_back_as_a_heading(self):
        result = gate.check_structure("<h2>%s</h2>" % self.ITEM, ladder=[1, 2, 3],
                                      headings=[])

        assert not result.ok
        assert any("body" in reason for reason in result.reasons), result.reasons

    def test_a_heading_the_page_prints_may_not_come_back_as_body(self):
        """The other direction: a head demoted is a chapter and a TOC entry gone."""
        result = gate.check_structure("<p>%s</p>" % self.HEAD, ladder=[1, 2, 3],
                                      headings=[(2, self.HEAD)])

        assert not result.ok

    def test_a_heading_may_not_change_level(self):
        """h3 is on this book's ladder, so the ladder rule cannot be what refuses
        this: h2 and h3 split the book differently and read differently."""
        result = gate.check_structure("<h3>%s</h3>" % self.HEAD, ladder=[1, 2, 3],
                                      headings=[(2, self.HEAD)])

        assert not result.ok
        assert any("h3" in reason for reason in result.reasons), result.reasons

    def test_a_noteref_inside_a_heading_is_still_that_heading(self):
        """The reader writes the marker ``[47]``; the model writes the anchor."""
        html = ('<h2>%s<a class="noteref" href="#fn_47">47</a></h2>'
                '<aside class="footnote" id="fn_47">47 Valens, Anthology.</aside>'
                % self.HEAD)

        assert gate.check_structure(html, ladder=[1, 2],
                                    headings=[(2, self.HEAD + "[47]")]).ok

    def test_a_page_that_declares_no_headings_is_judged_on_the_ladder_alone(self):
        assert gate.check_structure("<h2>%s</h2>" % self.ITEM, ladder=[1, 2]).ok


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


class TestTheOrderOfTheBlocksIsNotTheModelsToChange(object):
    """Defect A, seen from the gate -- and the allowance that had to go with it.

    This gate used to forgive one relocated block. The premise was a bug in our own
    reader: a run-in section head came back below the section it opens, and the
    model was asked, and licensed, to put it back. ``skeleton._lines_bbox`` removed
    the premise -- MEASURED on the acceptance book, 133 of its 221 run-in heads came
    out after their own first paragraph before that fix and none do after it -- and
    what the allowance forgave next was measured on page index 120 of the run of
    record: the model hoisted ``Serapio of Alexandria (First Century CE?)`` out of
    the middle of the page to the top of it, above a paragraph that continues the
    previous page, and the page passed. A gate comparing two token sequences cannot
    know where a block belongs. The reader does know, so the answer keeps the order
    it was given, and a page that comes back re-ordered is a page that ships the
    reader's own text instead.
    """

    HEAD = "Serapio of Alexandria (First Century CE?)"
    OPENING = ("the name Zoroaster in order to confer authority on the texts, as "
               "Porphyry complained in his own time.")
    BODY = ("Serapio of Alexandria was an astrologer who wrote on inceptional "
            "astrology, although only fragments of his work survive.")

    def _source(self):
        return "%s\n\n%s\n\n%s" % (self.OPENING, self.HEAD, self.BODY)

    def test_a_heading_marked_where_the_page_prints_it_is_clean(self):
        """The control: refusing relocation must not refuse the ordinary answer."""
        out = ("<p>%s</p>\n<h2>%s</h2>\n<p>%s</p>\n"
               % (self.OPENING, self.HEAD, self.BODY))

        assert gate.check_word_preservation(self._source(), out).verdict == "PASS"

    def test_a_heading_hoisted_to_the_top_of_the_page_is_refused(self):
        """MEASURED: the shape of the answer page index 120 actually returned, which
        put the section head above a sentence belonging to the page before."""
        out = ("<h2>%s</h2>\n<p>%s</p>\n<p>%s</p>\n"
               % (self.HEAD, self.OPENING, self.BODY))

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "FAIL", result.allowed_hits

    def test_a_page_whose_words_were_rearranged_is_refused(self):
        """The control that matters. Every word survives and the page is nonsense:
        a bag-of-words gate calls this clean."""
        out = "<p>%s</p>\n" % " ".join(self._source().split()[::-1])

        assert gate.check_word_preservation(self._source(), out).verdict == "FAIL"

    def test_a_heading_the_model_invented_is_refused(self):
        out = ("<h2>Teucer of Babylon (First Century BCE)</h2>\n"
               "<p>%s</p>\n<h2>%s</h2>\n<p>%s</p>\n"
               % (self.OPENING, self.HEAD, self.BODY))

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


class TestAMarkerPutBackBesideWreckageNothingMayDelete(object):
    """The other half of the same repair: add the number, touch nothing.

    MEASURED on pages 116, 118 and 124 of the acceptance book. The scanner does not
    always leave a quotation mark where a superscript was -- it leaves ``.'23`` for
    125, ``.''s`` for 135, ``r's`` for 175. Those hold digits and letters, so no
    rule here will let the model delete them, and the model deleting them anyway is
    what threw four of twenty-six paid-for pages away: the page came back with the
    marker restored and the whole answer was refused over the wreckage beside it.

    The fix is not a wider delete. It is to let the model do the thing that cannot
    lose a word: leave every character of the text layer where it is and put the
    noteref next to it. The scan's nonsense stays visible in the reader's book,
    which is honest -- it is what the page's text layer says -- and the footnote
    link works.
    """

    SRC = ("for material from Hermes on the advantageous places.'23 Timaeus is "
           "cited for some astrological doctrines.")

    def _out(self, marker='<a class="noteref" href="#fn_125">125</a>', src=None):
        return _html((src or self.SRC).replace(".'23", ".'23" + marker, 1))

    def test_a_noteref_added_beside_the_wreckage_is_allowed(self):
        result = gate.check_word_preservation(self.SRC, self._out(),
                                              recoverable_markers=[125])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_added" for d in result.allowed_hits), \
            "a repair nobody can audit is not a repair"
        assert result.recovered_markers == [125]

    def test_only_a_note_the_page_leaves_unreferenced_may_be_added(self):
        """Otherwise the allowance is "the model may write any number it likes",
        and the reader gets a link to a source the page never cited."""
        assert gate.check_word_preservation(self.SRC, self._out(),
                                            recoverable_markers=[126]).verdict == "FAIL"
        assert gate.check_word_preservation(self.SRC, self._out()).verdict == "FAIL"

    def test_the_same_missing_note_cannot_be_added_twice(self):
        out = _html(self.SRC.replace(".'23", ".'23" + '<a class="noteref" href="#fn_125">125</a>')
                            .replace("doctrines.",
                                     'doctrines.<a class="noteref" href="#fn_125">125</a>'))

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[125]).verdict == "FAIL"

    def test_a_word_may_not_arrive_with_the_number(self):
        """The allowance is one number and nothing else. Anything travelling with
        it is prose the page does not print."""
        out = self._out('<a class="noteref" href="#fn_125">125</a> indeed')

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[125]).verdict == "FAIL"

    def test_nothing_may_leave_the_page_to_make_room_for_it(self):
        """The measured failure, stated as a rule: the model may add the marker or
        it may swap it for one or two quotation marks, and ``'23`` is neither."""
        out = _html(self.SRC.replace(".'23", '.<a class="noteref" href="#fn_125">125</a>'))

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[125]).verdict == "FAIL"
