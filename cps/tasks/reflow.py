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
import hashlib
import shutil
import math

from flask_babel import lazy_gettext as N_
from sqlalchemy.exc import SQLAlchemyError

from cps import config, db, helper, logger
from cps.constants import REFLOW_DIR
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED, \
    STAT_STARTED, STAT_WAITING
from cps.services.reflow import admission, build_epub, extract, ledger as ledger_mod, \
    model, ocr, pipeline, publication, report, structural_pipeline, typed_model

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
#: ``billing_uncertain`` ends the same way but for a different cause: a dispatched
#: request's billing could not be proven either way, and its bound stays held.
STOP_STATUS = {"cost_cap": "capped", "model_errors": "incomplete", "model_rejections": "incomplete",
               "billing_uncertain": "billing_unknown", "prior_request_pending": "billing_unknown",
               "quality_gate": "limited", "not_configured": "limited", "estimate_stale": "limited", "source_changed": "incomplete", "billing_bound": "incomplete", "route_mismatch": "incomplete"}


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
        self.review_mode = "source_verified" if options.get("review_mode")=="source_verified" else "deterministic"
        tier = options.get("model_tier")
        self.model_tier = tier if tier in model.TIERS else config_default_tier()
        self.sample_pages = max(1, min(SAMPLE_PAGES_MAX,
                                       int(options.get("sample_pages")
                                           or SAMPLE_PAGES_DEFAULT)))
        self.cost_cap_usd = _clamp_cap(options.get("cost_cap_usd")) if self.review_mode=="source_verified" else 0.0
        self.include_report_page = options.get("include_report_page", True) is not False
        self.show_cost_in_report = bool(options.get("show_cost_in_report"))
        self.replace_existing_epub = bool(options.get("replace_existing_epub"))
        # Source recovery: local OCR of picture-only pages and demonstrably
        # damaged scan layers. "auto" recovers both; "textless" only the pages
        # with no text at all (a damaged layer is kept as printed); "off" keeps
        # everything native (image-only pages stay a labelled facsimile).
        recovery = options.get("source_recovery") or "auto"
        self.source_recovery = recovery if recovery in ("auto", "textless", "off") \
            else "auto"
        language = str(options.get("ocr_language") or "eng").strip() or "eng"
        self.ocr_language = language[:64]

    def to_dict(self):
        return {"mode": self.mode, "review_mode":self.review_mode,"model_tier": self.model_tier,
                "sample_pages": self.sample_pages, "cost_cap_usd": self.cost_cap_usd,
                "include_report_page": self.include_report_page,
                "show_cost_in_report": self.show_cost_in_report,
                "replace_existing_epub": self.replace_existing_epub,
                "source_recovery": self.source_recovery,
                "ocr_language": self.ocr_language}


def config_default_tier():
    tier = getattr(config, "config_reflow_default_tier", None)
    return tier if tier in model.TIERS else model.DEFAULT_TIER


def hard_cap_usd():
    """The administrator's ceiling. A per-job cap may be lower, never higher."""
    try:
        cap = float(getattr(config, "config_reflow_hard_cap_usd", 0) or 0)
    except (TypeError, ValueError):
        cap = 0.0
    return cap if math.isfinite(cap) and cap > 0 else 5.0


#: The largest PDF one conversion reads when the administrator has not said
#: otherwise (Finding 3 of the 7daffa5 retest: nothing bounded a whole-book job).
#: 2,000 pages is ~2.8x the longest book in the measured corpus (716 pages);
#: 500 MB holds a long greyscale 300-dpi scan and refuses a multi-gigabyte one.
MAX_PAGES_DEFAULT = 2000
MAX_PDF_MB_DEFAULT = 500


def _limit(name, default):
    try:
        value = int(getattr(config, name, 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else default


def max_pages():
    """The administrator's limit on one PDF's length, in pages."""
    return _limit("config_reflow_max_pages", MAX_PAGES_DEFAULT)


def max_pdf_mb():
    """The administrator's limit on one PDF's size, in megabytes (MiB)."""
    return _limit("config_reflow_max_pdf_mb", MAX_PDF_MB_DEFAULT)


def pdf_size_mb(size):
    """A file size in MB rounded UP to one decimal: a PDF just over the limit is
    never shown as exactly the limit."""
    return math.ceil(size * 10 / 1048576.0) / 10


def _clamp_cap(value):
    try:
        cap = float(value or 0)
    except (TypeError, ValueError):
        cap = 0.0
    ceiling = hard_cap_usd()
    if not math.isfinite(cap) or cap <= 0:
        return ceiling
    return min(cap, ceiling)


def make_client(review_mode):
    """Only explicit current two-stage review can enable the conditional route."""
    return structural_pipeline.TwoStageClient(
        (config.resolved_openrouter_key() or None) if review_mode=='source_verified' else None,
        enabled=review_mode=='source_verified' and typed_model.QUALITY_RELEASED)


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

            # The administrator's limits are checked again here, before a byte
            # of the PDF is read: the API refuses an oversized PDF before queueing
            # it, but a job may have been queued before the limits were lowered,
            # or the PDF replaced after (Finding 3).
            size = os.path.getsize(source)
            if size > max_pdf_mb() * 1048576:
                return self._handleError(
                    "This PDF is %.1f MB, over the limit of %d MB an administrator set "
                    "for one conversion." % (pdf_size_mb(size), max_pdf_mb()))
            if getattr(self.options,"source_sha256",None) and extract.document_fingerprint(source)!=self.options.source_sha256:
                raise ValueError("Source changed after consent; prepare the current PDF again.")
            self.results["title"] = book.title
            ledger = ledger_mod.Ledger(
                os.path.join(reflow_dir("jobs", str(self.book_id)),
                             "%s.jsonl" % self.job_id),
                cap_usd=self.options.cost_cap_usd, job_id=self.job_id)
            cache = pipeline.PageCache(reflow_dir("cache"))
            client = make_client(self.options.review_mode)

            document = pymupdf.open(source)
            try:
                if document.page_count > max_pages():
                    return self._handleError(
                        "This PDF has %d pages, over the limit of %d an administrator set "
                        "for one conversion." % (document.page_count, max_pages()))
                if getattr(self.options,"source_sha256",None) and extract.document_fingerprint(document)!=self.options.source_sha256:
                    raise ValueError("Source changed after consent; prepare the current PDF again.")
                ledger.record({"kind": "job", "event": "start", "user_id": self.user_id,
                               "mode": self.options.mode, "title": book.title,
                               "tier": client.describe().get("tier",self.options.model_tier),
                               "cap_usd": self.options.cost_cap_usd,
                               "pages": document.page_count,
                               # The record is tied to the exact file it spent on.
                               "source": extract.document_fingerprint(document)})
                result = self._convert(document, client, ledger, cache)
                if result.recovery is not None:
                    summary = result.recovery.summary()
                    summary.pop("pages", None)
                    ledger.record(dict(kind="recovery", **summary))
                if self.cancelled:
                    # The pages already paid for stay in the cache, so restarting is
                    # cheap — but a conversion nobody waited for does not become the
                    # book. Cancel means cancel, not "file whatever was ready".
                    ledger.record({"kind": "job", "event": "finish",
                                   "status": "cancelled"})
                    pending = ledger.pending_usd()
                    held = (" · up to $%.4f unresolved" % pending) if pending else ""
                    self.message = ("cancelled after %d of %d pages · $%.2f%s"
                                    % (result.pages_done, len(result.routed),
                                       result.spend_usd, held))
                    return self._finish_cancelled()
                built = self._write_epub(document, result, ledger, client, book,
                                         local_db)
            finally:
                document.close()

            self.results.update(built)
            self.results["spend_usd"] = result.spend_usd
            self.message = self._summary(result)
            ledger.record({"kind": "job", "event": "finish",
                           "status": STOP_STATUS.get(result.stopped, "done")})
            self._handleSuccess()
        except (ocr.OCRCancelled, build_epub.BuildCancelled, model.AttemptCancelled):
            # The user stopped the recognition stage: nothing was filed, the
            # original is untouched, and the identity cache keeps what was
            # already recognized, so a resume spends nothing twice.
            if ledger is not None:
                ledger.record({"kind": "job", "event": "finish",
                               "status": "cancelled"})
            self.message = ("cancelled during source recovery or EPUB assembly; the original is "
                            "unchanged and compatible recovery may resume")
            return self._finish_cancelled()
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
        recovery_opts = {
            "mode": self.options.source_recovery,
            "language": self.options.ocr_language,
            "cache_dir": reflow_dir("ocr-cache"),
            "scratch_dir": reflow_dir("ocr-scratch"),
        }
        from ..services.reflow.structural_quote import consent_observer
        quote=getattr(self.options,"consent_quote",None)
        observer=consent_observer(quote,document) if quote is not None else None
        sample=self.options.mode=='sample'
        # A sample reads the front of the book, not all of it (N2): only a paid
        # sample keeps the complete context, because its consent quote was
        # measured against it (see structural_pipeline.run_structural).
        result = structural_pipeline.run_structural(document,client=client,ledger=ledger,cache=cache,
                            sample_count=self.options.sample_pages if sample else None,
                            sample_context=sample and observer is None,
                            progress=self._on_progress,should_stop=lambda:self.cancelled,
                            recovery_opts=recovery_opts,prepared_observer=observer,measure_eligibility=self.options.review_mode=="source_verified")
        result.structural["review_mode"]=self.options.review_mode
        # The task/report ledger records the same final user-facing scope.
        ledger.record({"kind":"structural_summary","summary":result.structural})
        return result

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
        if self.options.mode=='full':publication.relative(config.get_book_path(),target)
        page = None
        if self.options.include_report_page:
            def page(links=None, losses=(), _payload=payload):
                return report.about_page(_payload,
                                         show_cost=self.options.show_cost_in_report,
                                         links=links, losses=losses)
        # The staging folder is named in the job's journal before it exists, so a
        # process that dies anywhere from here on -- mid-build is minutes on a long
        # book -- leaves nothing the next recovery pass cannot find and remove.
        # Before, such a folder stayed hidden in the book's folder forever (N4).
        full=self.options.mode=='full'
        staging=os.path.join(os.path.dirname(target),'.reflow-%s-staging'%self.job_id)
        staging_path=os.path.relpath(staging,config.get_book_path() if full else REFLOW_DIR)
        ledger.record(dict(kind='staging',event='created',root='library' if full else 'reflow',
                           path=staging_path),durable=True)
        os.mkdir(staging,0o700)
        candidate=os.path.join(staging,'candidate.epub')
        publication_prepared=False
        try:
            built = build_epub.build(result.book, candidate, page_html=result.page_html,
                                     metadata=_metadata(book), doc=document,
                                     report_html=page,
                                     sidecar=report.sidecar(
                                         payload, show_cost=self.options.show_cost_in_report),
                                     source_pages=getattr(result,'source_pages',None),
                                     operation_plans=getattr(result,'operation_plans',None),
                                     figure_transform=(result.recovery.figure_rect
                                                       if result.recovery else None),
                                     should_stop=lambda: self.cancelled,
                                     evidence_progress=lambda done, total: self._on_progress(
                                         pipeline.Progress("evidence", page=done, pages=total,
                                             spend_usd=result.spend_usd,
                                             message="preserving original evidence %d/%d" % (done, total))))
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
            digest=hashlib.sha256()
            with open(built.path,'rb') as handle:
                for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
            artifact={'sha256':digest.hexdigest(),'bytes':os.path.getsize(built.path)}
            if self.cancelled:
                raise build_epub.BuildCancelled('cancelled before publication')
            if extract.document_fingerprint(self._pdf_path(local_db,book))!=result.fingerprint:
                raise ValueError("Source changed during conversion; no EPUB was published.")
            if self.options.mode == "sample":
                os.replace(built.path,target)
                ledger.record(dict(kind='artifact',**artifact))
                return dict(artifact,sample=os.path.basename(target),path=target,report=payload)
            with publication.lock(os.path.join(REFLOW_DIR,'publication-locks'),target):
                if os.path.exists(target) and not self.options.replace_existing_epub:
                    raise ValueError('An EPUB already exists; explicit replacement is required.')
                previous_format=_format_state(local_db,self.book_id)
                desired_format=dict(name=os.path.splitext(os.path.basename(target))[0],size=artifact['bytes'])
                record=publication.prepare(ledger,config.get_book_path(),self.book_id,book.path,
                    self._pdf_path(local_db,book),target,staging,previous_format,desired_format,expected_source=result.fingerprint)
                publication_prepared=True
                os.replace(built.path,target)
                publication.sync(target);publication.sync(os.path.dirname(target))
                built.path=target
                try:
                    self._add_format(local_db,book,built)
                    publication.committed(ledger)
                except Exception:
                    # Re-read committed metadata: a commit may have succeeded even
                    # when its caller did not receive a successful return.
                    publication.reconcile(ledger,config.get_book_path(),record,
                        _format_state(local_db,self.book_id),book.path,self._pdf_path(local_db,book))
                    raise
                publication.reconcile(ledger,config.get_book_path(),record,
                    _format_state(local_db,self.book_id),book.path,self._pdf_path(local_db,book))
                return dict(artifact,path=target,report=payload)
        finally:
            # A prepared publication owns durable recovery evidence. Never destroy
            # its backup on an unhandled failure or conflicting later mutation.
            if not publication_prepared:
                shutil.rmtree(staging,ignore_errors=True)
            if not os.path.lexists(staging):
                try:ledger.record(dict(kind='staging',event='removed',path=staging_path))
                except OSError:pass                                   # pragma: no cover

    def _target_path(self, book, local_db):
        if self.options.mode == "sample":
            return sample_path(self.user_id, self.job_id)
        data = (local_db.get_book_format(self.book_id, "EPUB")
                or local_db.get_book_format(self.book_id, "PDF"))
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
            row.name = os.path.splitext(os.path.basename(built.path))[0]
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
        if hasattr(result,'structural'):
            counts=result.structural
            return ("%d pages prepared · %d formatting changes approved · %d eligible pages not reviewed · $%.6f confirmed · up to $%.6f unresolved"
                % (result.pages,counts['approved_operations'],counts['unreviewed'],result.spend_usd,result.pending_usd))
        if result.stopped == "billing_uncertain":
            return ("stopped after %d of %d pages: a model answer was lost after "
                    "dispatch and its billing could not be confirmed · $%.2f "
                    "confirmed · up to $%.4f unresolved"
                    % (result.pages_done, len(result.routed), result.spend_usd,
                       result.pending_usd))
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


#: How a conversion the process did not survive is filed. ``cancelled`` is the
#: user's act and ``failed`` is the job's own error; an interrupted run is
#: neither -- the app stopped underneath it, mid-conversion.
INTERRUPTED_STATUS = "interrupted"


def _format_state(local_db,book_id):
    row=local_db.session.query(db.Data).filter(db.Data.book==book_id).filter(db.Data.format=='EPUB').one_or_none()
    return None if row is None else dict(name=row.name,size=int(row.uncompressed_size))


#: What the jobs list says about a publication recovery could not settle: first
#: with every file kept where it was, then -- on a later pass, if nothing has
#: resolved it -- with that evidence moved out of the book's folder, exactly.
REVIEW_IN_PLACE = "Publication recovery needs review; existing files were preserved."
REVIEW_MOVED = ("Publication recovery needs review; the files it preserved were moved out of "
                "the book's folder, unchanged, into Reflow's publication-conflicts folder.")


def _conflicted_before(led):
    """True once an earlier recovery pass recorded a conflict on this publication."""
    events=led.entries('publication')
    start=max((i for i,e in enumerate(events) if e.get('event')=='prepared'),default=-1)
    return any(e.get('event')=='conflict' for e in events[start+1:])


def _recover_publication(led):
    """Settle one job's publication journal.

    Returns True when there is nothing left to do, None when a live publication
    holds the target's lock (try again on the next pass), or the error text the
    job is filed with when a person has to look.

    A conflict is recorded and every file is kept in place, as before. When the
    same journal is still conflicted on a later pass -- the library did not
    return to a state recovery can prove -- its staging evidence is copied out of
    the book's folder, verified, closed in the journal and only then removed
    (N4): a hidden ``.reflow-*`` folder holding two EPUBs no longer lives in the
    library forever. Only a :class:`publication.PublicationConflict` is closed
    this way; an I/O or database error is never proof of anything and keeps
    being retried.
    """
    record=publication.pending(led)
    if record is None:return True
    local_db=None
    try:
        target=publication.resolve(config.get_book_path(),record['target'])
        with publication.lock(os.path.join(REFLOW_DIR,'publication-locks'),target,blocking=False) as acquired:
            if not acquired:return None
            # A worker may have completed while startup waited for its lock.
            led._entries=ledger_mod.Ledger(led.path,led.cap_usd,led.job_id).entries()
            record=publication.pending(led)
            if record is None:return True
            book=None
            try:
                book_id=int(record['book_id'])
                if str(book_id)!=os.path.basename(os.path.dirname(led.path)):
                    raise publication.PublicationConflict('publication ledger book identity differs')
                local_db=db.CalibreDB(expire_on_commit=False,init=True)
                book=local_db.get_book(book_id)
                data=local_db.get_book_format(book_id,'PDF')
                if book is None or data is None:raise publication.PublicationConflict('publication source book is unavailable')
                source=os.path.join(config.get_book_path(),book.path,data.name+'.pdf')
                publication.reconcile(led,config.get_book_path(),record,_format_state(local_db,book_id),book.path,source)
                return True
            except publication.PublicationConflict as exc:
                if not _conflicted_before(led):
                    raise
                evidence=publication.close_conflict(
                    led,config.get_book_path(),record,
                    os.path.join(REFLOW_DIR,'publication-conflicts',
                                 os.path.basename(os.path.dirname(led.path)),led.job_id),
                    str(exc),current_book_path=getattr(book,'path',None))
                log.error('reflow publication conflict for job %s still stands (%s); its files '
                          'were preserved in %s',led.job_id,exc,evidence or 'no staging folder remained')
                return REVIEW_MOVED
    except (OSError,ValueError,KeyError,TypeError,SQLAlchemyError) as exc:
        reason=str(exc)
        if not any(e.get('event')=='conflict' and e.get('reason')==reason for e in led.entries('publication')):
            led.record(dict(kind='publication',event='conflict',reason=reason),durable=True)
        log.error('reflow publication recovery needs review for job %s: %s',led.job_id,reason)
        return REVIEW_IN_PLACE
    finally:
        if local_db is not None:local_db.session.close()


def _remove_orphaned_staging(led):
    """Remove every staging folder this job's journal created and nothing removed.

    Called only for a job no live task owns and whose publication is settled, so
    the folder holds nothing anyone needs: a partly built candidate, or a copy
    of an EPUB that is still in place. Only a real folder named for this job,
    inside its root and reached without symlinks, is ever removed. Returns the
    number removed.
    """
    removed={e.get('path') for e in led.entries('staging') if e.get('event')=='removed'}
    count=0
    for entry in led.entries('staging'):
        if entry.get('event')!='created' or entry.get('path') in removed:
            continue
        root=config.get_book_path() if entry.get('root')=='library' else REFLOW_DIR
        if not root:
            continue
        path=publication.owned_staging(root,entry.get('path'),led.job_id)
        if path is None:
            continue
        shutil.rmtree(path)
        publication.sync(os.path.dirname(path))
        led.record(dict(kind='staging',event='removed',path=entry['path'],recovered=True),durable=True)
        removed.add(entry['path'])
        count+=1
    return count


def recover_interrupted_jobs(worker=None):
    """Settle the record of every conversion its process did not finish.

    A reflow job's durable state is its ledger, and a ``start`` with no
    ``finish`` reads as "running" forever. The other half of that answer -- the
    worker's queue -- is in-memory, so the process that comes up after a
    restart inherits ledgers for jobs nobody is running, and the jobs list
    would keep advertising them as live conversions with no way past. Called
    once at startup, before the server takes requests, so no task of this
    process can own a job yet. ``worker`` is accepted all the same so the
    ownership guard holds if the sweep is ever run late: a job a live task of
    this process owns is never terminalized.

    Recovery first reconciles prepared file publication against current library
    identities and format metadata, then appends the terminal job record. Confirmed spend and
    unresolved holds are the job's own history: no reservation is released,
    reconciled, or zeroed because the process that wrote it is gone. A retry is
    a new job that reuses the page cache, never a silent resumption of this
    one's provider work.

    Idempotent: the appended ``finish`` is the marker the next sweep reads, so
    a repeated run -- or a second process racing boot -- adds nothing. Returns
    the recovered job ids, for the startup log line.
    """
    root = os.path.join(REFLOW_DIR, "jobs")
    if not os.path.isdir(root):
        return []
    active_ids = set()
    if worker is not None:
        for __, __, __, task, __ in worker.tasks:
            if not getattr(task, "is_reflow", False):
                continue
            if task.stat not in (STAT_WAITING, STAT_STARTED):
                continue
            job_id = getattr(task, "job_id", None)
            if job_id:
                active_ids.add(job_id)
    recovered = []
    for book_id in sorted(os.listdir(root)):
        directory = os.path.join(root, book_id)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".jsonl"):
                continue
            job_id = name[:-len(".jsonl")]
            led = ledger_mod.Ledger(os.path.join(directory, name),
                                    cap_usd=0.0, job_id=job_id)
            if job_id in active_ids:
                continue
            settled=_recover_publication(led)
            if settled is None:
                continue
            if settled is not True:
                if led.job().get('error')!=settled:
                    led.record({"kind":"job","event":"finish","status":"failed","error":settled})
                    recovered.append(job_id)
                continue
            try:
                _remove_orphaned_staging(led)
            except (OSError,publication.PublicationConflict) as exc:
                log.error('reflow: could not remove the staging folder of job %s: %s',job_id,exc)
            if led.job().get("status") != "running":
                continue
            led.record({"kind": "job", "event": "finish",
                        "status": INTERRUPTED_STATUS,
                        "error": "The application restarted while this "
                                 "conversion was running."})
            recovered.append(job_id)
    return recovered


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
           "recover_interrupted_jobs", "hard_cap_usd", "config_default_tier",
           "max_pages", "max_pdf_mb", "pdf_size_mb", "MAX_PAGES_DEFAULT", "MAX_PDF_MB_DEFAULT",
           "make_client", "reflow_dir", "INTERRUPTED_STATUS",
           "SAMPLE_PAGES_DEFAULT", "SAMPLE_PAGES_MAX"]
