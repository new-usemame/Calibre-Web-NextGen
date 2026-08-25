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
from datetime import datetime, timezone
from functools import wraps
from typing import TypedDict, NotRequired
from flask import Blueprint, request, make_response, jsonify, abort, g, after_this_request
from werkzeug.datastructures import Headers
import requests
from lxml import etree

from . import logger, calibre_db, db, config, ub, csrf
from .cw_login import current_user, login_required
from .services import hardcover

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
        if not config.config_kobo_sync and not contains_check_for_changes:
            log.debug("Kobo sync disabled, proxying to Kobo")
            return proxy_to_kobo_reading_services()
        if current_user.is_authenticated:
            if config.config_kobo_sync:
                try:
                    from .services.device_registry import register_kobo_device_best_effort
                    g.annotation_origin_device_id = register_kobo_device_best_effort(
                        user_id=current_user.id, headers=request.headers, return_internal=True,
                    )
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


def _dispatch_kobo_annotation_deletes(annotation_sync, deleted, entitlement_id, book):
    if deleted is not None and not isinstance(deleted, list):
        log.warning(
            "Ignoring deletedAnnotationIds for entitlement %s: expected a list",
            entitlement_id,
        )
    elif deleted:
        # Nickel can only name annotations Kobo created: CWNG has no annotation
        # writeback to Kobo. If F-3b565b implements writeback, this provenance
        # authority must be revisited.
        annotation_sync.dispatch_annotation_deletes(
            deleted, current_user, book_id=book.id,
            deletable_sources={"kobo"},
        )


@csrf.exempt
@readingservices_api_v3.route("/content/<entitlement_id>/annotations", methods=["GET", "PATCH"])
@requires_reading_services_auth_and_config
def handle_annotations(entitlement_id):
    """Handle annotation requests for a specific book.

    GET: proxied directly to Kobo.
    PATCH: intercept — persist locally (source='kobo'), then dispatch through
    each registered + enabled annotation_sync handler (Hardcover today; future
    Readwise / Notion / etc.). All DB writes happen in the dispatcher; this
    handler is a thin orchestrator.

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
        # move with it -- but it must NOT become a blanket 503: a 503 on the
        # annotations GET is one of the three measured answers that makes Nickel
        # empty the book's local annotation set. Refuse the PATCH, proxy the GET.
        log.exception(
            "Could not read the annotation request body for entitlement %s",
            entitlement_id,
        )
        if request.method == "PATCH":
            return make_response(
                jsonify({"error": "Annotation capture temporarily unavailable"}), 503,
            )
        return proxy_to_kobo_reading_services()
    capture_session = _begin_exchange_capture(
        "annotations_patch" if request.method == "PATCH" else "annotations_get",
        raw_body,
        authentication="authenticated",
        user_id=getattr(current_user, "id", None),
    )
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
                # Falsy non-list spellings cannot contain an annotation. Treat
                # them as an empty update set so a delete-carrying batch is not
                # trapped in a permanent retry. A truthy non-list value may be
                # a malformed annotation and still goes through the defensive
                # dispatcher, whose False result is refused below.
                if deterministic_update_rejection:
                    _dispatch_kobo_annotation_deletes(
                        annotation_sync, deleted, entitlement_id, book,
                    )
                if updated:
                    raw_materializations = None
                    trace_id = None
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
                    _dispatch_kobo_annotation_deletes(
                        annotation_sync, deleted, entitlement_id, book,
                    )
            patch_spool_outcome = "dispatch_completed"
        except Exception:
            log.exception("Error processing PATCH annotations")
            return make_response(
                jsonify({"error": "Annotation capture temporarily unavailable"}), 503,
            )
        finally:
            _mark_patch_spool_outcome(patch_spool_ticket, patch_spool_outcome)
    # Proxy both GET + PATCH. Do not refuse GET: hardware testing showed that a
    # 503 (or a hung request) makes Nickel empty its local annotations. The safe
    # containment point is checkforchanges, before Nickel decides to GET.
    if capture_session is None:
        return proxy_to_kobo_reading_services()
    return proxy_to_kobo_reading_services(capture_session=capture_session)


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
