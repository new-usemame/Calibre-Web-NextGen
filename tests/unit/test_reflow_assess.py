# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 0 and stage 4: what kind of PDF is this, and which pages are worth paying
a model to look at.

The user sees both of these before they spend anything: the assessment decides the
plain-language sentence on the Reflow page, and the routing decides the dollar
figure in the consent checkbox. A routing rule that fires on every page turns a
$0.15 conversion into a $1.50 one; one that never fires ships the defects.
"""

import pytest

from cps.services.reflow import assemble, assess, extract, model, route, skeleton
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


def _doc(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    return doc


def _assess(*builders):
    doc = _doc(*builders)
    try:
        return assess.assess(doc)
    finally:
        doc.close()


def _routes(*builders):
    doc = _doc(*builders)
    try:
        raw_pages = extract.read_pages(doc)
        style = skeleton.book_style(raw_pages)
        skeletons = [skeleton.page_skeleton(raw, style) for raw in raw_pages]
        book = assemble.assemble(skeletons, style, raw_pages)
        return route.route_pages(book, skeletons, assess.assess(doc))
    finally:
        doc.close()


def _scan(doc, pages=3):
    png = F.solid_png()
    for _ in range(pages):
        F.image_only_page(doc, png)


# ---------------------------------------------------------------------- verdicts

def test_a_text_pdf_reads_as_born_digital():
    assessment = _assess(F.prose_page, F.prose_page, F.defect_a_page)

    assert assessment.verdict == "BORN_DIGITAL"


def test_a_pdf_with_no_text_layer_at_all_is_named_as_such():
    """The verdict the user has to see before spending: every page will be routed."""
    assessment = _assess(_scan)

    assert assessment.verdict == "NO_TEXT_LAYER"


def test_a_noise_text_layer_is_not_mistaken_for_text():
    """A scanned book can carry a text layer that is pure mojibake, and every
    character counter in the world reports it as thousands of characters."""
    assessment = _assess(F.garbage_text_page, F.garbage_text_page, F.garbage_text_page)

    assert assessment.verdict == "GARBAGE_TEXT"


def test_a_scan_with_an_ocr_layer_is_named_as_a_scan():
    """The acceptance book's own shape: readable words over a photograph of the page.
    Told apart from a born-digital PDF it is not, the user is promised a structure
    that is not in the file, and told apart from a text-free PDF it is not, they are
    quoted for rebuilding every page from its image."""
    png = F.solid_png()
    assessment = _assess(*[(lambda d: F.ocr_scan_page(d, png))] * 3)

    assert assessment.verdict == "OCR_LAYER"
    assert assessment.scan_share == pytest.approx(1.0)
    assert assessment.layer_is_trusted, "the OCR words are readable and must be kept"


def test_a_sparse_scan_is_named_as_thin_rather_than_called_a_book():
    """Seven lines a page is a real PDF and a bad one. Calling it born-digital would
    promise a conversion that comes out as one paragraph per page."""
    assessment = _assess(F.thin_page, F.thin_page, F.thin_page)

    assert assessment.verdict == "THIN_TEXT"
    assert assessment.median_chars < assess.THIN_TEXT_CHARS


def test_every_verdict_can_be_said_in_a_sentence():
    """The verdict reaches the user as prose on the Reflow page before they spend
    anything. A verdict with no sentence behind it raises on the page that asks for
    consent, which is the worst possible place to find out."""
    for verdict in assess.VERDICTS:
        sentence = assess.Assessment(
            verdict=verdict, pages=1, median_chars=0.0, prose_share=0.0,
            scan_share=0.0, empty_share=0.0, drawings_total=0,
            thin_text=False).describe()

        assert sentence.endswith(".") and len(sentence.split()) > 5, verdict


def test_the_census_reports_the_evidence_behind_its_verdict():
    """A verdict with no numbers under it cannot be argued with."""
    assessment = _assess(F.prose_page, F.prose_page)

    assert assessment.pages == 2
    assert assessment.median_chars > 0
    assert assessment.prose_share == pytest.approx(1.0)
    assert assessment.to_dict()["verdict"] == assessment.verdict


# ------------------------------------------------------------------------ routing

def test_a_page_the_deterministic_pass_resolved_is_not_routed():
    """The whole design is that the deterministic pass does the work. A page whose
    run-in heading, markers and joins all resolved has nothing to ask a model."""
    routes = _routes(F.prose_page, F.defect_a_page, F.prose_page)

    assert not routes[1].routed, routes[1].reasons


def test_a_page_with_an_unexplained_marker_mismatch_is_routed():
    """Two residues, one unmarked note: the deterministic pass refused to guess, so
    this is exactly the page the model is for."""
    routes = _routes(F.prose_page, F.ambiguous_residue_page, F.prose_page)

    assert routes[1].routed
    assert "note_marker_mismatch" in routes[1].reasons


def test_a_page_that_lost_a_notes_number_is_routed_although_nothing_else_is_wrong():
    """MEASURED, page 115 of the acceptance book: two notes printed, both marked,
    every count on the page agreeing -- and a third note's text hanging off the end of
    the first because the scan ate its number. 6 of the book's 29 swept notes sit on
    pages like this, and until the page is routed nobody ever looks at them: the model
    is the only reader in the pipeline that can see the number printed on the scan."""
    routes = _routes(F.prose_page, F.quietly_swept_note_page, F.prose_page)

    assert routes[1].routed, routes[1].reasons
    assert routes[1].reasons == ["note_number_swept"]
    assert routes[1].to_dict()["why"] != ["note_number_swept"], \
        "a reason with no sentence behind it reaches the user as a raw key"


def test_a_page_with_no_text_layer_is_routed():
    routes = _routes(F.prose_page, lambda d: F.image_only_page(d, F.solid_png()))

    assert routes[1].routed
    assert "no_text_layer" in routes[1].reasons


def test_an_uncertain_page_turn_routes_both_sides_of_the_turn():
    """A join is a decision about two pages. Showing the model only one of them asks
    it to judge a sentence it cannot see the end of."""
    routes = _routes(F.uncertain_join_pages)

    routed = [r.pno for r in routes if r.routed]

    assert routed == [0, 1], [(r.pno, r.reasons) for r in routes]
    assert "page_boundary_join_uncertain" in routes[1].reasons


def test_a_page_turn_the_lowercase_opening_settles_is_not_routed():
    """The control for the test above. Defect B is a paragraph broken mid-sentence
    across a page turn with footnotes between — and the lowercase opening on the far
    side answers it for nothing. If this routed, every page turn in the book would."""
    routes = _routes(F.defect_b_pages)

    assert not any("page_boundary_join_uncertain" in r.reasons for r in routes), \
        [(r.pno, r.reasons) for r in routes]


def test_routing_is_the_basis_of_the_quoted_price():
    """SPEC §5: the estimate is routed pages times the measured price per page, not
    a fixed share of the book. Every tier is quoted from the same page count."""
    routes = _routes(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    routed = sum(1 for r in routes if r.routed)

    quote = route.estimate(routed, pages=len(routes))

    assert quote["routed_pages"] == routed
    for name, spec in model.TIERS.items():
        assert quote[name] == pytest.approx(round(spec.price_per_page * routed, 4)), name
    # The cheapest tier is the cheapest quote, which is what the page orders its
    # choices by. The other two are NOT asserted to differ: MEASURED 2026-09-13,
    # Luna's own endpoints are priced a little under deepseek's dearest, so the tier
    # named for quality is not automatically the tier that costs the most. Pinning an
    # order here would be pinning OpenRouter's price list, not Reflow's behaviour.
    assert quote["cheap"] == min(quote[name] for name in model.TIERS)
    assert quote["worst_case"]["standard"] > quote["standard"]


def test_the_whole_book_worst_case_is_quoted_too():
    """A user who is about to spend money is entitled to the ceiling, not only the
    expected figure."""
    quote = route.estimate(10, pages=100)

    assert quote["worst_case"]["standard"] == pytest.approx(
        quote["standard"] * 10, rel=0.01)
