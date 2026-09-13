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
