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
    F.chapter_opening_page(doc, "Serapio of Alexandria")
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
        config_reflow_hard_cap_usd=5.0))
    monkeypatch.setattr(mod.helper, "mark_book_format_materialised",
                        lambda *a, **kw: None)
    return SimpleNamespace(mod=mod, book=book, formats=formats, local_db=local_db,
                           folder=folder, root=str(tmp_path / "reflow"))


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
    """A document that is not well-formed XML is a chapter the reader cannot open,
    and it is a well-formed zip entry -- so the file looks finished from outside.
    OBSERVED on the acceptance book: one model page carried a raw "&", the whole
    document stopped parsing, and nothing in the job noticed. The builder's own
    check is the thing that can notice, so the job has to ask it before it tells
    the library there is an EPUB. Injected here as a markup fault in the writer,
    because the point is the faults nobody has met yet."""
    document = rig.mod.build_epub._document
    monkeypatch.setattr(rig.mod.build_epub, "_document",
                        lambda title, body, language="en":
                        document(title, body + "<p>Hephaestio 9 & 29</p>", language))

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


def test_a_cancelled_job_does_not_file_a_half_reviewed_conversion(rig):
    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "full", "cost_cap_usd": 1.0})
    task.stat = STAT_ENDED          # what WorkerThread.end_task does
    task.run(None)

    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / "Book - Author.epub"))
    assert _ledger_rows(rig)[0]["status"] == "cancelled"


def test_the_epubs_own_sidecar_keeps_the_cost_private_unless_the_user_shows_it(rig):
    """``show_cost_in_report`` decides whether what a conversion cost travels
    inside the book. The about page honoured it; the machine-readable twin in
    META-INF/reflow.json carried the confirmed spend, the held amount, the cap,
    the token counts and the per-model bill regardless -- readable by anyone the
    file is shared with. Breaks if the sidecar is written from the unredacted
    report payload again."""
    from cps.services.reflow import build_epub

    private = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=0.75)
    shown = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=0.75,
                 show_cost_in_report=True)
    for task in (private, shown):
        assert task.stat == STAT_FINISH_SUCCESS, task.error

    hidden = build_epub.read_sidecar(rig.mod.sample_path(7, private.job_id))
    public = build_epub.read_sidecar(rig.mod.sample_path(7, shown.job_id))
    money = {"usd", "pending_usd", "cap_usd", "calls", "reused", "prompt_tokens",
             "completion_tokens", "models", "unresolved_attempts"}
    assert not money & set(hidden.get("spend") or {}), hidden.get("spend")
    assert hidden["spend"] == {"shown": False}
    # The job's own record is where the owner reads what was spent; it is untouched.
    assert _ledger_rows(rig)[0]["cap_usd"] == pytest.approx(0.75)
    # The same conversion with the cost shown carries every figure the page shows.
    assert public["spend"]["cap_usd"] == pytest.approx(0.75)
    assert money <= set(public["spend"])


# ── the record ───────────────────────────────────────────────────────────────

def test_the_job_says_what_it_was_and_how_it_ended(rig):
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=0.75)

    row = _ledger_rows(rig)[0]
    assert row["job_id"] == task.job_id
    assert row["mode"] == "sample"
    assert row["user_id"] == 7
    assert row["status"] == "limited"  # Explicit review was requested, but no key was configured.
    assert row["cap_usd"] == pytest.approx(0.75)
    assert row["started"] and row["finished"]


def test_a_conversion_with_rejected_model_answers_does_not_say_it_finished(rig, monkeypatch):
    from cps.services.reflow.structural_pipeline import TwoStageClient
    from tests.unit.test_reflow_typed_transport import Session,reply
    session=Session(reply('not a protocol response'))
    monkeypatch.setattr(rig.mod,'make_client',lambda tier:TwoStageClient('inert',enabled=True,session=session))
    task=_run(rig,mode='full',cost_cap_usd=1)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert session.calls
    assert os.path.isfile(str(rig.folder/'Book - Author.epub'))
    row=_ledger_rows(rig)[0]
    assert row['structural']['rejected']>0
    assert row['structural']['approved_operations']==0
    assert row['status']=='incomplete'


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


def test_a_successful_bill_is_debited_exactly_once_across_every_boundary(
        rig, monkeypatch):
    """client → ledger → pipeline → job row: reconciliation is the durable debit,
    the page record references the same attempt, and a reload counts the charge
    once -- never zero, never twice."""
    import json
    from cps.services.reflow.structural_pipeline import TwoStageClient
    from tests.unit.test_reflow_typed_transport import Session,reply
    class Answered(Session):
        def post(self,*args,**kwargs):
            payload=json.loads(kwargs['data'])
            body=json.loads(payload['messages'][1]['content'][1]['text'])
            self.data=reply(json.dumps(body['empty_response']))
            return super().post(*args,**kwargs)
    monkeypatch.setattr(rig.mod,'make_client',lambda tier:TwoStageClient('inert',enabled=True,session=Answered()))
    task = _run(rig, mode="full", cost_cap_usd=1.0)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    row = _ledger_rows(rig)[0]
    path = os.path.join(rig.root, "jobs", "5", "%s.jsonl" % task.job_id)
    entries = ledger_mod.Ledger(path, cap_usd=1.0).entries()
    reconciled = [e for e in entries
                  if e.get("kind") == "reservation" and e.get("event") == "reconciled"]
    assert reconciled, "every answered attempt reconciled durably"
    assert row["spend_usd"] == pytest.approx(sum(e["cost_usd"] for e in reconciled))
    assert row["pending_usd"] == 0.0
    assert all("cost_usd" not in e for e in entries if e.get("kind")=="typed_stage")
    assert row["status"] == "done"


def test_a_job_with_unresolved_billing_stops_safely_and_holds_the_amount(
        rig, monkeypatch):
    """A lost answer after dispatch: the book still ships (its pages keep their
    deterministic text), and the job record tells the truth -- stopped early,
    with the unresolved amount held rather than reported as spent or as zero."""
    import requests

    from cps.services.reflow.structural_pipeline import TwoStageClient
    from tests.unit.test_reflow_typed_transport import Session
    session=Session(error=requests.ReadTimeout('lost answer'))
    monkeypatch.setattr(rig.mod,'make_client',lambda tier:TwoStageClient('inert',enabled=True,session=session))
    task = _run(rig, mode="full", cost_cap_usd=1.0)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert "unresolved" in task.message
    row = _ledger_rows(rig)[0]
    assert row["status"] == "billing_unknown"
    assert row["pending_usd"] > 0
    assert row["spend_usd"] == 0.0, "nothing was confirmed"


def test_a_job_that_failed_says_so_rather_than_disappearing(rig):
    from cps.services.reflow import report as report_mod

    def _disagree(_payload, _ledger):
        return ["pages_sent 3 in the report, 0 in the ledger"]

    original = report_mod.check_completion
    report_mod.check_completion = _disagree
    try:
        task = _run(rig, mode="full", cost_cap_usd=1.0)
    finally:
        report_mod.check_completion = original

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


def test_a_sample_starts_at_the_book_and_not_at_its_front_matter(rig):
    import pymupdf

    doc = F.new_doc()
    F.title_page(doc)
    F.prose_page(doc)
    F.prose_page(doc)
    path = str(rig.folder / "sampled.pdf")
    doc.save(path)
    doc.close()

    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "sample", "sample_pages": 2})
    opened = pymupdf.open(path)
    try:
        assert task._sample_pages(opened) == [1, 2]
    finally:
        opened.close()


# ── housekeeping ─────────────────────────────────────────────────────────────

def test_a_sample_nobody_downloaded_does_not_live_forever(rig):
    keep = rig.mod.sample_path(7, "aaaaaaaaaaaaaaaa")
    drop = rig.mod.sample_path(7, "bbbbbbbbbbbbbbbb")
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    for path in (keep, drop):
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04")
    old = time.time() - 40 * 86400
    os.utime(drop, (old, old))

    assert rig.mod.cleanup_samples(max_age_days=7) == 1
    assert os.path.isfile(keep)
    assert not os.path.exists(drop)


# ── a restart mid-conversion ─────────────────────────────────────────────────

def _orphan_ledger(rig, job_id="df83053b791743a1", book_id=5):
    """The record a job leaves when its process dies: a start, some spend, and
    no finish -- the shape Terra's restart repro left on disk (a sample stopped
    at "page 4 of 20", the container restarted under it)."""
    directory = os.path.join(rig.root, "jobs", str(book_id))
    os.makedirs(directory, exist_ok=True)
    led = ledger_mod.Ledger(os.path.join(directory, "%s.jsonl" % job_id),
                            cap_usd=5.0, job_id=job_id)
    led.record({"kind": "job", "event": "start", "mode": "sample", "user_id": 7,
                "tier": "cheap", "cap_usd": 5.0, "pages": 20})
    led.record({"kind": "page", "page": 3, "cost_usd": 0.0009, "gate": "PASS",
                "model": "deepseek/deepseek-v4.1-flash"})
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
    led.reserve_attempt("4", 0.0217, model_id="deepseek/deepseek-v4.1-flash",
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


def test_cancel_during_original_evidence_stops_rendering_without_filing(rig, monkeypatch):
    from cps.services.reflow import assemble
    from cps.services.reflow.source_display import SourceDisplay
    task = rig.mod.TaskReflowPdf(5, 7, {"mode": "full", "cost_cap_usd": 1.0})
    convert = task._convert
    def uncertain_pages(*args, **kwargs):
        result = convert(*args, **kwargs)
        for pno in result.book.pages:
            result.book.notes.append(assemble.Note(num=1, text='Uncertain reference',
                pno=pno, uncertain=True, bbox=(40,500,350,550)))
        from cps.services.reflow.enriched_source import prepare_source_page
        for pno in result.page_html:
            canonical=prepare_source_page(result.book,pno,{'layer':'native'})
            result.source_pages[pno]=canonical;result.page_html[pno]=canonical.html
        monkeypatch.setattr(SourceDisplay, 'jpeg', cancel_after_first)
        return result
    monkeypatch.setattr(task, '_convert', uncertain_pages)
    render = SourceDisplay.jpeg
    rendered = []
    def cancel_after_first(*args, **kwargs):
        rendered.append(args[0].pno)
        pixels = render(*args, **kwargs)
        task.stat = STAT_ENDED
        return pixels
    task.run(None)
    assert rendered == [0], 'cancellation must stop before another evidence raster'
    assert rig.local_db.session.commits == 0
    assert not os.path.exists(str(rig.folder / 'Book - Author.epub'))
    rows = _ledger_rows(rig)
    assert len(rows) == 1 and rows[0]['status'] == 'cancelled'


def test_actual_task_prepares_full_source_for_sample_and_files_exact_hash(rig,monkeypatch):
    import hashlib
    from cps.services.reflow import structural_pipeline
    observed={}
    real=structural_pipeline.run_structural
    def run(*args,**kwargs):
        result=real(*args,**kwargs)
        observed.update(source_pages=set(result.book.pages),output_pages=set(result.page_html),
                        evidence=set(result.source_pages),plans=result.operation_plans)
        return result
    monkeypatch.setattr(structural_pipeline,'run_structural',run)
    task=_run(rig,mode='sample',sample_pages=1,cost_cap_usd=1)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert len(observed['source_pages'])==3
    assert len(observed['output_pages'])==1 and observed['output_pages']==observed['evidence']
    assert observed['plans']==[]
    with open(task.results['path'],'rb') as f:data=f.read()
    assert task.results['sha256']==hashlib.sha256(data).hexdigest()
    assert task.results['bytes']==len(data)
    ledger=ledger_mod.Ledger(os.path.join(rig.root,'jobs','5',task.job_id+'.jsonl'),cap_usd=1)
    assert ledger.entries('artifact')[-1]['sha256']==task.results['sha256']
    assert rig.local_db.session.commits==0


def test_actual_task_quality_gate_prevents_both_typed_stages_with_configured_key(rig,monkeypatch):
    import requests
    monkeypatch.setattr(rig.mod.config,'resolved_openrouter_key',lambda:'inert-configured-key')
    def forbidden(*args,**kwargs):raise AssertionError('quality-gated task attempted network')
    monkeypatch.setattr(requests.sessions.Session,'request',forbidden)
    task=_run(rig,mode='full',cost_cap_usd=1)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert task.results['report']['stopped']=='quality_gate'
    structural=task.results['report']['structural']
    assert structural['approved_operations']==0
    assert structural['unreviewed']==structural['eligible']
    assert task.results['report']['spend']['usd']==0


@pytest.mark.parametrize('decision',['decline','approve','stale'])
def test_actual_task_records_two_stage_decision_and_only_builds_approved_subset(rig,monkeypatch,decision):
    approve=decision=='approve'
    import json,zipfile
    from cps.services.reflow.structural_pipeline import TwoStageClient
    from tests.unit.test_reflow_typed_transport import Session,reply
    doc=F.new_doc();page=doc.new_page(width=500,height=700)
    page.insert_text((50,100),'"Original displayed words remain exactly as printed."',fontsize=12)
    for y in (180,195,210):page.insert_text((50,y),'Ordinary body context supports the source display.',fontsize=12)
    doc.save(str(rig.folder/'Book - Author.pdf'));doc.close()
    class Answering:
        def __init__(self):self.calls=[]
        def get(self,url,**kw):return Session().get(url,**kw)
        def post(self,*args,**kwargs):
            payload=json.loads(kwargs['data']);self.calls.append(payload)
            body=json.loads(payload['messages'][1]['content'][1]['text'])
            response=body['empty_response'].copy()
            if payload['model'].endswith('luna'):
                response['select']=[next(c['candidate_id'] for c in body['source']['candidates'] if c['kind']=='quote')]
            else:
                response['approve']=body['source']['verification']['proposed_ids'] if decision!='decline' else []
                if decision=='stale':response['snapshot_id']='0'*64
            data=reply(json.dumps(response));data['model']=payload['model']
            return Session(data).post()
    session=Answering()
    monkeypatch.setattr(rig.mod,'make_client',lambda tier:TwoStageClient('inert',enabled=True,session=session))
    task=_run(rig,mode='full',cost_cap_usd=1)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert len(session.calls)==2
    payload=task.results['report']
    assert payload['structural']['proposed_operations']==1
    assert payload['structural']['approved_operations']==int(approve)
    assert payload['structural']['verifier_abstained']==int(decision=='decline')
    assert payload['structural']['rejected']==int(decision=='stale')
    assert payload['spend']['usd']==2*.000123456789
    row=_ledger_rows(rig)[0]
    assert row['spend_usd']==payload['spend']['usd']
    assert row['structural']['verifier_abstained']==int(decision=='decline')
    with zipfile.ZipFile(task.results['path']) as z:
        body=''.join(z.read(n).decode() for n in z.namelist() if '/ch' in n and n.endswith('.xhtml'))
    assert ('<blockquote' in body)==approve
    import hashlib
    assert task.results['sha256']==hashlib.sha256(open(task.results['path'],'rb').read()).hexdigest()
    assert rig.local_db.session.commits==1

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

@pytest.mark.parametrize('existing',[False,True])
def test_process_death_after_publication_restores_consistent_library(rig,monkeypatch,existing):
    import multiprocessing
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
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(30)
    assert process.exitcode==86
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
    import multiprocessing,json,hashlib
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
    process=multiprocessing.get_context('fork').Process(target=task.run,args=(None,))
    process.start();process.join(30);assert process.exitcode==86
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
    import multiprocessing
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True})
    real_replace=os.replace
    def child():
        def replace(src,dst):
            real_replace(src,dst)
            if os.fspath(dst)==str(target):os._exit(86)
        monkeypatch.setattr(rig.mod.os,'replace',replace);task.run(None)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(30);assert process.exitcode==86
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


def test_explicit_deterministic_job_needs_no_key_or_model_candidate_preparation(rig,monkeypatch):
    from cps.services.reflow import structural_ops
    def forbidden(*args,**kwargs):raise AssertionError('deterministic conversion prepared AI candidates')
    monkeypatch.setattr(structural_ops,'prepare',forbidden)
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'sample','sample_pages':1,'review_mode':'deterministic','cost_cap_usd':4})
    task.run(None)
    assert task.stat==STAT_FINISH_SUCCESS,task.error
    assert task.results['report']['stopped'] is None
    assert task.options.cost_cap_usd==0
    structural=task.results['report']['structural']
    assert structural['review_mode']=='deterministic' and structural['eligibility_measured'] is False
    assert structural['approved_operations']==0 and structural['attempted_stages']==0
    assert os.path.isfile(task.results['path'])
    import zipfile
    with zipfile.ZipFile(task.results['path']) as epub:
        reports=''.join(epub.read(name).decode() for name in epub.namelist() if name.endswith('.xhtml'))
    assert 'Eligibility was not measured' in reports and 'Eligible pages</td>' not in reports


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
    original=rig.mod.build_epub.build
    def changed(*args,**kwargs):
        built=original(*args,**kwargs)
        path=rig.folder/'Book - Author.pdf'
        path.write_bytes(path.read_bytes()+b'\n% replaced during build\n')
        return built
    monkeypatch.setattr(rig.mod.build_epub,'build',changed)
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
    import multiprocessing
    real_replace=os.replace
    def child():
        def replace(src,dst):
            real_replace(src,dst)
            if os.fspath(dst)==str(target):os._exit(86)
        monkeypatch.setattr(rig.mod.os,'replace',replace);task.run(None)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(60);assert process.exitcode==86


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
    import multiprocessing
    target=rig.folder/'Book - Author.epub';target.write_bytes(b'old EPUB')
    task=rig.mod.TaskReflowPdf(5,7,{'mode':'full','replace_existing_epub':True,'review_mode':'deterministic'})
    def child():
        def die(book,out_path,**_kwargs):
            with open(out_path+'.tmp','wb') as partial:partial.write(b'PK half an EPUB')
            os._exit(86)
        monkeypatch.setattr(rig.mod.build_epub,'build',die);task.run(None)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(60);assert process.exitcode==86
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
