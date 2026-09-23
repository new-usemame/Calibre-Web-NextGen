# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reflow endpoints for /api/v1 — the price, the consent, and the job (SPEC §4).

This is the boundary where a user authorises somebody's money to be spent, so three
things are deliberate and worth stating.

*The fast assessment is source-only.* Full current source preparation happens
in a bounded, cancellable local process. Typed reservation ceilings use the exact
shared proposer and verifier request serializers, never the legacy router prices.

*Consent names the current route and source.* Deterministic conversion needs no
key or paid cap. Optional review requires a current owner-scoped prepared quote,
explicit two-stage consent and a finite cap. A smaller cap permits partial review;
remaining pages retain complete deterministic output.

*A sample belongs to whoever paid for it.* The download path is built from
``current_user.id`` and a validated job id; no part of it comes from the URL, so
there is no traversal to defend against — the shape refuses rather than the filter.

SECURITY-REVIEW: ``/admin/reflow`` is write-only for the key. GET returns
``configured`` and the source of the key, never the value; POST accepts a new key
or an explicit clear and echoes neither back. The key never reaches a log line, a
task message, or a repr (``OpenRouterClient.__repr__`` exists for that reason).
"""

import hashlib
import json
import os
import re
import time
import uuid
import math
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import jsonify, request, send_file

try:  # pragma: no cover - selected by the production runtime
    from gevent.threadpool import ThreadPool as _GeventThreadPool
    _HAVE_GEVENT_POOL = True
except ImportError:  # pragma: no cover - unit/minimal environments
    _GeventThreadPool = None
    _HAVE_GEVENT_POOL = False

from . import api_v1, log
from .. import calibre_db, config
from ..constants import REFLOW_DIR
from ..cw_login import current_user
from ..services.reflow import (admission, build_epub, extract,
                               ledger as ledger_mod, model, ocr, pipeline, structural_quote, typed_model, quote_preparation)
from ..services.worker import STAT_STARTED, STAT_WAITING, WorkerThread
from ..tasks import reflow as tasks_reflow
from ..usermanagement import login_required_if_no_ano

#: A job id is a hex token the task made. Anything else is not one, and this is the
#: only thing a URL is allowed to contribute to a filesystem path here.
_JOB_ID = re.compile(r"^[0-9a-f]{6,32}$")

#: The two states that mean "this book is being converted right now". The worker
#: keeps finished tasks in its list until somebody clears them, and a page that
#: read those as active would sit on a full progress bar with its start button
#: disabled, while POST /reflow -- which looks at exactly these two states --
#: would have accepted the next conversion. What a finished job did is in the
#: ledger, which is what ``items`` is read from.
_ACTIVE_STATS = {STAT_WAITING: "waiting", STAT_STARTED: "running"}

#: A survey is CPU-bound PyMuPDF work. Two at a time keeps a burst of book pages
#: from turning the box over to estimating; a third caller waits its turn.
_SURVEY_POOL_SIZE = 2
if _HAVE_GEVENT_POOL:                                             # pragma: no cover
    _SURVEY_POOL = _GeventThreadPool(_SURVEY_POOL_SIZE)
else:
    _SURVEY_POOL = ThreadPoolExecutor(max_workers=_SURVEY_POOL_SIZE,
                                      thread_name_prefix="reflow-estimate")


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_edit():
    """Signed in, not the guest, and allowed to edit books.

    "Allowed to edit" is the predicate the rest of the editing surface uses
    (``editbooks.edit_required``, ``api/duplicates``): the edit role OR the admin
    role. A conversion files a format onto a book, which an administrator may
    already do by hand, so refusing one here only made Reflow the one place an
    admin without the edit bit could not reach. Library restrictions still apply
    through ``get_filtered_book``.
    """
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not (current_user.role_edit() or current_user.role_admin()):
        return _err("forbidden", "Edit permission is required to convert a book", 403)
    return None


def _require_admin():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _err("forbidden", "Admin permission required", 403)
    return None


# ── the book and its files ───────────────────────────────────────────────────

def _format_path(book, fmt):
    """Where the library says this format is, when it is really there."""
    data = calibre_db.get_book_format(book.id, fmt)
    if data is None:
        return None
    path = os.path.join(config.get_book_path(), book.path,
                        "%s.%s" % (data.name, fmt.lower()))
    return path if os.path.isfile(path) else None


def _book_or_error(book_id):
    book = calibre_db.get_filtered_book(book_id)
    if book is None:
        return None, _err("not_found", "Book not found", 404)
    return book, None


def _source_or_error(book_id):
    book, failure = _book_or_error(book_id)
    if failure:
        return None, None, failure
    source = _format_path(book, "PDF")
    if source is None:
        return None, None, _err("no_pdf", "This book has no PDF to convert", 404)
    return book, source, None


# ── the estimate, measured once per PDF ──────────────────────────────────────

def _survey_uncached(path):
    """The deterministic pass over a spread of pages. Seconds, not milliseconds."""
    import pymupdf

    document = pymupdf.open(path)
    try:
        return pipeline.survey(document)
    finally:
        document.close()


def _cache_key(path):
    """The file as it is now, priced by the converter as it is now.

    A rebuilt PDF is a different book to price -- and an upgraded converter, or a
    re-measured price table, is a different price for the same book. The PDF does not
    change when either of those does, so both go in the key: the quote is the figure a
    person authorises a payment against, and serving one this build would not have
    made is quoting them last release's money.

    Hashed with sha1 rather than ``hash()`` because the estimate cache outlives the
    process and PYTHONHASHSEED does not: a str hash is randomised per run, so every
    restart would miss its own cache.
    """
    stat = os.stat(path)
    priced_by = "\0".join((os.path.abspath(path), build_epub.CONVERTER_VERSION,
                           model.PRICE_TABLE_MEASURED))
    digest = hashlib.sha1(priced_by.encode("utf-8")).hexdigest()[:16]
    return "%s-%d-%d" % (digest, stat.st_size, int(stat.st_mtime))


def _cache_file(path):
    return os.path.join(REFLOW_DIR, "estimates", "%s.json" % _cache_key(path))


def _survey_cached(path):
    cached = _cache_file(path)
    try:
        with open(cached, "r", encoding="utf-8") as handle:
            quote = json.load(handle)
        quote["cached"] = True
        return quote
    except (IOError, OSError, ValueError):
        pass

    quote = _offload(path)
    quote["cached"] = False
    try:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        # Unique temporary name: two estimates of the same PDF on concurrent
        # requests share a cache file, and a fixed .tmp would let one writer's
        # rename pull the file from under the other.
        tmp = "%s.%s.tmp" % (cached, uuid.uuid4().hex)
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(quote, handle)
            os.replace(tmp, cached)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except (IOError, OSError):                                    # pragma: no cover
        log.warning("reflow: could not cache the estimate for %s", path)
    return quote


def _offload(path):
    """Run the survey off the request greenlet.

    CWNG runs gevent WITHOUT monkey.patch_all(), so a couple of seconds of PyMuPDF
    on the request greenlet parks every other request for that long. PyMuPDF drops
    the GIL for its own work, so a real worker thread genuinely runs alongside the
    hub — provided the wait is the gevent-aware one (``cps/services/cover_preview``
    documents the py-spy trace where the stdlib executor's wait blocked the loop).
    """
    if _HAVE_GEVENT_POOL:                                         # pragma: no cover
        return _SURVEY_POOL.apply(_survey_uncached, (path,))
    return _SURVEY_POOL.submit(_survey_uncached, path).result()


def _survey(path):
    """Indirection the tests replace; production always goes through the cache."""
    return _survey_cached(path)


def _target_usd():
    try:
        target = float(getattr(config, "config_reflow_target_usd", 0.5) or 0.5)
    except (TypeError, ValueError):
        target = 0.5
    return round(target, 4)


#: The engine probe costs two short subprocesses; a minute between checks is
#: fresh enough for a settings page and cheap enough for every page open.
_ENGINE_TTL = 60.0
_engine_checks = {}


def _ocr_engine_state(language):
    """Is the local text recognition usable for ``language`` — and if not, why.

    The detail sentence is the adapter's own: it names the missing component
    (the engine, the language data, or the orientation data), which is what an
    administrator has to act on. Never probed with secrets in reach (ocr._env).
    """
    now = time.monotonic()
    cached = _engine_checks.get(language)
    if cached and now - cached[0] < _ENGINE_TTL:
        return cached[1]
    try:
        __, version, __ = ocr._engine(language)
        state = {"available": True, "detail": "", "version": version}
    except ocr.OCRUnavailable as exc:
        state = {"available": False, "detail": str(exc), "version": ""}
    _engine_checks[language] = (now, state)
    return state


CONSENT_CONTRACT='source-review-1'
_PREPARATION_STORES={}
_PREPARATION_LOCK=threading.Lock()


def _source_options(body):
    recovery=body.get('source_recovery','auto');language=body.get('ocr_language','eng')
    if recovery not in ('auto','textless','off') or not isinstance(language,str) or len(language)>64:
        raise ValueError('invalid source recovery options')
    parts=language.split('+')
    if not 1<=len(parts)<=4 or any(not ocr.LANGUAGE.fullmatch(p) for p in parts):
        raise ValueError('invalid OCR language')
    return {'source_recovery':recovery,'ocr_language':language}


def _quote_store():
    root=os.path.abspath(REFLOW_DIR)
    with _PREPARATION_LOCK:
        if root not in _PREPARATION_STORES:
            _PREPARATION_STORES[root]=quote_preparation.PreparationStore(os.path.join(root,'typed-quotes'))
        return _PREPARATION_STORES[root]


def _quote_work(path,options,progress,stop,cache_root):
    return quote_preparation.measure_isolated(path,options,progress,stop,cache_root)


def _estimate_payload(book, source):
    # This fast assessment describes the source. Typed prices only come from
    # complete source preparation and the actual two-stage request serializers.
    quote=_survey(source);engine=_ocr_engine_state('eng')
    pages=int(quote.get('pages') or 0)
    return {
        'book_id':book.id,'title':book.title,'verdict':quote.get('verdict',''),
        'pages':pages,'text_layer':bool(quote.get('text_layer')),
        'source_sha256':extract.document_fingerprint(source),'consent_contract':CONSENT_CONTRACT,
        'existing_epub':calibre_db.get_book_format(book.id,'EPUB') is not None,
        'configured':bool(config.resolved_openrouter_key()),'hard_cap_usd':tasks_reflow.hard_cap_usd(),
        'sample_pages_default':tasks_reflow.SAMPLE_PAGES_DEFAULT,'sample_pages_max':tasks_reflow.SAMPLE_PAGES_MAX,
        'sample_suggested':pages>tasks_reflow.SAMPLE_PAGES_DEFAULT,
        'sampled':int(quote.get('sampled') or 0),'cached':bool(quote.get('cached')),
        'review':{'quality_released':typed_model.QUALITY_RELEASED,'route_version':typed_model.ROUTE_VERSION,
            'source_revision':typed_model.SOURCE_REVISION,'provider':'openai/flex','service_tier':'flex',
            'proposer':typed_model.STAGES['proposer'].model_id,'verifier':typed_model.STAGES['verifier'].model_id,
            'max_output_tokens':typed_model.MAX_OUTPUT_TOKENS},
        'recovery':{'ocr_candidates':int(quote.get('ocr_candidates') or 0),
            'image_only':int(quote.get('ocr_image_only') or 0),'damaged':int(quote.get('ocr_damaged') or 0),
            'estimated_seconds':int(quote.get('ocr_estimated_seconds') or 0),
            'engine_available':bool(engine['available']),'engine_version':engine['version'],'engine_detail':engine['detail'],
            'language':'eng','dpi':300,'pdf_sha256':extract.document_fingerprint(source)[:16],
            'non_latin_share':float(quote.get('non_latin_share') or 0)},
    }


@api_v1.route('/books/<int:book_id>/reflow/estimate/prepare',methods=['POST'])
@login_required_if_no_ano
def reflow_prepare_estimate(book_id):
    guard=_require_edit()
    if guard:return guard
    _book,source,failure=_source_or_error(book_id)
    if failure:return failure
    body=request.get_json(silent=True)
    if not isinstance(body,dict):return _err('invalid_options','Source preparation options are required.',400)
    try:options=_source_options(body)
    except ValueError:return _err('invalid_options','Source recovery options are invalid.',400)
    cache_root=REFLOW_DIR
    try:
        job=_quote_store().start(current_user.id,book_id,source,options,
            lambda path,opts,progress,stop:_quote_work(path,opts,progress,stop,cache_root))
    except quote_preparation.PreparationBusy:return _err('preparation_busy','Local source preparation is busy. Try again shortly.',503)
    return jsonify(job),200 if job['status']=='ready' else 202


@api_v1.route('/books/<int:book_id>/reflow/estimate/preparations/<identifier>',methods=['GET','DELETE'])
@login_required_if_no_ano
def reflow_estimate_preparation(book_id,identifier):
    guard=_require_edit()
    if guard:return guard
    _book,_source,failure=_source_or_error(book_id)
    if failure:return failure
    try:
        store=_quote_store()
        job=store.cancel(current_user.id,book_id,identifier) if request.method=='DELETE' else store.get(current_user.id,book_id,identifier)
    except KeyError:return _err('preparation_missing','This source preparation is unavailable. Prepare the estimate again.',404)
    return jsonify(job)


@api_v1.route("/books/<int:book_id>/reflow/estimate")
@login_required_if_no_ano
def reflow_estimate(book_id):
    guard = _require_edit()
    if guard:
        return guard
    _book, source, failure = _source_or_error(book_id)
    if failure:
        return failure
    try:
        return jsonify(_estimate_payload(_book, source))
    except Exception as exc:                                      # noqa: BLE001
        log.error_or_exception("reflow: could not estimate book %s: %s" % (book_id, exc))
        return _err("estimate_failed",
                    "This PDF could not be read well enough to price a conversion", 422)


# ── starting a job ───────────────────────────────────────────────────────────

def _sample_share(payload, sample_pages):
    """What a sample of *sample_pages* would cost, at this book's routing rate.

    A sample priced as if it were the whole book would ask a user to authorise
    twenty dollars to look at twenty pages, which is the opposite of what a sample
    is for.
    """
    pages = max(1, int(payload.get("pages") or 1))
    routed = int(payload.get("routed_pages_estimate") or 0)
    return max(1, int(round(routed * (min(sample_pages, pages) / float(pages)))))


def _required_usd(payload, tier, mode, sample_pages):
    spec = model.TIERS.get(tier) or model.TIERS[model.DEFAULT_TIER]
    if mode == "sample":
        return round(spec.price_per_page * _sample_share(payload, sample_pages), 4)
    return round(float(payload["estimate_usd"].get(tier) or 0.0), 4)


def _running_task(book_id):
    worker = WorkerThread.get_instance()
    for __, __, __, task, __ in worker.tasks:
        if getattr(task, "book_id", None) != book_id:
            continue
        if not getattr(task, "is_reflow", False):
            continue
        if task.stat in _ACTIVE_STATS:
            return task
    return None


@api_v1.route("/books/<int:book_id>/reflow", methods=["POST"])
@login_required_if_no_ano
def reflow_start(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book, source, failure = _source_or_error(book_id)
    if failure:
        return failure

    body = request.get_json(silent=True)
    if not isinstance(body,dict):return _err('invalid_options','Conversion options are required.',400)
    if body.get('consent') is not True:
        return _err('consent_required','Agree to the current conversion settings before starting.',400)
    if body.get('consent_contract')!=CONSENT_CONTRACT or body.get('review_mode') not in ('deterministic','source_verified'):
        return _err('current_consent_required','Refresh Reflow and choose the current conversion settings.',409)
    fingerprint=extract.document_fingerprint(source)
    if body.get('source_sha256')!=fingerprint:
        return _err('source_changed','The PDF changed. Refresh its assessment before starting.',409)
    try:
        source_options=_source_options(body)
        if body.get('mode') not in ('full','sample'):raise ValueError()
        sample=body.get('sample_pages',tasks_reflow.SAMPLE_PAGES_DEFAULT)
        if isinstance(sample,bool) or not isinstance(sample,int) or not 1<=sample<=tasks_reflow.SAMPLE_PAGES_MAX:raise ValueError()
        options=tasks_reflow.ReflowOptions(dict(body,**source_options))
    except (TypeError,ValueError,OverflowError):return _err('invalid_options','Conversion options are invalid.',400)
    needed=0.0;quote=None
    if options.review_mode=='source_verified':
        if not typed_model.QUALITY_RELEASED:return _err('review_unavailable','AI formatting review is not available in this build. Source-only conversion is available.',409)
        if not config.resolved_openrouter_key():return _err('not_configured','AI review needs a configured provider key. Source-only conversion is available.',400)
        cap=body.get('cost_cap_usd')
        if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or not 0<cap<=tasks_reflow.hard_cap_usd():
            return _err('invalid_cap','Choose a finite positive cap within the administrator limit.',400)
        identifier=body.get('preparation_id')
        if not isinstance(identifier,str) or not _JOB_ID.fullmatch(identifier):
            return _err('estimate_stale','Prepare a current source estimate before AI review.',409)
        try:quote=_quote_store().ready(current_user.id,book_id,body.get('preparation_id'),source,source_options)
        except (KeyError,ValueError):return _err('estimate_stale','Prepare a current estimate for these source recovery settings.',409)
        selected=(range(quote['first_body_page'],min(quote['source_context_pages'],quote['first_body_page']+options.sample_pages))
                  if options.mode=='sample' else range(quote['source_context_pages']))
        needed=sum(p['proposer_bound_usd']+p['verifier_bound_usd'] for p in quote['pages'] if p['page_index0'] in selected)
        options.consent_quote=quote
    options.source_sha256=fingerprint
    try:payload=_estimate_payload(book,source)
    except Exception:return _err('estimate_failed','This PDF could not be assessed.',422)

    if payload["existing_epub"] and options.mode == "full" \
            and not options.replace_existing_epub:
        return _err("epub_exists",
                    "This book already has an EPUB. Choose 'replace the existing "
                    "EPUB' if you want Reflow to overwrite it.", 409)

    # A chosen recovery with no engine to do it fails here, before consent or
    # spend, naming the missing component -- never page by page after the money.
    recovery = payload.get("recovery") or {}
    recoverable = recovery.get("image_only", 0) + (
        recovery.get("damaged", 0) if options.source_recovery == "auto" else 0)
    if options.source_recovery != "off" and recoverable:
        engine = _ocr_engine_state(options.ocr_language)
        if not engine["available"]:
            return _err(
                "ocr_unavailable",
                "This PDF has %d pages that need local text recognition, and %s "
                "An administrator must install it, or the conversion can keep a "
                "facsimile of the page images instead (Source recovery: none)."
                % (recoverable, engine["detail"]), 422)

    # A smaller cap authorizes a partial paid review. Every actual stage reserves
    # its current bound; remaining pages retain complete deterministic output.
    if _running_task(book_id) is not None:
        return _err("already_queued",
                    "A conversion of this book is already running", 409)

    task = tasks_reflow.TaskReflowPdf(book_id, current_user.id, options)
    # Check-then-enqueue was the admission race: two requests inside the same gap
    # both saw a free book and both spent. The book is reserved before the task is
    # published, and the reservation -- not the queue scan -- is what serializes
    # admissions. The task releases it at its terminal state; a failure to enqueue
    # releases it here so a book is never locked by a job that does not exist.
    if not admission.reserve(book_id, WorkerThread.get_instance(), task):
        return _err("already_queued",
                    "A conversion of this book is already running", 409)
    try:
        WorkerThread.add(current_user.name, task)
    except Exception:
        admission.release(book_id, task)
        raise
    return jsonify({"task_id": task.id, "job_id": task.job_id,
                    "mode": options.mode, "review_mode":options.review_mode,
                    "cost_cap_usd": options.cost_cap_usd,
                    "reservation_ceiling_usd": needed,"partial_review_possible":options.review_mode=="source_verified" and options.cost_cap_usd<needed}), 202


# ── what happened ────────────────────────────────────────────────────────────

def _jobs_dir(book_id):
    return os.path.join(REFLOW_DIR, "jobs", str(int(book_id)))


@api_v1.route("/books/<int:book_id>/reflow/jobs")
@login_required_if_no_ano
def reflow_jobs(book_id):
    guard = _require_edit()
    if guard:
        return guard

    mine = int(current_user.id)
    is_admin = bool(current_user.role_admin())
    items = []
    for row in ledger_mod.read_summaries(_jobs_dir(book_id)):
        owner = row.get("user_id")
        if not is_admin and owner is not None and int(owner) != mine:
            continue
        job_id = row.get("job_id") or ""
        item = {
            "job_id": job_id,
            "mode": row.get("mode", "full"),
            "status": row.get("status", "running"),
            "started": row.get("started"),
            "finished": row.get("finished"),
            "spend_usd": row.get("spend_usd", 0.0),
            # Held, not spent: strict bounds of dispatched requests whose billing
            # is unresolved. Never folded into spend_usd, never dropped.
            "pending_usd": row.get("pending_usd", 0.0),
            "cap_usd": row.get("cap_usd", 0.0),
            "pages": row.get("pages", 0),
            "calls": row.get("calls", 0),
            "reused": row.get("reused", 0),
            "gate": row.get("gate", {}),
            "models": row.get("models", {}),
            "recovery": row.get("recovery", {}),
            "structural": row.get("structural"),
            "artifact": row.get("artifact"),
            "error": row.get("error"),
            "sample_url": None,
        }
        if row.get("mode") == "sample" and _JOB_ID.match(job_id) \
                and (owner is None or int(owner) == mine):
            item["sample_url"] = ("/api/v1/books/%d/reflow/jobs/%s/sample.epub"
                                  % (int(book_id), job_id))
            item["sample_ready"] = os.path.isfile(
                tasks_reflow.sample_path(mine, job_id))
        items.append(item)

    active = []
    worker = WorkerThread.get_instance()
    for __, user, __, task, __ in worker.tasks:
        if getattr(task, "book_id", None) != book_id:
            continue
        if not getattr(task, "is_reflow", False):
            continue
        if not is_admin and user != current_user.name:
            continue
        state = _ACTIVE_STATS.get(task.stat)
        if state is None:
            continue
        active.append({"task_id": str(task.id), "job_id": getattr(task, "job_id", None),
                       "status": state,
                       "progress": round(float(getattr(task, "progress", 0) or 0), 4),
                       "message": str(getattr(task, "message", "") or ""),
                       "cancellable": bool(getattr(task, "is_cancellable", False))})
    return jsonify({"items": items, "active": active})


@api_v1.route("/books/<int:book_id>/reflow/jobs/<job_id>/sample.epub")
@login_required_if_no_ano
def reflow_sample(book_id, job_id):
    guard = _require_edit()
    if guard:
        return guard
    if not _JOB_ID.match(job_id or ""):
        return _err("not_found", "No such sample", 404)
    # Built from the signed-in user's own id, never from the URL: another user's
    # sample is not addressable from here, rather than addressable and refused.
    path = tasks_reflow.sample_path(current_user.id, job_id)
    if not os.path.isfile(path):
        return _err("not_found", "No such sample", 404)
    response = send_file(path, mimetype="application/epub+zip", as_attachment=True,
                         download_name="reflow-sample-%d.epub" % int(book_id))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api_v1.route("/books/<int:book_id>/reflow/report")
@login_required_if_no_ano
def reflow_report(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book, failure = _book_or_error(book_id)
    if failure:
        return failure
    path = _format_path(book, "EPUB")
    if path is None:
        return _err("not_found", "This book has no EPUB", 404)
    sidecar = build_epub.read_sidecar(path)
    if not sidecar:
        return _err("not_found", "This EPUB was not made by Reflow", 404)
    return jsonify(sidecar)


# ── the administrator's settings ─────────────────────────────────────────────

@api_v1.route("/admin/reflow")
@login_required_if_no_ano
def reflow_admin_config():
    """What is configured — never the key itself. See the SECURITY-REVIEW note."""
    guard = _require_admin()
    if guard:
        return guard
    return jsonify({
        "configured": bool(config.resolved_openrouter_key()),
        "key_source": config.openrouter_key_source(),
        "default_tier": tasks_reflow.config_default_tier(),
        "target_usd": _target_usd(),
        "hard_cap_usd": tasks_reflow.hard_cap_usd(),
        "tiers": model.tier_choices(),
        "priced_on": model.PRICE_TABLE_MEASURED,
    })
