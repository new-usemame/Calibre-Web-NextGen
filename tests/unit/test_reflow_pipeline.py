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

import io
import types

import pymupdf
import pytest

from cps.services.reflow import (assemble, extract, gate, ledger as ledger_mod, model,
                                 pipeline)
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


class FakeClient(object):
    """A model that answers the way the test tells it to, and counts its calls."""

    def __init__(self, answer=None, price=0.002, uncertain=None):
        self.calls = []
        self.hints = []
        self.images = []
        self.headings = []
        self.ladders = []
        self._uncertain = uncertain if callable(uncertain) else (
            lambda _text, records=list(uncertain or ()): records)
        self._answer = answer
        self.spec = types.SimpleNamespace(price_per_page=price, model_id="test/model")
        self.model_id = "test/model"
        self.tier = "standard"
        self.dry_run = False

    def describe(self):
        return {"model": self.model_id, "tier": self.tier, "configured": True,
                "dry_run": False, "prompt_version": "test-1"}

    def _faithful(self, page_text, headings):
        """What a model that does as it is told returns: the blocks it was given, in
        the order it was given them, with the declared headings marked and nothing
        else made into one."""
        levels = {text.strip(): int(level) for level, text in headings}
        blocks = []
        for block in page_text.split("\n\n"):
            level = levels.get(block.strip())
            blocks.append("<h%d>%s</h%d>" % (level, block, level) if level
                          else "<p>%s</p>" % block)
        return "\n".join(blocks)

    def edit_page(self, page_text, image_jpeg=None, ladder=(), hints=None,
                  page_label=None, ledger=None, headings=(), **kwargs):
        if ledger is not None:
            ledger.reserve(self.spec.price_per_page)
        self.calls.append(page_text)
        self.hints.append(list(hints or []))
        self.images.append(image_jpeg)
        self.headings.append([(int(level), text) for level, text in (headings or ())])
        self.ladders.append(list(ladder or ()))
        html = (self._answer(page_text) if self._answer is not None
                else self._faithful(page_text, headings or ()))
        return model.ModelResult(html=html, model=self.model_id,
                                 uncertain=[dict(r) for r in self._uncertain(page_text)],
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


class TestWhatIsAHeadingIsNotTheModelsToDecide(object):
    """The reader measures the headings; the model is told them and held to them.

    MEASURED on page index 102 of the acceptance book, in the run of record: asked
    to mark headings itself, the model made ``<h2>`` of items 6, 7 and 8 of a
    numbered list that runs on from the previous page. ``build_epub.SPLIT_LEVELS``
    starts a chapter at every h1 and h2, so that one page would have added three
    chapters to the reader's table of contents, titled with three sentences.
    """

    PAGES = (F.prose_page, F.heading_page_the_model_has_to_see, F.prose_page)
    HEAD = "Serapio of Alexandria (First Century CE?)"

    def _answer_that_promotes_the_body(self, page_text):
        head, body, notes = page_text.split("\n\n")
        return ('<h1>%s</h1>\n<h2>%s</h2>\n'
                '<aside class="footnote" id="fn_88">%s</aside>'
                % (head, body, notes.replace("[88] ", "88 ")))

    def test_the_model_is_told_which_lines_the_page_sets_as_headings(self, tmp_path):
        doc = _doc(*self.PAGES)
        client = FakeClient()
        try:
            _run(doc, client, tmp_path)
        finally:
            doc.close()

        assert client.headings, "nothing was routed"
        assert client.headings[-1] == [(1, self.HEAD)], client.headings

    def test_a_sentence_the_model_promoted_keeps_the_deterministic_text(self, tmp_path):
        doc = _doc(*self.PAGES)
        client = FakeClient(answer=self._answer_that_promotes_the_body)
        try:
            result, _ = _run(doc, client, tmp_path)
        finally:
            doc.close()

        assert result.outcomes[1].source == "deterministic"
        assert any("body text" in reason
                   for reason in result.outcomes[1].gate_reasons), \
            result.outcomes[1].gate_reasons

    def test_the_same_answer_with_the_sentence_left_alone_is_adopted(self, tmp_path):
        """The control. A gate that refused every page would pass the test above."""
        doc = _doc(*self.PAGES)
        client = FakeClient(answer=lambda text: self._answer_that_promotes_the_body(
            text).replace("<h2>", "<p>").replace("</h2>", "</p>"))
        try:
            result, _ = _run(doc, client, tmp_path)
        finally:
            doc.close()

        assert result.outcomes[1].source == "model", result.outcomes[1].gate_reasons
        assert "<h1>%s</h1>" % self.HEAD in result.page_html[1]


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


def _restore_marker(number, where):
    """A model that reads the scan and puts back a marker the text layer lost.

    Faithful in every other respect: the page's own paragraphs and its own note
    text, marked up and not rewritten, so the only thing the gate has to judge is
    the marker."""
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


def test_a_marker_the_scanner_destroyed_can_come_back_off_the_page_image(tmp_path):
    """The page the deterministic pass deliberately refused to guess at.

    ``ambiguous_residue_page`` prints two quotation residues and one unreferenced
    note, so the counts disagree and the deterministic repair steps back — which is
    correct, and which is also why the page is routed. The model is looking at the
    scan, where the superscript is legible. If the gate refuses its answer anyway,
    routing the page bought nothing and the reader keeps a footnote with no link.
    """
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(answer=_restore_marker(88, ".'\""))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "PASS", result.outcomes[1].gate_reasons
    assert result.outcomes[1].source == "model"
    assert 'href="#fn_88"' in result.page_html[1]


class TestAQuestionTheGateWillNotLetTheModelAnswer(object):
    """A page is paid for to settle something. These two reasons settle nothing.

    ``run_in_candidate_rejected`` and ``large_type_not_a_heading`` are the reader
    declining to call a line a heading. Since G3 checks the answer's headings
    against the ones the reader measured, the model cannot overrule that decision
    -- so a page whose only open question is one of these has no answer the gate
    would take, and sending it buys a raster, a prompt and a bill.

    MEASURED on the acceptance book: 22 pages raise one of the two and the
    deterministic answer is right on all 22. Twenty are chart glyphs and scan
    wreckage set large (``'Ts a 9``, ``©``, ``12 ytilks``); two are the
    ``CHAPTER 7`` line standing above a chapter title the reader already marks as
    the h1; six are first lines of paragraphs set bold. Seven of the 22 raise
    nothing else, and those seven are what this stops paying for.
    """

    def test_a_bold_lead_in_alone_is_not_worth_a_model_call(self, tmp_path):
        doc = _doc(F.prose_page, F.lead_in_page)
        client = FakeClient()
        try:
            result, _ = _run(doc, client, tmp_path)
        finally:
            doc.close()

        assert result.routed == [], result.routing["reasons"]
        assert client.calls == []

    def test_the_reader_still_says_it_looked(self, tmp_path):
        """Silently dropping the reason would leave the report claiming the page
        had nothing unusual on it, which is not the same statement."""
        doc = _doc(F.prose_page, F.lead_in_page)
        try:
            result, _ = _run(doc, FakeClient(), tmp_path)
        finally:
            doc.close()

        assert result.routing["reasons"].get("run_in_candidate_rejected") == 1

    def test_a_page_with_a_real_question_on_it_is_still_sent(self, tmp_path):
        """The reason is inert, not poisonous: it must not stop a page that has
        something else the model can settle."""
        doc = _doc(F.prose_page, F.lead_in_and_lost_marker_page)
        client = FakeClient()
        try:
            result, _ = _run(doc, client, tmp_path)
        finally:
            doc.close()

        assert result.routed == [1]
        assert "run_in_candidate_rejected" in result.routing["reasons"]


def _add_marker_beside(number, after):
    """A model that leaves the text layer alone and puts the noteref next to it."""
    def answer(text):
        out = []
        for part in text.split("\n\n"):
            if part.startswith("["):
                num, _, rest = part.partition("] ")
                out.append('<aside class="footnote" id="fn_%s">%s %s</aside>'
                           % (num[1:], num[1:], rest))
            else:
                out.append("<p>%s</p>"
                           % part.replace(after, after + '<a class="noteref" '
                                          'href="#fn_%d">%d</a>' % (number, number), 1))
        return "\n".join(out)
    return answer


def test_a_marker_may_be_put_back_beside_wreckage_that_cannot_be_deleted(tmp_path):
    """The measured shape the first version of this repair could not handle.

    ``wrecked_marker_page`` prints a superscript 135 that the scan returned as
    ``'ts``. Deleting that is a letter leaving the page, which the gate refuses
    and should. If adding the noteref beside it is refused too then the page has
    no answer the gate will take, and four of the twenty-six pages of the
    acceptance sample were thrown away for exactly that.
    """
    doc = _doc(F.prose_page, F.wrecked_marker_page)
    client = FakeClient(answer=_add_marker_beside(135, ".'ts"))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "PASS", result.outcomes[1].gate_reasons
    assert result.outcomes[1].source == "model"
    assert 'href="#fn_135"' in result.page_html[1]
    assert ".'ts" in result.page_html[1], \
        "the scan's nonsense is what the page says; the repair does not hide it"


def test_the_number_beside_the_wreckage_is_still_one_the_page_is_missing(tmp_path):
    """The allowance is a note this page prints and never refers to. Without that
    the model may write any number it likes next to any word it likes."""
    doc = _doc(F.prose_page, F.wrecked_marker_page)
    client = FakeClient(answer=_add_marker_beside(136, ".'ts"))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "FAIL"
    assert result.outcomes[1].source == "deterministic"


# ------------------------------------------------- R3: damage marked, not replaced

def test_a_reading_the_model_flagged_is_marked_in_the_page_the_reader_gets(tmp_path):
    """R3 is only kept if the mark reaches the book. The record on its own is a
    line in a log; the reader has to be able to see which word of their book the
    conversion is not sure about, and read the printed one anyway."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(
        uncertain=lambda text: [{"token": text.split()[0], "candidates": ["mangled"]}])
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    outcome = result.outcomes[1]
    flagged = client.calls[0].split()[0]
    assert outcome.gate == "PASS", outcome.gate_reasons
    assert outcome.marked == 1
    assert ('<span class="reflow-uncertain" title="likely: mangled">%s</span>' % flagged
            in result.page_html[1])


def test_a_page_the_gate_refused_carries_no_marks(tmp_path):
    """The marks belong to the model's answer. A page whose answer was thrown away
    ships the deterministic text, and a highlight on it would be pointing at a
    reading nothing in this book ever adopted."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(answer=_drop_a_word,
                        uncertain=[{"token": "the", "candidates": ["teh"]}])
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "FAIL"
    assert "reflow-uncertain" not in result.page_html[1]
    assert result.outcomes[1].marked == 0


# --------------------------------------- R3: recovered source evidence survives

def _recovery_with_uncertain(pno, token, score=20.0):
    """The recovery state a local OCR run leaves when its engine was unsure of
    one word on the page: provenance, and the word as a source-evidence record."""
    from cps.services.reflow import ocr, source
    word = ocr.OCRWord(token, (0, 0, 30, 10), (0, 0, 30, 10), (0, 0, 30, 10),
                       score, 1, 1, 1)
    spans = source.uncertain_spans(types.SimpleNamespace(words=(word,)))
    recovery = source.Recovery()
    recovery.provenance[pno] = source.PageRecovery(
        pno=pno, layer="ocr", words=1, uncertain_words=1, uncertain=spans)
    return recovery


def _run_with_recovery(doc, client, tmp_path, recovery, cache=None):
    from cps.services.reflow import source
    book = ledger_mod.Ledger(tmp_path / "job.jsonl", cap_usd=1.0)
    cache = cache or pipeline.PageCache(str(tmp_path / "cache"))

    def fake_recover(_doc, raw_pages, fingerprint, **kwargs):
        recovery.pages = raw_pages
        return recovery

    original = source.recover
    source.recover = fake_recover
    try:
        result = pipeline.run(doc, client=client, ledger=book, cache=cache)
    finally:
        source.recover = original
    return result


def test_a_model_answer_keeps_the_recovered_uncertain_readings(tmp_path):
    """A valid, word-conserving model reply cannot silently strip the OCR layer's
    own low-confidence evidence: the adopted page keeps the visible mark and the
    reportable source record (independent repro: low_confidence_marks=0)."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    recovery = _recovery_with_uncertain(1, "Rhetorius")
    try:
        result = _run_with_recovery(doc, FakeClient(), tmp_path, recovery)
    finally:
        doc.close()

    outcome = result.outcomes[1]
    assert outcome.gate == "PASS", outcome.gate_reasons
    assert outcome.marked >= 1
    assert 'class="reflow-uncertain"' in result.page_html[1]
    evidence = [record for record in outcome.uncertain
                if record.get("token") == "Rhetorius"]
    assert evidence, outcome.uncertain
    assert evidence[0].get("score") == 20.0
    assert evidence[0].get("source_bbox")


def test_a_warm_cache_replay_keeps_the_recovered_uncertain_readings(tmp_path):
    """The cache stores the model's own list, which may be empty. A warm resume
    must re-derive the same source evidence at adoption, not replay the empty
    list and ship the page unmarked."""
    cache = pipeline.PageCache(str(tmp_path / "cache"))
    recovery = _recovery_with_uncertain(1, "Rhetorius")
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    try:
        cold = _run_with_recovery(doc, FakeClient(), tmp_path, recovery, cache=cache)
    finally:
        doc.close()
    assert cold.outcomes[1].gate == "PASS"

    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient()
    try:
        warm = _run_with_recovery(doc, client, tmp_path, recovery, cache=cache)
    finally:
        doc.close()

    assert client.calls == [], "the second run must come from the warm cache"
    outcome = warm.outcomes[1]
    assert outcome.cached and outcome.gate == "PASS"
    assert outcome.marked >= 1
    assert 'class="reflow-uncertain"' in warm.page_html[1]
    assert any(record.get("token") == "Rhetorius" and record.get("score") == 20.0
               for record in outcome.uncertain), outcome.uncertain


def test_a_marker_the_gate_says_came_back_is_not_also_called_unresolved(tmp_path):
    """MEASURED on the acceptance book: 19 of 28 flagged readings were superscripts
    the same answer had already put back as noterefs. Listing those on the about
    page sends a reader to look at damage that is not there, and inflates the one
    number the page uses to describe how uncertain the conversion was."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(answer=_restore_marker(88, ".'\""),
                        uncertain=[{"token": ".'\"", "candidates": ["88"]}])
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    outcome = result.outcomes[1]
    assert outcome.gate == "PASS", outcome.gate_reasons
    assert outcome.recovered_markers == [88]
    assert outcome.uncertain == []


def test_a_reading_nothing_explains_is_kept_even_when_it_cannot_be_marked(tmp_path):
    """The fallback that keeps the filter honest: a reading the conversion cannot
    point at is still a reading, and the about page still has to say so."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(uncertain=[{"token": "its\u00b0", "candidates": ["it."]}])
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    outcome = result.outcomes[1]
    assert outcome.gate == "PASS", outcome.gate_reasons
    assert outcome.marked == 0
    assert [span["token"] for span in outcome.uncertain] == ["its\u00b0"]


def test_the_model_is_told_what_the_deterministic_pass_could_not_settle(tmp_path):
    """Paying for a page and not saying why it was sent wastes the call.

    The page below prints note 88 and never refers to it. Told that, the model knows
    to look for one superscript in the scan; told nothing, it is being asked to
    re-mark a page that already looks finished, and the commonest damage in the book
    goes unrepaired.
    """
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.hints, "the page was not routed"
    said = " ".join(client.hints[-1])
    assert "88" in said, said


def _split_swept_note(number, where):
    """A model that finds the note the scan folded into the note before it.

    It rewrites nothing: the note whose text holds *where* is cut there, and the
    number the scan ate goes back at the head of the second half, where the page
    prints it. The body marker survived fused to the word in front of it, so the
    only other thing the answer does is detach it."""
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


def test_the_number_of_a_note_the_scan_swept_away_can_come_back(tmp_path):
    """MEASURED, page 103 of the acceptance book: the page prints notes 58, 59 and 60,
    and the text layer returns 59's text run on into 58's behind a stray quotation
    mark. The page is routed for exactly this. Refusing the answer that fixes it is
    the worst of both -- the call is paid for and the note stays buried."""
    doc = _doc(F.prose_page, F.swept_note_page)
    client = FakeClient(answer=_split_swept_note(59, ' " '))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    outcome = result.outcomes[1]
    assert outcome.gate == "PASS", outcome.gate_reasons
    assert outcome.recovered_markers == [59]
    assert 'id="fn_59">59 Pliny' in result.page_html[1], result.page_html[1]


def test_the_model_is_told_which_number_the_page_stopped_printing(tmp_path):
    """The hint is the difference between a model that looks for a missing note and
    one that reformats a page which already looks finished. It has to say the number:
    the gate will take that number back and no other."""
    doc = _doc(F.prose_page, F.swept_note_page)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.hints, "the page was not routed"
    said = " ".join(client.hints[-1])
    assert "59" in said, said
    assert "58" in said, said


def test_the_model_is_told_which_numbers_the_scan_read_wrong(tmp_path):
    """MEASURED, page 125 of the acceptance book: the page prints 190 under the rule,
    the text layer returned 198, and the deterministic pass repaired it off the
    numbering either side. The model was then sent the repaired text and a raster of
    the same small print, read 198 off the image exactly as the scanner had, and had
    its whole page refused over two digits we already knew the answer to.

    A page is not paid for twice, so the repair has to travel with the page.
    """
    doc = _doc(F.prose_page, F.note_number_read_too_high_page)
    client = FakeClient()
    try:
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.hints, "the page was not routed"
    told = client.hints[-1]
    assert any("38" in hint and "30" in hint for hint in told), told
    assert any("39" in hint and "31" in hint for hint in told), told


def test_a_number_the_page_never_lost_is_still_refused(tmp_path):
    """The control for the widened allowance. The gap says 59 and nothing else, so a
    model that reads 57 off the same residue is guessing at a citation."""
    doc = _doc(F.prose_page, F.swept_note_page)
    client = FakeClient(answer=_split_swept_note(57, ' " '))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "FAIL"
    assert result.outcomes[1].source == "deterministic"


def test_a_marker_for_a_note_the_page_does_not_print_is_still_refused(tmp_path):
    """The control. The allowance is the page's own unreferenced notes and nothing
    else; a model that reads ``89`` off a page whose note is 88 is guessing, and the
    guess would be a link to the wrong source."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(answer=_restore_marker(89, ".'\""))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].gate == "FAIL"
    assert result.outcomes[1].source == "deterministic"


def test_an_answer_that_invents_a_heading_level_is_refused(tmp_path):
    """G3. Every word is there, so the word gate is happy; the page has been given a
    heading level this book does not use, which is a guess that would land in the
    reader's table of contents."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    client = FakeClient(answer=lambda text: "<h4>%s</h4>" % text)
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[1].source == "deterministic"
    assert result.outcomes[1].gate == "FAIL"
    assert any("h4" in reason for reason in result.outcomes[1].gate_reasons), \
        result.outcomes[1].gate_reasons
    assert "<h4>" not in result.page_html[1]


def test_an_answer_that_drops_the_pages_figure_is_refused(tmp_path):
    """G2. An illustration leaves no words in the text layer, so an answer that
    simply does not mention it keeps every word it was given and takes the picture
    out of the reader's book."""
    doc = _doc(lambda d: F.illustrated_page(d, F.solid_png()))
    client = FakeClient()
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.calls, "a page with an uncaptioned figure was not routed"
    assert result.outcomes[0].gate == "FAIL"
    assert any("figure" in reason for reason in result.outcomes[0].gate_reasons), \
        result.outcomes[0].gate_reasons
    assert "<figure>" in result.page_html[0]


def test_an_answer_that_keeps_the_pages_figure_is_adopted(tmp_path):
    """The control: a gate that refused every answer would pass the test above."""
    doc = _doc(lambda d: F.illustrated_page(d, F.solid_png()))
    figure = ('<figure><img src="images/fig_p0000_0.jpg" alt=""/>'
              '<figcaption class="reflow-no-caption"></figcaption></figure>')
    client = FakeClient(answer=lambda text: "<p>%s</p>%s" % (text, figure))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[0].gate == "PASS", result.outcomes[0].gate_reasons
    assert result.outcomes[0].source == "model"


def test_an_answer_carrying_active_or_remote_markup_is_refused_whole(tmp_path):
    """The adoption boundary, end to end: every word in the answer is the page's
    own, so the word gate cannot see the problem -- the answer is refused at the
    structure gate, the page ships its deterministic text, and the refusal is
    recorded. Stripping the markup and adopting the page anyway would report a
    PASS for markup the reader never actually receives."""
    doc = _doc(F.ambiguous_residue_page)
    client = FakeClient(answer=lambda text: (
        "<p>%s</p><img src=\"https://example.invalid/pixel.gif\" alt=\"\" "
        "onerror=\"fetch('https://example.invalid/ev')\"/>" % text))
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[0].gate == "FAIL", result.outcomes[0].gate_reasons
    assert result.outcomes[0].source == "deterministic"
    assert "example.invalid" not in result.page_html[0]
    assert any("onerror" in reason or "src" in reason
               for reason in result.outcomes[0].gate_reasons), \
        result.outcomes[0].gate_reasons
    entry = [e for e in book.entries("page") if e.get("page") == 0][0]
    assert entry["gate"] == "FAIL"


# ------------------------------------------------------------------- what resumes

def test_concurrent_cache_writes_for_one_key_never_torn_or_lost(tmp_path):
    """Two writers on one cache key used to share a single fixed ``.tmp`` name:
    interleaved dumps wrote through each other's file, and the second
    ``os.replace`` could rename the winner away under the loser. Each write now
    gets its own temporary file, so a writer can only ever replace the published
    file with its own complete payload."""
    import threading

    cache = pipeline.PageCache(str(tmp_path / "cache"))
    # Many keys, so json.dump makes many small write() calls and genuinely
    # interleaves with another thread writing the same file.
    payloads = [{"html": "<p>page %d</p>" % n,
                 "rows": {"%d" % i: "word %d" % i for i in range(300)},
                 "n": n} for n in range(4)]
    failures = []

    def write(payload):
        try:
            for _ in range(10):
                cache.put("fp", 3, "test/model", "source", payload)
        except Exception as exc:                                    # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(payload,))
               for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, failures
    stored = cache.get("fp", 3, "test/model", "source")
    assert stored in payloads, "the published file is one writer's whole payload"
    import os as _os
    leftovers = []
    for _dirpath, _dirnames, names in _os.walk(str(tmp_path / "cache")):
        leftovers.extend(n for n in names if n.endswith(".tmp"))
    assert leftovers == [], leftovers


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


def test_changed_page_instructions_are_not_answered_by_an_old_cached_reply(
        tmp_path, monkeypatch):
    """The words can stay the same while the reader learns where a note belongs.

    Resume must send the newly measured instructions, not repeatedly reject an
    answer bought for the old geometry or note hints.
    """
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient()
    real_hints = pipeline.page_hints
    try:
        _run(doc, client, tmp_path)
        monkeypatch.setattr(pipeline, "page_hints", lambda *args, **kwargs:
                            real_hints(*args, **kwargs) + ["The note starts below the rule."])
        second, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.calls) == 2, "new instructions were replaced with an old answer"
    assert client.hints[-1][-1] == "The note starts below the rule."
    assert second.reused == 0
    assert second.outcomes[1].gate == "PASS"


def test_a_provider_resolved_model_name_does_not_charge_again_on_resume(tmp_path):
    """A response's concrete model name need not equal the requested alias."""
    class ResolvedModel(FakeClient):
        def edit_page(self, *args, **kwargs):
            answer = super().edit_page(*args, **kwargs)
            answer.model = "test/resolved-model-version"
            return answer

    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = ResolvedModel()
    try:
        first, _ = _run(doc, client, tmp_path)
        second, log = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert first.outcomes[1].gate == "PASS"
    assert len(client.calls) == 1, "the alias missed its own paid answer"
    assert second.reused == 1
    assert second.outcomes[1].model == "test/resolved-model-version"
    assert second.outcomes[1].source == "model"
    assert second.outcomes[1].cost_usd == 0
    assert log.totals()["calls"] == 1


def test_a_page_the_reader_now_reads_differently_is_not_answered_from_the_cache(
        tmp_path, monkeypatch):
    """The other half of the same rule, and the half that actually fires.

    The prompt version changes when we rewrite the instructions; the page changes
    every time the deterministic reader gets better at reading it -- a note number
    repaired, a marker recovered, a sentence stitched -- which is most releases. The
    answer bought for the old reading was never an answer to the new question, and
    serving it means the improvement is paid for and then thrown away.
    """
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient()
    real = assemble.page_source_text
    try:
        _run(doc, client, tmp_path)
        # A reader that now finds one more line on the page than it used to.
        monkeypatch.setattr(assemble, "page_source_text",
                            lambda book, pno: real(book, pno) + "\n\nand a later hand adds a gloss")
        second, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.calls) == 2, "the page was answered from a reading it no longer has"
    assert "a later hand adds a gloss" in client.calls[-1]
    assert second.outcomes[1].gate == "PASS", second.outcomes[1].gate_reasons


def test_an_answer_the_gate_refused_is_not_remembered_as_this_pages_answer(tmp_path):
    """A refusal is not a result. The answer may have come back truncated by a
    provider hiccup; remembering it under the page's key would make one bad minute
    permanent for that book, and the page would never be looked at again."""
    doc = _doc(F.prose_page, F.ambiguous_residue_page, F.prose_page)
    mangler = FakeClient(answer=_drop_a_word)
    honest = FakeClient()
    try:
        first, _ = _run(doc, mangler, tmp_path)
        second, _ = _run(doc, honest, tmp_path)
    finally:
        doc.close()

    assert first.outcomes[1].gate == "FAIL"
    assert len(honest.calls) == 1, "the refused page was never tried again"
    assert second.outcomes[1].source == "model"
    assert second.reused == 0


def test_an_answer_replayed_from_the_cache_is_judged_again_and_not_grandfathered(
        tmp_path, monkeypatch):
    """The cache holds the model's answer, never the verdict on it.

    Every release the gate learns to refuse something it used to let through, and
    a book is re-run whenever a conversion is resumed, re-tried or extended. If a
    cached page skipped the gate on the way back in, the very first version of the
    gate would decide what a book says forever, and the pages already bought --
    the majority of any resumed run -- would be the ones nobody ever looked at
    again. OBSERVED on the acceptance book: three pages replayed from the cache
    came back FAIL under a stricter gate and kept the reader's own text.
    """
    doc = _doc(F.prose_page, F.ambiguous_residue_page)
    client = FakeClient(answer=_drop_a_word)
    blind = gate.GateResult("PASS", 1, 1, 1.0, [], [], [], [], [])
    try:
        # The gate as it was before it learned to count the words: the answer is
        # adopted, and so it is remembered.
        monkeypatch.setattr(pipeline.gate, "check_word_preservation",
                            lambda *a, **k: blind)
        first, _ = _run(doc, client, tmp_path)
        assert first.outcomes[1].source == "model", "the bad answer was not cached"
        monkeypatch.undo()

        second, book = _run(doc, client, tmp_path)
        lost = assemble.page_source_text(second.book, 1).split()[-1]
    finally:
        doc.close()

    assert second.reused == 1, "the page was bought again instead of replayed"
    assert len(client.calls) == 1, "the cached answer was not the one that came back"
    assert second.outcomes[1].gate == "FAIL", second.outcomes[1].gate_reasons
    assert second.outcomes[1].source == "deterministic"
    assert lost in second.page_html[1], "the reader was left with the refused answer"
    assert book.totals()["gate"].get("FAIL") == 1, book.totals()


# -------------------------------------------------------------------- what stops

class _FailingClient(FakeClient):
    """A provider that refuses some pages. Content filters, truncation and 400s all
    arrive here as the same exception."""

    def __init__(self, fail_pages=(), **kwargs):
        FakeClient.__init__(self, **kwargs)
        self.fail_pages = set(fail_pages)

    def edit_page(self, page_text, **kwargs):
        if len(self.calls) in self.fail_pages:
            self.calls.append(page_text)
            self.hints.append([])
            raise model.ModelError("the provider refused this page")
        return FakeClient.edit_page(self, page_text, **kwargs)


def test_one_page_the_provider_refuses_does_not_end_the_conversion(tmp_path):
    """A 700-page book is 700 chances for a provider to return a 400. Letting the
    first one out of the loop throws away every page already paid for and leaves the
    user with a failed job and a bill."""
    doc = _doc(F.ambiguous_residue_page, F.ambiguous_residue_page,
               F.ambiguous_residue_page)
    client = _FailingClient(fail_pages=[0])
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.stopped is None
    assert len(client.calls) == 3
    assert result.outcomes[0].gate == "FAIL"
    assert "refused" in " ".join(result.outcomes[0].gate_reasons)
    assert result.outcomes[1].source == "model" and result.outcomes[2].source == "model"
    assert result.page_html[0], "the page still has its deterministic text"
    assert book.totals()["gate"]["FAIL"] == 1


def test_a_provider_that_refuses_everything_is_stopped_rather_than_walked_through(tmp_path):
    """The other side of the same rule. Something systemic — a revoked key, a model
    withdrawn, a region block — should stop after a few pages, not after 700."""
    doc = _doc(*([F.ambiguous_residue_page] * 6))
    client = _FailingClient(fail_pages=range(6))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.stopped == "model_errors"
    assert len(client.calls) < 6, client.calls


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


# ------------------------------------------------- the picture the model is given

def _ink_rows(jpeg):
    """Which rows of an image have ink on them, top to bottom."""
    pix = pymupdf.Pixmap(io.BytesIO(jpeg))
    rows = []
    for y in range(pix.height):
        start = y * pix.stride
        rows.append(min(pix.samples[start:start + pix.width * pix.n]) < 128)
    return rows


def _lines_of_ink(jpeg):
    """How many separate lines of type an image contains."""
    rows = _ink_rows(jpeg)
    return sum(1 for y, ink in enumerate(rows) if ink and not (y and rows[y - 1]))


def test_the_model_is_shown_the_page_its_words_came_from(tmp_path):
    """The running head is not in the picture, because it is not in the words.

    MEASURED against the acceptance book before this: every page of a 30-page range
    came back with the chapter title transcribed into the body -- five words the
    text layer never contained, so every page failed the word gate. The page image
    is the only place those words could have come from, and a prompt rule telling a
    vision model not to read what is in front of it did not hold.

    Counted in lines of type rather than in pixels: a crop that silently did not
    happen, or one measured against the wrong page, shows up as the extra line.
    """
    doc = _doc(F.ambiguous_glyph_marker_page)
    client = FakeClient()
    try:
        printed = _lines_of_ink(extract.render_page_jpeg(
            doc, 0, scale=pipeline.RASTER_SCALE, quality=pipeline.RASTER_QUALITY))
        _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert len(client.images) == 1 and client.images[0]
    assert _lines_of_ink(client.images[0]) == printed - 1


def _sectioned(page_builder):
    """A book whose section heads define a rung, and one page for the model.

    Three pages are the least that makes a rung a rung, so this is the smallest book
    in which the ladder means anything at all.
    """
    return _doc(lambda d: F.section_heading_page(d, "Sect", folio="400"),
                lambda d: F.section_heading_page(d, "Exaltations", folio="402"),
                lambda d: F.section_heading_page(d, "Triplicities", folio="404"),
                page_builder)


def test_the_ladder_is_the_books_and_the_level_of_a_heading_is_the_readers(tmp_path):
    """The ladder is the book's, not the sample's -- and it is no longer what decides
    a heading's level.

    MEASURED on book 567: over PDF pages 100-129 the deterministic pass emits only
    level 1, because the two levels this book sets are its chapter heads (15.7pt,
    none in that range) and its section heads (13.0pt) -- so a ladder built from what
    was emitted refuses the model's <h2> and throws away everything else it did with
    that page, including the footnote markers the page was routed for. What the book
    sets is the ladder; below the bottom rung there is one more level, because that
    is where a run-in head set on the body's own leading lands.

    Since the reader declares each heading's level with the heading itself, the rung
    below the last is now defence in depth rather than a route: a level that reaches
    the answer legitimately is a level the reader emitted, and one that does not is
    refused twice over. The ladder still bounds what the prompt offers, which is what
    this asserts, and ``test_a_level_below_the_ladders_last_rung_is_still_refused``
    asserts that the bound is enforced.
    """
    doc = _sectioned(F.heading_page_the_model_has_to_see)
    client = FakeClient()
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert client.ladders[-1] == [1, 2], client.ladders
    assert client.headings[-1] == [(1, "Serapio of Alexandria (First Century CE?)")]
    assert result.outcomes[3].gate == "PASS", result.outcomes[3].gate_reasons
    assert "<h1>Serapio of Alexandria (First Century CE?)</h1>" in result.page_html[3]


def test_a_level_below_the_ladders_last_rung_is_still_refused(tmp_path):
    """The control, and the reason the rule is 'one more level' and not 'any level'.
    A book with one heading rung has two levels open to it; a fourth is a guess that
    would land in the reader's table of contents."""
    doc = _sectioned(F.ambiguous_residue_page)
    client = FakeClient(answer=lambda text: "<h4>%s</h4>" % text)
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[3].gate == "FAIL"
    assert any("h4" in reason for reason in result.outcomes[3].gate_reasons), \
        result.outcomes[3].gate_reasons
    assert "<h4>" not in result.page_html[3]


class _AmbiguousBillingClient(FakeClient):
    """A provider whose answer was lost after the second dispatch: the request may
    have been billed, and the run must not treat that as free or keep spending."""

    def edit_page(self, page_text, ledger=None, **kwargs):
        if len(self.calls) == 1:
            self.calls.append(page_text)
            raise model.UncertainBilling(
                "the request was dispatched and its answer was lost",
                held_usd=self.spec.price_per_page * 3)
        return FakeClient.edit_page(self, page_text, ledger=ledger, **kwargs)


def test_an_answer_lost_after_dispatch_stops_the_run_holding_the_liability(tmp_path):
    """The ambiguous failure is not a refusal to note and walk past: the run stops
    at the page, the liability stays held, and the page's record never calls a
    possible charge $0.00."""
    doc = _doc(*([F.ambiguous_residue_page] * 3))
    client = _AmbiguousBillingClient()
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.stopped == "billing_uncertain"
    assert len(client.calls) == 2, "the run stops at the ambiguous page"
    outcome = result.outcomes[1]
    assert outcome.gate == "FAIL"
    assert any("billing" in reason or "unresolved" in reason
               for reason in outcome.gate_reasons), outcome.gate_reasons
    entry = [e for e in book.entries("page") if e.get("page") == 1][0]
    assert entry["gate"] == "FAIL"
    assert entry["cost_usd"] is None, "a possible charge is never written as $0.00"
    assert entry["billing"] == "unresolved"
    assert entry["held_usd"] > 0


class _UnusableAnswerClient(FakeClient):
    """A provider that is answering, and answering something this page cannot use.

    It reserves against the cap and it bills, because that is what actually arrives:
    prose where an HTML fragment was asked for, or an answer cut off at the token
    ceiling, is an HTTP 200 with a usage block on it. The provider read the page and
    wrote something; the tokens were charged whether or not we could use them."""

    def __init__(self, bad_pages=(), **kwargs):
        FakeClient.__init__(self, **kwargs)
        self.bad_pages = set(bad_pages)

    def edit_page(self, page_text, ledger=None, **kwargs):
        if len(self.calls) in self.bad_pages:
            if ledger is not None:
                ledger.reserve(self.spec.price_per_page)
            self.calls.append(page_text)
            self.hints.append([])
            raise model.UnusableAnswer(
                "the model answered prose instead of an HTML fragment: the page "
                "image is blank and the text layer you supplied is empty",
                cost_usd=self.spec.price_per_page, cost_source="provider",
                prompt_tokens=900, completion_tokens=12)
        return FakeClient.edit_page(self, page_text, ledger=ledger, **kwargs)


def test_a_run_of_pages_the_model_will_not_transcribe_does_not_end_the_conversion(tmp_path):
    """A scanned book has blank leaves, and a blank leaf carries a raster with no ink
    on it, so it is routed to the model as a page with no text layer. The model reads
    it, says in prose that there is nothing there, and is right.

    MEASURED on the acceptance book: three of those in a row tripped the guard meant
    for a revoked key and ended a 698-page conversion twenty pages early, while the
    job still reported itself done. A provider that is answering has not gone away."""
    doc = _doc(*([F.ambiguous_residue_page] * 6))
    client = _UnusableAnswerClient(bad_pages=range(4))
    try:
        result, _ = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.stopped is None, result.stopped
    assert len(client.calls) == 6, client.calls
    assert [result.outcomes[p].gate for p in range(4)] == ["FAIL"] * 4
    assert result.outcomes[4].source == "model"
    assert result.outcomes[5].source == "model"


def test_an_answer_the_page_cannot_use_still_costs_what_the_provider_charged(tmp_path):
    """A 200 with prose in it is a bill. The provider read the page, wrote an answer
    and charged for the tokens; that we cannot use the answer is our problem and not
    a refund.

    Recording it at $0.00 is not a bookkeeping nicety. ``Ledger.spent()`` is the only
    number the cap reserves against, so a page that billed and recorded nothing is
    money the reader's ceiling cannot see -- and a book whose every page answers this
    way would bill the whole way through a cap that never moves."""
    doc = _doc(*([F.ambiguous_residue_page] * 4))
    client = _UnusableAnswerClient(bad_pages=[0, 1], price=0.002)
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert [result.outcomes[p].gate for p in (0, 1)] == ["FAIL", "FAIL"]
    assert result.outcomes[0].cost_usd == pytest.approx(0.002)
    # Two pages billed for nothing usable and two pages billed for an answer.
    assert book.spent() == pytest.approx(0.008)
    assert result.spend_usd == pytest.approx(0.008)
    # The tokens of the answers nobody could use are in the job's totals too: two
    # adopted pages at 600 completion tokens and two thrown away at 12.
    assert book.totals()["completion_tokens"] == 600 * 2 + 12 * 2


def test_a_page_the_provider_would_not_answer_at_all_costs_nothing(tmp_path):
    """The other half of the rule, and the one that stops the fix from becoming
    'charge for everything': a provider that refused outright produced no tokens, so
    charging the reader's cap for it would invent a bill."""
    doc = _doc(*([F.ambiguous_residue_page] * 3))
    client = _FailingClient(fail_pages=[0], price=0.002)
    try:
        result, book = _run(doc, client, tmp_path)
    finally:
        doc.close()

    assert result.outcomes[0].gate == "FAIL"
    assert result.outcomes[0].cost_usd == 0.0
    assert book.spent() == pytest.approx(0.004), "only the two answered pages"


def test_a_run_of_answers_the_provider_billed_for_still_stops_at_the_cap(tmp_path):
    """G5 on the path that produces no page.

    MEASURED on the acceptance book: 42 pages over four conversions answered in prose
    or ran into the token ceiling, and every one of them was written to the ledger at
    $0.00. Each was a real charge on the account. The guard that ends a run of
    outright refusals (``model_errors``) does not fire here by design -- a provider
    that is answering has not gone away -- so the cap is the only thing left holding
    the spend down, and it can only hold what it can see."""
    doc = _doc(*([F.ambiguous_residue_page] * 6))
    client = _UnusableAnswerClient(bad_pages=range(6), price=0.002)
    try:
        result, book = _run(doc, client, tmp_path, cap=0.005)
    finally:
        doc.close()

    assert result.stopped == "cost_cap", result.stopped
    assert len(client.calls) == 2, client.calls
    assert book.spent() <= 0.005

@pytest.mark.parametrize('kind', ['caption', 'note'])
@pytest.mark.parametrize('cached', [False, True])
def test_native_source_evidence_cannot_be_erased_by_model_or_cache(tmp_path, kind, cached):
    """A native scan layer can be damaged without having gone through OCR.
    Preserve its source-qualified presentation on both adoption paths."""
    from cps.services.reflow import build_epub
    if kind == 'caption':
        elements = [assemble.Element(kind='fig', pno=0),
                    assemble.Element(kind='caption', pno=0,
                        runs=[['t', 'Figure 9. The ancient and modern worlds.']])]
        notes = []
        protected = elements[1]
        field = 'caption_uncertain'
    else:
        elements = [assemble.Element(kind='p',pno=0,
            runs=[['t','Reading the older source says '], ['sup','1',0],
                  ['t',' in this account.']])]
        notes = [assemble.Note(num=1,pno=0,marked=True,
                               text='The damaged reference cannot establish this identity.')]
        protected = notes[0]
        field = 'uncertain'
    book = assemble.Book(elements=elements,pages={0:elements},notes=notes)
    clean = build_epub.page_fragment(book,0)
    result = pipeline.ReflowResult(book=book,fingerprint='a'*64,page_html={0:clean})
    cache = pipeline.PageCache(str(tmp_path/'cache'))
    client = FakeClient(answer=lambda text: clean)
    doc = _doc(F.prose_page)
    try:
        if cached:
            primed = pipeline._edit_one_page(doc,book,0,client,None,cache,result,(),True)
            assert primed.gate == 'PASS', primed.gate_reasons
            client.calls.clear()
        setattr(protected,field,True)
        baseline = build_epub.page_fragment(book,0)
        assert 'reflow-uncertain' in baseline
        result.page_html[0] = baseline
        outcome = pipeline._edit_one_page(doc,book,0,client,None,cache,result,(),True)
    finally:
        doc.close()
    assert result.recovery is None, 'this is native evidence, not OCR confidence'
    assert bool(client.calls) is not cached
    assert outcome.cached is cached
    assert outcome.gate == 'FAIL', 'an unqualified answer must not replace source evidence'
    assert outcome.source == 'deterministic'
    assert result.page_html[0] == baseline
    assert any('source-backed' in reason for reason in outcome.gate_reasons)
