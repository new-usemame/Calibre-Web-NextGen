#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Reading Services API for Kobo Annotations/Highlights
Handles annotation sync from Kobo devices

These routes are at the root level: /api/v3/..., /api/UserStorage/...
"""

import json
import hashlib
import os
import secrets
import zipfile
import re
from functools import wraps
from typing import TypedDict, NotRequired
from flask import Blueprint, request, make_response, jsonify, g, after_this_request
from sqlalchemy import func
from werkzeug.datastructures import Headers
import requests
from lxml import etree

from . import logger, calibre_db, db, config, ub, csrf
from .cw_login import current_user

log = logger.create()

# Create blueprints to handle the relevant reading services API routes
readingservices_api_v3 = Blueprint("readingservices_api_v3", __name__, url_prefix="/api/v3")
readingservices_userstorage = Blueprint("readingservices_userstorage", __name__, url_prefix="/api/UserStorage")

KOBO_READING_SERVICES_URL = "https://readingservices.kobo.com"

# Constants for annotation processing
MAX_PROGRESS_PERCENTAGE = 100  # Cap progress at 100%
REQUEST_TIMEOUT = (2, 10)  # (connect, read) timeouts in seconds

CONNECTION_SPECIFIC_HEADERS = [
    "connection",
    "content-encoding",
    "content-length",
    "transfer-encoding",
]


def _is_check_for_changes_path(path):
    """Recognize equivalent spellings of the destructive Nickel trigger."""
    normalized_parts = [part.casefold() for part in path.split("/") if part]
    return normalized_parts == ["api", "v3", "content", "checkforchanges"]


def _is_annotation_path(path):
    """Recognize the one reading-services route whose PATCH carries user data."""
    normalized_parts = [part.casefold() for part in path.split("/") if part]
    return (
        len(normalized_parts) == 5
        and normalized_parts[:3] == ["api", "v3", "content"]
        and normalized_parts[-1] == "annotations"
    )


def _annotation_entitlement_argument(args, kwargs):
    entitlement_id = kwargs.get("entitlement_id")
    if entitlement_id is None and args:
        entitlement_id = args[0]
    return entitlement_id if isinstance(entitlement_id, str) else None


def redact_headers(headers):
    """Redact sensitive headers from the headers dictionary.
    
    Returns a new dictionary with sensitive headers redacted to avoid
    mutating the original headers object.
    """
    redacted = dict(headers)
    sensitive_headers = {'authorization', 'x-kobo-userkey', 'cookie', 'set-cookie'}
    for header_name in redacted:
        if header_name.lower() in sensitive_headers:
            redacted[header_name] = '***REDACTED***'
    return redacted


def proxy_to_kobo_reading_services(data=None, capture_session=None):
    """Proxy the request to Kobo's reading services API."""
    try:
        kobo_url = KOBO_READING_SERVICES_URL + request.path
        if request.query_string:
            kobo_url += "?" + request.query_string.decode('utf-8')
        
        # Query values can carry opaque tokens.  The method + route prove the
        # proxy leg without putting credentials in logs.
        log.debug("Proxying %s to Kobo Reading Services path=%s", request.method, request.path)
        
        # Forward headers (including Authorization, x-kobo-userkey, etc.)
        outgoing_headers = Headers(request.headers)
        outgoing_headers.remove("Host")
        # Remove CWA session cookie - Kobo doesn't need it and it causes issues
        outgoing_headers.pop("Cookie", None)
        if data is not None:
            # requests must calculate this again for a filtered request body.
            outgoing_headers.pop("Content-Length", None)
        
        outgoing_body = request.get_data() if data is None else data
        if capture_session is not None:
            capture_session.record_upstream_request(
                method=request.method,
                path=request.path,
                query_string=request.query_string,
                headers=outgoing_headers.items(),
                body=outgoing_body,
            )

        readingservices_response = requests.request(
            method=request.method,
            url=kobo_url,
            headers=outgoing_headers,
            data=outgoing_body,
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT
        )
        
        if capture_session is not None:
            capture_session.record_upstream_response(
                status=readingservices_response.status_code,
                headers=readingservices_response.headers.items(),
                body=readingservices_response.content,
            )

        if readingservices_response.status_code >= 400:
            log.warning(f"Kobo Reading Services error {readingservices_response.status_code}")
            log.warning(
                "Kobo error response bytes=%d sha256=%s",
                len(readingservices_response.content),
                hashlib.sha256(readingservices_response.content).hexdigest(),
            )
            log.warning(f"Response headers: {redact_headers(dict(readingservices_response.headers))}")
        
        response_headers = readingservices_response.headers
        for header_key in CONNECTION_SPECIFIC_HEADERS:
            response_headers.pop(header_key, default=None)
        
        return make_response(
            readingservices_response.content, readingservices_response.status_code, response_headers.items()
        )
    except requests.exceptions.Timeout:
        if capture_session is not None:
            capture_session.record_upstream_error("timeout")
        log.error("Timeout connecting to Kobo Reading Services")
        return make_response(jsonify({"error": "Gateway timeout"}), 504)
    except requests.exceptions.ConnectionError as e:
        if capture_session is not None:
            capture_session.record_upstream_error("connection_error")
        log.error(f"Connection error to Kobo Reading Services: {e}")
        return make_response(jsonify({"error": "Bad gateway"}), 502)
    except requests.exceptions.RequestException as e:
        if capture_session is not None:
            capture_session.record_upstream_error("request_exception")
        log.error(f"Request failed to Kobo Reading Services: {e}")
        return make_response(jsonify({"error": "Bad gateway"}), 502)
    except Exception as e:
        if capture_session is not None:
            capture_session.record_upstream_error("unexpected_exception")
        log.error(f"Unexpected error proxying to Kobo Reading Services: {e}")
        import traceback
        log.error(traceback.format_exc())
        return make_response(jsonify({"error": "Internal server error"}), 500)


def requires_reading_services_auth_and_config(f):
    """Auth gate for Reading Services endpoints.

    Sub-project (2): the Hardcover-specific config check has been removed
    from this gate. We always intercept Kobo PATCH requests when the user
    is authenticated + Kobo sync is on — the dispatcher then decides which
    enabled handlers (if any) to push to. This lets us capture annotations
    locally even when Hardcover is off, which is the whole point of (2).

    Authentication uses the existing Flask session, whether it came from a
    Kobo-sync handshake or a browser login. If Kobo sync is off OR the user
    isn't logged in, we normally proxy through to Kobo untouched. Two routes
    are exceptions: checkforchanges must still run ownership containment, and
    an annotation PATCH must fail authentication instead of forwarding a
    success that would make Nickel forget an upload CWNG did not capture.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        contains_check_for_changes = _is_check_for_changes_path(request.path)
        contains_annotation_get = (
            request.method == "GET" and _is_annotation_path(request.path)
        )
        if not config.config_kobo_sync and not contains_check_for_changes:
            if contains_annotation_get:
                if current_user.is_authenticated:
                    return f(*args, **kwargs)
                entitlement_id = _annotation_entitlement_argument(args, kwargs)
                if entitlement_id is None:
                    return _annotation_get_temporarily_unavailable()
                ownership = resolve_entitlement_ownership(entitlement_id)
                return _annotation_get_without_live_authority(
                    None, ownership, entitlement_id,
                    authenticated_user_id=None,
                )
            log.debug("Kobo sync disabled, proxying to Kobo")
            return proxy_to_kobo_reading_services()
        if current_user.is_authenticated:
            if config.config_kobo_sync:
                try:
                    from .services.device_registry import (
                        KoboDeviceLimitReached,
                        register_kobo_device_best_effort,
                    )
                    g.annotation_origin_device_id = register_kobo_device_best_effort(
                        user_id=current_user.id, headers=request.headers, return_internal=True,
                    )
                except KoboDeviceLimitReached as error:
                    return make_response(jsonify({"error": str(error)}), 409)
                except Exception:
                    log.warning("Best-effort Kobo device observation failed", exc_info=True)
            return f(*args, **kwargs)
        if contains_check_for_changes:
            return f(*args, **kwargs)
        if request.method == "PATCH" and _is_annotation_path(request.path):
            _capture_unauthenticated_annotation_patch()
            log.warning(
                "Refusing unauthenticated annotation PATCH so the device can retry"
            )
            return make_response(jsonify({"error": "Authentication required"}), 401)
        if contains_annotation_get:
            entitlement_id = _annotation_entitlement_argument(args, kwargs)
            if entitlement_id is None:
                log.error(
                    "Refusing unauthenticated annotation GET without an "
                    "addressable entitlement"
                )
                return _annotation_get_temporarily_unavailable()
            ownership = resolve_entitlement_ownership(entitlement_id)
            return _annotation_get_without_live_authority(
                None, ownership, entitlement_id, authenticated_user_id=None,
            )
        log.debug("Reading services request without auth, proxying to Kobo")
        return proxy_to_kobo_reading_services()
    return decorated_function


#: Ownership could not be determined (e.g. the metadata DB errored). Distinct
#: from ``None``, which means the exact-match lookup returned no row. Conflating
#: the two is what makes a destructive fallback fire on a transient failure.
OWNERSHIP_UNKNOWN = object()


def get_book_by_entitlement_id(entitlement_id):
    """Get book from database by UUID (entitlement_id).

    Returns ``None`` both for "not ours" and for a lookup failure. Callers whose
    fallback is destructive must use :func:`resolve_entitlement_ownership`
    instead, which keeps those two cases apart.
    """
    try:
        book = calibre_db.get_book_by_uuid(entitlement_id)
        return book
    except Exception as e:
        log.error(f"Error getting book by entitlement ID {entitlement_id}: {e}")
        return None


def resolve_entitlement_ownership(entitlement_id):
    """Tri-state ownership: the Books row, ``None``, or ``OWNERSHIP_UNKNOWN``."""
    # Normalize only for this ownership lookup; callers still forward the
    # original ContentId unchanged. Stripping whitespace/braces and casefolding
    # can only widen the set classified as owned, so representation drift fails
    # in the safe direction instead of bypassing both containment layers.
    normalized_id = entitlement_id.strip().strip("{}").strip().casefold()
    try:
        return calibre_db.get_book_by_uuid(normalized_id)
    except Exception:
        log.exception(
            "Could not determine ownership of entitlement %s; treating as UNKNOWN",
            entitlement_id,
        )
        return OWNERSHIP_UNKNOWN


POSSIBLE_OWNERSHIP_LOOKUP_FAILED = object()


def _normalized_annotation_entitlement_id(entitlement_id):
    if not isinstance(entitlement_id, str):
        return ""
    return entitlement_id.strip().strip("{}").strip().casefold()


def _possible_annotation_ownership(entitlement_id, *, user_id=None):
    """Return durable app-DB evidence independent of live ``metadata.db``.

    ``KoboAnnotationBookState`` is the primary ownership ledger and exact
    snapshot owner. Older annotation rows also retain Kobo's entitlement as
    the prefix of ``content_id``. Either signal means that a failed/negative
    live lookup cannot prove this book has always been outside CWNG.

    The result is a mapping from ``(user_id, book_id)`` to its ledger row (or
    ``None`` for annotation-only evidence). A lookup error is deliberately a
    separate sentinel: absence cannot be inferred from an unreadable app DB.
    """
    normalized_id = _normalized_annotation_entitlement_id(entitlement_id)
    if not normalized_id:
        return POSSIBLE_OWNERSHIP_LOOKUP_FAILED
    try:
        state_query = ub.session.query(ub.KoboAnnotationBookState).filter(
            ub.KoboAnnotationBookState.content_id == normalized_id,
        )
        annotation_query = ub.session.query(
            ub.Annotation.user_id, ub.Annotation.book_id,
        ).filter(
            func.lower(ub.Annotation.content_id).startswith(
                normalized_id + "!!", autoescape=True,
            ),
        )
        if user_id is not None:
            state_query = state_query.filter(
                ub.KoboAnnotationBookState.user_id == user_id,
            )
            annotation_query = annotation_query.filter(
                ub.Annotation.user_id == user_id,
            )

        evidence = {
            (state.user_id, state.book_id): state
            for state in state_query.all()
        }
        for annotation_user_id, book_id in annotation_query.distinct().all():
            evidence.setdefault((annotation_user_id, book_id), None)
        return evidence
    except Exception:
        try:
            ub.session.rollback()
        except Exception:
            pass
        log.exception(
            "Could not read durable Kobo annotation ownership evidence for "
            "entitlement %s",
            entitlement_id,
        )
        return POSSIBLE_OWNERSHIP_LOOKUP_FAILED


def _annotation_get_temporarily_unavailable():
    return make_response(jsonify({
        "error": "Authoritative annotation set temporarily unavailable",
    }), 503)


def _annotation_snapshot_or_503(
    *, user_id, book_id, capture_session, entitlement_id,
):
    try:
        from cps.services.kobo_annotation_authority import (
            load_last_served_complete_set,
        )
        rendered = load_last_served_complete_set(
            user_id=user_id, book_id=book_id, log=log,
        )
    except Exception:
        rendered = None
        log.exception(
            "Kobo annotation fail-closed snapshot lookup failed "
            "user_id=%s book_id=%s",
            user_id, book_id,
        )
    if rendered is None:
        return _annotation_get_temporarily_unavailable()

    body, etag = rendered
    if capture_session is not None:
        capture_session.add_decision(
            stage="local_authority",
            index=0,
            content_id=entitlement_id,
            ownership="possible_owned",
            authority_status="ever_authoritative",
            action="answered_from_snapshot",
        )
    response = make_response(body, 200)
    response.headers["Content-Type"] = "application/json"
    response.headers["Content-Length"] = str(len(body))
    response.headers["ETag"] = etag
    return response


def _annotation_get_without_live_authority(
    capture_session, ownership, entitlement_id, *, authenticated_user_id,
):
    """Resolve a GET after auth or live ownership ceased to be trustworthy.

    Only an affirmative live ``None`` plus a readable, empty durable evidence
    lookup proves that Kobo has always remained the wire authority. UNKNOWN,
    an app-DB lookup error, a known-owned unauthenticated request, or any
    ledger/annotation evidence fails closed. An authenticated exact current
    snapshot is the sole safe 200 on that path.
    """
    global_evidence = _possible_annotation_ownership(entitlement_id)
    scoped_evidence = global_evidence
    if authenticated_user_id is not None and isinstance(global_evidence, dict):
        scoped_evidence = {
            key: state
            for key, state in global_evidence.items()
            if key[0] == authenticated_user_id
        }

    if (
        authenticated_user_id is not None
        and isinstance(scoped_evidence, dict)
        and len(scoped_evidence) == 1
    ):
        ((evidence_user_id, book_id), state), = scoped_evidence.items()
        if state is not None and (
            bool(state.ever_authoritative)
            or state.authority_status == "authoritative"
        ):
            return _annotation_snapshot_or_503(
                user_id=evidence_user_id,
                book_id=book_id,
                capture_session=capture_session,
                entitlement_id=entitlement_id,
            )

    evidence_failed = (
        scoped_evidence is POSSIBLE_OWNERSHIP_LOOKUP_FAILED
        or global_evidence is POSSIBLE_OWNERSHIP_LOOKUP_FAILED
    )
    has_evidence = (
        isinstance(scoped_evidence, dict) and bool(scoped_evidence)
    ) or (
        isinstance(global_evidence, dict) and bool(global_evidence)
    )
    provably_never_owned = (
        ownership is None and not evidence_failed and not has_evidence
    )
    if provably_never_owned:
        return _proxy_annotation_request(
            capture_session, ownership, entitlement_id,
        )

    log.warning(
        "Refusing Kobo annotation GET because local authority cannot be "
        "excluded entitlement=%s authenticated=%s live_ownership=%s",
        entitlement_id,
        authenticated_user_id is not None,
        _capture_ownership_label(ownership),
    )
    if capture_session is not None:
        capture_session.add_decision(
            stage="local_authority",
            index=0,
            content_id=entitlement_id,
            ownership=_capture_ownership_label(ownership),
            authority_status="unknown",
            action="refused_fail_closed",
        )
    return _annotation_get_temporarily_unavailable()


def _parse_check_for_changes_request(raw_body):
    """Return a recognized check-for-changes request list, or ``None``."""
    try:
        entries = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(entries, list):
        return None
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("ContentId"), str)
        for entry in entries
    ):
        return None
    return entries


def _check_for_changes_response_content_ids(entries):
    """Return ids from a recognized Kobo response list, or ``None``."""
    if not isinstance(entries, list):
        return None
    content_ids = []
    for entry in entries:
        if isinstance(entry, str):
            content_ids.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("ContentId"), str):
            content_ids.append(entry["ContentId"])
        else:
            return None
    return content_ids


def _filter_check_for_changes_entries(entries, filtered_content_ids):
    """Filter prevalidated string or dict-with-string-ContentId entries.

    Callers must first validate with ``_parse_check_for_changes_request`` or
    ``_check_for_changes_response_content_ids``.
    """
    filtered_content_ids = set(filtered_content_ids)
    return [
        entry for entry in entries
        if (entry if isinstance(entry, str) else entry["ContentId"])
        not in filtered_content_ids
    ]


def _check_for_changes_ownership_is_filtered(ownership):
    """Whether an ownership result must be contained at the trigger boundary."""
    # UNKNOWN is deliberately treated as owned here. During a DB outage this
    # suppresses Kobo-cloud annotation sync for every affected batch until the
    # DB recovers; a false negative can delete the user's only highlight copy.
    return ownership is not None


def _begin_exchange_capture(
    exchange, raw_body, *, authentication="not_recorded", user_id=None,
):
    """Attach a best-effort after-response observer to the current request."""
    try:
        from .services import kobo_exchange_capture
        capture_session = kobo_exchange_capture.begin_capture(
            exchange=exchange,
            method=request.method,
            path=request.path,
            query_string=request.query_string,
            headers=request.headers.items(),
            body=raw_body,
            authentication=authentication,
            user_id=user_id,
        )
        if capture_session is None:
            return None

        @after_this_request
        def _finish_capture(response):
            try:
                capture_session.finish(
                    status=response.status_code,
                    headers=response.headers.items(),
                    body=response.get_data(),
                )
            except Exception:
                # The observer is not part of the request's success contract.
                log.warning(
                    "Kobo exchange capture finalizer failed exchange=%s",
                    exchange, exc_info=True,
                )
            return response

        return capture_session
    except Exception:
        log.warning(
            "Kobo exchange capture could not attach exchange=%s",
            exchange, exc_info=True,
        )
        return None


def _capture_unauthenticated_annotation_patch():
    """Opt-in, separately bounded capture of a PATCH refused before auth.

    The always-on recovery spool is deliberately not used here.  Without an
    authenticated principal there is no safe replay target, and unresolved
    spool records are protected from eviction.  Admitting them would let an
    unauthenticated caller exhaust the recovery budget for real users.

    Do not consume an unauthenticated body unless the private diagnostic is
    explicitly enabled, and require a bounded Content-Length before reading.
    """
    try:
        from .services import kobo_exchange_capture
        if not kobo_exchange_capture.enabled():
            return None
        content_length = request.content_length
        if (
            content_length is None
            or content_length < 0
            or content_length > kobo_exchange_capture.UNAUTHENTICATED_MAX_BODY_BYTES
        ):
            log.warning(
                "Unauthenticated Kobo annotation PATCH capture skipped: "
                "request length is missing or outside diagnostic bound bytes=%s",
                content_length,
            )
            return None
        raw_body = request.get_data(cache=True)
        return _begin_exchange_capture(
            "annotations_patch_unauthenticated",
            raw_body,
            authentication="unauthenticated",
            user_id=None,
        )
    except Exception:
        log.warning(
            "Unauthenticated Kobo annotation PATCH capture could not attach",
            exc_info=True,
        )
        return None


def _capture_authority_status(ownership):
    """Observe per-book rollout state without making it route-load-bearing."""
    if ownership is None or ownership is OWNERSHIP_UNKNOWN:
        return None
    try:
        user_id = getattr(current_user, "id", None)
        if user_id is None:
            return "unavailable"
        return (
            ub.session.query(ub.KoboAnnotationBookState.authority_status)
            .filter_by(user_id=user_id, book_id=ownership.id)
            .scalar()
        )
    except Exception:
        return "unavailable"


def _capture_ownership_label(ownership):
    if ownership is OWNERSHIP_UNKNOWN:
        return "unknown"
    if ownership is None:
        return "unowned"
    return "owned"


def _record_annotation_decision(capture_session, ownership, action, entitlement_id):
    """Best-effort exchange-capture decision for one annotation request."""
    if capture_session is None:
        return
    capture_session.add_decision(
        stage="local_authority",
        index=0,
        content_id=entitlement_id,
        ownership=_capture_ownership_label(ownership),
        authority_status=_capture_authority_status(ownership),
        action=action,
    )


def _proxy_annotation_request(capture_session, ownership, entitlement_id):
    _record_annotation_decision(
        capture_session, ownership, "proxied", entitlement_id,
    )
    if capture_session is None:
        return proxy_to_kobo_reading_services()
    return proxy_to_kobo_reading_services(capture_session=capture_session)


def _owned_annotation_patch_ack(capture_session, ownership, entitlement_id):
    """The bare 204 shape Nickel receives from Kobo on a successful PATCH."""
    _record_annotation_decision(
        capture_session, ownership, "answered_locally", entitlement_id,
    )
    response = make_response(b"", 204)
    response.headers["Content-Type"] = "text/html"
    response.headers["Content-Length"] = "0"
    return response


def _owned_patch_is_local_authority(ownership, entitlement_id):
    """Use the exact same complete-set proof as the owned GET."""
    try:
        from cps.services.kobo_annotation_authority import (
            AUTHORITY_EVER,
            AUTHORITY_LOOKUP_FAILED,
            authority_evidence_for_route,
            ever_authoritative,
            local_get_is_eligible,
        )
        # Once CWNG has ever become authoritative, Kobo's cloud copy is known
        # to have drifted because owned PATCHes stopped feeding it. A later
        # instance/user/device gate failure must therefore never resume
        # forwarding against that stale copy.
        history = ever_authoritative(current_user.id, ownership.id)
        if history == AUTHORITY_LOOKUP_FAILED:
            history = authority_evidence_for_route(
                current_user.id, ownership.id,
            )
        if history == AUTHORITY_EVER:
            return True
    except Exception:
        history = "lookup_failed"
        log.exception("Sticky Kobo PATCH authority lookup failed")

    try:
        if history == AUTHORITY_LOOKUP_FAILED:
            # No evidence says this is a starved cloud. Preserve the
            # pre-authority status-quo path; critically, lookup failure was not
            # collapsed into a false historical value.
            return False
        return local_get_is_eligible(
            settings=config,
            user=current_user,
            book_id=ownership.id,
            entitlement_id=entitlement_id,
            page_limit=100,
            device_id=getattr(g, "annotation_origin_device_id", None),
            log=log,
        )
    except Exception:
        # A gate failure must preserve the pre-authority PATCH path.  Proxying
        # keeps feeding Kobo's copy; locally acknowledging would split the two
        # verbs and could make the next replacement-set GET destructive.
        log.exception(
            "Kobo PATCH authority gate failed; proxying user_id=%s book_id=%s",
            getattr(current_user, "id", None), getattr(ownership, "id", None),
        )
        return False


def _owned_annotation_page_limit():
    """Return the requested one-page bound, or ``None`` to force the proxy."""
    try:
        values = request.args.getlist("limit")
        if not values:
            return 100
        if len(values) != 1:
            return None
        page_limit = int(values[0])
        if page_limit < 1 or page_limit > 100:
            return None
        return page_limit
    except (TypeError, ValueError, OverflowError):
        return None


def _proxy_owned_annotation_get(capture_session, ownership, entitlement_id):
    """Proxy one owned GET and best-effort feed its response to M2 seeding."""
    seed_capture_id = None
    device_id = getattr(g, "annotation_origin_device_id", None)
    request_offset_token = request.args.get("pageOffsetToken")
    try:
        from cps.services import kobo_annotation_seeding
        seed_capture_id = kobo_annotation_seeding.begin_or_resume_capture(
            settings=config,
            user=current_user,
            book=ownership,
            device_id=device_id,
            request_offset_token=request_offset_token,
            device_etag=request.headers.get("If-None-Match"),
            log=log,
        )
    except Exception:
        log.warning(
            "Kobo annotation seed capture could not attach user_id=%s book_id=%s",
            getattr(current_user, "id", None), getattr(ownership, "id", None),
            exc_info=True,
        )

    response = _proxy_annotation_request(
        capture_session, ownership, entitlement_id,
    )
    if seed_capture_id is not None:
        try:
            from cps.services import kobo_annotation_seeding
            kobo_annotation_seeding.record_proxy_response(
                seed_capture_id,
                response=response,
                book=ownership,
                user=current_user,
                device_id=device_id,
                request_offset_token=request_offset_token,
                log=log,
            )
        except Exception:
            # The proxied response remains the GET's wire authority. Seeding is
            # durable best-effort and can retry on a later request.
            log.warning(
                "Kobo annotation seed response could not persist capture_id=%s",
                seed_capture_id,
                exc_info=True,
            )
    return response


def _owned_annotation_get_response(capture_session, ownership, entitlement_id):
    """Return one complete eligible local page, otherwise proxy unchanged."""
    sticky = False
    authority_history_known = False
    page_limit = _owned_annotation_page_limit()
    try:
        from cps.services.kobo_annotation_authority import (
            AUTHORITY_EVER,
            AUTHORITY_LOOKUP_FAILED,
            STICKY_GET_LOCAL,
            authority_evidence_for_route,
            ever_authoritative,
            load_last_served_complete_set,
            local_get_is_eligible,
            prepare_authoritative_device_get,
            render_authoritative_complete_set,
            render_owned_annotations,
            sticky_render_page_limit,
        )

        history = ever_authoritative(current_user.id, ownership.id)
        if history == AUTHORITY_LOOKUP_FAILED:
            history = authority_evidence_for_route(
                current_user.id, ownership.id,
            )
        if history == AUTHORITY_LOOKUP_FAILED:
            return _annotation_snapshot_or_503(
                user_id=current_user.id,
                book_id=ownership.id,
                capture_session=capture_session,
                entitlement_id=entitlement_id,
            )
        authority_history_known = True
        sticky = history == AUTHORITY_EVER
        has_cursor = request.args.get("pageOffsetToken") is not None
        if sticky:
            pre_serve = prepare_authoritative_device_get(
                user_id=current_user.id,
                book_id=ownership.id,
                device_id=getattr(g, "annotation_origin_device_id", None),
                log=log,
            )
            if pre_serve != STICKY_GET_LOCAL:
                rendered = load_last_served_complete_set(
                    user_id=current_user.id,
                    book_id=ownership.id,
                    log=log,
                )
                if rendered is None:
                    log.critical(
                        "Kobo authoritative GET has neither live proof nor "
                        "last-served snapshot user_id=%s book_id=%s",
                        current_user.id, ownership.id,
                    )
                    return make_response(jsonify({
                        "error": "Authoritative annotation set temporarily unavailable",
                    }), 503)
                body, etag = rendered
                _record_annotation_decision(
                    capture_session, ownership, "answered_from_snapshot",
                    entitlement_id,
                )
                response = make_response(body, 200)
                response.headers["Content-Type"] = "application/json"
                response.headers["Content-Length"] = str(len(body))
                response.headers["ETag"] = etag
                return response
            page_limit = sticky_render_page_limit(
                current_user.id, ownership.id, page_limit,
            )
        if not sticky and (has_cursor or not local_get_is_eligible(
            settings=config,
            user=current_user,
            book_id=ownership.id,
            entitlement_id=entitlement_id,
            page_limit=page_limit,
            device_id=getattr(g, "annotation_origin_device_id", None),
            log=log,
        )):
            return _proxy_owned_annotation_get(
                capture_session, ownership, entitlement_id,
            )

        rendered = render_owned_annotations(
            user_id=current_user.id,
            book_id=ownership.id,
            entitlement_id=entitlement_id,
            page_limit=page_limit,
            device_id=getattr(g, "annotation_origin_device_id", None),
            log=log,
        )
        if rendered is None:
            if not sticky:
                return _proxy_owned_annotation_get(
                    capture_session, ownership, entitlement_id,
                )
            rendered = render_authoritative_complete_set(
                user_id=current_user.id,
                book_id=ownership.id,
                entitlement_id=entitlement_id,
                page_limit=max(page_limit or 100, 2 ** 31 - 1),
                device_id=getattr(g, "annotation_origin_device_id", None),
                log=log,
                reason="authoritative_render_proof_rebuilt_live",
            )
            if rendered is None:
                log.critical(
                    "Kobo authoritative GET could not read live rows or a "
                    "last-served snapshot user_id=%s book_id=%s",
                    current_user.id, ownership.id,
                )
                return make_response(jsonify({
                    "error": "Authoritative annotation set temporarily unavailable",
                }), 503)
        body, etag = rendered
        _record_annotation_decision(
            capture_session, ownership, "answered_locally", entitlement_id,
        )
        response = make_response(body, 200)
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Length"] = str(len(body))
        response.headers["ETag"] = etag
        return response
    except Exception:
        log.exception(
            "Owned Kobo annotation GET local authority failed entitlement=%s",
            entitlement_id,
        )
        if sticky:
            from cps.services.kobo_annotation_authority import (
                load_last_served_complete_set,
                render_authoritative_complete_set,
                sticky_render_page_limit,
            )
            rendered = load_last_served_complete_set(
                user_id=current_user.id,
                book_id=ownership.id,
                log=log,
            )
            emergency_limit = sticky_render_page_limit(
                current_user.id, ownership.id, page_limit,
            )
            if rendered is None:
                rendered = render_authoritative_complete_set(
                    user_id=current_user.id,
                    book_id=ownership.id,
                    entitlement_id=entitlement_id,
                    page_limit=max(emergency_limit, 2 ** 31 - 1),
                    device_id=getattr(g, "annotation_origin_device_id", None),
                    log=log,
                    reason="authoritative_route_exception_rebuilt_live",
                )
            if rendered is None:
                log.critical(
                    "Kobo authoritative GET terminal fallback exhausted "
                    "user_id=%s book_id=%s", current_user.id, ownership.id,
                )
                return make_response(jsonify({
                    "error": "Authoritative annotation set temporarily unavailable",
                }), 503)
            body, etag = rendered
            response = make_response(body, 200)
            response.headers["Content-Type"] = "application/json"
            response.headers["Content-Length"] = str(len(body))
            response.headers["ETag"] = etag
            return response
        if not authority_history_known:
            return _annotation_snapshot_or_503(
                user_id=current_user.id,
                book_id=ownership.id,
                capture_session=capture_session,
                entitlement_id=entitlement_id,
            )
        return _proxy_owned_annotation_get(
            capture_session, ownership, entitlement_id,
        )


def _stage_patch_for_recovery(raw_body, entitlement_id):
    """Bounded off-hub durable stage; never change route success."""
    try:
        from .services import kobo_patch_spool
        return kobo_patch_spool.stage_patch(
            raw_body=raw_body,
            entitlement_id=entitlement_id,
            user_id=getattr(current_user, "id", None),
            origin_device_id=getattr(g, "annotation_origin_device_id", None),
        )
    except Exception:
        log.error(
            "Kobo PATCH recovery spool could not stage user_id=%s bytes=%s",
            getattr(current_user, "id", None), len(raw_body), exc_info=True,
        )
        return None


def _mark_patch_spool_outcome(ticket, status):
    if ticket is None:
        return
    try:
        ticket.mark_dispatch_outcome(status)
    except Exception:
        log.error(
            "Kobo PATCH recovery spool outcome failed spool_id=%s status=%s",
            getattr(ticket, "spool_id", None), status, exc_info=True,
        )


def get_book_identifiers(book):
    """Extract relevant identifiers from book."""
    identifiers = {}
    if book and book.identifiers:
        for identifier in book.identifiers:
            id_type = identifier.type.lower()
            if id_type in ['hardcover-id', 'hardcover-edition', 'hardcover-slug', 'isbn']:
                identifiers[id_type] = identifier.val
    return identifiers


def log_annotation_data(entitlement_id, method, data=None):
    """Log structural annotation telemetry without user content or secrets."""
    book = get_book_by_entitlement_id(entitlement_id)
    updated_count = 0
    deleted_count = 0
    if isinstance(data, dict):
        updated = data.get("updatedAnnotations")
        deleted = data.get("deletedAnnotationIds")
        updated_count = len(updated) if isinstance(updated, list) else 0
        deleted_count = len(deleted) if isinstance(deleted, list) else 0
    log.debug(
        "ANNOTATION method=%s user_id=%s book_id=%s updated_count=%s deleted_count=%s",
        method, getattr(current_user, "id", None), getattr(book, "id", None),
        updated_count, deleted_count,
    )


class EpubProgressCalculator:
    """
    Helper class to calculate progress from EPUB/KEPUB files efficiently.
    Parses the book structure once and reuses it for multiple calculations.
    """
    def __init__(self, book: db.Books):
        self.book = book
        self.spine_items: list[str] = []
        self.chapter_lengths: list[int] = []
        self.total_chars = 0
        self.initialized = False
        self.error = False

    def _initialize(self):
        if self.initialized:
            return

        if not self.book or not self.book.path:
            self.error = True
            return

        book_data = None
        kepub_datas = [data for data in self.book.data if data.format.lower() == 'kepub']
        if len(kepub_datas) >= 1:
            book_data = kepub_datas[0]
        else:
            epub_datas = [data for data in self.book.data if data.format.lower() == 'epub']
            if len(epub_datas) >= 1:
                book_data = epub_datas[0]
        
        if not book_data:
            self.error = True
            return

        try:
            file_path = os.path.normpath(os.path.join(
                config.get_book_path(),
                self.book.path,
                book_data.name + "." + book_data.format.lower()
            ))
            
            if not os.path.exists(file_path):
                self.error = True
                return
            
            with zipfile.ZipFile(file_path, 'r') as epub_zip:
                # Find OPF
                container_data = epub_zip.read('META-INF/container.xml')
                container_tree = etree.fromstring(container_data)
                ns = {
                    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
                    'opf': 'http://www.idpf.org/2007/opf'
                }
                opf_path = container_tree.xpath(
                    '//container:rootfile/@full-path',
                    namespaces={'container': ns['container']}
                )[0]
                
                # Parse OPF
                opf_data = epub_zip.read(opf_path)
                opf_tree = etree.fromstring(opf_data)
                opf_dir = os.path.dirname(opf_path)
                
                # Get manifest
                manifest = {}
                for item in opf_tree.xpath('//opf:manifest/opf:item', namespaces={'opf': ns['opf']}):
                    item_id = item.get('id')
                    href = item.get('href')
                    if item_id and href:
                        full_href = os.path.normpath(os.path.join(opf_dir, href)).replace('\\', '/')
                        manifest[item_id] = full_href
                
                # Get spine
                for itemref in opf_tree.xpath('//opf:spine/opf:itemref', namespaces={'opf': ns['opf']}):
                    idref = itemref.get('idref')
                    if idref and idref in manifest:
                        self.spine_items.append(manifest[idref])
                
                if not self.spine_items:
                    self.error = True
                    return

                # Calculate lengths
                for spine_item in self.spine_items:
                    try:
                        content = epub_zip.read(spine_item).decode('utf-8', errors='ignore')
                        try:
                            html_tree = etree.fromstring(content.encode('utf-8'))
                            text_content = ''.join(html_tree.itertext())
                            char_count = len(text_content.strip())
                        except etree.XMLSyntaxError:
                            text_content = re.sub(r'<[^>]+>', '', content)
                            char_count = len(text_content.strip())
                        self.chapter_lengths.append(char_count)
                    except Exception:
                        self.chapter_lengths.append(0)
                
                self.total_chars = sum(self.chapter_lengths)
                self.initialized = True

        except Exception as e:
            log.error(f"Error initializing EPUB calculator: {e}")
            self.error = True

    def calculate(self, chapter_filename: str, chapter_progress: float):
        if not self.initialized:
            self._initialize()
        
        if self.error or self.total_chars == 0:
            return None

        normalized_chapter = chapter_filename.replace('\\', '/')
        target_chapter_index = None
        
        for idx, spine_item in enumerate(self.spine_items):
            if normalized_chapter in spine_item or spine_item.endswith(normalized_chapter):
                target_chapter_index = idx
                break
        
        if target_chapter_index is None:
            return None
        
        chars_before = sum(self.chapter_lengths[:target_chapter_index])
        chars_in_chapter = self.chapter_lengths[target_chapter_index]
        chars_read = chars_before + (chars_in_chapter * chapter_progress)
        
        return (chars_read / self.total_chars) * 100


class AnnotationSpan(TypedDict):
    """Kobo annotation span location data."""
    chapterFilename: str
    chapterProgress: float
    chapterTitle: str
    endChar: int
    endPath: str
    startChar: int
    startPath: str


class AnnotationLocation(TypedDict):
    """Kobo annotation location data."""
    span: AnnotationSpan


class KoboAnnotation(TypedDict):
    """Kobo annotation structure from Reading Services API."""
    clientLastModifiedUtc: str
    highlightColor: str
    highlightedText: NotRequired[str]
    id: str
    location: AnnotationLocation
    noteText: NotRequired[str]
    type: str  # "note" or "highlight"


def _dispatch_kobo_annotation_deletes(
    annotation_sync, deleted, entitlement_id, book, *, commit=True,
    deferred_effects=None,
):
    if deleted is not None and not isinstance(deleted, list):
        log.warning(
            "Ignoring deletedAnnotationIds for entitlement %s: expected a list",
            entitlement_id,
        )
        return True
    elif deleted:
        # Nickel can only name annotations Kobo created: CWNG has no annotation
        # writeback to Kobo. If F-3b565b implements writeback, this provenance
        # authority must be revisited.
        kwargs = {
            "book_id": book.id,
            "deletable_sources": {"kobo"},
        }
        if not commit or deferred_effects is not None:
            kwargs.update(commit=commit, deferred_effects=deferred_effects)
        return annotation_sync.dispatch_annotation_deletes(
            deleted, current_user, **kwargs,
        )
    return True


class _AtomicOwnedPatchRefused(RuntimeError):
    """Abort an owned PATCH without allowing its SAVEPOINT to release."""


def _persist_owned_patch_atomically(
    annotation_sync, *, updated, deleted, deterministic_update_rejection,
    book, entitlement_id, dispatch_kwargs,
):
    """Commit the complete owned PATCH and authority watermark exactly once."""
    from cps.services.kobo_annotation_authority import (
        advance_authoritative_patch_revision,
        mark_authoritative_oversize,
    )

    effects = annotation_sync.new_deferred_dispatch_effects()
    try:
        # #1925 request-level discipline: begin_contained_nested() first forces
        # a real SQLite outer transaction, so releasing this SAVEPOINT cannot
        # make annotation rows durable ahead of the checked outer commit.
        with ub.begin_contained_nested(ub.session):
            if deterministic_update_rejection:
                if not _dispatch_kobo_annotation_deletes(
                    annotation_sync, deleted, entitlement_id, book,
                    commit=False, deferred_effects=effects,
                ):
                    raise _AtomicOwnedPatchRefused("delete dispatch refused")
            if updated:
                persisted = annotation_sync.dispatch_annotation_sync(
                    updated, book, current_user,
                    commit=False, deferred_effects=effects, **dispatch_kwargs,
                )
                if persisted is False:
                    raise _AtomicOwnedPatchRefused("update dispatch refused")
            if not deterministic_update_rejection:
                if not _dispatch_kobo_annotation_deletes(
                    annotation_sync, deleted, entitlement_id, book,
                    commit=False, deferred_effects=effects,
                ):
                    raise _AtomicOwnedPatchRefused("delete dispatch refused")
            if not mark_authoritative_oversize(
                current_user.id, book.id, log=log, commit=False,
            ):
                raise _AtomicOwnedPatchRefused("oversize classification refused")
            if not advance_authoritative_patch_revision(
                current_user.id, book.id, log=log, commit=False,
            ):
                raise _AtomicOwnedPatchRefused("authority watermark refused")
            ub.session.flush()
        if ub.session_commit() is False:
            raise _AtomicOwnedPatchRefused("combined request commit failed")
    except Exception:
        if ub.session is not None:
            ub.session.rollback()
        log.exception(
            "Kobo owned PATCH transaction rolled back user_id=%s book_id=%s",
            getattr(current_user, "id", None), book.id,
        )
        return False

    annotation_sync.finalize_deferred_dispatch_effects(
        current_user, effects, book=book,
    )
    return True


@csrf.exempt
@readingservices_api_v3.route("/content/<entitlement_id>/annotations", methods=["GET", "PATCH"])
@requires_reading_services_auth_and_config
def handle_annotations(entitlement_id):
    """Handle annotation requests for a specific book.

    GET: fully seeded owned books are answered from CWNG's complete visible
    set; unseeded and unowned books retain the byte-transparent Kobo proxy.
    PATCH: persist locally (source='kobo'), then dispatch through
    each registered + enabled annotation_sync handler (Hardcover today; future
    Readwise / Notion / etc.). All DB writes happen in the dispatcher; this
    handler is a thin orchestrator. Fully seeded owned books are acknowledged
    locally; unseeded books continue upstream so Kobo's copy is not starved.

    The exact PATCH body is durably staged before parsing and dispatch so an
    interrupted local capture can be replayed server-side. Local persistence is
    independent of whether any external sync target is enabled.
    """
    try:
        raw_body = request.get_data(cache=True)
    except Exception:
        # Reading the body used to sit inside the PATCH try-block, so a failed
        # read (oversized body, client disconnect) became the deliberate 503.
        # The capture needs the bytes earlier than that, so the guard has to
        # move with it. GET must re-run the same durable-authority containment
        # as its normal path: a current snapshot is the only safe failure-path
        # 200, and Kobo may be contacted only after ownership is disproved.
        log.exception(
            "Could not read the annotation request body for entitlement %s",
            entitlement_id,
        )
        if request.method == "PATCH":
            return make_response(
                jsonify({"error": "Annotation capture temporarily unavailable"}), 503,
            )
        ownership = resolve_entitlement_ownership(entitlement_id)
        if ownership is not None and ownership is not OWNERSHIP_UNKNOWN:
            return _owned_annotation_get_response(None, ownership, entitlement_id)
        return _annotation_get_without_live_authority(
            None,
            ownership,
            entitlement_id,
            authenticated_user_id=getattr(current_user, "id", None),
        )
    capture_session = _begin_exchange_capture(
        "annotations_patch" if request.method == "PATCH" else "annotations_get",
        raw_body,
        authentication="authenticated",
        user_id=getattr(current_user, "id", None),
    )
    if request.method == "GET":
        ownership = resolve_entitlement_ownership(entitlement_id)
        if ownership is not None and ownership is not OWNERSHIP_UNKNOWN:
            return _owned_annotation_get_response(
                capture_session, ownership, entitlement_id,
            )
        return _annotation_get_without_live_authority(
            capture_session,
            ownership,
            entitlement_id,
            authenticated_user_id=getattr(current_user, "id", None),
        )

    book = None
    if request.method == "PATCH":
        patch_spool_ticket = _stage_patch_for_recovery(raw_body, entitlement_id)
        # The conservative default is replayable. Every post-stage exit crosses
        # the single finally block below, so a new return cannot silently leave
        # its ticket in the ambiguous initial state. Only a completed dispatch
        # overrides it; every refusal remains an unresolved replay candidate.
        patch_spool_outcome = "dispatch_exception"
        try:
            # The raw body is the authority here. A proxy or client can strip
            # or alter Content-Type without changing whether these bytes are a
            # valid, addressable JSON batch.
            data = request.get_json(silent=True, force=True)
            nonempty_unaddressable_body = False
            if not isinstance(data, dict):
                if raw_body.strip():
                    nonempty_unaddressable_body = True
                # A genuinely empty body is a legitimate no-op sent by Kobo.
                data = {}
            book = resolve_entitlement_ownership(entitlement_id)
            if book is OWNERSHIP_UNKNOWN:
                log.error(
                    "Cannot capture Kobo annotations for entitlement %s: "
                    "ownership lookup failed; refusing the PATCH so the device "
                    "does not treat an uncaptured upload as successful",
                    entitlement_id,
                )
                # Do not proxy: checkforchanges containment prevents an owned
                # book from healing this local gap by downloading from Kobo.
                # A visible PATCH failure preserves the opportunity to retry.
                patch_spool_outcome = "dispatch_refused"
                return make_response(
                    jsonify({"error": "Annotation capture temporarily unavailable"}), 503,
                )
            log_annotation_data(entitlement_id, "PATCH", data)
            if book is None:
                log.warning(
                    "Book not found for entitlement %s; skipping local + Hardcover sync",
                    entitlement_id,
                )
            else:
                if nonempty_unaddressable_body:
                    log.warning(
                        "Refusing local annotation capture for entitlement %s: "
                        "PATCH body is not a JSON object", entitlement_id,
                    )
                    patch_spool_outcome = "dispatch_refused"
                    return make_response(
                        jsonify({"error": "Annotation capture temporarily unavailable"}),
                        503,
                    )
                from cps.services import annotation_sync
                updated = data.get("updatedAnnotations")
                deleted = data.get("deletedAnnotationIds")
                deterministic_update_rejection = (
                    bool(updated) and not isinstance(updated, list)
                )
                local_authority = _owned_patch_is_local_authority(
                    book, entitlement_id,
                )
                # Falsy non-list spellings cannot contain an annotation. Treat
                # them as an empty update set so a delete-carrying batch is not
                # trapped in a permanent retry. A truthy non-list value may be
                # a malformed annotation and still goes through the defensive
                # dispatcher, whose False result is refused below.
                raw_materializations = None
                trace_id = None
                if updated:
                    if isinstance(updated, list) and updated:
                        trace_id = secrets.token_hex(8)
                        try:
                            from cps.services.kobo_annotation_capture import (
                                extract_updated_annotation_materializations,
                            )
                            raw_materializations = extract_updated_annotation_materializations(raw_body)
                            from cps.services import kobo_annotation_stage0
                            kobo_annotation_stage0.record_event(
                                "raw_capture", "extracted", trace_id=trace_id,
                                user_id=getattr(current_user, "id", None), book_id=book.id,
                                annotation_count=len(raw_materializations),
                            )
                        except Exception:
                            log.warning(
                                "Kobo raw lexical capture failed trace_id=%s user_id=%s book_id=%s",
                                trace_id, getattr(current_user, "id", None), book.id,
                                exc_info=True,
                            )
                            from cps.services import kobo_annotation_stage0
                            kobo_annotation_stage0.record_event(
                                "raw_capture", "failed", trace_id=trace_id,
                                user_id=getattr(current_user, "id", None), book_id=book.id,
                                annotation_count=len(updated),
                            )
                dispatch_kwargs = {
                    "origin_device_id": getattr(g, "annotation_origin_device_id", None),
                }
                if raw_materializations is not None:
                    dispatch_kwargs.update(
                        raw_materializations=raw_materializations,
                        trace_id=trace_id,
                    )

                if local_authority:
                    if not _persist_owned_patch_atomically(
                        annotation_sync,
                        updated=updated,
                        deleted=deleted,
                        deterministic_update_rejection=deterministic_update_rejection,
                        book=book,
                        entitlement_id=entitlement_id,
                        dispatch_kwargs=dispatch_kwargs,
                    ):
                        log.error(
                            "Kobo annotation PATCH was not fully persisted with "
                            "its authority watermark for user_id=%s book_id=%s",
                            getattr(current_user, "id", None), book.id,
                        )
                        patch_spool_outcome = "dispatch_refused"
                        return make_response(
                            jsonify({"error": "Annotation capture temporarily unavailable"}),
                            503,
                        )
                else:
                    if deterministic_update_rejection:
                        _dispatch_kobo_annotation_deletes(
                            annotation_sync, deleted, entitlement_id, book,
                        )
                    if updated:
                        persisted = annotation_sync.dispatch_annotation_sync(
                            updated, book, current_user, **dispatch_kwargs,
                        )
                        if persisted is False:
                            # False means complete batch persistence is unproven,
                            # not that zero rows reached SQLite. On this engine a
                            # released SAVEPOINT can survive a later rollback, so
                            # keep the raw body for replay/reconciliation either way.
                            log.error(
                                "Kobo annotation PATCH was not fully persisted for "
                                "user_id=%s book_id=%s; refusing to acknowledge it upstream",
                                getattr(current_user, "id", None), book.id,
                            )
                            patch_spool_outcome = "dispatch_refused"
                            return make_response(
                                jsonify({"error": "Annotation capture temporarily unavailable"}),
                                503,
                            )
                    if not deterministic_update_rejection:
                        deletes_persisted = _dispatch_kobo_annotation_deletes(
                            annotation_sync, deleted, entitlement_id, book,
                        )
                        if deletes_persisted is False:
                            log.error(
                                "Kobo annotation deletes were not fully persisted for "
                                "user_id=%s book_id=%s; refusing to acknowledge them",
                                getattr(current_user, "id", None), book.id,
                            )
                            patch_spool_outcome = "dispatch_refused"
                            return make_response(
                                jsonify({"error": "Annotation capture temporarily unavailable"}),
                                503,
                            )
            patch_spool_outcome = "dispatch_completed"
            if book is not None and local_authority:
                return _owned_annotation_patch_ack(
                    capture_session, book, entitlement_id,
                )
        except Exception:
            log.exception("Error processing PATCH annotations")
            return make_response(
                jsonify({"error": "Annotation capture temporarily unavailable"}), 503,
            )
        finally:
            _mark_patch_spool_outcome(patch_spool_ticket, patch_spool_outcome)
    return _proxy_annotation_request(capture_session, book, entitlement_id)


@csrf.exempt
@readingservices_api_v3.route("/content/checkforchanges", methods=["POST"])
@requires_reading_services_auth_and_config
def handle_check_for_changes():
    """Keep locally-owned content out of Nickel's destructive GET trigger."""
    return _handle_check_for_changes()


def _handle_check_for_changes():
    """Apply ownership containment independent of route and session spelling."""
    raw_body = request.get_data(cache=True)
    capture_session = _begin_exchange_capture("checkforchanges", raw_body)
    entries = _parse_check_for_changes_request(raw_body)
    if entries is None:
        log.warning("Not proxying an unrecognized Kobo checkforchanges request")
        return jsonify([])

    filtered_ids = set()
    for index, entry in enumerate(entries):
        content_id = entry["ContentId"]
        ownership = resolve_entitlement_ownership(content_id)
        is_filtered = _check_for_changes_ownership_is_filtered(ownership)
        if is_filtered:
            filtered_ids.add(content_id)
        if capture_session is not None:
            capture_session.add_decision(
                stage="device_request",
                index=index,
                content_id=content_id,
                ownership=_capture_ownership_label(ownership),
                authority_status=_capture_authority_status(ownership),
                action="suppressed" if is_filtered else "proxied",
            )
    outbound_entries = _filter_check_for_changes_entries(entries, filtered_ids)
    if not outbound_entries:
        return jsonify([])

    outbound_body = json.dumps(
        outbound_entries, separators=(",", ":")
    ).encode("utf-8")
    if capture_session is None:
        upstream = proxy_to_kobo_reading_services(data=outbound_body)
    else:
        upstream = proxy_to_kobo_reading_services(
            data=outbound_body, capture_session=capture_session,
        )
    if upstream.status_code in (401, 403):
        # An auth error is not a successful changed-ContentIds answer, even if
        # its body happens to parse as a list. Propagate it so Nickel can
        # re-authenticate without triggering a destructive annotation GET.
        return upstream
    upstream_entries = upstream.get_json(silent=True)
    response_ids = _check_for_changes_response_content_ids(upstream_entries)
    if response_ids is None:
        log.warning("Discarding an unrecognized Kobo checkforchanges response")
        return jsonify([])

    for index, content_id in enumerate(response_ids):
        ownership = resolve_entitlement_ownership(content_id)
        is_filtered = _check_for_changes_ownership_is_filtered(ownership)
        if is_filtered:
            filtered_ids.add(content_id)
        if capture_session is not None:
            capture_session.add_decision(
                stage="upstream_response",
                index=index,
                content_id=content_id,
                ownership=_capture_ownership_label(ownership),
                authority_status=_capture_authority_status(ownership),
                action="suppressed" if is_filtered else "returned",
            )
    return jsonify(_filter_check_for_changes_entries(upstream_entries, filtered_ids))


@csrf.exempt
@readingservices_userstorage.route("/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@requires_reading_services_auth_and_config
def handle_user_storage(subpath):
    """
    Handle UserStorage API requests (e.g., /api/UserStorage/Metadata).
    Proxies to Kobo's reading services.
    """
    
    # Proxy to Kobo reading services
    return proxy_to_kobo_reading_services()


@csrf.exempt
@readingservices_api_v3.route("/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@requires_reading_services_auth_and_config
def handle_unknown_reading_service_request(subpath):
    """
    Catch-all handler for any reading services requests not explicitly handled.
    Logs the request and proxies to Kobo's reading services.
    """
    if _is_check_for_changes_path(request.path):
        return _handle_check_for_changes()
    # Proxy to Kobo reading services
    return proxy_to_kobo_reading_services()
