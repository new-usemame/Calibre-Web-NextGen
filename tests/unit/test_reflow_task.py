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


# ── the record ───────────────────────────────────────────────────────────────

def test_the_job_says_what_it_was_and_how_it_ended(rig):
    task = _run(rig, mode="sample", sample_pages=2, cost_cap_usd=0.75)

    row = _ledger_rows(rig)[0]
    assert row["job_id"] == task.job_id
    assert row["mode"] == "sample"
    assert row["user_id"] == 7
    assert row["status"] == "done"
    assert row["cap_usd"] == pytest.approx(0.75)
    assert row["started"] and row["finished"]


class _DeadProvider(object):
    """A provider that has gone away: it takes every page and answers none of them."""

    def __init__(self):
        self.calls = 0
        self.model_id = "test/model"
        self.tier = "standard"
        self.dry_run = False
        self.spec = SimpleNamespace(price_per_page=0.002, model_id="test/model")

    def describe(self):
        return {"model": self.model_id, "tier": self.tier, "configured": True,
                "dry_run": False, "prompt_version": "test-1"}

    def edit_page(self, *_args, **_kwargs):
        self.calls += 1
        raise model_mod.ModelError("OpenRouter 503: upstream is unavailable")


def test_a_conversion_the_model_service_ended_early_does_not_say_it_finished(rig,
                                                                            monkeypatch):
    """A book that stopped is not a book that finished.

    The conversion walks away from the model after a run of refusals, which is the
    right thing to do with a service that has died -- but every page after the walk
    away keeps the text read straight out of the PDF, and the user is never told.
    MEASURED on the acceptance book: a 698-page run ended twenty routed pages early
    and the jobs list called it done, so the only place the truth appeared was a
    sentence inside the EPUB nobody had a reason to open.
    """
    provider = _DeadProvider()
    monkeypatch.setattr(rig.mod, "make_client", lambda _tier: provider)

    task = _run(rig, mode="full", cost_cap_usd=1.0)

    # Every page still has its deterministic text, so the book is worth filing.
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert provider.calls == rig.mod.pipeline.MAX_CONSECUTIVE_REFUSALS
    assert os.path.isfile(str(rig.folder / "Book - Author.epub"))
    # What it may not do is call itself a finished conversion.
    row = _ledger_rows(rig)[0]
    assert row["status"] == "incomplete", row


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
    class _Answered(object):
        def post(self, *_args, **_kwargs):
            return SimpleNamespace(
                status_code=200, headers={}, text="",
                json=lambda: {
                    "model": "deepseek/deepseek-v4.1-flash",
                    "choices": [{"message": {"content": "no markup in this answer"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1500, "completion_tokens": 12,
                              "cost": 0.0031}})

    monkeypatch.setattr(rig.mod, "make_client",
                        lambda _tier: model_mod.OpenRouterClient(
                            "key", session=_Answered(), backoff=0.0))
    task = _run(rig, mode="full", cost_cap_usd=1.0)

    assert task.stat == STAT_FINISH_SUCCESS, task.error
    row = _ledger_rows(rig)[0]
    path = os.path.join(rig.root, "jobs", "5", "%s.jsonl" % task.job_id)
    entries = ledger_mod.Ledger(path, cap_usd=1.0).entries()
    page_sum = sum(e["cost_usd"] for e in entries
                   if e.get("kind") == "page" and e.get("cost_usd") is not None)
    reconciled = [e for e in entries
                  if e.get("kind") == "reservation" and e.get("event") == "reconciled"]
    assert reconciled, "every answered attempt reconciled durably"
    assert row["spend_usd"] == pytest.approx(page_sum)
    assert row["spend_usd"] == pytest.approx(sum(e["cost_usd"] for e in reconciled))
    assert row["pending_usd"] == 0.0
    assert row["status"] == "done"


def test_a_job_with_unresolved_billing_stops_safely_and_holds_the_amount(
        rig, monkeypatch):
    """A lost answer after dispatch: the book still ships (its pages keep their
    deterministic text), and the job record tells the truth -- stopped early,
    with the unresolved amount held rather than reported as spent or as zero."""
    import requests

    class _Lost(object):
        def post(self, *_args, **_kwargs):
            raise requests.exceptions.ReadTimeout("the answer was lost")

    monkeypatch.setattr(rig.mod, "make_client",
                        lambda _tier: model_mod.OpenRouterClient(
                            "key", session=_Lost(), max_retries=2, backoff=0.0))
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
    assert rig.mod.ReflowOptions({"cost_cap_usd": 99.0}).cost_cap_usd == 2.0
    assert rig.mod.ReflowOptions({"cost_cap_usd": 0.25}).cost_cap_usd == 0.25
    # No cap named at all is the ceiling, never "unlimited".
    assert rig.mod.ReflowOptions({}).cost_cap_usd == 2.0


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
