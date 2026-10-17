# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The background job, at the two edges where it touches somebody's library.

Everything the pipeline does is tested against the pipeline. What is left here is
what a user would notice going wrong: an EPUB they already had, overwritten; a
sample they asked to *look* at, filed as the book; a cancelled job that put a
half-reviewed conversion in the library anyway; and a job that left no record of
what it spent.
"""

import os
import time
from types import SimpleNamespace

import pytest

from cps.services.reflow import ledger as ledger_mod, model as model_mod
from cps.services.worker import STAT_ENDED, STAT_FAIL, STAT_FINISH_SUCCESS
from tests.fixtures import reflow_pdfs as F
from tests.fixtures.forking import run_forked

pytestmark = pytest.mark.unit


# ── a library that is only as real as it needs to be ─────────────────────────

class _Session(object):
    def __init__(self):
        self.merged = []
        self.commits = 0
        self.existing = None

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def one_or_none(self):
        return self.existing

    def merge(self, row):
        self.merged.append(row)
        return row

    def commit(self):
        self.commits += 1
        if self.merged:self.existing=self.merged[-1]

    def rollback(self):                                           # pragma: no cover
        pass

    def close(self):
        pass


class _LocalDb(object):
    def __init__(self, book, formats):
        self.book = book
        self.formats = formats
        self.session = _Session()

    def get_book(self, _book_id):
        return self.book

    def get_book_format(self, _book_id, fmt):
        return self.formats.get(fmt)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A two-page PDF where the library says it is, and a task module pointed at it."""
    from cps.tasks import reflow as mod

    library = tmp_path / "library"
    folder = library / "Author/Book (5)"
    folder.mkdir(parents=True)
    doc = F.new_doc()
    page=F.chapter_opening_page(doc, "Workshop of Birchwood")
    page.insert_text((84,400),'"A separately displayed source quotation remains complete."',
                     fontsize=12,fontname="tiro")
    F.prose_page(doc)
    F.prose_page(doc)
    doc.save(str(folder / "Book - Author.pdf"))
    doc.close()

    monkeypatch.setattr(mod, "REFLOW_DIR", str(tmp_path / "reflow"))
    book = SimpleNamespace(id=5, title="A Book", path="Author/Book (5)", authors=[],
                           languages=[], publishers=[], comments=[], tags=[])
    formats = {"PDF": SimpleNamespace(name="Book - Author", format="PDF")}
    local_db = _LocalDb(book, formats)
    monkeypatch.setattr(mod.db, "CalibreDB", lambda **kw: local_db)
    monkeypatch.setattr(mod, "config", SimpleNamespace(
        get_book_path=lambda: str(library),
        resolved_openrouter_key=lambda: "",
        config_reflow_default_tier="standard",
        config_reflow_hard_cap_usd=5.0,
        config_reflow_instance_budget_usd=5.0))
    monkeypatch.setattr(mod.helper, "mark_book_format_materialised",
                        lambda *a, **kw: None)
    return SimpleNamespace(mod=mod, book=book, formats=formats, local_db=local_db,
                           folder=folder, root=str(tmp_path / "reflow"))



class _PixelSession:
    """Synthetic metered responses at the parent HTTP boundary."""
    cost=.000123456789
    def __init__(self,error=None,malformed=False):
        self.calls=[];self.error=error;self.malformed=malformed
    def post(self,*args,**kwargs):
        import json,requests
        wire=kwargs['json'];self.calls.append(wire)
        if self.error:raise self.error
        text='The clear sample remains readable. Another simple line follows the first.'
        block=dict(id='body',role='body',bbox=[70,80,960,230],marker='',
                   continues_previous=False,continues_next=False,
                   tokens=[dict(text=v,low_confidence=False) for v in text.split()])
        content='invalid synthetic response' if self.malformed else json.dumps({'blocks':[block]})
        response=requests.Response();response.status_code=200
        response._content=json.dumps(dict(id='synthetic-generation-'+str(len(self.calls)),
            model=wire['model'],provider='OpenAI',usage=dict(cost=self.cost,prompt_tokens=100,completion_tokens=100),
            choices=[dict(finish_reason='stop',message=dict(content=content))])).encode()
        return response


def _run_pixel_response(rig,monkeypatch,session,**options):
    from cps.services.reflow import vision_quote
    from tests.unit.test_reflow_app_vision_route import _source
    path=rig.folder/'Book - Author.pdf'
    _source(path,scanned=True,pages=2)
    with F.pymupdf.open(path) as document:quote=vision_quote.measure(document)
    monkeypatch.setattr(rig.mod.config,'resolved_openrouter_key',lambda:'inert-configured-key')
    monkeypatch.setattr(model_mod.requests,'post',session.post)
    values=dict(mode='full',review_mode='source_verified',cost_cap_usd=1)
    values.update(options)
    task=rig.mod.TaskReflowPdf(5,7,values)
    task.options.consent_quote=quote
    task.options.source_sha256=quote['source_sha256']
    task.run(None)
    return task


def _run(rig, **options):
    options.setdefault("review_mode","source_verified")
    task = rig.mod.TaskReflowPdf(5, 7, options)
    task.stat = STAT_ENDED if options.pop("_cancelled", False) else task.stat
    task.run(None)
    return task


def _ledger_rows(rig, book_id=5):
    return ledger_mod.read_summaries(os.path.join(rig.root, "jobs", str(book_id)))


# ── the library ──────────────────────────────────────────────────────────────

def test_an_epub_the_user_already_has_is_not_overwritten_without_being_asked(rig):
    rig.formats["EPUB"] = SimpleNamespace(name="Book - Author", format="EPUB")
    task = _run(rig, mode="full", cost_cap_usd=1.0)

    assert task.stat == STAT_FAIL
    assert "replace" in (task.error or "").lower()
    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / "Book - Author.epub"))


def test_a_replacement_the_user_asked_for_is_written(rig):
    rig.formats["EPUB"] = SimpleNamespace(name="Book - Author", format="EPUB")
    task = _run(rig, mode="full", cost_cap_usd=1.0, replace_existing_epub=True)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert os.path.isfile(str(rig.folder / "Book - Author.epub"))
    assert rig.local_db.session.commits == 1


def test_a_book_whose_pages_do_not_open_is_not_filed_in_the_library(rig, monkeypatch):
    """A book whose pages do not open is not filed in the library."""
    # Inject at the actual process boundary: parent monkeypatches deliberately
    # cannot change the fresh child's module globals.
    from cps.services.reflow.native_ipc import NativeDocument
    import zipfile
    build = NativeDocument.convert_vision
    def malformed(self, path, *args, **kwargs):
        result = build(self, path, *args, **kwargs)
        with zipfile.ZipFile(path) as archive:
            entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
        with zipfile.ZipFile(path, 'w') as archive:
            for item, data in entries:
                if item.filename.endswith('ch001.xhtml'):
                    data = data.replace(b'</body>', b'<p>Catalogue 9 & 29</p></body>')
                archive.writestr(item, data)
        return result
    monkeypatch.setattr(NativeDocument, 'convert_vision', malformed)

    task = _run(rig, mode="full", cost_cap_usd=1.0)

    assert task.stat == STAT_FAIL
    assert "parse" in (task.error or ""), task.error
    # Nothing was filed, and nothing was left behind for an importer to find.
    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / "Book - Author.epub"))
    assert [row["status"] for row in _ledger_rows(rig)] == ["failed"]


def test_a_sample_is_a_look_and_not_a_filing(rig):
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=1.0)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert os.path.isfile(rig.mod.sample_path(7, task.job_id))
    # Nothing was added to the book, and nothing landed next to the PDF.
    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / "Book - Author.epub"))


def test_a_completed_job_keeps_a_text_free_recovery_census_for_crash_diagnosis(rig):
    """The compact job row cannot explain which source pages used OCR after the
    Python service disappears.  The durable census keeps page/layer facts and a
    digest, never OCR readings or cache locations.  Breaks if conversion only
    writes the old aggregate recovery summary again.
    """
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=1.0)

    audit = ledger_mod.Ledger(os.path.join(rig.root, "jobs", "5", task.job_id + ".jsonl"),
                              cap_usd=1.0)
    records = audit.entries("recovery_provenance")

    assert len(records) == 1
    record = records[0]
    assert record["page_count"] == 3
    assert record["options"] == {"mode": "auto", "language": "eng"}
    assert len(record["digest"]) == 64
    assert [row["page"] for row in record["pages"]] == [0, 1, 2]
    assert all("cache" not in row and "text" not in row for row in record["pages"])


def test_optional_trace_write_failure_keeps_actual_builder_validation_and_sample_output(rig, monkeypatch):
    """A failed fsync for optional crash markers cannot fail a healthy EPUB.

    This injects one failure only for the real task's build-trace append after
    assembly starts.  Required job/audit/artifact writes still use the real
    ledger; changing the task to catch all ledger errors makes the companion
    critical-ledger test meaningless.
    """
    real_record = ledger_mod.Ledger.record
    trace_attempts = []

    def fail_one_trace(self, entry, durable=False):
        if entry.get("kind") == "build_trace":
            trace_attempts.append(dict(entry))
            raise OSError("injected optional trace fsync failure")
        return real_record(self, entry, durable=durable)

    monkeypatch.setattr(ledger_mod.Ledger, "record", fail_one_trace)
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=1.0)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert len(trace_attempts) == 1
    assert rig.mod.build_epub.validate(task.results["path"]) == []
    audit = ledger_mod.Ledger(os.path.join(rig.root, "jobs", "5", task.job_id + ".jsonl"),
                              cap_usd=1.0)
    degraded = audit.entries("runtime_diagnostics")
    assert len(degraded) == 1
    assert {key: value for key, value in degraded[0].items() if key != "ts"} == {
        "kind": "runtime_diagnostics", "event": "build_trace_degraded",
        "reason": "durable_trace_write_failed", "job": task.job_id}


def test_required_audit_ledger_failure_still_fails_closed(rig, monkeypatch):
    """Only build-trace append errors are optional; validated audit evidence is not."""
    real_record = ledger_mod.Ledger.record

    def fail_required_audit(self, entry, durable=False):
        if entry.get("kind") == "vision_artifact":
            raise OSError("injected required audit failure")
        return real_record(self, entry, durable=durable)

    monkeypatch.setattr(ledger_mod.Ledger, "record", fail_required_audit)
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=1.0)

    assert task.stat == STAT_FAIL
    assert not os.path.exists(rig.mod.sample_path(7, task.job_id))


def test_a_cancelled_job_does_not_file_a_half_reviewed_conversion(rig):
    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "full", "cost_cap_usd": 1.0})
    task.stat = STAT_ENDED          # what WorkerThread.end_task does
    task.run(None)

    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / "Book - Author.epub"))
    assert _ledger_rows(rig)[0]["status"] == "cancelled"


def test_the_epubs_own_sidecar_keeps_the_cost_private_unless_the_user_shows_it(rig):
    """Sharing the EPUB cannot reveal the bill unless its owner opts in."""
    from cps.services.reflow import build_epub
    hidden_task=_run(rig,mode='sample',sample_pages=2,cost_cap_usd=.75)
    public_task=_run(rig,mode='sample',sample_pages=2,cost_cap_usd=.75,show_cost_in_report=True)
    for task in (hidden_task,public_task):assert task.stat==STAT_FINISH_SUCCESS,task.error
    hidden=build_epub.read_sidecar(hidden_task.results['path'])
    public=build_epub.read_sidecar(public_task.results['path'])
    assert hidden['app_report']['cost']=={'shown':False}
    forbidden={'spend_usd','pending_usd','cap_usd','cost_usd','new_cost_usd','bound_usd',
               'prompt_tokens','completion_tokens','input_tokens','output_tokens','unresolved_attempts'}
    def check(value):
        if isinstance(value,dict):
            assert not forbidden.intersection(value),forbidden.intersection(value)
            for child in value.values():check(child)
        elif isinstance(value,list):
            for child in value:check(child)
    check(hidden)
    assert _ledger_rows(rig)[0]['cap_usd']==pytest.approx(.75)
    assert hidden_task.results['report']['cost']['cap_usd']==pytest.approx(.75)
    assert public['app_report']['cost']['cap_usd']==pytest.approx(.75)
    assert public['app_report']['cost']['spend_usd']==public_task.results['report']['cost']['spend_usd']
    assert public['app_report']['cost']['pending_usd']==public_task.results['report']['cost']['pending_usd']


# ── the record ───────────────────────────────────────────────────────────────

def test_the_job_says_what_it_was_and_how_it_ended(rig):
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=0.75)

    row = _ledger_rows(rig)[0]
    assert row["job_id"] == task.job_id
    assert row["mode"] == "sample"
    assert row["user_id"] == 7
    assert row["status"] == "done"  # Native text and disclosed source pages complete without a key.
    assert row["spend_usd"] == row["pending_usd"] == 0
    assert row["cap_usd"] == pytest.approx(0.75)
    assert row["started"] and row["finished"]


def test_rejected_pixel_answers_publish_only_disclosed_originals_and_keep_the_bill(rig,monkeypatch):
    session=_PixelSession(malformed=True)
    task=_run_pixel_response(rig,monkeypatch,session)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert session.calls
    assert (rig.folder/'Book - Author.epub').is_file()
    row=_ledger_rows(rig)[0]
    payload=task.results['report']
    assert payload['fidelity']['pages_model']==0
    assert payload['fidelity']['pages_disclosed']==2
    assert payload['cost']['spend_usd']==pytest.approx(len(session.calls)*session.cost)
    assert row['spend_usd']==payload['cost']['spend_usd'] and row['pending_usd']==0
    sidecar=rig.mod.build_epub.read_sidecar(task.results['path'])
    assert all(page['source']=='original_source' for page in sidecar['pages'].values())
    audit=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),1)
    assert len([r for r in audit.entries('reservation') if r['event']=='reconciled'])==len(session.calls)


def test_a_finished_conversion_releases_the_books_reservation(rig):
    """The book's admission reservation lives from enqueue to terminal state."""
    from cps.services.reflow import admission

    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "sample", "sample_pages": 2,
                                        "cost_cap_usd": 1.0})
    worker = object()
    assert admission.reserve(5, worker, task)
    assert not admission.reserve(5, worker, rig.mod.TaskReflowPdf(5, 7, {})), \
        "the book is held while the task runs"

    task.run(None)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert admission.reserve(5, worker, rig.mod.TaskReflowPdf(5, 7, {})), \
        "a finished conversion must give the book back"


def test_a_failed_conversion_releases_the_books_reservation_too(rig):
    """Failure is a terminal state: the book is not locked by a dead job."""
    from cps.services.reflow import admission

    rig.formats["EPUB"] = SimpleNamespace(name="Book - Author", format="EPUB")
    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "full", "cost_cap_usd": 1.0})
    worker = object()
    assert admission.reserve(5, worker, task)

    task.run(None)

    assert task.stat == STAT_FAIL
    assert admission.reserve(5, worker, rig.mod.TaskReflowPdf(5, 7, {}))


def test_a_task_cancelled_before_it_ever_ran_does_not_hold_the_book(rig):
    """A WAITING task cancelled from the queue never reaches run(), so the
    reservation cannot rely on it: the next admission evicts the stale entry by
    the task's terminal state."""
    from cps.services.reflow import admission
    from cps.services.worker import STAT_CANCELLED

    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "full", "cost_cap_usd": 1.0})
    worker = object()
    assert admission.reserve(5, worker, task)
    task.stat = STAT_CANCELLED          # what WorkerThread.end_task does

    assert admission.reserve(5, worker, rig.mod.TaskReflowPdf(5, 7, {}))


def test_only_the_reservations_own_task_can_release_it(rig):
    from cps.services.reflow import admission

    first = rig.mod.TaskReflowPdf(5, 7, {})
    second = rig.mod.TaskReflowPdf(5, 7, {})
    worker = object()
    assert admission.reserve(5, worker, first)

    admission.release(5, second)        # not its reservation to give back
    assert not admission.reserve(5, worker, second)
    admission.release(5, first)
    assert admission.reserve(5, worker, second)


def test_a_successful_bill_is_debited_exactly_once_across_every_boundary(rig,monkeypatch):
    session=_PixelSession()
    task=_run_pixel_response(rig,monkeypatch,session)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    audit=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),1)
    reconciled=[e for e in audit.entries('reservation') if e['event']=='reconciled']
    assert len(session.calls)==1,'Identical page pixels must reuse the first paid response.'
    assert len(reconciled)==len(session.calls)
    assert all(e['cost_usd']==session.cost for e in reconciled)
    row=_ledger_rows(rig)[0]
    assert row['spend_usd']==pytest.approx(sum(e['cost_usd'] for e in reconciled))
    assert row['pending_usd']==0
    assert task.results['report']['cost']['spend_usd']==row['spend_usd']
    assert task.results['report']['fidelity']['pages_model']==2
    assert row['status']=='done'


def test_paid_task_uses_live_shared_admission_and_the_hard_job_cap(rig, monkeypatch):
    from cps.services.reflow import shared_budget

    store = rig.mod.instance_budget_store()
    store.reserve('earlier', 4.25, job='earlier', book=9, user=7, context={})
    store.finish('earlier', 4.25)
    original_convert = rig.mod.TaskReflowPdf._convert
    checked = []

    def check_admission(self, document, client, ledger, cache):
        assert ledger.cap_usd == 5
        with pytest.raises(ledger_mod.CapExceeded):
            ledger.reserve_attempt('too-large', 5.01)
        with pytest.raises(shared_budget.BudgetError):
            ledger.reserve_attempt('over-shared', 1)
        attempt = ledger.reserve_attempt('pending', .7)
        with pytest.raises(shared_budget.BudgetError):
            ledger.reserve_attempt('over-remaining', .1)
        ledger.release_attempt(attempt, 'pre_dispatch')
        checked.append(True)
        return original_convert(self, document, client, ledger, cache)

    monkeypatch.setattr(rig.mod.TaskReflowPdf, '_convert', check_admission)
    session = _PixelSession()
    task = _run_pixel_response(rig, monkeypatch, session, cost_cap_usd=500)

    assert checked == [True]
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert session.calls
    assert store.status()['remaining_usd'] == pytest.approx(.75 - session.cost)


def test_a_job_with_unresolved_billing_stops_safely_and_holds_the_amount(rig,monkeypatch):
    import requests
    session=_PixelSession(error=requests.ReadTimeout('lost answer'))
    task=_run_pixel_response(rig,monkeypatch,session)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert 'unresolved' in task.message
    row=_ledger_rows(rig)[0]
    assert row['status']=='billing_unknown'
    assert row['pending_usd']>0 and row['spend_usd']==0
    assert len(session.calls)==1,'Unknown billing must not retry or buy another page.'
    audit=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),1)
    pending=[r for r in audit.entries('reservation') if r['event']=='pending']
    assert len(pending)==1 and row['pending_usd']==pending[0]['bound_usd']
    assert task.results['report']['cost']['pending_usd']==row['pending_usd']
    assert task.results['report']['fidelity']['pages_disclosed']==2


def test_a_job_that_failed_says_so_rather_than_disappearing(rig):
    from cps.services.reflow import report as report_mod

    def _disagree(_payload, _ledger):
        raise ValueError("pages_sent 3 in the report, 0 in the ledger")

    original = report_mod.check_vision_completion
    report_mod.check_vision_completion = _disagree
    try:
        task = _run(rig, mode="full", cost_cap_usd=1.0)
    finally:
        report_mod.check_vision_completion = original

    assert task.stat == STAT_FAIL
    assert rig.local_db.session.commits == 0
    row = _ledger_rows(rig)[0]
    assert row["status"] == "failed"
    assert "ledger" in (row.get("error") or "")


# ── what the user consented to ───────────────────────────────────────────────

def test_a_cap_cannot_be_raised_past_the_administrators_ceiling(rig, monkeypatch):
    monkeypatch.setattr(rig.mod.config, "config_reflow_hard_cap_usd", 2.0)
    assert rig.mod.ReflowOptions({"review_mode":"source_verified","cost_cap_usd": 99.0}).cost_cap_usd == 2.0
    assert rig.mod.ReflowOptions({"review_mode":"source_verified","cost_cap_usd": 0.25}).cost_cap_usd == 0.25
    # No cap named at all is the ceiling, never "unlimited".
    assert rig.mod.ReflowOptions({"review_mode":"source_verified"}).cost_cap_usd == 2.0


def test_a_tier_nobody_offers_falls_back_to_the_configured_default(rig, monkeypatch):
    monkeypatch.setattr(rig.mod.config, "config_reflow_default_tier", "cheap")
    assert rig.mod.ReflowOptions({"model_tier": "free-forever"}).model_tier == "cheap"
    assert rig.mod.ReflowOptions({"model_tier": "quality"}).model_tier == "quality"


def test_a_sample_is_capped_however_many_pages_are_asked_for(rig):
    assert rig.mod.ReflowOptions({"sample_pages": 9000}).sample_pages \
        == rig.mod.SAMPLE_PAGES_MAX
    assert rig.mod.ReflowOptions({"sample_pages": 0}).sample_pages \
        == rig.mod.SAMPLE_PAGES_DEFAULT


def _long_book(rig, pages, front_matter=1):
    """The rig's PDF replaced by title pages and then pages of prose."""
    doc = F.new_doc()
    for _ in range(front_matter):
        F.title_page(doc)
    for _ in range(pages - front_matter):
        F.prose_page(doc)
    doc.save(str(rig.folder / "Book - Author.pdf"))
    doc.close()


def test_a_sample_starts_at_the_book_and_not_at_its_front_matter(rig):
    """The sample the user downloads is body pages, not a title page. Run through
    the conversion itself: the helper this used to call was never on the path."""
    _long_book(rig, 6)
    task = _run(rig, mode="sample", sample_pages=2, review_mode="deterministic")
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    report = task.results["report"]
    assert report["fidelity"]["pages"] == 2
    assert report["vision"]["pages"] == [1, 2]


def test_a_sample_of_a_long_book_reads_only_the_pages_it_needs(rig, monkeypatch):
    """N2(a) of the 7daffa5 retest: a 20-page sample of a 3,000-page scan read and
    recognised all 3,000 pages before it chose 20. A sample now reads the front
    of the book only -- the pages the body is looked for in, the sample, and one
    page past it -- whatever the book's length, and says so. Breaks if a sample
    reads the whole book again."""
    from cps.services.reflow import extract
    _long_book(rig, 40)
    read = []
    real = extract.read_page
    def counting(doc, pno):
        read.append(pno)
        return real(doc, pno)
    monkeypatch.setattr(extract, "read_page", counting)
    task = _run(rig, mode="sample", sample_pages=3, review_mode="deterministic")
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert set(read) <= set(range(15)), sorted(set(read))
    report = task.results["report"]
    assert report["vision"]["pages"] == [1, 2, 3]
    assert report["source"]["pages"] == 40
    assert report["vision"]["source_context_pages"] == 15
    assert report["vision"]["source_pages"] == 40
    assert report["structure"]["scope"] == "sample_context"


def test_a_sample_is_as_long_as_asked_when_the_front_matter_outruns_the_search(rig):
    """A bounded search can miss a body that starts late. The sample is then the
    pages it asked for from the start of the book -- never fewer pages because
    the body was spotted in the context read past the search."""
    _long_book(rig, 30, front_matter=13)
    task = _run(rig, mode="sample", sample_pages=3, review_mode="deterministic")
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    report = task.results["report"]
    assert report["vision"]["pages"] == [0, 1, 2]


@pytest.mark.parametrize("limit", ["pages", "size"])
def test_a_queued_conversion_over_the_limit_fails_before_reading_a_page(rig, monkeypatch, limit):
    """The API refuses a PDF over the administrator's limits before queueing it;
    a job queued before the limits were lowered, or a PDF replaced after, is
    refused by the job itself before it reads a page (Finding 3)."""
    from cps.services.reflow import extract
    read = []
    monkeypatch.setattr(extract, "read_page", lambda doc, pno: read.append(pno))
    if limit == "pages":
        monkeypatch.setattr(rig.mod.config, "config_reflow_max_pages", 2, raising=False)
    else:
        monkeypatch.setattr(rig.mod.config, "config_reflow_max_pdf_mb", 1, raising=False)
        with open(rig.folder / "Book - Author.pdf", "ab") as handle:
            handle.write(b"\0" * (1024 * 1024))
    task = _run(rig, mode="full", review_mode="deterministic")
    assert task.stat == STAT_FAIL
    assert "limit" in (task.error or "").lower(), task.error
    assert read == [] and rig.local_db.session.commits == 0


# ── housekeeping ─────────────────────────────────────────────────────────────

def test_the_daily_maintenance_removes_a_sample_nobody_downloaded(rig, monkeypatch):
    """A sample is a preview, not a library: a week after it was made it goes.
    That rule existed (``cleanup_samples``) and nothing ever called it, so every
    sample anyone made stayed on the config volume for good. The daily scheduled
    maintenance now runs Reflow's housekeeping, which applies it -- and leaves
    alone whatever belongs to a job the worker still holds."""
    from cps import schedule
    monkeypatch.setattr(schedule, "config", SimpleNamespace(
        schedule_metadata_backup=False, schedule_generate_book_covers=False,
        schedule_generate_series_covers=False))
    fresh, expired, held = (rig.mod.sample_path(7, job) for job in (
        "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"))
    old = time.time() - 8 * 86400
    for path in (fresh, expired, held):
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04")
        if path != fresh:
            os.utime(path, (old, old))
    queued = rig.mod.TaskReflowPdf(5, 7, {"mode": "sample"})
    queued.job_id = "cccccccccccccccc"

    (make, hidden), = [(task, hidden) for task, name, hidden
                       in schedule.get_scheduled_tasks(reconnect=False)
                       if name == "reflow housekeeping"]
    make().run(SimpleNamespace(tasks=[(1, "admin", None, queued, False)]))

    assert hidden, "housekeeping is not a task anyone asked for"
    assert os.path.isfile(fresh)
    assert not os.path.exists(expired)
    assert os.path.isfile(held), "the sample of a job the worker holds was removed"


# ── a restart mid-conversion ─────────────────────────────────────────────────

def _orphan_ledger(rig, job_id="df83053b791743a1", book_id=5):
    """The record a job leaves when its process dies: a start, some spend, and
    no finish -- a synthetic interrupted-run record (a sample stopped
    at "page 4 of 20", the container restarted under it)."""
    directory = os.path.join(rig.root, "jobs", str(book_id))
    os.makedirs(directory, exist_ok=True)
    led = ledger_mod.Ledger(os.path.join(directory, "%s.jsonl" % job_id),
                            cap_usd=5.0, job_id=job_id)
    led.record({"kind": "job", "event": "start", "mode": "sample", "user_id": 7,
                "tier": "cheap", "cap_usd": 5.0, "pages": 20})
    led.record({"kind": "page", "page": 3, "cost_usd": 0.0009, "gate": "PASS",
                "model": "test-provider/synthetic-response"})
    return led


def test_a_job_the_process_died_during_is_terminalized_as_interrupted(rig):
    """A start with no finish means the process is gone (the worker's queue is
    in-memory, so a restarted app has no task for the job). Startup settles the
    record as interrupted -- with when -- instead of advertising it as running
    forever. What it had confirmed spending is the job's own history and stays."""
    _orphan_ledger(rig)

    recovered = rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))

    assert recovered == ["df83053b791743a1"]
    row = _ledger_rows(rig)[0]
    assert row["status"] == "interrupted"
    assert row["finished"] is not None
    assert row.get("error")
    assert row["spend_usd"] == pytest.approx(0.0009)


def test_recovery_never_settles_or_erases_a_charge_the_provider_may_still_bill(rig):
    """A request dispatched before the death may still be billed: its bound stays
    held across recovery. No release, no reconcile, no zeroing -- the reservation
    history is byte-for-byte what the dead process left."""
    led = _orphan_ledger(rig)
    led.reserve_attempt("4", 0.0217, model_id="test-provider/synthetic-response",
                        prompt_version="reflow-structure-6")

    rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))

    row = _ledger_rows(rig)[0]
    assert row["status"] == "interrupted"
    assert row["pending_usd"] == pytest.approx(0.0217)
    assert row["spend_usd"] == pytest.approx(0.0009)
    reread = ledger_mod.Ledger(led.path, cap_usd=5.0, job_id=led.job_id)
    assert [e.get("event") for e in reread.entries("reservation")] == ["pending"]


def test_a_job_that_already_ended_is_not_rewritten(rig):
    """Recovery is for the orphaned record only. A job that reached its own
    ending -- done, capped, failed, cancelled -- is left exactly as it filed
    itself, and a repeated sweep is a no-op."""
    led = _orphan_ledger(rig)
    led.record({"kind": "job", "event": "finish", "status": "done"})
    with open(led.path, "rb") as handle:
        before = handle.read()

    worker = SimpleNamespace(tasks=[])
    assert rig.mod.recover_interrupted_jobs(worker=worker) == []
    assert rig.mod.recover_interrupted_jobs(worker=worker) == []
    with open(led.path, "rb") as handle:
        assert handle.read() == before
    assert _ledger_rows(rig)[0]["status"] == "done"


def test_recovery_is_idempotent_for_the_orphan_too(rig):
    led = _orphan_ledger(rig)
    worker = SimpleNamespace(tasks=[])

    assert rig.mod.recover_interrupted_jobs(worker=worker) == ["df83053b791743a1"]
    assert rig.mod.recover_interrupted_jobs(worker=worker) == []

    reread = ledger_mod.Ledger(led.path, cap_usd=5.0, job_id=led.job_id)
    finishes = [e for e in reread.entries("job") if e.get("event") == "finish"]
    assert len(finishes) == 1


def test_a_job_still_running_in_this_process_is_never_interrupted(rig):
    """The ownership guard: a start without a finish is only an orphan when no
    live task owns it. A sweep that runs while a conversion is genuinely under
    way -- the wrong ordering this whole boundary exists to prevent -- must not
    touch the record."""
    from cps.services.worker import STAT_STARTED

    _orphan_ledger(rig)
    live = SimpleNamespace(is_reflow=True, job_id="df83053b791743a1",
                           stat=STAT_STARTED)
    worker = SimpleNamespace(tasks=[(0, "ed", 0, live, False)])

    assert rig.mod.recover_interrupted_jobs(worker=worker) == []
    assert _ledger_rows(rig)[0]["status"] == "running"


def test_a_book_that_never_ran_has_nothing_to_recover(rig):
    assert rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[])) == []


def test_cancel_during_original_evidence_stops_rendering_without_filing(rig,monkeypatch):
    from tests.unit.test_reflow_app_vision_route import _source
    _source(rig.folder/'Book - Author.pdf',scanned=True,pages=2)
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','review_mode':'deterministic'})
    observed=[]
    original=task._on_progress
    def cancel_after_first(event):
        original(event)
        if event.stage=='vision':
            observed.append(event.page-1);task.stat=STAT_ENDED
    monkeypatch.setattr(task,'_on_progress',cancel_after_first)
    task.run(None)
    assert observed==[0], 'cancellation must stop before another source-page progress event'
    assert rig.local_db.session.commits==0
    assert not (rig.folder/'Book - Author.epub').exists()
    rows=_ledger_rows(rig)
    assert len(rows)==1 and rows[0]['status']=='cancelled'


def test_actual_task_runs_selected_vision_scope_and_files_exact_hash(rig,monkeypatch):
    import hashlib
    from cps.services.reflow.native_ipc import NativeDocument
    observed={}
    actual=NativeDocument.convert_vision
    def run(self,*args,**kwargs):
        result=actual(self,*args,**kwargs)
        observed.update(source_pages=self.page_count,output_pages=set(map(int,result['pages'])),
                        selected=set(kwargs['page_numbers']))
        return result
    monkeypatch.setattr(NativeDocument,'convert_vision',run)
    task=_run(rig,mode='sample',sample_pages=1,cost_cap_usd=1)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert observed['source_pages']==3
    assert len(observed['output_pages'])==1 and observed['output_pages']==observed['selected']
    data=open(task.results['path'],'rb').read()
    assert task.results['sha256']==hashlib.sha256(data).hexdigest()
    assert task.results['bytes']==len(data)
    audit=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),1)
    assert audit.entries('artifact')[-1]['sha256']==task.results['sha256']
    assert rig.local_db.session.commits==0


@pytest.mark.parametrize('failure',['validation','database','cancel'])
@pytest.mark.parametrize('existing',[False,True])
def test_failed_publication_preserves_previous_file_and_no_partial_format(rig,monkeypatch,failure,existing):
    from sqlalchemy.exc import SQLAlchemyError
    target=rig.folder/'Book - Author.epub'
    original=b'Existing user EPUB bytes'
    if existing:
        target.write_bytes(original)
        rig.formats['EPUB']=SimpleNamespace(name='Book - Author',format='EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':existing})
    validate=rig.mod.build_epub.validate
    if failure=='validation':monkeypatch.setattr(rig.mod.build_epub,'validate',lambda path:['injected invalid XML'])
    elif failure=='database':
        def fail():raise SQLAlchemyError('database refused commit')
        monkeypatch.setattr(rig.local_db.session,'commit',fail)
    else:
        def cancel(path):
            errors=validate(path);task.stat=STAT_ENDED;return errors
        monkeypatch.setattr(rig.mod.build_epub,'validate',cancel)
    task.run(None)
    assert task.stat==(STAT_ENDED if failure=='cancel' else STAT_FAIL)
    assert rig.local_db.session.commits==0
    assert target.read_bytes()==original if existing else not target.exists()
    assert not list(rig.folder.glob('.reflow-*'))
    assert _ledger_rows(rig)[0].get('artifact') is None
    journal=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),1)
    assert len(journal.entries('vision_summary'))==1
    assert not journal.entries('vision_publication')
    assert len(journal.entries('vision_artifact'))==int(failure!='validation')
    assert all(e['event']=='validated' for e in journal.entries('vision_artifact'))

@pytest.mark.parametrize('existing',[False,True])
def test_process_death_after_publication_restores_consistent_library(rig,monkeypatch,existing):
    target=rig.folder/'Book - Author.epub'
    if existing:
        target.write_bytes(b'previous private EPUB')
        rig.formats['EPUB']=SimpleNamespace(name='Book - Author',format='EPUB')
        rig.local_db.session.existing=SimpleNamespace(name='Book - Author',uncompressed_size=target.stat().st_size)
    previous=target.read_bytes() if existing else None
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':existing})
    real_replace=os.replace
    def die_after_publish(src,dst):
        real_replace(src,dst)
        if os.fspath(dst)==str(target):os._exit(86)
    def child():
        monkeypatch.setattr(rig.mod.os,'replace',die_after_publish)
        task.run(None)
    assert run_forked(child,30)==86
    assert target.exists() and target.read_bytes()!=previous
    with rig.mod.publication.lock(os.path.join(rig.root,'publication-locks'),str(target)):
        assert rig.mod.recover_interrupted_jobs()==[]
        assert _ledger_rows(rig)[0]['status']=='running'
    rig.mod.recover_interrupted_jobs()
    assert (target.read_bytes() if target.exists() else None)==previous
    before=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),0).entries()
    assert rig.mod.recover_interrupted_jobs()==[]
    assert ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),0).entries()==before
    assert not list(rig.folder.glob('.reflow-*'))

@pytest.mark.parametrize('crash_after_receipt',[False,True])
def test_process_death_after_database_commit_retains_exact_new_file(rig,monkeypatch,crash_after_receipt):
    import json,hashlib
    marker=rig.folder/'format-state.json'
    target=rig.folder/'Book - Author.epub'
    target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True})
    def commit():
        row=rig.local_db.session.merged[-1]
        marker.write_text(json.dumps({'name':row.name,'uncompressed_size':row.uncompressed_size}))
        if not crash_after_receipt:os._exit(86)
    monkeypatch.setattr(rig.local_db.session,'commit',commit)
    monkeypatch.setattr(rig.local_db.session,'one_or_none',lambda:SimpleNamespace(**json.loads(marker.read_text())) if marker.exists() else None)
    real_committed=rig.mod.publication.committed
    def committed(led):
        real_committed(led);os._exit(86)
    if crash_after_receipt:monkeypatch.setattr(rig.mod.publication,'committed',committed)
    assert run_forked(task.run,30,None)==86
    expected=hashlib.sha256(target.read_bytes()).hexdigest()
    ledpath=os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl')
    led=ledger_mod.Ledger(ledpath,5,task.job_id)
    led.record({'kind':'reservation','event':'pending','attempt':'unresolved','bound_usd':.123456789})
    led.record({'kind':'reservation','event':'reconciled','attempt':'billed','cost_usd':.0123456789})
    assert rig.mod.recover_interrupted_jobs()==[task.job_id]
    assert hashlib.sha256(target.read_bytes()).hexdigest()==expected
    after=ledger_mod.Ledger(ledpath,5,task.job_id)
    assert after.spent()==.0123456789 and after.pending_usd()==.123456789
    assert after.entries('artifact')[-1]['sha256']==expected
    assert not list(rig.folder.glob('.reflow-*'))
    assert rig.mod.recover_interrupted_jobs()==[]

@pytest.mark.parametrize('changed',['target','metadata','source','backup','extra-staging'])
def test_restart_preserves_later_user_changes_instead_of_rolling_them_back(rig,monkeypatch,changed):
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True})
    real_replace=os.replace
    def child():
        def replace(src,dst):
            real_replace(src,dst)
            if os.fspath(dst)==str(target):os._exit(86)
        monkeypatch.setattr(rig.mod.os,'replace',replace);task.run(None)
    assert run_forked(child,30)==86
    staging=next(rig.folder.glob('.reflow-*'))
    if changed=='target':target.write_bytes(b'later user EPUB')
    elif changed=='metadata':rig.local_db.session.existing=SimpleNamespace(name='other user format',uncompressed_size=123)
    elif changed=='source':(rig.folder/'Book - Author.pdf').write_bytes(b'later user source')
    elif changed=='backup':
        # Break the old hard link before editing its replacement.
        (staging/'previous.epub').unlink();(staging/'previous.epub').write_bytes(b'later backup')
    else:(staging/'user-file').write_bytes(b'later user evidence')
    before={str(p):p.read_bytes() for p in rig.folder.rglob('*') if p.is_file()}
    if changed=='metadata':
        ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),5,task.job_id).record(
            {'kind':'job','event':'finish','status':'failed','error':'earlier commit failure'})
    rig.mod.recover_interrupted_jobs()
    assert before=={str(p):p.read_bytes() for p in rig.folder.rglob('*') if p.is_file()}
    row=_ledger_rows(rig)[0]
    assert row['status']=='failed' and 'recovery' in row['error'].lower()


def test_existing_epub_filename_is_the_actual_replacement_target(rig):
    target=rig.folder/'Alternate.epub';target.write_bytes(b'old indexed EPUB')
    rig.formats['EPUB']=SimpleNamespace(name='Alternate',format='EPUB')
    rig.local_db.session.existing=SimpleNamespace(name='Alternate',uncompressed_size=16)
    task=_run(rig,mode='full',replace_existing_epub=True)
    assert task.stat==STAT_FINISH_SUCCESS
    assert task.results['path']==str(target)
    assert rig.local_db.session.existing.name=='Alternate'
    assert not (rig.folder/'Book - Author.epub').exists()


def test_explicit_deterministic_job_needs_no_key_or_structural_candidate_preparation(rig,monkeypatch):
    from cps.services.reflow import structural_ops
    def forbidden(*args,**kwargs):raise AssertionError('Source-only conversion prepared structural AI candidates')
    monkeypatch.setattr(structural_ops,'prepare',forbidden)
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'sample','sample_pages':1,'review_mode':'deterministic','cost_cap_usd':4})
    task.run(None)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert task.options.cost_cap_usd==0
    payload=task.results['report']
    assert payload['route']=='vision'
    assert payload['model']['review_mode']=='deterministic' and payload['model']['used'] is False
    assert payload['cost']['spend_usd']==payload['cost']['pending_usd']==payload['cost']['cap_usd']==0
    assert os.path.isfile(task.results['path'])
    import zipfile
    with zipfile.ZipFile(task.results['path']) as epub:
        reports=''.join(epub.read(name).decode() for name in epub.namelist() if name.endswith('.xhtml'))
        assert 'OEBPS/vision-about.xhtml' in epub.namelist()
        from xml.etree import ElementTree as ET
        assert ET.fromstring(epub.read('OEBPS/vision-about.xhtml')).tag.endswith('}html')
    assert 'Eligible pages</td>' not in reports


def test_changed_queued_source_is_rejected_before_preparation_or_dispatch(rig,monkeypatch):
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'sample'})
    path=rig.folder/'Book - Author.pdf'
    task.options.source_sha256=rig.mod.extract.document_fingerprint(str(path))
    path.write_bytes(path.read_bytes()+b'\n% changed after consent\n')
    def forbidden(*args,**kwargs):raise AssertionError('changed consent must stop before preparation')
    monkeypatch.setattr(task,'_convert',forbidden)
    task.run(None)
    assert task.stat==STAT_FAIL and 'source changed' in (task.error or '').lower()
    assert not os.path.isfile(rig.mod.sample_path(7,task.job_id))


def test_sample_source_change_during_build_never_publishes(rig,monkeypatch):
    from cps.services.reflow.native_ipc import NativeDocument
    original=NativeDocument.convert_vision
    def changed(*args,**kwargs):
        built=original(*args,**kwargs)
        path=rig.folder/'Book - Author.pdf'
        path.write_bytes(path.read_bytes()+b'\n% replaced during build\n')
        return built
    monkeypatch.setattr(NativeDocument,'convert_vision',changed)
    task=_run(rig,mode='sample',review_mode='deterministic')
    assert task.stat==STAT_FAIL and 'source changed' in (task.error or '').lower()
    assert not os.path.isfile(rig.mod.sample_path(7,task.job_id))


# ── publication on real-world libraries (N4, 7daffa5 retest) ─────────────────

def test_a_replacement_is_filed_on_a_library_that_cannot_hard_link(rig,monkeypatch):
    """An SMB/FUSE/rclone library refuses os.link; "replace the existing EPUB"
    failed there every time. The whole conversion now files, and the previous
    file was backed up by copy until the commit. Breaks with a link-only backup."""
    import errno
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'previous user EPUB')
    rig.formats['EPUB']=SimpleNamespace(name='Book - Author',format='EPUB')
    rig.local_db.session.existing=SimpleNamespace(name='Book - Author',uncompressed_size=target.stat().st_size)
    def refused(*_args,**_kwargs):raise OSError(errno.EXDEV,'Invalid cross-device link')
    monkeypatch.setattr(rig.mod.publication.os,'link',refused)
    task=_run(rig,mode='full',review_mode='deterministic',replace_existing_epub=True)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert target.read_bytes()[:2]==b'PK' and rig.local_db.session.commits==1
    assert not list(rig.folder.glob('.reflow-*'))


def test_a_library_reached_through_a_symlinked_root_is_converted_into(rig,monkeypatch):
    """A library root configured as a symlink (/books -> /mnt/nas/books) had every
    full conversion refused as 'escapes the library or uses a symlink'. It files
    now, into the real folder, with a journal of library-relative paths."""
    link=rig.folder.parent.parent.parent/'library-link'
    link.symlink_to(rig.folder.parent.parent,target_is_directory=True)
    monkeypatch.setattr(rig.mod.config,'get_book_path',lambda:str(link))
    task=_run(rig,mode='full',review_mode='deterministic')
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert (rig.folder/'Book - Author.epub').read_bytes()[:2]==b'PK'
    ledger=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),0,task.job_id)
    prepared=[e for e in ledger.entries('publication') if e.get('event')=='prepared'][-1]
    assert prepared['target']=='Author/Book (5)/Book - Author.epub'


def _crash_after_publish(rig,monkeypatch,task,target):
    real_replace=os.replace
    def child():
        def replace(src,dst):
            real_replace(src,dst)
            if os.fspath(dst)==str(target):os._exit(86)
        monkeypatch.setattr(rig.mod.os,'replace',replace);task.run(None)
    assert run_forked(child,60)==86


def test_conflict_evidence_leaves_the_book_folder_on_the_next_recovery_pass(rig,monkeypatch):
    """A publication journal whose files changed underneath it is a conflict: the
    first recovery preserves everything in place for review. It used to stay in
    the book's folder forever as a hidden .reflow-* directory holding two EPUBs.
    The next pass moves that evidence -- byte for byte -- into the Reflow data
    folder, closes the journal, and leaves the user's later file alone. Breaks if
    the staging folder is left in the library after the second pass, or if any
    preserved byte is lost."""
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True,'review_mode':'deterministic'})
    _crash_after_publish(rig,monkeypatch,task,target)
    staging=next(rig.folder.glob('.reflow-*'))
    kept={name:(staging/name).read_bytes() for name in os.listdir(staging)}
    assert set(kept)=={'candidate.epub','previous.epub'} or set(kept)=={'previous.epub'}
    target.write_bytes(b'later user EPUB')                      # the conflict

    rig.mod.recover_interrupted_jobs()                           # pass 1: preserve in place
    assert staging.is_dir() and target.read_bytes()==b'later user EPUB'
    rig.mod.recover_interrupted_jobs()                           # pass 2: out of the library
    assert not list(rig.folder.glob('.reflow-*'))
    assert target.read_bytes()==b'later user EPUB'
    evidence=os.path.join(rig.root,'publication-conflicts','5',task.job_id)
    assert {name:open(os.path.join(evidence,name),'rb').read() for name in kept}==kept
    led=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),0,task.job_id)
    assert rig.mod.publication.pending(led) is None
    before=led.entries()
    rig.mod.recover_interrupted_jobs()                           # pass 3: nothing left to do
    assert ledger_mod.Ledger(led.path,0,task.job_id).entries()==before
    assert _ledger_rows(rig)[0]['status']=='failed'


def test_a_conflict_that_resolves_is_cleaned_up_by_the_ordinary_recovery(rig,monkeypatch):
    """The other way a conflict ends: the library returns to a state recovery can
    prove (here the user restores the file Reflow published). The next pass then
    reconciles normally and removes the staging folder itself."""
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True,'review_mode':'deterministic'})
    _crash_after_publish(rig,monkeypatch,task,target)
    published=target.read_bytes();target.write_bytes(b'briefly changed')
    rig.mod.recover_interrupted_jobs()
    assert list(rig.folder.glob('.reflow-*'))
    target.write_bytes(published)                                # resolved
    rig.mod.recover_interrupted_jobs()
    assert not list(rig.folder.glob('.reflow-*'))
    assert target.read_bytes()==b'old EPUB'                      # rolled back: never committed
    assert not os.path.exists(os.path.join(rig.root,'publication-conflicts','5',task.job_id))


def test_a_staging_folder_orphaned_before_publication_is_removed_on_recovery(rig,monkeypatch):
    """The process can die while the EPUB is still being built -- minutes, for a
    long book -- after the staging folder exists and before any journal entry
    names it. Nothing was published and nothing needs it, but nothing removed it
    either. Recovery now does. Breaks if such a folder survives recovery."""
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True,'review_mode':'deterministic'})
    def child():
        def die(path):
            assert os.path.basename(os.path.dirname(path))=='.reflow-%s-staging'%task.job_id
            with open(path+'.tmp','wb') as partial:partial.write(b'PK half an EPUB')
            os._exit(86)
        monkeypatch.setattr(rig.mod.build_epub,'validate',die);task.run(None)
    assert run_forked(child,60)==86
    assert list(rig.folder.glob('.reflow-*'))
    assert rig.mod.recover_interrupted_jobs()==[task.job_id]
    assert not list(rig.folder.glob('.reflow-*'))
    assert target.read_bytes()==b'old EPUB'
    assert _ledger_rows(rig)[0]['status']=='interrupted'


@pytest.mark.parametrize('failure',['io','database'])
def test_a_transient_failure_on_a_later_pass_never_moves_the_evidence(rig,monkeypatch,failure):
    """Only a standing conflict is proof enough to take evidence out of the book's
    folder. A NAS that drops out, or a database that is locked, on the later pass
    says nothing about the files: they stay exactly where they are, the journal
    stays open, and the next pass tries again. Breaks if a transient error is
    treated as a standing conflict."""
    import errno
    from sqlalchemy.exc import OperationalError
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True,'review_mode':'deterministic'})
    _crash_after_publish(rig,monkeypatch,task,target)
    target.write_bytes(b'later user EPUB')
    rig.mod.recover_interrupted_jobs()                           # pass 1: a real conflict
    staging=next(rig.folder.glob('.reflow-*'))
    if failure=='io':
        def unavailable(*_args,**_kwargs):raise OSError(errno.EIO,'Input/output error')
        monkeypatch.setattr(rig.mod.publication,'reconcile',unavailable)
    else:
        def locked(_book_id):raise OperationalError('SELECT','{}',Exception('database is locked'))
        monkeypatch.setattr(rig.local_db,'get_book',locked)
    rig.mod.recover_interrupted_jobs()                           # pass 2: transient
    assert staging.is_dir() and not os.path.exists(os.path.join(rig.root,'publication-conflicts'))
    led=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),0,task.job_id)
    assert rig.mod.publication.pending(led) is not None


def test_actual_free_task_discloses_original_pixels_for_an_unreadable_symbolic_font(rig,monkeypatch):
    """A source-only pixel route keeps unresolved font ink as disclosed imagery."""
    from cps.services.reflow import extract
    from PIL import Image
    import io,zipfile
    path=rig.folder/'Book - Author.pdf'
    with extract.pymupdf.open(path) as doc:
        for font in doc[1].get_fonts():
            doc.xref_set_key(font[0],'FontDescriptor','<< /Type /FontDescriptor /Flags 4 >>')
        doc.saveIncr()
    task=_run(rig,mode='full',cost_cap_usd=0)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    target=str(rig.folder/'Book - Author.epub')
    assert rig.mod.build_epub.validate(target)==[]
    sidecar=rig.mod.build_epub.read_sidecar(target)
    assert sidecar['pages']['1']['source']=='original_source'
    assert sidecar['source_document_sha256']==extract.document_fingerprint(path)
    original=next(unit for unit in sidecar['degraded_units'] if unit['page']==1 and unit['block'] is None)
    with zipfile.ZipFile(target) as archive:
        name=original['source_image']
        if not name.startswith('OEBPS/'):name='OEBPS/'+name
        with Image.open(io.BytesIO(archive.read(name))) as image:
            assert image.width>=500 and image.height>=700
            assert image.convert('L').getextrema()[0]<200
        from xml.etree import ElementTree as ET
        roots=[ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith('.xhtml')]
        controls=[node for root in roots for node in root.iter()
                  if node.get('id')==original['source_reference_origin']]
        assert len(controls)==1 and controls[0].get('href','').startswith('#')
        targets=[node for root in roots for node in root.iter()
                 if node.get('id')==controls[0].get('href')[1:]]
        assert len(targets)==1
        assert any(node.tag.endswith('}img') and node.get('src')==original['source_image']
                   for node in targets[0].iter())
    assert task.results['report']['cost']['spend_usd']==0
    assert rig.local_db.session.commits==1


@pytest.mark.parametrize("failure", ["grown", "missing", "database", "protected"])
def test_queued_failure_before_native_work_is_durable_owned_and_visible(
        rig, monkeypatch, failure):
    import inspect
    from unittest.mock import patch
    from cps.api import reflow as api
    from cps.services.reflow import native_ipc
    from tests.unit.test_reflow_api import _ctx, _json, _start, _status, _user, _wire

    monkeypatch.setattr(api, "REFLOW_DIR", rig.root)
    _wire(api, monkeypatch, {"library": str(rig.folder.parent.parent),
                             "name": "Book - Author"})
    monkeypatch.setattr(rig.mod.config, "config_reflow_max_pdf_mb", 1, raising=False)
    old = ledger_mod.Ledger(os.path.join(rig.root, "jobs", "5", "b" * 16 + ".jsonl"), 0)
    old.record({"kind": "job", "event": "start", "user_id": 7, "mode": "full"})
    old.record({"kind": "job", "event": "finish", "status": "done"})
    os.utime(old.path, (1, 1))
    response, added = _start(api, {"mode": "full", "consent": True})
    assert _status(response) == 202
    task = added.call_args[0][1]
    source = rig.folder / "Book - Author.pdf"
    if failure == "grown":
        with source.open("ab") as handle:
            handle.write(b"0" * 1048576)
        reason = "over the limit"
    elif failure == "missing":
        source.unlink()
        reason = "no PDF"
    elif failure == "database":
        def unavailable(**_kwargs):
            raise RuntimeError("Library database unavailable")
        monkeypatch.setattr(rig.mod.db, "CalibreDB", unavailable)
        reason = "Library database unavailable"
    else:
        def protected(*_args, **_kwargs):
            raise ValueError("This PDF is password-protected. Remove its password before converting it.")
        monkeypatch.setattr(native_ipc, "NativeDocument", protected)
        reason = "password-protected"
    task.run(None)
    assert task.stat == STAT_FAIL and reason in str(task.error)
    assert task.done_event.is_set()

    # Remove all retained task state: the endpoint must read the durable result.
    monkeypatch.setattr(api.WorkerThread, "get_instance",
                        staticmethod(lambda: SimpleNamespace(tasks=[])))
    def listing(user):
        with _ctx("/api/v1/books/5/reflow/jobs"), patch.object(api, "current_user", user):
            return _json(inspect.unwrap(api.reflow_jobs)(5))
    mine = listing(_user())
    assert mine["active"] == []
    assert mine["items"][0]["job_id"] == task.job_id
    assert mine["items"][0]["status"] == "failed"
    assert reason in mine["items"][0]["error"]
    assert len(mine["items"]) == 2
    assert listing(_user(uid=8))["items"] == []
    assert listing(_user(uid=8, admin=True))["items"][0]["job_id"] == task.job_id
    task_id = task.job_id
    del task
    rig.mod.recover_interrupted_jobs(worker=SimpleNamespace(tasks=[]))
    restarted = listing(_user())["items"][0]
    assert restarted["job_id"] == task_id and restarted["status"] == "failed"
    assert reason in restarted["error"]
