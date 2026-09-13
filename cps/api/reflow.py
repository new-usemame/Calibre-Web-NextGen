# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reflow endpoints for /api/v1 — the price, the consent, and the job (SPEC §4).

This is the boundary where a user authorises somebody's money to be spent, so three
things are deliberate and worth stating.

*The estimate is measured, not assumed.* ``pipeline.survey`` runs the real
deterministic pass over a spread of pages and scales that book's own routing rate.
That costs seconds of CPU, which under gevent-without-monkey-patching would freeze
every other request, so it runs on the gevent-aware thread pool and its answer is
cached against the PDF's size and mtime — the page that shows it is reopened often
and the file rarely changes.

*Consent is checked against the number the user was shown.* A start with no consent
flag, or with a cap below what the chosen tier would cost, is refused with the
figure in the message rather than quietly clamped upward.

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
from ..services.reflow import build_epub, ledger as ledger_mod, model, pipeline
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
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_edit():
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
    """The file as it is now. A rebuilt PDF is a different book to price.

    Hashed with sha1 rather than ``hash()`` because the estimate cache outlives the
    process and PYTHONHASHSEED does not: a str hash is randomised per run, so every
    restart would miss its own cache.
    """
    stat = os.stat(path)
    digest = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
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
        with open(cached + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(quote, handle)
        os.replace(cached + ".tmp", cached)
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


def _estimate_payload(book, source):
    quote = _survey(source)
    pages = int(quote.get("pages") or 0)
    routed = int(quote.get("routed_pages") or 0)
    tiers = {name: float(quote.get(name) or 0.0) for name in model.TIERS}
    worst = {name: float((quote.get("worst_case") or {}).get(name) or 0.0)
             for name in model.TIERS}
    default_tier = tasks_reflow.config_default_tier()
    target = _target_usd()
    over = tiers.get(default_tier, 0.0) > target
    return {
        "book_id": book.id,
        "title": book.title,
        "verdict": quote.get("verdict", ""),
        "pages": pages,
        "text_layer": bool(quote.get("text_layer")),
        "routed_pages_estimate": routed,
        "routed_share": round(routed / float(pages), 4) if pages else 0.0,
        "estimate_usd": tiers,
        "worst_case_usd": worst,
        "target_usd": target,
        "over_target": bool(over),
        # An expensive book is exactly the one a person should look at before they
        # buy all of it, so the suggestion follows the price rather than the length.
        "sample_suggested": bool(over),
        "existing_epub": calibre_db.get_book_format(book.id, "EPUB") is not None,
        "configured": bool(config.resolved_openrouter_key()),
        "default_tier": default_tier,
        "hard_cap_usd": tasks_reflow.hard_cap_usd(),
        "sample_pages_default": tasks_reflow.SAMPLE_PAGES_DEFAULT,
        "sample_pages_max": tasks_reflow.SAMPLE_PAGES_MAX,
        "tiers": model.tier_choices(),
        "priced_on": quote.get("priced_on", ""),
        "sampled": int(quote.get("sampled") or 0),
        "reasons": dict(quote.get("reasons") or {}),
        "cached": bool(quote.get("cached")),
    }


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

    body = request.get_json(silent=True) or {}
    if body.get("consent") is not True:
        return _err("consent_required",
                    "Start the conversion from the Reflow page so the cost is agreed "
                    "before anything is sent", 400)
    if not config.resolved_openrouter_key():
        return _err("not_configured",
                    "No OpenRouter key is configured, so there is nothing to convert "
                    "with. An administrator can add one in Admin → Reflow.", 400)

    options = tasks_reflow.ReflowOptions(body)
    try:
        payload = _estimate_payload(book, source)
    except Exception as exc:                                      # noqa: BLE001
        log.error_or_exception("reflow: could not price book %s: %s" % (book_id, exc))
        return _err("estimate_failed",
                    "This PDF could not be read well enough to price a conversion", 422)

    if payload["existing_epub"] and options.mode == "full" \
            and not options.replace_existing_epub:
        return _err("epub_exists",
                    "This book already has an EPUB. Choose 'replace the existing "
                    "EPUB' if you want Reflow to overwrite it.", 409)

    needed = _required_usd(payload, options.model_tier, options.mode,
                           options.sample_pages)
    if options.cost_cap_usd + 1e-9 < needed:
        return _err("cap_below_estimate",
                    "This conversion is estimated at $%.2f and the cap you set is "
                    "$%.2f. Raise the cap or choose a cheaper model."
                    % (needed, options.cost_cap_usd), 400)

    if _running_task(book_id) is not None:
        return _err("already_queued",
                    "A conversion of this book is already running", 409)

    task = tasks_reflow.TaskReflowPdf(book_id, current_user.id, options)
    WorkerThread.add(current_user.name, task)
    return jsonify({"task_id": task.id, "job_id": task.job_id,
                    "mode": options.mode, "model_tier": options.model_tier,
                    "cost_cap_usd": options.cost_cap_usd,
                    "estimate_usd": needed}), 202


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
            "cap_usd": row.get("cap_usd", 0.0),
            "pages": row.get("pages", 0),
            "calls": row.get("calls", 0),
            "reused": row.get("reused", 0),
            "gate": row.get("gate", {}),
            "models": row.get("models", {}),
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
