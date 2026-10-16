# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""R3: a damaged word is marked where it stands, and never replaced.

The whole conversion rests on one promise -- that no word of the book was changed
-- so the one place a model's opinion about a word may appear is beside it, never
instead of it. These tests are mostly about the ways that could go wrong quietly:
a mark that splits a word in two, a mark that lands inside an attribute and breaks
the markup, a mark applied twice, and a list of "unresolved" readings that is
really a list of things the conversion already fixed.
"""
import re

import pytest

from cps.services.reflow import annotate, gate

pytestmark = pytest.mark.unit


def _words(html):
    """The page's words as a reader meets them, with the markup taken out."""
    return re.sub(r"<[^>]*>", " ", html).split()


class TestTheMarkItself(object):

    def test_a_damaged_word_is_marked_where_it_stands(self):
        html = '<p>as Catalogue.s° has it</p>'

        out, placed = annotate.mark_uncertain(
            html, [{"token": "Catalogue.s°", "candidates": ["Catalogue.50"]}])

        assert len(placed) == 1
        assert 'class="reflow-uncertain"' in out
        assert ">Catalogue.s°</span>" in out

    def test_the_likely_reading_and_the_rest_ride_in_the_title(self):
        out, _placed = annotate.mark_uncertain(
            "<p>note 4s here</p>",
            [{"token": "4s", "candidates": ["45", "4s", "48"]}])

        assert 'title="likely: 45; also: 48"' in out

    def test_a_token_the_model_could_not_better_is_still_marked(self):
        """A reading nobody can improve is still a reading worth doubting."""
        out, placed = annotate.mark_uncertain(
            "<p>note 4s here</p>", [{"token": "4s", "candidates": ["4s"]}])

        assert len(placed) == 1
        assert 'title="uncertain reading"' in out

    def test_the_same_damaged_word_twice_is_marked_twice(self):
        out, placed = annotate.mark_uncertain(
            '<p>Cabinet." and later Cabinet." again</p>',
            [{"token": 'Cabinet."', "candidates": ["Cabinet.35"]},
             {"token": 'Cabinet."', "candidates": ["Cabinet.36"]}])

        assert len(placed) == 2
        assert out.count('class="reflow-uncertain"') == 2
        assert 'title="likely: Cabinet.35"' in out and 'title="likely: Cabinet.36"' in out

    def test_a_word_already_marked_is_not_marked_again(self):
        once, _first = annotate.mark_uncertain(
            "<p>note 4s here</p>", [{"token": "4s", "candidates": ["45"]}])

        twice, placed = annotate.mark_uncertain(
            once, [{"token": "4s", "candidates": ["45"]}])

        assert not placed
        assert twice == once

    def test_a_quotation_mark_in_a_candidate_cannot_break_the_markup(self):
        out, _placed = annotate.mark_uncertain(
            "<p>the wel1 here</p>",
            [{"token": "wel1", "candidates": ['say "well" & mean it']}])

        assert 'title="likely: say &quot;well&quot; &amp; mean it"' in out


class TestWhatItRefusesToDo(object):

    def test_a_mark_that_would_split_a_word_is_refused(self):
        """The gate reads a page with its tags taken out and every tag replaced by
        a space, so a span opened inside a word cuts that word in two: the book
        loses one word and gains two. This is the failure the R3 path has to be
        incapable of, so a flagged string that does not carry all of a word's
        letters and digits is not placed at all."""
        html = "<p>Register.52 says so</p>"

        out, placed = annotate.mark_uncertain(
            html, [{"token": ".52", "candidates": [".53"]}])

        assert not placed
        assert out == html

    def test_a_mark_may_not_annex_the_word_next_to_it(self):
        html = "<p>a 4s-old reading</p>"

        out, placed = annotate.mark_uncertain(
            html, [{"token": "4s", "candidates": ["45"]}])

        assert not placed
        assert out == html

    def test_a_token_that_only_occurs_inside_markup_is_not_marked(self):
        html = '<aside class="footnote" id="fn_52">52 Binder.</aside>'

        out, placed = annotate.mark_uncertain(
            html, [{"token": "footnote", "candidates": ["footnotes"]}])

        assert not placed
        assert out == html

    def test_a_token_that_is_not_on_the_page_is_left_alone(self):
        html = "<p>nothing to see</p>"

        out, placed = annotate.mark_uncertain(
            html, [{"token": "Poeme", "candidates": ["Poème"]}])

        assert not placed
        assert out == html

    def test_an_empty_or_broken_record_marks_nothing(self):
        html = "<p>nothing to see</p>"

        out, placed = annotate.mark_uncertain(
            html, [{"token": "", "candidates": ["x"]}, {}, None, 42])

        assert not placed
        assert out == html

    def test_a_model_that_sent_a_bare_string_still_gets_its_word_marked(self):
        """Measured: providers do send ``"uncertain": ["wel1"]``."""
        out, placed = annotate.mark_uncertain("<p>read wel1. enough</p>", ["wel1"])

        assert len(placed) == 1
        assert 'title="uncertain reading">wel1.</span>' in out


class TestThePromiseItKeeps(object):

    #: Eight invented damaged readings exercise token boundaries and escaping.
    #: The whole token stays printed, with suggestions carried only in markup.
    MEASURED = [
        {"token": "of wood", "candidates": ["of wood", "ofwood"]},
        {"token": "daybefore", "candidates": ["day before", "daybefore"]},
        {"token": "inferrs", "candidates": ["infers", "inferrs"]},
        {"token": "plankio", "candidates": ["πλανκιο", "plenkio", "plankio"]},
        {"token": "forms ofWooden", "candidates": ["forms of Wooden"]},
        {"token": "WoodenBox", "candidates": ["Wooden Box"]},
        {"token": "Windo.w.", "candidates": ['window.', "Windo.w"]},
        {"token": "wel1", "candidates": ["well", "wel1"]},
    ]

    PAGE = ('<h2>Workshop of Birchwood</h2>'
            '<p>The length of wood is marked on the daybefore card. The label inferrs '
            'that (plankio) describes forms ofWooden pieces fitted wel1. '
            'See <em>WoodenBox</em>, and Windo.w. nearby.</p>'
            '<aside class="footnote" id="fn_52">52 Binder, Shelving, p. 76.</aside>')


    def test_marking_a_page_never_changes_one_of_its_words(self):
        out, placed = annotate.mark_uncertain(self.PAGE, self.MEASURED)

        assert len(placed) == len(self.MEASURED)
        assert _words(out) == _words(self.PAGE)

    def test_a_marked_page_still_passes_the_word_gate(self):
        """The gate is what the reader is actually promised. A page it would
        refuse is a page this function must not have produced."""
        source = " ".join(_words(self.PAGE))

        out, _placed = annotate.mark_uncertain(self.PAGE, self.MEASURED)

        assert gate.check_word_preservation(source, out).verdict == "PASS"

    def test_the_page_it_produces_is_still_the_structure_the_builder_trusts(self):
        out, _placed = annotate.mark_uncertain(self.PAGE, self.MEASURED)

        assert gate.check_structure(out, ladder=(1, 2)).ok

    def test_an_ampersand_in_the_token_finds_its_escaped_self(self):
        html = '<p>by Paper &amp; Board, undated</p>'

        out, placed = annotate.mark_uncertain(
            html, [{"token": 'Paper & Board', "candidates": ['Paper and Board']}])

        assert len(placed) == 1
        assert _words(out) == _words(html)
        assert "&amp;" in out


class TestReadingTheCandidates(object):

    def test_a_candidate_that_repeats_the_token_is_not_offered_as_a_correction(self):
        token, readings = annotate.uncertain_readings(
            {"token": "4s", "candidates": ["4s", "45"]})

        assert token == "4s"
        assert readings == ["45"]

    def test_the_same_candidate_twice_is_offered_once(self):
        _token, readings = annotate.uncertain_readings(
            {"token": "x", "candidates": ["y", "y", "z"]})

        assert readings == ["y", "z"]


class TestWhatTheReaderIsStillToldAbout(object):
    """Resolved markers leave no duplicate unresolved-reading annotation."""

    #: Flagged readings are reconciled with the marker identities the same
    #: answer resolved.
    PAGE_102 = [
        {"token": "Catalogue.s°", "candidates": ["Catalogue.50"]},
        {"token": 'Checklist.si', "candidates": ['Checklist.51']},
        {"token": 'Register."', "candidates": ["Register.52"]},
        {"token": 'Drawers,"', "candidates": ["Drawers,53"]},
        {"token": 'Shelves,"', "candidates": ["Shelves,55"]},
        {"token": "Workbench.s6", "candidates": ["Workbench.56"]},
    ]
    RECOVERED_102 = [50, 51, 52, 53, 55, 56]

    HTML_102 = ('<p>Catalogue.<a class="noteref" href="#fn_50">50</a> and Checklist.<a class="noteref" href="#fn_51">51</a> follow Register.<a class="noteref" href="#fn_52">52</a></p>')

    def test_a_reading_the_gate_says_was_restored_is_not_listed_as_uncertain(self):
        _html, uncertain, marked = annotate.annotate_page(
            self.HTML_102, self.PAGE_102, recovered=self.RECOVERED_102)

        assert marked == 0
        assert uncertain == []

    def test_a_reading_nobody_restored_is_still_listed(self):
        """Page 106 flagged ``its°`` for ``it.80``; no marker came back."""
        _html, uncertain, _marked = annotate.annotate_page(
            "<p>nothing about it here</p>",
            [{"token": "its°", "candidates": ["it.", "it"]}],
            recovered=[50, 51])

        assert [span["token"] for span in uncertain] == ["its°"]

    def test_a_reading_the_reader_can_see_is_never_dropped(self):
        """It is marked in the text, so it is a reading, whatever its digits say."""
        html, uncertain, marked = annotate.annotate_page(
            "<p>note 4s here</p>", [{"token": "4s", "candidates": ["45"]}],
            recovered=[45])

        assert marked == 1
        assert [span["token"] for span in uncertain] == ["4s"]
        assert 'class="reflow-uncertain"' in html

    def test_with_nothing_recovered_every_reading_is_still_a_reading(self):
        _html, uncertain, _marked = annotate.annotate_page(
            self.HTML_102, self.PAGE_102, recovered=[])

        assert len(uncertain) == len(self.PAGE_102)

    def test_a_page_with_no_readings_is_returned_untouched(self):
        html, uncertain, marked = annotate.annotate_page("<p>clean</p>", [])

        assert (html, uncertain, marked) == ("<p>clean</p>", [], 0)
