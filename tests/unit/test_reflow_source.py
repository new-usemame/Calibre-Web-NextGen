# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The chosen source layer: which pages keep native text, which are recovered by
local OCR, and how the recovery is reported.

The pipeline-level contracts live in test_reflow_ocr_source_contract.py (clear
pixels, damaged hidden layer, sideways spread). These cover the decision edges
and the disclosure seams around them.
"""

import shutil

import pytest

from cps.services.reflow import assess, extract, ocr, pipeline, report, source
from cps.tasks import reflow as tasks_reflow
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit

_tesseract = pytest.mark.skipif(not shutil.which("tesseract"),
                                reason="needs Tesseract")


def _raw(build, *builders):
    doc = F.new_doc()
    build(doc)
    for builder in builders:
        builder(doc)
    try:
        pages = extract.read_pages(doc)
        return doc, pages
    except Exception:
        doc.close()
        raise


# ------------------------------------------------------------- the page decision

class TestNeedsRecovery(object):
    def test_an_image_only_page_is_a_candidate(self):
        doc, pages = _raw(lambda d: F.image_only_page(d, F.solid_png()))
        try:
            assert source.needs_recovery(pages[0]) == "image_only"
        finally:
            doc.close()

    def test_a_sparse_but_real_page_is_thin_not_damaged(self):
        """A page whose few words are real words keeps its native layer: thin is
        honest, damaged is a different claim."""
        doc, pages = _raw(lambda d: F.scan_date_tail_page(d, F.solid_png()))
        try:
            assert source.needs_recovery(pages[0]) == ""
        finally:
            doc.close()

    def test_a_garbage_scan_page_is_damaged(self):
        doc, pages = _raw(lambda d: F.garbage_scan_page(d, F.solid_png()))
        try:
            assert source.needs_recovery(pages[0]) == "damaged_layer"
        finally:
            doc.close()

    def test_garbage_without_pixels_is_not_recovered(self):
        """A born-digital page of noise has no better source underneath: OCR of
        the same garbage would only make garbage of it."""
        doc, pages = _raw(F.garbage_text_page)
        try:
            assert source.needs_recovery(pages[0]) == ""
        finally:
            doc.close()

    def test_noise_tokens_are_judged_by_shape(self):
        assert source.looks_like_noise("xqzvv rrrq qqqq zzzzz")
        assert not source.looks_like_noise("November 2016")
        assert not source.looks_like_noise("rhythm glyphs")


# --------------------------------------------------------------- the run modes

@_tesseract
def test_mode_off_never_recognizes(tmp_path):
    doc, pages = _raw(lambda d: F.image_only_page(d, F.solid_png()))
    try:
        recovery = source.recover(doc, pages, "0" * 64, mode="off",
                                  cache_dir=str(tmp_path))
        assert recovery.attempted == 0
        assert recovery.pages == pages
    finally:
        doc.close()


@_tesseract
def test_a_missing_language_stops_an_explicit_choice_before_any_work(tmp_path):
    doc, pages = _raw(lambda d: F.image_only_page(d, F.solid_png()))
    try:
        with pytest.raises(ocr.OCRUnavailable):
            source.recover(doc, pages, "0" * 64, mode="auto",
                           language="fra-not-installed", cache_dir=str(tmp_path))
    finally:
        doc.close()


@_tesseract
def test_the_default_path_discloses_a_missing_language_without_stopping(tmp_path):
    doc, pages = _raw(lambda d: F.image_only_page(d, F.solid_png()))
    try:
        recovery = source.recover(doc, pages, "0" * 64,
                                  mode="auto_if_available",
                                  language="fra-not-installed",
                                  cache_dir=str(tmp_path))
        assert recovery.engine_unavailable
        assert recovery.pages == pages
        assert recovery.failed == 1
    finally:
        doc.close()


# ------------------------------------------------------------- the disclosures

@_tesseract
def test_the_survey_quotes_ocr_pages_and_engine_state():
    doc, _ = _raw(lambda d: F.ocr_scan_page(d, F.solid_png()))
    try:
        quote = pipeline.survey(doc)
    finally:
        doc.close()
    assert "ocr_candidates" in quote
    assert "ocr_estimated_seconds" in quote
    assert "ocr_engine_unavailable" in quote


@_tesseract
def test_the_report_names_the_layer_and_the_engine():
    doc, _ = _raw(lambda d: F.ocr_scan_page(d, F.solid_png()))
    try:
        result = pipeline.run(doc)
    finally:
        doc.close()
    payload = report.numbers(result)
    recovery = payload["source_recovery"]
    assert recovery["performed"]
    assert recovery["pages_ocr"] >= 0
    assert "engine_unavailable" in recovery
    about = report.about_page(payload)
    if recovery["pages_ocr"]:
        assert "Source text recovery" in about
        assert "transcription of the scan" in about


# --------------------------------------------------------------- task options

def test_task_options_clamp_the_recovery_choice():
    options = tasks_reflow.ReflowOptions({"source_recovery": "nonsense",
                                          "ocr_language": "fra"})
    assert options.source_recovery == "auto"
    assert options.ocr_language == "fra"
    assert tasks_reflow.ReflowOptions(
        {"source_recovery": "textless"}).source_recovery == "textless"
    assert tasks_reflow.ReflowOptions(
        {"source_recovery": "off"}).source_recovery == "off"
