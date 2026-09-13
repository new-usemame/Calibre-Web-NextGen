# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The orchestrator: what gets sent, what gets kept, and what happens when it stops.

Everything expensive or destructive lives here. A page sent twice is money spent
twice; a page whose model answer is adopted without passing the gate is the reader's
book silently rewritten; a run that cannot resume after a cap stop spends the cap
again from the top. Each of those is a test below.
"""

import types

import pytest

from cps.services.reflow import assemble, gate, ledger as ledger_mod, model, pipeline
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


class FakeClient(object):
    """A model that answers the way the test tells it to, and counts its calls."""

    def __init__(self, answer=None, price=0.002):
        self.calls = []
        self._answer = answer or (lambda text: "<p>%s</p>" % text)
        self.spec = types.SimpleNamespace(price_per_page=price, model_id="test/model")
        self.model_id = "test/model"
        self.tier = "standard"
        self.dry_run = False

    def describe(self):
        return {"model": self.model_id, "tier": self.tier, "configured": True,
                "dry_run": False, "prompt_version": "test-1"}

    def edit_page(self, page_text, image_jpeg=None, ladder=(), hints=None,
                  page_label=None, ledger=None, **kwargs):
        if ledger is not None:
            ledger.reserve(self.spec.price_per_page)
        self.calls.append(page_text)
        return model.ModelResult(html=self._answer(page_text), model=self.model_id,
                                 cost_usd=self.spec.price_per_page,
                                 cost_source="price_table", prompt_tokens=900,
                                 completion_tokens=600)


def _doc(*builders):
    doc = F.new_doc()
    for build in builders:
        build(doc)
    return doc


def _run(doc, client, tmp_path, cap=1.0, **kwargs):
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=cap)
    cache = pipeline.PageCache(str(tmp_path / "cache"))
    return pipeline.run(doc, client=client, ledger=book, cache=cache, **kwargs), book


def _drop_a_word(text):
    """A model that quietly loses a word — the failure the gate exists for."""
    words = text.split()
    return "<p>%s</p>" % " ".join(words[:-1])


# ------------------------------------------------------------------- what is sent

def test_only_the_pages_the_router_chose_cost_anything(tmp_path):
    """The whole economic argument: a page the deterministic pass resolved is never
    sent. If this stops being true the estimate the user consented to is a fiction."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    client = FakeClient()
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.calls) == 1, client.calls
    assert result.routed == [1]
    assert book.spent() == pytest.approx(0.002)


def test_the_model_is_asked_about_the_page_the_page_actually_prints(tmp_path):
    """The gate compares the answer with what was sent. Sending a paragraph that was
    already stitched onto its neighbour would make every page turn look like the
    model had added words."""
    doc = _doc(F.uncertain_join_pages)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.calls, "nothing was routed"
    assert "Firmicus gives the same doctrine" in client.calls[-1]
    assert "one manuscript breaks off" not in client.calls[-1], \
        "the previous page's tail was sent along with this page"


# ------------------------------------------------------------------ what is kept

def test_a_page_the_model_mangles_keeps_the_deterministic_text(tmp_path):
    """G1. The answer is not adopted because it came back 200; it is adopted because
    it still contains every word that was sent."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    client = FakeClient(answer=_drop_a_word)
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.gate_failures == 1
    assert result.outcomes[1].source == "deterministic"
    assert result.outcomes[1].gate == "FAIL"
    assert "FAIL" in book.totals()["gate"]
    lost = assemble.page_source_text(result.book, 1).split()[-1]
    assert lost in gate.strip_markup(result.page_html[1]), result.page_html[1][-200:]


def test_a_page_the_model_marked_up_faithfully_is_adopted(tmp_path):
    """The control for the test above: a gate that fails everything would pass it."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    client = FakeClient()
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.gate_failures == 0
    assert result.outcomes[1].source == "model"
    assert result.outcomes[1].gate == "PASS"


# ------------------------------------------------------------------- what resumes

def test_a_second_run_spends_nothing_on_the_pages_already_paid_for(tmp_path):
    """Resumability is not a nicety. A crash, a cancel or a cap stop halfway through
    a 700-page book must not cost the user the first half twice."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
        assert len(client.calls) == 1
        second, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.calls) == 1, "the finished page was sent again"
    assert second.outcomes[1].source == "model"
    assert second.outcomes[1].cost_usd == 0.0
    assert second.reused == 1

    totals = book.totals()

    assert totals["reused"] == 1, totals
    assert totals["calls"] == 1, "the cached page was counted as a second call"
    assert totals["pages"] == 1, totals


def test_a_changed_prompt_invalidates_what_was_cached(tmp_path, monkeypatch):
    """A cached answer belongs to the question that produced it. Answering a new
    prompt with an old page's reply is the kind of bug nobody ever sees."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
        monkeypatch.setattr(pipeline.prompts, "PROMPT_VERSION", "reflow-structure-99")
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.calls) == 2


# -------------------------------------------------------------------- what stops

def test_the_cost_cap_stops_the_run_and_keeps_what_it_paid_for(tmp_path):
    """G5. Stopping at the cap is a normal outcome, not a crash: the finished pages
    stay finished, the book still builds, and the reason is named."""
    doc = _doc(F.uncertain_join_pages, F.ambiguous_residue_page)
    client = FakeClient()
    try:
        result, book = _run(doc, client, tmp_path, cap=0.002)
    finally:
        doc.close()

    assert result.stopped == "cost_cap"
    assert len(client.calls) == 1
    assert book.spent() <= 0.002
    assert result.pages_done == 1
    assert result.page_html, "the book was thrown away when the cap was reached"


def test_a_cancelled_run_stops_between_pages(tmp_path):
    """Cancel has to leave a consistent job behind, which means it can only take
    effect between pages — never half way through one that has been paid for."""
    doc = _doc(F.uncertain_join_pages, F.ambiguous_residue_page)
    client = FakeClient()
    stop = {"now": False}

    def should_stop():
        if client.calls:
            stop["now"] = True
        return stop["now"]

    try:
        result, book = _run(doc, client, tmp_path, should_stop=should_stop)
    finally:
        doc.close()

    assert result.stopped == "cancelled"
    assert len(client.calls) == 1
    assert book.spent() == pytest.approx(0.002)


# ------------------------------------------------------------------- what is told

def test_the_counts_in_the_result_are_the_counts_in_the_ledger(tmp_path):
    """G4. The report is read by somebody deciding whether to trust the conversion.
    If its numbers and the ledger's can drift apart, neither is worth reading."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.uncertain_join_pages)
    client = FakeClient()
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    totals = book.totals()

    assert result.pages_done == totals["pages"]
    assert result.spend_usd == pytest.approx(totals["spend_usd"])
    assert sum(1 for o in result.outcomes.values() if o.source == "model") \
        == totals["gate"].get("PASS", 0)


def test_progress_is_reported_for_every_page_in_order(tmp_path):
    seen = []
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.uncertain_join_pages)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path, progress=seen.append)
    finally:
        doc.close()

    pages = [p.page for p in seen if p.stage == "model"]

    assert pages == sorted(pages)
    assert seen[-1].pages == 4
    assert all(0.0 <= p.fraction <= 1.0 for p in seen)


def test_a_run_with_no_key_configured_still_produces_a_book(tmp_path):
    """The deterministic pass is the product; the model is the improvement. With no
    key at all a user still gets an EPUB, and is told which pages went unreviewed."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = model.OpenRouterClient(api_key=None)
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.page_html
    assert book.spent() == 0.0
    assert result.outcomes[1].source in ("deterministic", "model")
    assert result.stopped in (None, "not_configured")


def test_the_gate_is_the_real_one(tmp_path):
    """A pipeline that imported a different gate from the one the gate tests pin
    would pass every test in both files and ship the defect."""
    assert pipeline.gate is gate


# ------------------------------------------------------------------ what a sample is

def test_a_sample_starts_at_the_book_and_not_at_its_front_matter(tmp_path):
    """SPEC §5. A 30-page sample taken from page one shows a title page, a copyright
    notice and a table of contents, and tells the user nothing about how their book
    converts — which is the whole reason the sample is offered."""
    doc = _doc(F.title_page, F.prose_page, F.prose_page)
    try:
        result, _ = _run(doc, None, tmp_path)
        chosen = pipeline.sample_pages(result.book, result.style, 2)
    finally:
        doc.close()

    assert chosen == [1, 2], chosen


def test_a_sample_of_a_book_with_no_front_matter_starts_at_its_first_page(tmp_path):
    """The control: the rule may not skip a page just because it is the first."""
    doc = _doc(F.prose_page, F.prose_page)
    try:
        result, _ = _run(doc, None, tmp_path)
        chosen = pipeline.sample_pages(result.book, result.style, 2)
    finally:
        doc.close()

    assert chosen == [0, 1], chosen
