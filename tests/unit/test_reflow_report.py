# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 6: what the book says about how it was made.

A conversion the reader cannot audit is a conversion the reader has to take on
trust, and this one used a language model. So the page has to be true in the
awkward directions too: it has to name the pages the gate refused, it has to admit
when no model ran at all, and its numbers have to be the ledger's numbers — a
report that can drift from the evidence it summarises is worse than no report,
because it reads exactly like one that cannot.
"""

import types
from xml.etree import ElementTree as ET

import pytest

from cps.services.reflow import ledger as ledger_mod, model, pipeline, report
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


class FakeClient(object):
    def __init__(self, answer=None, price=0.002, uncertain=None):
        self.calls = []
        self._answer = answer or (lambda text: "<p>%s</p>" % text)
        self._uncertain = uncertain
        self.spec = types.SimpleNamespace(price_per_page=price)
        self.model_id = "test/model"
        self.tier = "standard"
        self.dry_run = False

    def describe(self):
        return {"model": self.model_id, "tier": self.tier, "configured": True,
                "dry_run": False, "prompt_version": "reflow-structure-1"}

    def edit_page(self, page_text, image_jpeg=None, ladder=(), hints=None,
                  page_label=None, ledger=None, **kwargs):
        if ledger is not None:
            ledger.reserve(self.spec.price_per_page)
        self.calls.append(page_text)
        spans = self._uncertain(len(self.calls)) if self._uncertain else []
        return model.ModelResult(html=self._answer(page_text), model=self.model_id,
                                 cost_usd=self.spec.price_per_page,
                                 uncertain=spans, cost_source="price_table",
                                 prompt_tokens=900, completion_tokens=600)


def _drop_a_word(text):
    return "<p>%s</p>" % " ".join(text.split()[:-1])


def _run(tmp_path, *builders, **kwargs):
    client = kwargs.pop("client", None)
    doc = F.new_doc()
    for build in builders:
        build(doc)
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=kwargs.pop("cap", 1.0))
    cache = pipeline.PageCache(str(tmp_path / "cache"))
    try:
        result = pipeline.run(doc, client=client, ledger=book, cache=cache, **kwargs)
    finally:
        doc.close()
    return result, book, client


def _page(payload, show_cost=False):
    return report.about_page(payload, show_cost=show_cost)


def _text(html):
    return ET.tostring(ET.fromstring("<body>%s</body>" % html), method="text",
                       encoding="unicode")


# ------------------------------------------------------------------- the source

def test_the_report_says_in_plain_words_what_the_source_was(tmp_path):
    """A user looking at a bad conversion needs to learn that their PDF was a scan
    before they conclude the converter is broken."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.prose_page)
    payload = report.numbers(result, ledger)

    text = _text(_page(payload))

    assert str(payload["source"]["pages"]) in text
    assert payload["source"]["verdict_plain"] in text


def test_the_report_states_that_the_wording_was_not_changed(tmp_path):
    """SPEC §7 asks for this sentence by name, because it is the promise the whole
    gate exists to keep."""
    result, ledger, _ = _run(tmp_path, F.prose_page)

    text = _text(_page(report.numbers(result, ledger)))

    assert "No wording was changed by this conversion." in text


# ---------------------------------------------------------------- what it admits

def test_a_page_the_gate_refused_is_stated_and_not_quietly_dropped(tmp_path):
    """The interesting number in the whole report. A converter that hides its
    refusals looks better than one that reports them and is worth less."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(answer=_drop_a_word))
    payload = report.numbers(result, ledger)

    text = _text(_page(payload))

    assert payload["model"]["pages_refused"] == 1, payload["model"]
    assert "1" in text and "refused" in text.lower(), text


def _restore_marker(number, where):
    def answer(text):
        out = []
        for part in text.split("\n\n"):
            if part.startswith("["):
                num, _, rest = part.partition("] ")
                out.append('<aside class="footnote" id="fn_%s">%s %s</aside>'
                           % (num[1:], num[1:], rest))
            else:
                out.append("<p>%s</p>"
                           % part.replace(where, '.<a class="noteref" href="#fn_%d">%d</a>'
                                          % (number, number), 1))
        return "\n".join(out)
    return answer


def test_a_footnote_the_model_reconnected_is_no_longer_counted_as_unmarked(tmp_path):
    """The count the reader acts on. ``notes_unmarked`` is measured before the model
    runs; reporting it afterwards tells a reader their book still has an orphaned
    footnote when the link is right there in it — and the repair, which changed a
    printed character, would go unstated."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(answer=_restore_marker(88, ".\'\"")))
    payload = report.numbers(result, ledger)

    assert payload["structure"]["markers_recovered"] == 1, payload["structure"]
    assert payload["structure"]["footnotes_unmarked"] == 0, payload["structure"]
    assert "88" in _text(_page(payload))


def _split_swept_note(number, where):
    """The model answer that puts back a note the scan folded into its neighbour."""
    def answer(text):
        out = []
        for part in text.split("\n\n"):
            if not part.startswith("["):
                out.append("<p>%s</p>" % part.replace(
                    ".%d" % number,
                    '.<a class="noteref" href="#fn_%d">%d</a>' % (number, number), 1))
                continue
            num, _, rest = part.partition("] ")
            num = num[1:]
            head, found, tail = rest.partition(where)
            out.append('<aside class="footnote" id="fn_%s">%s %s</aside>'
                       % (num, num, head.strip() if found else rest))
            if found:
                out.append('<aside class="footnote" id="fn_%d">%d %s</aside>'
                           % (number, number, tail.strip()))
        return "\n".join(out)
    return answer


def test_a_note_the_scan_swept_away_is_admitted_although_nothing_else_flagged_it(tmp_path):
    """The damage no other count can see. The page printed three notes and the book
    ships two, with the third's citation sitting inside the second. Nothing is
    unmarked, nothing is unresolved, the conservation check is happy because no word
    was lost -- so unless this page says it, the reader is told the conversion went
    perfectly and the note they cannot find is their own fault."""
    control = tmp_path / "control"
    control.mkdir()
    quiet, quiet_ledger, _ = _run(control, F.prose_page, F.prose_page)
    result, ledger, _ = _run(tmp_path, F.prose_page, F.quietly_swept_note_page)

    payload = report.numbers(result, ledger)

    assert payload["structure"]["footnotes_swept"] == 1, payload["structure"]
    assert len(payload["unplaced"]) == \
        len(report.numbers(quiet, quiet_ledger)["unplaced"]) + 1, payload["unplaced"]
    assert "125" not in _text(_page(payload)), \
        "the number is a guess at what the page printed, not something to quote"


def test_a_swept_note_the_model_put_back_is_not_still_called_missing(tmp_path):
    """The other direction, and the one a reader acts on: a report that keeps saying
    a note is buried after the conversion dug it out sends them looking for damage
    that is not in their book."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.swept_note_page,
                             client=FakeClient(answer=_split_swept_note(59, ' " ')))
    payload = report.numbers(result, ledger)

    assert payload["structure"]["footnotes_swept_before_review"] == 1
    assert payload["structure"]["footnotes_swept_restored"] == 1, payload["structure"]
    assert payload["structure"]["footnotes_swept"] == 0


def test_a_conversion_with_no_model_says_so_rather_than_reporting_a_clean_sweep(tmp_path):
    """With no key configured every page is deterministic. Reporting that as 'no
    pages failed the gate' would be true and deeply misleading."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page)
    payload = report.numbers(result, ledger)

    text = _text(_page(payload))

    assert payload["model"]["used"] is False
    assert "no model" in text.lower() or "not reviewed" in text.lower(), text


def test_the_pages_that_were_never_reviewed_are_counted(tmp_path):
    """A run stopped by its cap leaves routed pages unreviewed. The reader is
    entitled to know how much of the book the model never saw."""
    result, ledger, _ = _run(tmp_path, F.uncertain_join_pages,
                             F.ambiguous_residue_page,
                             client=FakeClient(), cap=0.002)
    payload = report.numbers(result, ledger)

    assert payload["stopped"] == "cost_cap"
    assert payload["fidelity"]["pages_routed_not_reviewed"] >= 1, payload["fidelity"]
    assert "cap" in _text(_page(payload)).lower()


# ------------------------------------------------------------------ the spend

def test_the_spend_is_shown_only_when_the_job_asked_for_it(tmp_path):
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient())
    payload = report.numbers(result, ledger)

    assert "$" not in _text(_page(payload, show_cost=False))
    assert "$" in _text(_page(payload, show_cost=True))


def test_the_spend_is_in_the_sidecar_whether_or_not_the_page_shows_it(tmp_path):
    """The page is for the reader; the JSON is the operator's record, and an
    operator reconciling a bill cannot be opted out of it by a checkbox."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient())

    payload = report.numbers(result, ledger)

    assert payload["spend"]["usd"] == pytest.approx(0.002)
    assert payload["spend"]["calls"] == 1


# --------------------------------------------------------------------- G4

def test_the_reports_counts_are_the_ledgers_counts(tmp_path):
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             F.uncertain_join_pages, client=FakeClient())

    problems = report.check_completion(report.numbers(result, ledger), ledger)

    assert problems == [], problems


@pytest.mark.parametrize("field, delta", [
    ("pages_sent", 1), ("pages_adopted", 1), ("pages_refused", 1),
])
def test_a_report_that_has_drifted_from_the_ledger_is_caught(tmp_path, field, delta):
    """G4 seen red. Each of these is a number a reader would act on."""
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient())
    payload = report.numbers(result, ledger)
    payload["model"][field] += delta

    assert report.check_completion(payload, ledger), payload["model"]


def test_arithmetic_alone_never_fails_the_completion_gate(tmp_path):
    """MEASURED on the acceptance fixture: a two-page run reported $0.002634 against
    a ledger that held $0.002633 and G4 refused the job over it. The two numbers are
    sums of the same per-page costs, so any difference between them is the run's own
    arithmetic — and a gate that fails a good conversion is worse than no gate,
    because the user is told their book is wrong and it is not."""
    result, ledger, _ = _run(tmp_path, F.ambiguous_residue_page, F.ambiguous_residue_page,
                             F.ambiguous_residue_page,
                             client=FakeClient(price=0.0000005))
    payload = report.numbers(result, ledger)

    assert report.check_completion(payload, ledger) == []


def test_a_spend_that_has_drifted_from_the_ledger_is_caught(tmp_path):
    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient())
    payload = report.numbers(result, ledger)
    payload["spend"]["usd"] += 0.01

    assert report.check_completion(payload, ledger)


# ------------------------------------------------------------------ uncertainty

def test_only_the_first_uncertain_readings_are_listed_and_the_rest_are_counted(tmp_path):
    """A scan can produce hundreds. A page that prints all of them is one nobody
    reads, and the count is the part that tells the reader how much to trust."""
    def many(call):
        return [{"token": "reading %d" % n, "candidates": ["a", "b"]}
                for n in range(30)]

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=many))
    payload = report.numbers(result, ledger)

    listed = payload["fidelity"]["uncertain"]

    assert payload["fidelity"]["uncertain_total"] == 30
    assert len(listed) == report.MAX_UNCERTAIN_LISTED
    assert len(listed) < payload["fidelity"]["uncertain_total"], "nothing was trimmed"
    text = _text(_page(payload))
    assert "30" in text


def test_an_uncertain_reading_links_to_the_page_it_is_on(tmp_path):
    def one(call):
        return [{"token": "luminanes", "candidates": ["luminaries"]}]

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=one))
    payload = report.numbers(result, ledger)

    html = report.about_page(payload, links={1: "ch001.xhtml"})

    assert 'href="ch001.xhtml#pg_0001"' in html, html


def test_the_reading_the_model_flagged_is_the_one_the_list_prints(tmp_path):
    """A list that names no word sends the reader to a page to look for nothing.

    The spans are built here by the real contract parser rather than by hand, so
    the page and the prompt cannot drift apart: the shape the model is asked for
    is the shape the reader's list has to be able to read.
    """
    line = ('{"uncertain": [{"token": "Hephaestio.s\u00b0", '
            '"candidates": ["Hephaestio.50"]}], "notes": ""}')
    spans, _notes, _html = model._split_contract("<p>x</p>\n" + line)

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=lambda call: spans))
    payload = report.numbers(result, ledger)

    text = _text(_page(payload))

    assert "Hephaestio.s\u00b0" in text
    assert "Hephaestio.50" in text


def test_the_page_says_how_many_of_the_readings_a_reader_can_actually_find(tmp_path):
    """A reading is marked in the text where the conversion can point at the word,
    and only described where it cannot. Saying "12 marked uncertain" when the book
    highlights three sends nine readers looking for a highlight that is not there.

    ``Rhetorius`` is printed on the fixture page; ``Poeme`` is not.
    """
    def two(call):
        return [{"token": "Rhetorius", "candidates": ["Rhetorios"]},
                {"token": "Poeme", "candidates": ["Po\u00e8me"]}]

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=two))
    payload = report.numbers(result, ledger)

    assert payload["fidelity"]["uncertain_total"] == 2
    assert payload["fidelity"]["uncertain_marked"] == 1
    assert 'class="reflow-uncertain"' in result.page_html[1]
    assert "1 of them is highlighted in the text" in _page(payload)


def test_a_book_where_every_reading_is_marked_does_not_count_them_twice(tmp_path):
    def one(call):
        return [{"token": "Rhetorius", "candidates": ["Rhetorios"]}]

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=one))
    payload = report.numbers(result, ledger)

    assert payload["fidelity"]["uncertain_marked"] == 1
    assert "it is highlighted in the text" in _page(payload)


def test_a_reading_the_conversion_cannot_point_at_promises_no_highlight(tmp_path):
    def one(call):
        return [{"token": "Poeme", "candidates": ["Po\u00e8me"]}]

    result, ledger, _ = _run(tmp_path, F.prose_page, F.ambiguous_residue_page,
                             client=FakeClient(uncertain=one))
    payload = report.numbers(result, ledger)

    assert payload["fidelity"]["uncertain_marked"] == 0
    assert "highlighted" not in _page(payload)


# ---------------------------------------------------------------- well-formedness

def test_the_report_is_well_formed_even_when_the_book_is_awkward(tmp_path):
    """Font names, titles and OCR readings all reach this page as text. One
    ampersand among them and no reader will open the book."""
    result, ledger, _ = _run(tmp_path, F.typographers_page,
                             F.ambiguous_residue_page,
                             client=FakeClient(uncertain=lambda call: [
                                 {"token": "Hall & Fisher <sic>",
                                  "candidates": ["Hall and Fisher"]}]))
    payload = report.numbers(result, ledger)
    payload["source"]["fonts"] = {"Times & Co <Roman>": 4000}

    text = _text(report.about_page(payload, show_cost=True))

    assert "Hall & Fisher" in text
    assert "Times & Co <Roman>" in text
