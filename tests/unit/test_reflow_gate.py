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
    'the pine crate must be stored beside plywood.[160] We use these labels when the cabinet is called Workshop of Birchwood. Our tray is incomplete, since the rear edge of its bracket cracked.'
)


def _html(body):
    return "<p>%s</p>\n" % body


def test_faithful_structure_edit_passes():
    """Marking a heading and splitting paragraphs changes no words, so it passes.

    Breaks if the gate starts counting markup or whitespace as content."""
    out = (
        """<h3>Workshop of Birchwood</h3>
<p>the pine crate must be stored beside plywood.<a class="noteref" href="#fn_160">160</a> We use these labels when the cabinet is called Workshop of Birchwood. Our tray is incomplete, since the rear edge of its bracket cracked.</p>
"""
    )
    src = SRC.replace("Workshop of Birchwood. Our tray", "Workshop of Birchwood. Our tray")
    result = gate.check_word_preservation("Workshop of Birchwood\n" + src, out)

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
    out = _html(SRC.replace("[160]", " 160 ") + " This paragraph discusses a cabinet.")

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
    src = "the adjustable bolts of the worlcbench. Checklist holds them"
    out = _html("the adjustable bolts of the workbench. Checklist holds them")

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "FAIL"


def test_footnote_marker_split_is_an_allowed_edit_and_is_reported():
    """Detaching a marker the OCR fused onto a word is a structural edit we make
    on purpose — allowed, but never silent: it lands in allowed_hits."""
    src = "lined with plywood.160 We fit a new hinge"
    out = _html('lined with plywood.<a class="noteref" href="#fn_160">160</a> We fit a new hinge')

    result = gate.check_word_preservation(src, out)

    assert result.verdict == "PASS"
    assert result.allowed_hits, "an intentional edit must still be reported"


def test_an_allowance_cannot_hide_a_lost_word():
    """The allow-list must be a narrow re-tokenisation rule, not a wildcard.

    A re-split that also drops a word is not the allowed edit."""
    src = "lined with plywood.160 We fit a new hinge"
    out = _html("lined with plywood. 160 We fit a new")

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
    """Destroyed superscripts can be restored without changing source words."""

    SRC = ('the sample diagram shows a small blue tray beside Plan Cabinet." The next drawing shows how its lid fits.')

    def _out(self, marker='<a class="noteref" href="#fn_35">35</a>'):
        return _html(self.SRC.replace('Cabinet."', "Cabinet." + marker))

    def test_a_marker_read_as_a_quotation_mark_may_be_restored(self):
        result = gate.check_word_preservation(self.SRC, self._out(),
                                              recoverable_markers=[35])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits), \
            "a repair nobody can audit is not a repair"

    def test_the_quotation_mark_stays_punctuation_when_no_note_is_missing(self):
        """The default. Every page of dialogue in every book depends on it: 555 of
        the synthetic closing punctuation has no matching unmarked note."""
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
        out = self._out().replace("The next drawing shows ", "The next drawing ")

        assert gate.check_word_preservation(self.SRC, out,
                                            recoverable_markers=[35]).verdict == "FAIL"

    def test_an_apostrophe_and_quote_together_are_still_one_marker(self):
        """An apostrophe and quote together are still one marker."""
        src = "the wooden lids of the sample boxes.'\" Checklist holds them later."
        out = _html(src.replace(".'\"", '.<a class="noteref" href="#fn_161">161</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[161])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_a_whole_word_of_punctuation_is_not_a_marker(self):
        """One or two characters is the measured damage. Three would start letting
        the model delete printed punctuation and call it a repair."""
        src = "the worktables.'\"' Checklist holds them later on in the same chapter."
        out = _html(src.replace(".'\"'", '.<a class="noteref" href="#fn_161">161</a>'))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[161]).verdict == "FAIL"

    def test_a_superscript_the_scanner_read_as_letters_may_also_be_restored(self):
        """A superscript the scanner read as letters may also be restored."""
        src = "we store spare bracket.ms New workers explain the procedure."
        out = _html(src.replace("bracket.ms",
                                'bracket.<a class="noteref" href="#fn_105">105</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[105])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_the_letters_may_stand_for_the_stop_as_well_as_the_number(self):
        """The letters may stand for the stop as well as the number."""
        src = "cannot be opened, because we must inspect a loose hinge on its\u00b0 Beside the cabinet"
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
        src = "the ms on the lid was painted green by a careful worker."
        out = _html(src.replace(" ms ", ' <a class="noteref" href="#fn_105">105</a> '))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[105]).verdict == "FAIL"

    def test_letters_inside_a_word_are_not_a_superscript(self):
        """Letters inside a word are not a superscript."""
        src = "the panel labels Tl la and Ti lb are set in the table."
        out = _html(src.replace("Tl la", "T11a"))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[11]).verdict == "FAIL"

    def test_the_full_stop_the_scanner_ate_with_the_marker_comes_back_with_it(self):
        """The full stop the scanner ate with the marker comes back with it."""
        src = 'instructions for the hinges, beside the newly built shelf" Workmaster'
        out = _html(src.replace('shelf"',
                                'shelf.<a class="noteref" href="#fn_68">68</a>'))

        result = gate.check_word_preservation(src, out, recoverable_markers=[68])

        assert result.verdict == "PASS", result.unexplained
        assert any(d.kind == "marker_recovered" for d in result.allowed_hits)

    def test_a_letter_may_not_go_missing_beside_a_recovered_marker(self):
        """The control, and a hole this book walked straight into: ``shelf"`` used to
        be allowed to become ``shel68``, because a rule that lets the scanner render a
        superscript as letters will read the ``f`` as one of them. A token holding a
        quotation mark has already explained its superscript. Punctuation may differ
        around the number because punctuation is not a word; the letters may not."""
        src = 'instructions for the hinges, beside the newly built shelf" Workmaster'

        for answer in ('shel.<a class="noteref" href="#fn_68">68</a>',
                       'shel<a class="noteref" href="#fn_68">68</a>'):
            out = _html(src.replace('shelf"', answer))

            assert gate.check_word_preservation(
                src, out, recoverable_markers=[68]).verdict == "FAIL", answer

    def test_a_printed_number_may_not_change_beside_a_recovered_marker(self):
        """The other control, and the one that matters in a book of citations: the
        page numbers, dates and section numbers around the marker are words."""
        src = 'Workshop Directory, Illustrations, p. 124." Checklist marks it twice.'
        out = _html(src.replace('124."',
                                '125.<a class="noteref" href="#fn_68">68</a>'))

        assert gate.check_word_preservation(src, out,
                                            recoverable_markers=[68]).verdict == "FAIL"

    def test_one_missing_note_cannot_be_spent_twice(self):
        src = 'to Plan Cabinet." Later, and again to Plan Shelving." The scribe agrees.'
        out = _html(src.replace('Cabinet."', 'Cabinet.<a class="noteref" href="#fn_35">35</a>')
                       .replace('Shelving."', 'Shelving.<a class="noteref" href="#fn_35">35</a>'))

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

    def test_an_unknown_tag_is_refused_by_the_markup_contract_itself(self):
        """The final builder boundary calls check_markup_safety directly, so the
        tag half of the contract has to live here -- leaving it to the caller is
        what let a direct <script> fragment through the packaging boundary."""
        assert gate.check_markup_safety("<script>alert(1)</script>")
        assert gate.check_markup_safety("<iframe></iframe>")
        assert gate.check_markup_safety("</script>")
        assert gate.check_markup_safety("<svg><script>x</script></svg>")

    def test_a_disallowed_tag_is_reported_once_no_matter_how_often_it_appears(self):
        reasons = gate.check_markup_safety(
            "<p>text</p><script>a()</script><p>more</p><script>b()</script>")

        assert len([r for r in reasons if "script" in r]) == 1, reasons

    def test_a_legitimate_url_on_a_disallowed_tag_is_still_refused(self):
        """The tag decides, not its attributes: an iframe pointing at a real
        pipeline figure is still an iframe."""
        assert gate.check_markup_safety(
            '<iframe src="images/fig_p0000_0.jpg"></iframe>')

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
                '<p><a href="#fnref_47">47</a> Folderbook, Handbook.</p></aside>')

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
        result = gate.check_structure("<h4>Workshop of Birchwood</h4>", ladder=[1, 2, 3])

        assert not result.ok

    def test_noteref_without_a_matching_aside_is_rejected(self):
        html = '<p>text<a class="noteref" href="#fn_160">160</a></p>'

        result = gate.check_structure(html, ladder=[1, 2, 3])

        assert not result.ok
        assert any("160" in r for r in result.reasons)

    def test_matched_noteref_and_aside_pass(self):
        html = ('<p>text<a class="noteref" href="#fn_160">160</a></p>'
                '<aside class="footnote" id="fn_160">Fixturebook.</aside>')

        assert gate.check_structure(html, ladder=[1, 2, 3]).ok

    def test_figure_without_a_caption_is_rejected(self):
        result = gate.check_structure("<figure><img src='x.png'/></figure>", ladder=[1])

        assert not result.ok


class TestTheModelDoesNotDecideWhatAHeadingIs(object):
    """Only deterministic heading evidence can authorize model heading markup."""

    HEAD = "Workshop of Birchwood (First Edition AB?)"
    ITEM = ("6. Folderbook describes some fitting methods for Workshop Guide that involve "
            "cabinet dimensions and assembly steps.")

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
                '<aside class="footnote" id="fn_47">47 Folderbook, Handbook.</aside>'
                % self.HEAD)

        assert gate.check_structure(html, ladder=[1, 2],
                                    headings=[(2, self.HEAD + "[47]")]).ok

    def test_a_page_that_declares_no_headings_is_judged_on_the_ladder_alone(self):
        assert gate.check_structure("<h2>%s</h2>" % self.ITEM, ladder=[1, 2]).ok


class TestANoteIsTheNumberItIdentifies(object):
    """An aside identity and its visible number must agree."""

    SOURCE = "text of the page[58]\n\n[58] Binder, Shelves and Drawers, p. 76."
    BODY = '<p>text of the page<a class="noteref" href="#fn_58">58</a></p>'

    def test_a_note_that_prints_its_number_passes(self):
        html = self.BODY + '<aside class="footnote" id="fn_58">58 Binder, ' \
                           'Shelves and Drawers, p. 76.</aside>'
        assert gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_a_note_that_leaves_its_number_in_the_id_passes_too(self):
        html = self.BODY + '<aside class="footnote" id="fn_58">Binder, ' \
                           'Shelves and Drawers, p. 76.</aside>'
        assert gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_a_number_already_printed_is_not_printed_twice(self):
        html = '<aside class="footnote" id="fn_58">58 Binder.</aside>'
        assert gate.number_the_notes(gate.number_the_notes(html)) == \
            gate.number_the_notes(html)
        assert gate.number_the_notes(html).count("58") == 2      # the id and the text

    def test_the_number_supplied_is_the_note_s_own_and_nothing_else(self):
        """A crafted violation: the aside says one number and is identified by
        another. Reading the id in place of the text would launder the wrong number
        into the page."""
        html = self.BODY + '<aside class="footnote" id="fn_58">99 Binder, ' \
                           'Shelves and Drawers, p. 76.</aside>'
        assert not gate.check_word_preservation(self.SOURCE, gate.number_the_notes(html)).ok

    def test_an_aside_that_is_not_numbered_at_all_is_left_alone(self):
        html = '<aside class="footnote" id="fn_note-a">Binder.</aside>'
        assert gate.number_the_notes(html) == html


class TestTheOrderOfTheBlocksIsNotTheModelsToChange(object):
    """Model markup preserves the source block reading order."""

    HEAD = "Workshop of Birchwood (First Edition AB?)"
    OPENING = ("the word BLUE in order to label the empty wooden tray, as "
               "the sample instructions explain in the next paragraph.")
    BODY = ('Workshop of Birchwood was a workshop that kept notes on the labeling of wooden boxes, although only drawings of the first cabinet remain.')

    def _source(self):
        return "%s\n\n%s\n\n%s" % (self.OPENING, self.HEAD, self.BODY)

    def test_a_heading_marked_where_the_page_prints_it_is_clean(self):
        """The control: refusing relocation must not refuse the ordinary answer."""
        out = ("<p>%s</p>\n<h2>%s</h2>\n<p>%s</p>\n"
               % (self.OPENING, self.HEAD, self.BODY))

        assert gate.check_word_preservation(self._source(), out).verdict == "PASS"

    def test_a_heading_hoisted_to_the_top_of_the_page_is_refused(self):
        """A heading hoisted ahead of its source opening is refused."""
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
        out = ("<h2>Timber of Birchwood (First Edition ABC)</h2>\n"
               "<p>%s</p>\n<h2>%s</h2>\n<p>%s</p>\n"
               % (self.OPENING, self.HEAD, self.BODY))

        result = gate.check_word_preservation(self._source(), out)

        assert result.verdict == "FAIL"


class TestOrphanPunctuationIsNotAWord(object):
    """Punctuation-only debris does not alter alphabetic word accounting."""

    SRC = ("possibly from the same shelf as the sample books of wooden patterns. \" "
           "Our tray is incomplete, since the rear bracket cracked.")

    def test_a_stray_quote_the_scanner_left_behind_may_be_dropped(self):
        out = _html(self.SRC.replace(' " ', " "))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "PASS", (result.missing, result.invented)
        assert any(d.kind == "punctuation" for d in result.allowed_hits)

    def test_a_stray_quote_turned_into_a_citation_is_refused(self):
        """A stray quote turned into a citation is refused."""
        out = _html(self.SRC.replace('"', "59"))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "FAIL"

    def test_a_word_next_to_the_quote_still_cannot_be_dropped(self):
        out = _html(self.SRC.replace(' " Our tray', " Our"))

        result = gate.check_word_preservation(self.SRC, out)

        assert result.verdict == "FAIL"


class TestAMarkerPutBackBesideWreckageNothingMayDelete(object):
    """Restoring a missing marker does not permit deleting neighboring prose."""

    SRC = ("for labels from Workshop on the blue wooden trays.'23 Manual is used for the matching shelf drawings.")

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
                            .replace("drawings.",
                                     'drawings.<a class="noteref" href="#fn_125">125</a>'))

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
