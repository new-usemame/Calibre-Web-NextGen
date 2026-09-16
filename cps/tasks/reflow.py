# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The background job: one PDF in, one reflowable EPUB out.

Three things make this different from ``TaskConvert``.

*It can stop and it can be stopped.* A 700-page book is minutes of work and real
money. The task checks for cancellation between pages, the cost cap raises between
pages, and either way the pages already paid for stay in the cache so the next run
starts where this one left off.

*It never overwrites a book's EPUB by accident.* A user who already has an EPUB
gets a refusal with a reason unless they asked for a replacement.

*A sample never touches the library.* Sample mode writes the EPUB under the config
volume and hands back a download, so a user can look before they commit.
"""

import os
import uuid

from flask_babel import lazy_gettext as N_
from sqlalchemy.exc import SQLAlchemyError

from cps import config, db, helper, logger
from cps.constants import REFLOW_DIR
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED
from cps.services.reflow import admission, build_epub, ledger as ledger_mod, model, \
    pipeline, report

log = logger.create()

#: A sample is capped so "look before you commit" cannot become "convert the book
#: twice". The default is what the UI offers.
SAMPLE_PAGES_DEFAULT = 20
SAMPLE_PAGES_MAX = 60

#: What a pipeline stop is called in the record the jobs list reads. ``cancelled``
#: never reaches this table -- it has its own branch, because a conversion nobody
#: waited for is not filed at all -- so what is left is the two ways a conversion
#: ends holding a file that is less than the book it was asked for. Neither of them
#: is ``done``: a run that stopped is a run the user has a reason to start again,
#: and a jobs list that reports both endings with one word takes that reason away.
STOP_STATUS = {"cost_cap": "capped", "model_errors": "incomplete"}


def reflow_dir(*parts):
    path = os.path.join(REFLOW_DIR, *parts)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def sample_path(user_id, job_id):
    return os.path.join(reflow_dir("samples", str(user_id)), "%s.epub" % job_id)


class ReflowOptions(object):
    """What the user consented to, with every value clamped to what is allowed."""

    def __init__(self, options=None):
        options = dict(options or {})
        self.mode = "sample" if options.get("mode") == "sample" else "full"
        tier = options.get("model_tier")
        self.model_tier = tier if tier in model.TIERS else config_default_tier()
        self.sample_pages = max(1, min(SAMPLE_PAGES_MAX,
                                       int(options.get("sample_pages")
                                           or SAMPLE_PAGES_DEFAULT)))
        self.cost_cap_usd = _clamp_cap(options.get("cost_cap_usd"))
        self.include_report_page = options.get("include_report_page", True) is not False
        self.show_cost_in_report = bool(options.get("show_cost_in_report"))
        self.replace_existing_epub = bool(options.get("replace_existing_epub"))

    def to_dict(self):
        return {"mode": self.mode, "model_tier": self.model_tier,
                "sample_pages": self.sample_pages, "cost_cap_usd": self.cost_cap_usd,
                "include_report_page": self.include_report_page,
                "show_cost_in_report": self.show_cost_in_report,
                "replace_existing_epub": self.replace_existing_epub}


def config_default_tier():
    tier = getattr(config, "config_reflow_default_tier", None)
    return tier if tier in model.TIERS else model.DEFAULT_TIER


def hard_cap_usd():
    """The administrator's ceiling. A per-job cap may be lower, never higher."""
    try:
        cap = float(getattr(config, "config_reflow_hard_cap_usd", 0) or 0)
    except (TypeError, ValueError):
        cap = 0.0
    return cap if cap > 0 else 5.0


def _clamp_cap(value):
    try:
        cap = float(value or 0)
    except (TypeError, ValueError):
        cap = 0.0
    ceiling = hard_cap_usd()
    if cap <= 0:
        return ceiling
    return min(cap, ceiling)


def make_client(tier):
    """The model client for a job, or one that is configured not to spend."""
    return model.OpenRouterClient(api_key=config.resolved_openrouter_key() or None,
                                  tier=tier)


class TaskReflowPdf(CalibreTask):
    #: The API filters the worker queue on this rather than on the class, so a
    #: "is another conversion of this book running?" check cannot be defeated by
    #: an import cycle making two copies of the class object.
    is_reflow = True

    def __init__(self, book_id, user_id, options=None, task_message=None):
        super(TaskReflowPdf, self).__init__(
            task_message or N_("Reflow: reading the PDF"))
        # WorkerThread.cancel_tasks_for_book() matches on this attribute, so a book
        # deleted while its conversion is queued takes the job with it.
        self.book_id = int(book_id)
        self.user_id = user_id
        self.options = options if isinstance(options, ReflowOptions) \
            else ReflowOptions(options)
        self.job_id = uuid.uuid4().hex[:16]
        self.results = {"job_id": self.job_id, "spend_usd": 0.0}

    @property
    def name(self):
        return N_("Reflow")

    def __str__(self):
        return "Reflow book %d (%s)" % (self.book_id, self.options.mode)

    @property
    def is_cancellable(self):
        return True

    @property
    def cancelled(self):
        return self.stat in (STAT_ENDED, STAT_CANCELLED)

    # ---------------------------------------------------------------------- run

    def run(self, worker_thread):
        try:
            self._run(worker_thread)
        finally:
            # The book's admission reservation ends with the task, however it ends
            # -- success, failure, or cancel. A task cancelled while still queued
            # never reaches this method; its entry is evicted by the next admission
            # attempt, which reads the task's terminal state (admission.reserve).
            admission.release(self.book_id, self)

    def _run(self, worker_thread):
        import pymupdf

        local_db = db.CalibreDB(expire_on_commit=False, init=True)
        ledger = None
        try:
            book = local_db.get_book(self.book_id)
            if book is None:
                return self._handleError("Book %d is not in the library" % self.book_id)
            source = self._pdf_path(local_db, book)
            if source is None:
                return self._handleError("This book has no PDF to convert")
            if self.options.mode == "full" and not self.options.replace_existing_epub \
                    and local_db.get_book_format(self.book_id, "EPUB"):
                return self._handleError(
                    "This book already has an EPUB. Choose 'replace the existing "
                    "EPUB' if you want Reflow to overwrite it.")

            self.results["title"] = book.title
            ledger = ledger_mod.Ledger(
                os.path.join(reflow_dir("jobs", str(self.book_id)),
                             "%s.jsonl" % self.job_id),
                cap_usd=self.options.cost_cap_usd, job_id=self.job_id)
            cache = pipeline.PageCache(reflow_dir("cache"))
            client = make_client(self.options.model_tier)

            document = pymupdf.open(source)
            try:
                ledger.record({"kind": "job", "event": "start", "user_id": self.user_id,
                               "mode": self.options.mode, "title": book.title,
                               "tier": self.options.model_tier,
                               "cap_usd": self.options.cost_cap_usd,
                               "pages": document.page_count})
                result = self._convert(document, client, ledger, cache)
                if self.cancelled:
                    # The pages already paid for stay in the cache, so restarting is
                    # cheap — but a conversion nobody waited for does not become the
                    # book. Cancel means cancel, not "file whatever was ready".
                    ledger.record({"kind": "job", "event": "finish",
                                   "status": "cancelled"})
                    self.message = ("cancelled after %d of %d pages · $%.2f"
                                    % (result.pages_done, len(result.routed),
                                       result.spend_usd))
                    return self._finish_cancelled()
                built = self._write_epub(document, result, ledger, client, book,
                                         local_db)
            finally:
                document.close()

            self.results.update(built)
            self.results["spend_usd"] = round(result.spend_usd, 6)
            self.message = self._summary(result)
            ledger.record({"kind": "job", "event": "finish",
                           "status": STOP_STATUS.get(result.stopped, "done")})
            self._handleSuccess()
        except Exception as exc:                                  # noqa: BLE001
            log.error_or_exception(exc)
            if ledger is not None:
                try:
                    ledger.record({"kind": "job", "event": "finish", "status": "failed",
                                   "error": str(exc)[:500]})
                except Exception:                                 # pragma: no cover
                    pass
            self._handleError(str(exc))
        finally:
            try:
                local_db.session.close()
            except Exception:                                     # pragma: no cover
                pass

    # ------------------------------------------------------------------ stages

    def _convert(self, document, client, ledger, cache):
        pages = None
        if self.options.mode == "sample":
            pages = self._sample_pages(document)
        return pipeline.run(document, client=client, ledger=ledger, cache=cache,
                            page_numbers=pages, progress=self._on_progress,
                            should_stop=lambda: self.cancelled)

    def _sample_pages(self, document):
        """A sample of the body, not of the front matter.

        Reading the whole book to find where the body starts would cost the user the
        wait they are trying to avoid, so the first pass is over a short window and
        the sample runs on from wherever that window says the book begins.
        """
        window = min(document.page_count, self.options.sample_pages * 3)
        head = pipeline.deterministic_window(document, window)
        start = pipeline.first_body_page(head)
        return list(range(start, min(document.page_count,
                                     start + self.options.sample_pages)))

    def _write_epub(self, document, result, ledger, client, book, local_db):
        payload = report.numbers(result, ledger=ledger, client=client)
        problems = report.check_completion(payload, ledger)
        if problems:
            # G4: the report and the ledger have to agree, and a report nobody can
            # trust is worse than no report at all.
            log.error("reflow: report does not match the ledger: %s", problems)
            raise ValueError("the conversion report does not match the job ledger: %s"
                             % "; ".join(problems))

        target = self._target_path(book, local_db)
        page = None
        if self.options.include_report_page:
            def page(links=None, losses=(), _payload=payload):
                return report.about_page(_payload,
                                         show_cost=self.options.show_cost_in_report,
                                         links=links, losses=losses)
        built = build_epub.build(result.book, target, page_html=result.page_html,
                                 metadata=_metadata(book), doc=document,
                                 report_html=page, sidecar=payload)
        for warning in built.warnings:
            # The reader is told the same thing in their own book, on the report
            # page; this is the terser half, for whoever has to find out why.
            log.warning("reflow: %s", warning)
        problems = build_epub.validate(built.path)
        if problems:
            # A document a reader's parser stops on is not a chapter with a mistake
            # in it; it is a chapter the reader never sees. Filing that as the book
            # is worse than failing, and it costs the user nothing to fail: every
            # page a model was paid for is in the cache, so a second run after a fix
            # buys nothing. Same stance as G4 above -- what cannot be trusted does
            # not ship.
            log.error("reflow: the EPUB just built does not open: %s", problems)
            try:
                os.remove(built.path)
            except OSError:                                       # pragma: no cover
                pass
            raise ValueError("the EPUB Reflow built is not a book a reader can "
                             "open: %s" % "; ".join(problems[:3]))
        if self.options.mode == "sample":
            return {"sample": os.path.basename(built.path), "path": built.path,
                    "report": payload}
        self._add_format(local_db, book, built)
        return {"path": built.path, "report": payload}

    def _target_path(self, book, local_db):
        if self.options.mode == "sample":
            return sample_path(self.user_id, self.job_id)
        data = local_db.get_book_format(self.book_id, "PDF")
        folder = os.path.join(config.get_book_path(), book.path)
        return os.path.join(folder, "%s.epub" % data.name)

    def _add_format(self, local_db, book, built):
        """The same shape ``TaskConvert`` uses: one Data row, one commit."""
        size = os.path.getsize(built.path)
        row = local_db.session.query(db.Data) \
            .filter(db.Data.book == self.book_id) \
            .filter(db.Data.format == "EPUB").one_or_none()
        if row is None:
            row = db.Data(name=os.path.splitext(os.path.basename(built.path))[0],
                          book_format="EPUB", book=self.book_id,
                          uncompressed_size=size)
        else:
            row.uncompressed_size = size
        try:
            local_db.session.merge(row)
            helper.mark_book_format_materialised(book, "EPUB")
            local_db.session.commit()
        except SQLAlchemyError as exc:
            local_db.session.rollback()
            log.error("reflow: could not record the EPUB: %s", exc)
            raise

    # ----------------------------------------------------------------- progress

    def _on_progress(self, event):
        pages = event.pages or 0
        if pages:
            self.progress = min(1.0, max(0.0, event.fraction))
        spend = " · $%.2f" % event.spend_usd if event.spend_usd else ""
        self.message = "%s%s" % (event.message or event.stage, spend)

    def _summary(self, result):
        if result.stopped == "cost_cap":
            return ("stopped at the cost cap after %d pages · $%.2f"
                    % (result.pages_done, result.spend_usd))
        if result.stopped == "model_errors":
            return ("the model service stopped answering after %d of %d pages · $%.2f"
                    % (result.pages_done, len(result.routed), result.spend_usd))
        if result.routed:
            return ("%d pages reviewed of %d · $%.2f"
                    % (result.pages_done, len(result.routed), result.spend_usd))
        return "%d pages, no page needed a model" % result.pages

    def _finish_cancelled(self):
        self.stat = STAT_ENDED
        self.progress = 1
        self.done_event.set()
        return None

    def _pdf_path(self, local_db, book):
        data = local_db.get_book_format(self.book_id, "PDF")
        if data is None:
            return None
        path = os.path.join(config.get_book_path(), book.path, data.name + ".pdf")
        return path if os.path.isfile(path) else None


def _metadata(book):
    return {"title": book.title,
            "authors": [author.name for author in (book.authors or [])],
            "language": (book.languages[0].lang_code if book.languages else "en"),
            "publisher": (book.publishers[0].name if book.publishers else ""),
            "description": (book.comments[0].text if book.comments else ""),
            "tags": [tag.name for tag in (book.tags or [])]}


def cleanup_samples(max_age_days=7):
    """A sample is a preview, not a library. Old ones go."""
    root = os.path.join(REFLOW_DIR, "samples")
    if not os.path.isdir(root):
        return 0
    import time

    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for directory, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:                                       # pragma: no cover
                continue
    return removed


__all__ = ["TaskReflowPdf", "ReflowOptions", "sample_path", "cleanup_samples",
           "hard_cap_usd", "config_default_tier", "make_client", "reflow_dir",
           "SAMPLE_PAGES_DEFAULT", "SAMPLE_PAGES_MAX"]
