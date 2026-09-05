#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import base64
from collections import Counter
import hashlib
import logging
from datetime import datetime, timedelta, timezone
import os
import secrets
import uuid
import zipfile
from time import gmtime, monotonic, strftime
import json
from urllib.parse import unquote

from flask import (
    Blueprint,
    request,
    make_response,
    jsonify,
    current_app,
    url_for,
    redirect,
    abort,
    Response,
    send_from_directory,
    g,
    has_request_context,
    after_this_request,
)
from .cw_login import current_user
from werkzeug.datastructures import Headers
from sqlalchemy import String, case, cast, func, literal
from sqlalchemy.sql.expression import and_, or_
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import joinedload, sessionmaker
from sqlalchemy.sql import select
import requests

from . import config, constants, logger, kobo_auth, db, calibre_db, helper, shelf as shelf_lib, ub, csrf, kobo_sync_status, magic_shelf, user_library
from . import isoLanguages
from .epub import get_epub_layout
from .constants import COVER_THUMBNAIL_SMALL, COVER_THUMBNAIL_MEDIUM, COVER_THUMBNAIL_LARGE, CACHE_TYPE_THUMBNAILS
from .kobo_cover_cache import build_cover_image_id, normalize_cover_uuid
from .helper import get_download_link
from .services import SyncToken as SyncToken, hardcover
from .services import cover_preview, parallel, user_cover
from .services import device_reading_position as device_positions
from .fs import FileSystem
from .web import download_required
from .kobo_auth import requires_kobo_auth, get_auth_token

KOBO_FORMATS = {"KEPUB": ["KEPUB"], "EPUB": ["EPUB3", "EPUB"]}
KOBO_STOREAPI_URL = "https://storeapi.kobo.com"
KOBO_IMAGEHOST_URL = "https://cdn.kobo.com/book-images"

SYNC_ITEM_LIMIT = 100
# Nickel reports a fresh/replaced download at the cover.  Only this narrow
# start-of-book shape is eligible for the armed rehydrate no-regress guard;
# ordinary newer backward jumps remain intentional reading movements.
KOB0_COVER_RESET_PROGRESS_EPSILON = 1.0

# Stored alongside the payload hash, never sent to Kobo.  Increment this when
# the server intentionally changes the entitlement renderer's declared shape;
# unchanged book/tombstone bases will then be lazily re-fingerprinted instead
# of being re-delivered to Nickel.
ENTITLEMENT_PAYLOAD_SCHEMA_VERSION = 1

# Stored in KoboDeviceEntitlementSeed, never sent to the device. Version 1
# replaces Books.timestamp watermark classification with the physical-device
# entitlement ledger and audits rows written by the legacy classifier once.
ENTITLEMENT_CLASSIFICATION_VERSION = 1

kobo = Blueprint("kobo", __name__, url_prefix="/kobo/<auth_token>")
kobo_auth.disable_failed_auth_redirect_for_blueprint(kobo)
kobo_auth.register_url_value_preprocessor(kobo)

log = logger.create()


def _entitlement_fingerprint(entitlement):
    """Stable hash of fields that can change Nickel's local book record."""
    payload = json.dumps(
        entitlement,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _capture_query_identities(query, identity_column):
    """Freeze one ordered candidate membership without rendering payloads."""
    identity_rows = (
        query.enable_eagerloads(False)
        .with_entities(identity_column)
        .all()
    )
    return tuple(row[0] for row in identity_rows)


def _bounded_query_pages(
        query, snapshot_identities, identity_column, row_identity, page_size):
    """Yield fixed pages from one immutable candidate-identity frontier.

    Replay suppression happens after payload rendering, so SQL cannot know
    which candidates consume response capacity. Capture ordered identities
    once, then refetch only each captured slice. Inserts after capture are not
    members, and deletes cannot shift a later member past an OFFSET. A short
    or empty slice therefore does not end the scan; later captured identities
    still get their turn. Every fetch is limited to its remaining slice size.
    """
    if page_size <= 0:
        return
    for start in range(0, len(snapshot_identities), page_size):
        identity_slice = snapshot_identities[start:start + page_size]
        identity_order = {
            identity: position
            for position, identity in enumerate(identity_slice)
        }
        page = (
            query.filter(identity_column.in_(identity_slice))
            .limit(len(identity_slice))
            .all()
        )
        page.sort(key=lambda row: identity_order[row_identity(row)])
        if page:
            yield page


def _ledger_timestamp_component(value):
    """Return one fixed-width UTC component for byte-comparable provenance."""
    if value is None:
        return "none"
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds") + "Z"


def _book_entitlement_change_basis(
    book_last_modified, archived_last_modified=None,
):
    """Canonical per-book proof used by both seed and live delivery paths.

    Membership clocks deliberately do not participate: they select a book but
    do not describe its entitlement bytes, and a book may have several shelf
    rows while its ledger has only one row.  Fingerprint equality handles
    byte-identical membership churn before this basis is consulted.
    """
    if book_last_modified is None and archived_last_modified is None:
        return None
    return "v1|book={}|archive={}".format(
        _ledger_timestamp_component(book_last_modified),
        _ledger_timestamp_component(archived_last_modified),
    )


def _deleted_entitlement_change_basis(deleted_at):
    """Canonical provenance for a hard-delete entitlement."""
    if deleted_at is None:
        return None
    return "v1|deleted={}".format(_ledger_timestamp_component(deleted_at))


def _entitlement_replay_decision(record, fingerprint, change_basis):
    """Return ``(suppress, shape_reseed, refresh_record)`` for one candidate.

    Exact bytes are always safe to suppress. A differing fingerprint is safe
    to reseed only across an explicitly declared renderer-schema transition
    whose non-null canonical book basis is unchanged. Every ambiguous case,
    including a legacy #1925 row with no basis, fails open by delivering.
    Out-of-tree metadata writers must advance ``Books.last_modified`` for each
    payload-affecting edit; direct writes that do not are indistinguishable
    from the declared renderer change at this server-side boundary.
    """
    if record is None:
        return False, False, False

    stored_basis = record.change_basis
    if record.fingerprint == fingerprint:
        refresh_record = bool(
            record.payload_schema_version
            != ENTITLEMENT_PAYLOAD_SCHEMA_VERSION
            or stored_basis != change_basis
        )
        return True, False, refresh_record

    declared_shape_transition = (
        record.payload_schema_version != ENTITLEMENT_PAYLOAD_SCHEMA_VERSION
        and stored_basis is not None
        and change_basis is not None
        and stored_basis == change_basis
    )
    if declared_shape_transition:
        return True, True, True

    return False, False, False


def _sync_token_hash(raw_token):
    """Return a fixed-size key for an opaque incoming Kobo sync token."""
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def _rollback_after_sync_failure():
    """Roll back a failed sync-state write without losing the retryable reply.

    A rollback can itself fail when the database has gone away; the caller
    is already on its failure path and must still reach abort(503).
    """
    try:
        ub.session.rollback()
    except Exception:  # noqa: BLE001 - failure-only boundary
        pass


def _mark_pending_page_reset_staged(staged):
    """Track the narrow request window that owns a non-CWNG page reset."""
    if has_request_context():
        g.kobo_pending_page_reset_staged = bool(staged)


def _pending_page_reset_is_staged():
    return bool(
        has_request_context()
        and getattr(g, "kobo_pending_page_reset_staged", False)
    )


def _warn_sync_header_budget(response):
    """Observe the HTTP/1 header block without affecting sync delivery.

    Count each Latin-1 WSGI header name/value, ': ', CRLF, and the final
    empty line. Server-added headers and the status line are not available
    here, so this measures the application's header block only.
    """
    try:
        header_bytes = 2 + sum(
            len(name.encode("latin-1")) + len(value.encode("latin-1")) + 4
            for name, value in response.headers
        )
        if header_bytes >= 4096:
            token_bytes = len(response.headers.get(
                SyncToken.SyncToken.SYNC_TOKEN_HEADER, "",
            ).encode("latin-1"))
            log.warning(
                "Kobo Sync: response header budget exceeded: header_bytes=%d "
                "synctoken_bytes=%d store_proxy_enabled=%s; see README Kobo "
                "sync 'nginx buffer sizes': proxy_buffer_size 32k",
                header_bytes, token_bytes, bool(config.config_kobo_proxy),
            )
    except Exception:
        # Diagnostics (including logging failures) must never break delivery.
        pass


def _pending_response(page):
    """Recreate a previously committed response without touching live state."""
    try:
        stored_headers = json.loads(page.response_headers_json or "{}")
        if not isinstance(stored_headers, dict):
            raise TypeError("pending response headers must decode to an object")
    except (TypeError, ValueError):
        log.exception(
            "Kobo Sync: pending response headers are corrupt for device %s",
            page.device_id,
        )
        return None
    response = make_response(page.response_body)
    for header_name, header_value in stored_headers.items():
        response.headers[header_name] = header_value
    response.headers[SyncToken.SyncToken.SYNC_TOKEN_HEADER] = page.outgoing_token
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    _warn_sync_header_budget(response)
    return response


def _pending_position_clock(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def _acknowledge_pending_page(
        page, requesting_device_id, acknowledged_rehydrate_book_ids=None):
    """Promote one response only after its returned token comes back.

    No commit happens here.  The acknowledgment and the replacement pending
    page land atomically at HandleSyncRequest's checked request boundary.
    """
    try:
        confirmation = json.loads(page.confirmation_json or "{}")
        payload_schema_version = int(confirmation.get(
            "payload_schema_version",
            ENTITLEMENT_PAYLOAD_SCHEMA_VERSION,
        ))

        live_rows = confirmation.get("live_entitlements", {})
        live_fingerprints = {
            int(book_id): values["fingerprint"]
            for book_id, values in live_rows.items()
        }
        live_change_bases = {
            int(book_id): values.get("change_basis")
            for book_id, values in live_rows.items()
        }
        kobo_sync_status.stage_device_entitlement_fingerprints(
            requesting_device_id,
            live_fingerprints,
            live_change_bases,
            payload_schema_version,
        )
        kobo_sync_status.add_synced_books_batch(
            confirmation.get("synced_books", []),
            commit=False,
        )
        device_positions.mark_rehydrate_needed(
            requesting_device_id,
            confirmation.get("rehydrate_book_ids", []),
        )

        deleted_rows = confirmation.get("deleted_entitlements", {})
        deleted_fingerprints = {
            book_uuid: values["fingerprint"]
            for book_uuid, values in deleted_rows.items()
        }
        deleted_change_bases = {
            book_uuid: values.get("change_basis")
            for book_uuid, values in deleted_rows.items()
        }
        kobo_sync_status.stage_device_deleted_entitlement_fingerprints(
            requesting_device_id,
            deleted_fingerprints,
            deleted_change_bases,
            payload_schema_version,
        )

        removed_book_ids = confirmation.get("removed_book_ids", [])
        if removed_book_ids:
            user_device_ids = ub.session.query(ub.Device.id).filter(
                ub.Device.user_id == current_user.id,
            ).scalar_subquery()
            ub.session.query(ub.KoboDeviceBookEntitlement).filter(
                ub.KoboDeviceBookEntitlement.device_id.in_(user_device_ids),
                ub.KoboDeviceBookEntitlement.book_id.in_(removed_book_ids),
            ).delete(synchronize_session=False)
            ub.session.query(ub.KoboSyncedBooks).filter(
                ub.KoboSyncedBooks.user_id == current_user.id,
                ub.KoboSyncedBooks.book_id.in_(removed_book_ids),
            ).delete(synchronize_session=False)
            # Once the device acknowledges that an entitlement is gone, its
            # device-specific repair journal is no longer meaningful. In
            # particular, a partial-token reset may have re-armed an
            # unconfirmed position echo before the removal was calculated.
            # The authoritative user reading state remains separate, and a
            # future entitlement acknowledgment recreates this device row.
            ub.session.query(ub.DeviceReadingPosition).filter(
                ub.DeviceReadingPosition.device_id
                == int(requesting_device_id),
                ub.DeviceReadingPosition.book_id.in_(removed_book_ids),
            ).delete(
                synchronize_session=False,
            )

        for emitted in confirmation.get("rehydrate_positions", []):
            position = ub.session.query(ub.DeviceReadingPosition).filter_by(
                id=int(emitted["id"]),
                device_id=int(requesting_device_id),
            ).one_or_none()
            if position is None or not position.rehydrate_needed:
                continue
            emitted_clock = datetime.fromisoformat(emitted["server_modified_at"])
            if not device_positions.timestamp_is_newer(
                position.server_modified_at, emitted_clock,
            ):
                position.rehydrate_needed = False
                if acknowledged_rehydrate_book_ids is not None:
                    acknowledged_rehydrate_book_ids.append(position.book_id)

        kobo_sync_status.delete_pending_sync_page(requesting_device_id)
        return True
    except Exception:
        _rollback_after_sync_failure()
        try:
            log.exception(
                "Kobo Sync: failed to acknowledge pending page for device %s",
                requesting_device_id,
            )
        except Exception:  # noqa: BLE001 - the caller's 503 must not depend on logging
            pass
        return False


def _reset_pending_page_for_non_cwng_token(page, requesting_device_id):
    """Abandon a serial page without losing its unconfirmed state echo.

    A partial, malformed, or store token cannot prove that the device received
    the exact pending page, so its entitlement confirmation must remain
    unpromoted. Entitlements have their acknowledged fingerprint recovery
    path, and ordinary repair pages retain their still-armed position latches.
    A confirmation echo is different: acknowledging its predecessor already
    cleared the latch. Re-arm only those echoed book IDs before discarding the
    page so the reset request can regenerate the state through the normal
    bounded rehydrate path.

    The re-arm and pending-page deletion remain staged in the caller's
    transaction. The request boundary rolls them back on both explicit aborts
    and exceptions that escape later query/render work. A successful request
    clears that boundary only after its checked commit durably replaces the
    page.
    """
    try:
        confirmation = json.loads(page.confirmation_json or "{}")
        confirmation_echo_book_ids = confirmation.get(
            "confirmation_echo_book_ids", [],
        )
        device_positions.mark_rehydrate_needed(
            requesting_device_id, confirmation_echo_book_ids,
        )
        kobo_sync_status.delete_pending_sync_page(requesting_device_id)
        _mark_pending_page_reset_staged(True)
        return True
    except Exception:
        _rollback_after_sync_failure()
        try:
            log.exception(
                "Kobo Sync: failed to reset pending page for device %s",
                requesting_device_id,
            )
        except Exception:  # noqa: BLE001 - preserve retryable 503
            pass
        return False


def _seed_existing_device_entitlement_ledgers(user_id):
    """Mark the conservative pre-ack upgrade boundary for known Kobos.

    ``KoboSyncedBooks`` is user-wide and proves only that this server once
    emitted something for a book.  It cannot prove which physical device
    received a response, so copying it into a device's confirmed ledger can
    permanently starve that device.  Existing rows therefore remain
    unconfirmed and drain through the normal bounded missing-ledger query.

    The marker still distinguishes a pre-existing device from a device added
    later, and all writes remain staged for HandleSyncRequest's checked commit.
    """
    device_ids = kobo_sync_status.get_unseeded_kobo_device_ids(user_id)
    if not device_ids:
        return True

    started = monotonic()
    try:
        kobo_sync_status.mark_device_entitlement_ledgers_seeded(device_ids)
    except Exception:
        _rollback_after_sync_failure()
        log.exception(
            "Kobo Sync: failed to seed existing entitlement ledger for user %s",
            user_id,
        )
        return False

    elapsed_ms = round((monotonic() - started) * 1000, 1)
    log.debug(
        "Kobo Sync ledger seed: user=%s devices=%d books=0 deleted=0 "
        "unconfirmed_devices=%d elapsed_ms=%.1f",
        user_id,
        len(device_ids),
        len(device_ids),
        elapsed_ms,
    )
    return True


def _migrate_device_entitlement_classification(user_id):
    """Reclassify every pre-ack device row as unconfirmed exactly once.

    Historical per-device entitlement rows record a committed server emission,
    not device receipt. No timestamp reconstruction can restore that missing
    fact, so those rows are removed and conservatively reannounced as New.
    A device-authored reading-position observation is the narrow durable proof
    that the physical Kobo possessed that book; those books retain only a
    non-matching classification sentinel so they fail open as Changed rather
    than being byte-suppressed from reconstructed present-day metadata.
    """
    device_ids = kobo_sync_status.get_kobo_device_ids_requiring_classification(
        user_id, ENTITLEMENT_CLASSIFICATION_VERSION,
    )
    if not device_ids:
        return True

    started = monotonic()
    try:
        removed = 0
        device_proven = 0
        for device_id in device_ids:
            removed += ub.session.query(
                ub.KoboDeviceBookEntitlement,
            ).filter(
                ub.KoboDeviceBookEntitlement.device_id == int(device_id),
            ).delete(synchronize_session=False)
            removed += ub.session.query(
                ub.KoboDeviceDeletedEntitlement,
            ).filter(
                ub.KoboDeviceDeletedEntitlement.device_id == int(device_id),
            ).delete(synchronize_session=False)
            proven_book_ids = {
                row.book_id for row in ub.session.query(
                    ub.DeviceReadingPosition.book_id,
                ).filter(
                    ub.DeviceReadingPosition.device_id == int(device_id),
                    ub.DeviceReadingPosition.client_modified_at.isnot(None),
                ).all()
            }
            if proven_book_ids:
                kobo_sync_status.stage_device_entitlement_fingerprints(
                    device_id,
                    {
                        book_id: "legacy-position-receipt"
                        for book_id in proven_book_ids
                    },
                    payload_schema_version=0,
                )
                device_proven += len(proven_book_ids)

        kobo_sync_status.mark_device_entitlement_classification(
            device_ids, ENTITLEMENT_CLASSIFICATION_VERSION,
        )
    except Exception:
        _rollback_after_sync_failure()
        log.exception(
            "Kobo Sync: failed to migrate entitlement classification for user %s",
            user_id,
        )
        return False

    log.debug(
        "Kobo Sync classification migration: user=%s devices=%d "
        "device_proven=%d rearmed=%d elapsed_ms=%.1f",
        user_id,
        len(device_ids),
        device_proven,
        removed,
        round((monotonic() - started) * 1000, 1),
    )
    return True


def _sync_cursor_summary(sync_token):
    """Compact cursor state for the one-line per-sync diagnostic."""
    return (
        sync_token.books_last_modified,
        sync_token.books_last_id,
        sync_token.books_last_created,
        sync_token.archive_last_modified,
        sync_token.reading_state_last_modified,
        sync_token.tags_last_modified,
        sync_token.magic_shelf_last_id,
        sync_token.magic_shelf_membership_at,
    )


_SYNC_CURSOR_LOG_FIELDS = (
    "books_modified",
    "books_id",
    "books_created",
    "archive_modified",
    "reading_state_modified",
    "tags_modified",
    "magic_shelf_id",
    "magic_shelf_membership",
)


def _sync_cursor_log_value(cursor_summary):
    """Serialize a compact, stable cursor vector for one structured log field."""
    rendered = {}
    for field, value in zip(_SYNC_CURSOR_LOG_FIELDS, cursor_summary):
        if isinstance(value, datetime):
            rendered[field] = value.isoformat()
        else:
            rendered[field] = value
    return json.dumps(rendered, separators=(",", ":"), ensure_ascii=True)


def _sync_device_log_id(device_id):
    """Return a short hashed label without logging a raw device id."""
    raw_device_id = None
    if has_request_context():
        raw_device_id = request.headers.get("x-kobo-deviceid")
    if raw_device_id:
        source = "header:" + raw_device_id
    elif device_id is not None:
        source = "internal:" + str(device_id)
    else:
        return "unknown"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def _begin_sync_exchange_capture(requesting_device_id, raw_sync_token):
    """Attach the existing private exchange recorder to one library sync."""
    try:
        from .services import kobo_exchange_capture

        device_hash = _sync_device_log_id(requesting_device_id)
        x_kobo_sync = request.headers.get("x-kobo-sync")
        selected_headers = [
            # The generic header renderer redacts token-bearing fields. The
            # exact opaque cursor is retained in sync_exchange below, inside
            # the same private, bounded envelope.
            (SyncToken.SyncToken.SYNC_TOKEN_HEADER, raw_sync_token or ""),
            ("x-cwng-device-hash", device_hash),
        ]
        if x_kobo_sync is not None:
            selected_headers.append(("x-kobo-sync", x_kobo_sync))
        capture_session = kobo_exchange_capture.begin_capture(
            exchange="library_sync",
            method=request.method,
            path=request.path,
            query_string=request.query_string,
            headers=selected_headers,
            body=request.get_data(cache=True),
            authentication="authenticated",
            user_id=getattr(current_user, "id", None),
        )
        if capture_session is None:
            return None
        capture_session.record_sync_request(
            incoming_token=raw_sync_token,
            x_kobo_sync=x_kobo_sync,
            device_hash=device_hash,
        )

        @after_this_request
        def _finish_sync_capture(response):
            try:
                capture_session.record_sync_response(
                    outgoing_token=response.headers.get(
                        SyncToken.SyncToken.SYNC_TOKEN_HEADER, "",
                    ),
                )
                capture_session.finish(
                    status=response.status_code,
                    headers=response.headers.items(),
                    body=response.get_data(),
                )
            except Exception:
                # Diagnostics are never part of the sync success contract.
                log.warning(
                    "Kobo exchange capture finalizer failed exchange=library_sync",
                    exc_info=True,
                )
            return response

        return capture_session
    except Exception:
        log.warning(
            "Kobo exchange capture could not attach exchange=library_sync",
            exc_info=True,
        )
        return None


def _sync_entitlement_counts(sync_results):
    """Count final wire entitlements, including proxied store additions."""
    counts = {"new": 0, "changed": 0, "removed": 0}
    for item in sync_results:
        if not isinstance(item, dict):
            continue
        if "NewEntitlement" in item:
            counts["new"] += 1
            entitlement = item.get("NewEntitlement")
        elif "ChangedEntitlement" in item:
            counts["changed"] += 1
            entitlement = item.get("ChangedEntitlement")
        else:
            continue
        if not isinstance(entitlement, dict):
            continue
        book_entitlement = entitlement.get("BookEntitlement")
        if (isinstance(book_entitlement, dict)
                and book_entitlement.get("IsRemoved") is True):
            counts["removed"] += 1
    return counts


def _entitlement_reemit_reason(
    record, fingerprint, change_basis, *, eligible, is_cwng_token, prefix,
):
    """Classify why a ledger-known entitlement was not replay-suppressed."""
    if record.fingerprint != fingerprint:
        if record.change_basis is None:
            detail = "fingerprint_mismatch_missing_basis"
        elif change_basis is not None and record.change_basis == change_basis:
            detail = "fingerprint_mismatch_same_basis"
        else:
            detail = "fingerprint_mismatch_basis_changed"
    elif not is_cwng_token:
        detail = "non_cwng_reset"
    elif not eligible:
        detail = "replay_suppression_disabled"
    else:
        detail = "replay_not_suppressed"
    return prefix + detail


def _observe_entitlement_reemit(
    reasons,
    record,
    fingerprint,
    change_basis,
    *,
    eligible,
    is_cwng_token,
    prefix,
):
    """Record diagnostic-only re-emission detail without affecting sync."""
    try:
        reasons[_entitlement_reemit_reason(
            record,
            fingerprint,
            change_basis,
            eligible=eligible,
            is_cwng_token=is_cwng_token,
            prefix=prefix,
        )] += 1
        return int(record.fingerprint != fingerprint)
    except Exception:
        log.warning(
            "Kobo Sync observability failed reason=reason_classification_failed",
            exc_info=True,
        )
        return 0


def _observe_live_scope_removal(reasons, record, entitlement):
    """Record the explicit removal reason behind the fail-open boundary."""
    try:
        reasons["live_scope_removal"] += 1
        return int(
            record.fingerprint != _entitlement_fingerprint(entitlement)
        )
    except Exception:
        log.warning(
            "Kobo Sync observability failed reason=reason_classification_failed",
            exc_info=True,
        )
        return 0


def _diagnostic_ledger_lookup(loader, device_id, identities, *, scope):
    """Read diagnostic-only ledger state without sharing request rollback.

    Production's file-backed SQLite database receives a separate read session.
    SQLAlchemy cannot open an independent connection to the harness's in-memory
    SQLite database, so tests use a read-only SAVEPOINT whose rollback cannot
    discard caller-owned acknowledgment work.
    """
    if not device_id or not identities:
        return {}

    owned_session = None
    try:
        bind = ub.session.get_bind()
        database = getattr(getattr(bind, "url", None), "database", None)
        in_memory_sqlite = (
            getattr(getattr(bind, "dialect", None), "name", None) == "sqlite"
            and database in {None, "", ":memory:"}
        )
        if in_memory_sqlite:
            with ub.begin_contained_nested(ub.session):
                return loader(
                    device_id, identities, _session=ub.session,
                )

        owned_session = sessionmaker(bind=bind)()
        return loader(device_id, identities, _session=owned_session)
    except Exception:
        log.warning(
            "Kobo Sync observability failed "
            "reason=diagnostic_ledger_read_failed scope=%s",
            scope,
            exc_info=True,
        )
        return {}
    finally:
        if owned_session is not None:
            try:
                owned_session.close()
            except Exception:
                log.warning(
                    "Kobo Sync observability failed "
                    "reason=diagnostic_ledger_reader_close_failed scope=%s",
                    scope,
                    exc_info=True,
                )


def _log_delivery_ledger_read_failure(scope):
    """Record a delivery-critical ledger failure without risking the 503.

    Unlike ``_diagnostic_ledger_lookup``, these reads decide whether an
    entitlement may safely be put on the wire. Their failure must abort the
    sync, while a logging backend failure remains diagnostic-only.
    """
    try:
        log.error(
            "Kobo Sync failed reason=delivery_ledger_read_failed scope=%s",
            scope,
            exc_info=True,
        )
    except Exception:
        pass


def _sync_observability(
    sync_results,
    *,
    suppressed_replay,
    suppressed_unchanged,
    suppressed_removed,
    reseeded_shape_change,
    fingerprint_mismatch_reemitted,
    reemit_reasons,
    replay_suppression_enabled,
    replay_suppression_eligible,
):
    counts = _sync_entitlement_counts(sync_results)
    return {
        "new": counts["new"],
        "changed": counts["changed"],
        "removed": counts["removed"],
        "suppressed_replay": int(suppressed_replay),
        "suppressed_unchanged": int(suppressed_unchanged),
        "suppressed_removed": int(suppressed_removed),
        "reseeded_shape_change": int(reseeded_shape_change),
        "fingerprint_mismatch_reemitted": int(
            fingerprint_mismatch_reemitted,
        ),
        "reemit_reasons": dict(sorted(reemit_reasons.items())),
        "replay_suppression_enabled": bool(replay_suppression_enabled),
        "replay_suppression_eligible": bool(replay_suppression_eligible),
    }


def _sync_observability_fail_open(sync_results, **kwargs):
    try:
        return _sync_observability(sync_results, **kwargs)
    except Exception:
        log.warning(
            "Kobo Sync observability failed reason=counter_build_failed",
            exc_info=True,
        )
        return _empty_sync_observability()


def _empty_sync_observability():
    """Counters for an error response produced before entitlement rendering."""
    return {
        "new": 0,
        "changed": 0,
        "removed": 0,
        "suppressed_replay": 0,
        "suppressed_unchanged": 0,
        "suppressed_removed": 0,
        "reseeded_shape_change": 0,
        "fingerprint_mismatch_reemitted": 0,
        "reemit_reasons": {},
        "replay_suppression_enabled": False,
        "replay_suppression_eligible": False,
    }


def _format_reemit_reasons(reasons):
    if not reasons:
        return "none"
    return ",".join(
        "{}:{}".format(reason, count)
        for reason, count in sorted(reasons.items())
    )


def _log_sync_observability(
    requesting_device_id,
    incoming_cursor,
    outgoing_cursor,
    observability,
    *,
    response_mode,
    capture_session=None,
):
    """Emit the PII-free one-line incident record for one response attempt."""
    incoming_cursor_log = None
    outgoing_cursor_log = None
    try:
        capture_id = getattr(capture_session, "capture_id", "none")
        incoming_cursor_log = _sync_cursor_log_value(incoming_cursor)
        outgoing_cursor_log = _sync_cursor_log_value(outgoing_cursor)
        log.info(
            "Kobo Sync summary: device=%s capture_id=%s response_mode=%s "
            "entitlements new=%d changed=%d removed=%d "
            "suppressed_replay=%d suppressed_unchanged=%d "
            "suppressed_removed=%d fingerprint_mismatch_reemitted=%d "
            "reemit_reasons=%s reseeded_shape_change=%d "
            "replay_suppression enabled=%s eligible=%s "
            "cursors in=%s out=%s",
            _sync_device_log_id(requesting_device_id),
            capture_id,
            response_mode,
            observability.get("new", 0),
            observability.get("changed", 0),
            observability.get("removed", 0),
            observability.get("suppressed_replay", 0),
            observability.get("suppressed_unchanged", 0),
            observability.get("suppressed_removed", 0),
            observability.get("fingerprint_mismatch_reemitted", 0),
            _format_reemit_reasons(observability.get("reemit_reasons", {})),
            observability.get("reseeded_shape_change", 0),
            observability.get("replay_suppression_enabled", False),
            observability.get("replay_suppression_eligible", False),
            incoming_cursor_log,
            outgoing_cursor_log,
        )
    except Exception:
        log.warning(
            "Kobo Sync observability failed reason=summary_emission_failed",
            exc_info=True,
        )

    if (capture_session is not None
            and incoming_cursor_log is not None
            and outgoing_cursor_log is not None):
        try:
            capture_session.record_sync_info_summary(
                response_mode=response_mode,
                incoming_cursor=incoming_cursor_log,
                outgoing_cursor=outgoing_cursor_log,
                counters=observability,
            )
        except Exception:
            log.warning(
                "Kobo Sync observability failed "
                "reason=capture_summary_record_failed",
                exc_info=True,
            )


def _pending_page_observability(page):
    """Read stored diagnostics, with a response-body fallback for old rows."""
    try:
        confirmation = json.loads(page.confirmation_json or "{}")
        observability = confirmation.get("observability")
        if isinstance(observability, dict):
            return observability
    except (TypeError, ValueError):
        pass

    try:
        sync_results = json.loads(page.response_body or "[]")
    except (TypeError, ValueError):
        sync_results = []
    counts = _sync_entitlement_counts(sync_results)
    return {
        "new": counts["new"],
        "changed": counts["changed"],
        "removed": counts["removed"],
        "suppressed_replay": 0,
        "suppressed_unchanged": 0,
        "suppressed_removed": 0,
        "reseeded_shape_change": 0,
        "fingerprint_mismatch_reemitted": 0,
        "reemit_reasons": {},
        "replay_suppression_enabled": False,
        "replay_suppression_eligible": False,
    }


def _log_pending_page_observability(
    page, requesting_device_id, incoming_cursor, capture_session=None,
):
    try:
        outgoing = SyncToken.SyncToken.from_headers({
            SyncToken.SyncToken.SYNC_TOKEN_HEADER: page.outgoing_token,
        })
        _log_sync_observability(
            requesting_device_id,
            incoming_cursor,
            _sync_cursor_summary(outgoing),
            _pending_page_observability(page),
            response_mode="pending_replay",
            capture_session=capture_session,
        )
    except Exception:
        log.warning(
            "Kobo Sync observability failed reason=summary_emission_failed",
            exc_info=True,
        )


def _serialize_pending_confirmation(confirmation):
    """Keep optional counters from invalidating an otherwise valid page."""
    try:
        return json.dumps(confirmation, separators=(",", ":"))
    except Exception:
        log.warning(
            "Kobo Sync observability failed "
            "reason=pending_observability_serialization_failed",
            exc_info=True,
        )
        confirmation_without_observability = dict(confirmation)
        confirmation_without_observability.pop("observability", None)
        return json.dumps(
            confirmation_without_observability, separators=(",", ":"),
        )


def _abort_sync_with_observability(
    status,
    requesting_device_id,
    incoming_cursor,
    *,
    response_mode,
    capture_session,
    outgoing_cursor=None,
    observability=None,
):
    """Log and link a sync failure that has no entitlement response body.

    First discard every staged sync-state mutation so a failure after pending
    page acknowledgment/reset cannot leave the request half-applied. The
    observability record is best-effort: this boundary exists so the intended
    status always reaches the device, even when the logging backend itself is
    failing (a second ``log.warning`` inside the summary fallback would
    otherwise escape as an HTTP 500).
    """
    _rollback_after_sync_failure()
    _mark_pending_page_reset_staged(False)
    try:
        _log_sync_observability(
            requesting_device_id,
            incoming_cursor,
            incoming_cursor if outgoing_cursor is None else outgoing_cursor,
            _empty_sync_observability() if observability is None
            else observability,
            response_mode=response_mode,
            capture_session=capture_session,
        )
    except Exception:  # noqa: BLE001 - failure-only boundary; nothing to log with
        pass
    return abort(status)


def normalized_books_last_modified(value):
    """Return a UTC, fixed-width SQL key for calibre's mixed timestamp text.

    ``metadata.db`` declares ``books.last_modified`` as TIMESTAMP but stores
    TEXT.  Calibre includes a UTC offset while SQLAlchemy writes a naive value,
    so comparing the raw strings makes the suffix decide the keyset result.

    SQLite's ``strftime('%f')`` stops at milliseconds.  Extract consecutive
    fractional digits only, right-pad with zeroes, and truncate to six digits
    so this SQL key has the same microsecond precision as the Python datetime
    carried by SyncToken. ``strftime`` supplies the UTC-normalized whole-second
    portion; timezone offsets therefore cannot affect comparison or ordering.
    """
    raw_value = cast(value, String)
    fraction_start = func.instr(raw_value, ".")
    fraction_tail = func.substr(raw_value, fraction_start + 1)

    # ltrim removes the leading digit run; the length difference is therefore
    # exactly the fraction's width up to its first non-digit. Offset digits can
    # never leak back in after their leading '+' or '-' stops the run.
    fraction_width = (
        func.length(fraction_tail)
        - func.length(func.ltrim(fraction_tail, "0123456789"))
    )
    fraction_digits = func.substr(fraction_tail, 1, fraction_width)

    microseconds = case(
        (fraction_start > 0,
         func.substr(fraction_digits + literal("000000"), 1, 6)),
        else_="000000",
    )
    return (
        func.strftime("%Y-%m-%d %H:%M:%S", value)
        + literal(".")
        + microseconds
    )


def books_keyset_after_cursor(cursor_lm, cursor_id):
    """Build the normalized ``(last_modified, id)`` Kobo keyset predicate."""
    normalized_stored = normalized_books_last_modified(db.Books.last_modified)
    normalized_cursor = normalized_books_last_modified(cursor_lm)
    return or_(
        normalized_stored > normalized_cursor,
        and_(normalized_stored == normalized_cursor, db.Books.id > cursor_id),
    )


def delivery_keyset_after_cursor(normalized_stored, cursor_lm, cursor_id):
    """Build a composite Kobo keyset for an already-normalized time key."""
    normalized_cursor = normalized_books_last_modified(cursor_lm)
    return or_(
        normalized_stored > normalized_cursor,
        and_(normalized_stored == normalized_cursor, db.Books.id > cursor_id),
    )


def books_cursor_datetime(value):
    """Put a dialect-parsed datetime on the cursor's UTC-naive basis."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_store_url_for_current_request():
    # Programmatically modify the current url to point to the official Kobo store
    __, __, request_path_with_auth_token = request.full_path.rpartition("/kobo/")
    __, __, request_path = request_path_with_auth_token.rstrip("?").partition(
        "/"
    )
    return KOBO_STOREAPI_URL + "/" + request_path


CONNECTION_SPECIFIC_HEADERS = [
    "connection",
    "content-encoding",
    "content-length",
    "transfer-encoding",
]


def get_kobo_created_ts(book):
    ts_created = None
    if book.Books.timestamp is not None:
        ts_created = book.Books.timestamp.replace(tzinfo=None)
    else:
        log.debug("Kobo Sync: book %s has no timestamp", book.Books.id)

    try:
        if book.date_added is not None:
            ts_created = max(ts_created, book.date_added) if ts_created else book.date_added
        else:
            log.debug("Kobo Sync: book %s has no date_added", book.Books.id)
    except AttributeError:
        pass

    if ts_created is None and book.Books.last_modified is not None:
        ts_created = book.Books.last_modified.replace(tzinfo=None)
    if ts_created is None:
        ts_created = datetime.min

    return ts_created


def get_kobo_activated():
    return config.config_kobo_sync


def make_request_to_kobo_store(sync_token=None):
    outgoing_headers = Headers(request.headers)
    outgoing_headers.remove("Host")
    if sync_token:
        sync_token.set_kobo_store_header(outgoing_headers)

    # Flask's request/app context does not propagate to the real OS thread
    # used by run_blocking. Materialize every request-bound value here, then
    # offload only requests/urllib3's blocking socket work.
    method = request.method
    store_url = get_store_url_for_current_request()
    body = request.get_data()
    store_response = parallel.run_blocking(lambda: requests.request(
        method=method,
        url=store_url,
        headers=outgoing_headers,
        data=body,
        allow_redirects=False,
        timeout=(2, 10)
    ))
    log.debug("Content: " + str(store_response.content))
    log.debug("StatusCode: " + str(store_response.status_code))
    return store_response


def redirect_or_proxy_request():
    if config.config_kobo_proxy:
        if request.method == "GET":
            return redirect(get_store_url_for_current_request(), 307)
        else:
            # The Kobo device turns other request types into GET requests on redirects,
            # so we instead proxy to the Kobo store ourselves.
            store_response = make_request_to_kobo_store()

            return make_proxy_response(store_response)
    else:
        return make_response(jsonify({}))


def make_proxy_response(store_response: requests.Response) -> Response:
    response_headers = store_response.headers
    for header_key in CONNECTION_SPECIFIC_HEADERS:
        response_headers.pop(header_key, default=None)

    return make_response(store_response.content, store_response.status_code, response_headers.items())


def convert_to_kobo_timestamp_string(timestamp):
    try:
        return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    except AttributeError as exc:
        log.debug("Timestamp not valid: {}".format(exc))
        # A response-generation timestamp makes an unchanged payload mutate
        # from one sync to the next.  Epoch is a deterministic sentinel for
        # malformed legacy rows and is accepted by the Kobo schema.
        return "1970-01-01T00:00:00Z"


def get_magic_shelf_book_ids_for_kobo(user_id):
    """Return ``(book_ids, reliable)`` for the user's kobo_sync magic shelves.

    ``reliable`` is ``False`` when any shelf's membership query FAILED. The
    two-way-sync deletion path must not treat an unreliable (possibly
    incomplete) set as authoritative: a transient DB error that looked like an
    empty shelf would archive the books off the device and force a redownload
    (fork #468). A genuinely-empty result — flag off, no kobo_sync shelves —
    is ``reliable=True`` (nothing to deliver, nothing wrongly archived).
    """
    if not config.config_kobo_sync_magic_shelves:
        # Per-shelf kobo_sync intent with the global flag off is the #359
        # trap — surface it in debug logs so support can spot it instantly.
        # (A one-time boot migration enables the global flag where intent
        # already exists; this log covers shelves marked afterwards, e.g.
        # via API, while the flag is deliberately off.)
        if log.isEnabledFor(logging.DEBUG):
            swallowed = ub.session.query(ub.MagicShelf).filter_by(
                user_id=user_id, kobo_sync=True).count()
            if swallowed:
                log.debug(
                    "Kobo Sync: %s magic shelves are marked kobo_sync but the "
                    "global 'Sync Magic Shelves to Kobo' setting is off — not "
                    "delivering (#359)", swallowed)
        return set(), True

    magic_shelves = ub.session.query(ub.MagicShelf).filter_by(user_id=user_id, kobo_sync=True).all()
    if not magic_shelves:
        return set(), True

    book_ids = set()
    reliable = True
    for shelf in magic_shelves:
        try:
            # Ask for the ID LIST, not hydrated Book objects (CWA #1307).
            # get_books_for_magic_shelf takes the authoritative id list and then
            # hydrates it through a fresh CalibreDB session, silently dropping
            # any id whose Books row that session did not return
            # (`if bid in book_map`). On a CWA build the metadata.db is swapped
            # underneath us by auto-ingest, so a sync landing mid-reload sees a
            # short hydration — no exception, reliable stays True, and the
            # dropped books fall out of allowed_book_ids and get ARCHIVED off
            # the device. The device then re-downloads them as a fresh
            # entitlement and the local annotations go with the old copy.
            # The id list is the membership; hydration is presentation.
            ids, _ = magic_shelf.get_book_ids_for_magic_shelf(
                shelf.id, raise_on_error=True
            )
        except Exception as e:
            # A failed membership query must NOT look like "this shelf is empty"
            # to the deletion path (#468). Mark the whole result unreliable so
            # the caller skips archiving this round; the next clean sync
            # reconciles.
            log.error(
                "Kobo Sync: magic shelf %s membership query failed (%s); marking "
                "membership UNRELIABLE so two-way-sync deletion skips archiving "
                "this round (#468)", shelf.id, e)
            reliable = False
            continue
        book_ids.update(ids or ())

    if book_ids:
        log.debug("Kobo Sync: magic shelf allowed books: %s", len(book_ids))

    return book_ids, reliable


def compute_kobo_books_to_archive(synced_book_ids, allowed_book_ids, membership_reliable):
    """Books to remove from the device = synced books that are no longer on any
    Kobo-sync shelf.

    FAIL-SAFE (fork #468): when the magic-shelf membership computation was
    unreliable (a query failed), return an EMPTY set — a transient read-side
    failure must never drive device-side archival/deletion. Leaving a stale
    book on the device for one more sync is harmless; wrongly archiving the
    book the user is reading forces a redownload and loses their place.
    """
    if not membership_reliable:
        return set()
    return set(synced_book_ids) - set(allowed_book_ids)


def archive_membership_reliable(kobo_only_shelves_sync,
                                magic_shelf_membership_reliable):
    """Whether every source that informed the allowed set was reliable.

    Magic shelves inform deletions only in shelf-sync mode. In sync-all mode,
    personal-library membership alone scopes the device, so an unrelated magic
    shelf failure must not indefinitely suppress an intentional removal.
    """
    return (
        not bool(kobo_only_shelves_sync)
        or bool(magic_shelf_membership_reliable)
    )


def get_magic_shelf_membership_added_at(user_id):
    """Return the most-recent MagicShelfCache.created_at across the user's
    kobo-sync magic shelves, or ``None`` if no cache rows exist.

    Plays the role ``BookShelf.date_added`` plays for regular shelves —
    a "membership added" timestamp that lets the sync cursor emit
    magic-shelf-only books once when the cache (re)builds, then fall
    out of the cursor on subsequent syncs.

    Returning ``None`` (rather than ``datetime.min``) lets the caller
    distinguish "no kobo-sync magic shelves at all" from "shelves
    exist but never cached" — the former should skip the magic-shelf
    arm entirely; the latter falls through to normal cursor behavior.
    """
    if not config.config_kobo_sync_magic_shelves:
        return None

    max_created_at = (
        ub.session.query(func.max(ub.MagicShelfCache.created_at))
        .join(ub.MagicShelf, ub.MagicShelf.id == ub.MagicShelfCache.shelf_id)
        .filter(ub.MagicShelf.user_id == user_id, ub.MagicShelf.kobo_sync == True)  # noqa: E712
        .scalar()
    )
    if max_created_at is None:
        return None
    if hasattr(max_created_at, "replace") and getattr(max_created_at, "tzinfo", None) is not None:
        max_created_at = max_created_at.replace(tzinfo=None)
    return max_created_at


def _run_sync_with_pending_page_reset_boundary(handler):
    """Contain failures only while this request owns a staged page reset.

    Most unexpected handler exceptions retain Flask's established HTTP 500
    behavior. Once a non-CWNG token has staged a pending-page reset, however,
    letting an exception escape would expose those uncommitted mutations to
    the next request because the user-database session has no teardown
    rollback. Log that exception, restore the old page and latch, and return
    the same retryable 503 used by checked sync-generation failures.
    """
    _mark_pending_page_reset_staged(False)
    try:
        return handler()
    except Exception:
        if not _pending_page_reset_is_staged():
            raise
        _rollback_after_sync_failure()
        _mark_pending_page_reset_staged(False)
        try:
            log.exception(
                "Kobo Sync failed "
                "reason=pending_page_reset_response_generation_failed",
            )
        except Exception:  # noqa: BLE001 - preserve retryable 503
            pass
        return abort(503)


@requires_kobo_auth
# @download_required
def HandleSyncRequest():
    raw_sync_token = request.headers.get(
        SyncToken.SyncToken.SYNC_TOKEN_HEADER, "",
    )
    sync_token = SyncToken.SyncToken.from_headers(request.headers)
    sync_cursor_in = _sync_cursor_summary(sync_token)
    requesting_device_id = getattr(g, "annotation_origin_device_id", None)
    capture_session = _begin_sync_exchange_capture(
        requesting_device_id, raw_sync_token,
    )
    if not current_user.role_download():
        try:
            log.info("Users need download permissions for syncing library to Kobo reader")
        except Exception:  # noqa: BLE001 - diagnostic only; the 403 below is the contract
            pass
        return _abort_sync_with_observability(
            403,
            requesting_device_id,
            sync_cursor_in,
            response_mode="download_forbidden",
            capture_session=capture_session,
        )

    pending_page = kobo_sync_status.get_pending_sync_page(
        requesting_device_id,
    )
    acknowledged_pending_page = False
    acknowledged_rehydrate_book_ids = []
    if (pending_page is not None
            and raw_sync_token == pending_page.outgoing_token):
        # TTL is a garbage-collection bound, not a validity window for direct
        # token evidence. A Kobo may accept its final page and then stay idle
        # beyond the TTL, so promote that returned-token acknowledgment before
        # expiry can discard the page's confirmation payload (F-802720).
        if not _acknowledge_pending_page(
            pending_page,
            requesting_device_id,
            acknowledged_rehydrate_book_ids,
        ):
            return _abort_sync_with_observability(
                503,
                requesting_device_id,
                sync_cursor_in,
                response_mode="pending_ack_failed",
                capture_session=capture_session,
            )
        acknowledged_pending_page = True
        pending_page = None

    pruned_pending_pages = kobo_sync_status.prune_expired_pending_sync_pages(
        current_user.id,
    )
    # Keep a successful acknowledgment in the caller-owned transaction: it
    # must commit atomically with the replacement page at the response boundary.
    if (pruned_pending_pages
            and not acknowledged_pending_page
            and ub.session_commit() is False):
        return _abort_sync_with_observability(
            503,
            requesting_device_id,
            sync_cursor_in,
            response_mode="pending_prune_commit_failed",
            capture_session=capture_session,
        )
    if not acknowledged_pending_page:
        # Pruning may have removed the row loaded for the pre-prune ack lookup.
        pending_page = kobo_sync_status.get_pending_sync_page(
            requesting_device_id,
        )
    if pending_page is not None:
        if pending_page.incoming_token_hash == _sync_token_hash(raw_sync_token):
            replay = _pending_response(pending_page)
            if replay is None:
                return _abort_sync_with_observability(
                    503,
                    requesting_device_id,
                    sync_cursor_in,
                    response_mode="pending_response_corrupt",
                    capture_session=capture_session,
                )
            log.info(
                "Kobo Sync: replaying unacknowledged page for device %s",
                requesting_device_id,
            )
            _log_pending_page_observability(
                pending_page, requesting_device_id, sync_cursor_in,
                capture_session,
            )
            return replay
        if not sync_token.is_cwng_token:
            # An official-store/malformed token is a reset boundary.  The old
            # response remains unconfirmed, but it must not block a fresh
            # device from starting a new serial page chain. Preserve any
            # confirmation echo whose predecessor acknowledgment already
            # cleared its repair latch before abandoning the page.
            if not _reset_pending_page_for_non_cwng_token(
                    pending_page, requesting_device_id):
                return _abort_sync_with_observability(
                    503,
                    requesting_device_id,
                    sync_cursor_in,
                    response_mode="pending_reset_failed",
                    capture_session=capture_session,
                )
        else:
            # One physical Kobo presents tokens serially. A different valid
            # CWNG token is not acknowledgment evidence and cannot safely
            # replace the retained page, so offer that page again and give the
            # device another chance to advance onto its returned token.
            log.warning(
                "Kobo Sync: device %s presented an unexpected token while a "
                "page is pending; replaying the pending page",
                requesting_device_id,
            )
            replay = _pending_response(pending_page)
            if replay is None:
                return _abort_sync_with_observability(
                    503,
                    requesting_device_id,
                    sync_cursor_in,
                    response_mode="pending_response_corrupt",
                    capture_session=capture_session,
                )
            _log_pending_page_observability(
                pending_page, requesting_device_id, sync_cursor_in,
                capture_session,
            )
            return replay
    # A server-time fence distinguishes repair work that existed when this
    # request began from work armed by an entitlement/reset in this response.
    # The latter must survive until the next request, after the device has had
    # a chance to download and report a cover-shaped reset.
    rehydrate_request_cutoff = device_positions.rehydrate_request_cutoff()
    replay_suppression_enabled = bool(getattr(
        config, "config_kobo_suppress_replayed_entitlements", True))
    # The acknowledged ledger belongs to the resolved physical device, so its
    # exact fingerprints remain authoritative even when Nickel returns no
    # token or a partial/foreign token. In particular, USB mass-storage eject
    # can preserve book cursors while dropping other token fields; allowing
    # token provenance to disable suppression re-emits held books and makes
    # Nickel mark their local files as not downloaded. A newly paired or
    # explicitly server-reset device has no ledger rows, and the missing-row
    # recovery arm below still announces those books New.
    replay_suppression_eligible = bool(
        replay_suppression_enabled
        and requesting_device_id
    )
    log.info("Kobo library sync request received")
    log.debug("SyncToken: {}".format(sync_token))
    log.debug("Download link format {}".format(get_download_url_for_book('[bookid]', '[bookformat]')))
    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')

    # if no books synced don't respect sync_token
    if not ub.session.query(ub.KoboSyncedBooks).filter(ub.KoboSyncedBooks.user_id == current_user.id).count():
        # A cleared legacy marker is the account-level reset signal. Re-arm
        # only this requesting device's existing position rows; another Kobo's
        # journal must not gain response work because this device lost state.
        device_positions.mark_existing_positions_for_rehydrate(
            requesting_device_id,
        )
        sync_token.books_last_modified = datetime.min
        sync_token.books_last_created = datetime.min
        sync_token.reading_state_last_modified = datetime.min
        # Reset books_last_id alongside books_last_modified — with
        # cursor_lm = datetime.min the stale cursor_id is harmless, but
        # keeping the two cursor components consistent makes the keyset
        # behavior easier to reason about for future readers.
        sync_token.books_last_id = -1
        # Reset the magic-shelf sub-cursor too — a fresh-device sync
        # should re-deliver every magic book.
        sync_token.magic_shelf_last_id = -1
        # Reset the magic-shelf membership timestamp so the cache-rebuild
        # detection on the next sync treats the cache as fresh.
        sync_token.magic_shelf_membership_at = datetime.min

    new_books_last_modified = books_cursor_datetime(
        sync_token.books_last_modified
    )  # needed for sync selected shelfs only
    new_books_last_created = sync_token.books_last_created  # needed to distinguish between new and changed entitlement
    new_reading_state_last_modified = sync_token.reading_state_last_modified

    # Preserve the incoming archive field as the outgoing floor so an empty
    # pass cannot manufacture an older token. It is only a tombstone
    # high-watermark when deletion timestamps are monotonic; missing per-device
    # ledger rows below recover backdated deletions.
    new_archived_last_modified = sync_token.archive_last_modified
    sync_results = []
    books_to_delete_ids = set()
    rehydrate_positions_emitted = []
    confirmation_echo_book_ids = []
    fingerprint_mismatch_reemitted = 0
    reemit_reasons = Counter()

    try:
        calibre_db.refresh_for_new_data()
    except Exception:
        log.exception("Kobo Sync: failed to refresh the library database")
        return _abort_sync_with_observability(
            503,
            requesting_device_id,
            sync_cursor_in,
            response_mode="library_refresh_failed",
            capture_session=capture_session,
        )

    # Upgrade bridge: flat delivery markers are user-wide and therefore cannot
    # prove receipt by any physical device. Mark the boundary but leave every
    # uncertain book absent from the confirmed ledger so it is reannounced New.
    if (requesting_device_id
            and not _seed_existing_device_entitlement_ledgers(current_user.id)):
        return _abort_sync_with_observability(
            503,
            requesting_device_id,
            sync_cursor_in,
            response_mode="ledger_seed_failed",
            capture_session=capture_session,
        )
    # Pre-ack rows prove only a committed server emission. Clear them once
    # before they can hide an uncertain book from the recovery arm below.
    if (requesting_device_id
            and not _migrate_device_entitlement_classification(current_user.id)):
        return _abort_sync_with_observability(
            503,
            requesting_device_id,
            sync_cursor_in,
            response_mode="ledger_migration_failed",
            capture_session=capture_session,
        )


    # Magic-shelf book IDs + membership timestamp are computed for BOTH sync
    # modes (kobo_only_shelves_sync True AND False). Original v4.0.147 fix
    # gated the computation on kobo_only_shelves_sync — which left
    # @recruiterguy stuck: their user is in 'sync-all-library' mode (False),
    # so the cache never refreshed via this code path, the magic-shelf arm
    # never gained a fresh `created_at`, and magic-shelf-only books with
    # old `Books.last_modified` continued to be filtered out by the
    # else-branch cursor. Computing here forces the cache TTL re-evaluation
    # on every sync, regardless of mode.
    magic_shelf_book_ids, magic_shelf_membership_reliable = get_magic_shelf_book_ids_for_kobo(current_user.id)
    magic_shelf_membership_added_at = get_magic_shelf_membership_added_at(current_user.id)

    # Device reconciliation covers both scoping modes. Shelf-only mode uses
    # the user's Kobo shelves; sync-all mode now means My Library when that
    # feature is enabled. The membership set is intersected with shelves so a
    # stale shelf link can never keep a removed book on the device.
    # Keep this compatibility input explicit: older integrations and tests
    # construct user-shaped objects without the named-mode helper.
    membership_enabled = bool(getattr(current_user, 'has_own_library', False))
    personal_library_mode = (
        membership_enabled
        or user_library.mode_for_user(current_user)
        == constants.LIBRARY_MODE_PERSONAL
    )
    if current_user.kobo_only_shelves_sync or membership_enabled:
        removal_result_start = len(sync_results)
        try:
            # Check all books that are on Kobo according to the database
            synced_books_query = ub.session.query(ub.KoboSyncedBooks.book_id).filter(ub.KoboSyncedBooks.user_id == current_user.id)
            synced_book_ids = {item.book_id for item in synced_books_query}

            if current_user.kobo_only_shelves_sync:
                # Check all books currently on a Kobo Sync shelf.
                allowed_books_query = (ub.session.query(ub.BookShelf.book_id)
                                       .join(ub.Shelf, ub.BookShelf.shelf == ub.Shelf.id)
                                       .filter(ub.Shelf.user_id == current_user.id,
                                               ub.Shelf.kobo_sync.is_(True)))
                allowed_book_ids = {item.book_id for item in allowed_books_query}
                if magic_shelf_book_ids:
                    allowed_book_ids |= magic_shelf_book_ids
            else:
                allowed_book_ids = set(synced_book_ids)

            if personal_library_mode:
                library_book_ids = {
                    row.book_id for row in
                    ub.session.query(ub.UserLibraryBook.book_id)
                    .filter(ub.UserLibraryBook.user_id == current_user.id)
                }
                allowed_book_ids &= library_book_ids

            # #468's fail-safe applies only when magic shelves informed this
            # allowed set. With shelf sync off, personal membership is the sole
            # scoping source and remains authoritative even if an unrelated
            # magic-shelf query failed.
            deletion_membership_reliable = archive_membership_reliable(
                current_user.kobo_only_shelves_sync,
                magic_shelf_membership_reliable,
            )
            if not deletion_membership_reliable:
                log.warning(
                    "Kobo Sync: magic-shelf membership unreliable for user %s; "
                    "skipping two-way-sync deletion this round to avoid wrongly "
                    "archiving books (#468)", current_user.name)
            books_to_delete_ids = compute_kobo_books_to_archive(
                synced_book_ids, allowed_book_ids,
                deletion_membership_reliable)

            if books_to_delete_ids:
                log.info(f"Kobo Sync: Found {len(books_to_delete_ids)} books to remove from device for user {current_user.name}")
                known_removal_fingerprints = _diagnostic_ledger_lookup(
                    kobo_sync_status.get_device_entitlement_fingerprints,
                    requesting_device_id,
                    books_to_delete_ids,
                    scope="live_scope_removal",
                )

                # Go through the “To be deleted” list
                for book_id in books_to_delete_ids:
                    book = calibre_db.get_book(book_id)
                    if book:
                        # Create a “Remove” command for the Kobo
                        entitlement = {
                            "BookEntitlement": create_book_entitlement(book, archived=True),
                            "BookMetadata": get_metadata(book),
                        }
                        sync_results.append({"ChangedEntitlement": entitlement})
                        known_record = known_removal_fingerprints.get(book_id)
                        if known_record is not None:
                            fingerprint_mismatch_reemitted += \
                                _observe_live_scope_removal(
                                    reemit_reasons,
                                    known_record,
                                    entitlement,
                                )

        except Exception as e:
            log.error(f"Kobo Sync: Error during deletion logic: {e}")
            ub.session.rollback()
            books_to_delete_ids = set()
            del sync_results[removal_result_start:]

    only_kobo_shelves = current_user.kobo_only_shelves_sync

    log.debug("Kobo Sync: books last modified: {}".format(sync_token.books_last_modified))

    rstate_join = and_(
        db.Books.id == ub.KoboReadingState.book_id,
        ub.KoboReadingState.user_id == current_user.id,
    )
    # Composite-keyset cursor: filter rows after (books_last_modified, books_last_id)
    # in lexicographic order. This walks paginated batches through blocks of books
    # that share one last_modified (fork #347 — 4458 books all stamped one second
    # in a bulk import; the timestamp-only cursor either re-sent the same first
    # SYNC_ITEM_LIMIT forever or skipped the remainder). For pre-upgrade tokens
    # books_last_id defaults to -1, so the keyset arm 'id > -1' is True for every
    # valid book id and no books at exactly books_last_modified are dropped on
    # the first post-upgrade sync.
    cursor_lm = sync_token.books_last_modified
    cursor_id = sync_token.books_last_id
    composite_keyset_books_only = books_keyset_after_cursor(cursor_lm, cursor_id)
    device_entitlement_recovery_filter = None
    if requesting_device_id:
        delivered_to_requesting_device = select(
            ub.KoboDeviceBookEntitlement.book_id,
        ).where(
            ub.KoboDeviceBookEntitlement.device_id == int(requesting_device_id),
        )
        # This arm is deliberately independent of both timestamp cursor and
        # token provenance. An old/partial cursor may be past the entire
        # library, but a missing physical-device ledger row still means the
        # entitlement must be announced New. Full Sync and per-book resend use
        # this same invariant after deliberately clearing the target rows.
        device_entitlement_recovery_filter = ~db.Books.id.in_(
            delivered_to_requesting_device,
        )

    # Magic-shelf membership arm (fork #359): magic-shelf-only books are not in
    # book_shelf_link, so BookShelf.date_added is NULL and Books.last_modified is
    # usually in the past — the standard cursor arms filter them out before the
    # outer (kobo_sync shelf OR magic-shelf) membership filter is evaluated, and
    # they never reach the device. The cache row's created_at is the membership
    # timestamp: when the cache (re)builds, emit all magic-shelf books once;
    # cursor then advances past created_at and the arm goes False so the device
    # doesn't re-emit (the #213 termination guard, preserved structurally).
    #
    # The arm fires for BOTH kobo_only_shelves_sync modes — in 'sync-all'
    # mode the outer membership filter doesn't apply, but the inner cursor
    # would still filter magic-shelf-only books out because their
    # Books.last_modified is in the past relative to the device cursor.
    # @recruiterguy's v4.0.147 verification showed this gap concretely.
    magic_shelf_arm_active = bool(
        magic_shelf_book_ids
        and magic_shelf_membership_added_at is not None
        and magic_shelf_membership_added_at > cursor_lm
    )

    # Magic-shelf SUB-CURSOR: the bare `id IN magic_shelf_book_ids` arm is
    # cursor-unaware — for magic shelves with >= SYNC_ITEM_LIMIT books, it
    # emits the same first SYNC_ITEM_LIMIT ids every round (Greptile P1 on
    # PR #366). Filtering `id > magic_shelf_last_id` walks the arm via a
    # second-tier keyset across batches.
    #
    # CRITICAL: when the cache is rebuilt (membership_added_at advances
    # past the last-recorded membership_at on the token), reset the
    # sub-cursor to -1 so the next walk starts from id=0. Without this
    # reset, a user who adds a low-id book to the shelf after a sync
    # ended at a high magic_shelf_last_id would see the new book
    # filtered out by `id > magic_shelf_last_id`; the fold then fires
    # on an empty batch, advances cursor past the new T_magic, and the
    # book stays undelivered until ANOTHER cache rebuild happens
    # (Greptile P on PR #367 v4.0.153).
    if (
        magic_shelf_membership_added_at is not None
        and magic_shelf_membership_added_at > sync_token.magic_shelf_membership_at
    ):
        magic_shelf_last_id = -1
    else:
        magic_shelf_last_id = sync_token.magic_shelf_last_id

    magic_shelf_arm = and_(
        db.Books.id.in_(magic_shelf_book_ids),
        db.Books.id > magic_shelf_last_id,
    )

    # only_kobo_shelves branch joins BookShelf, so its inner filter includes
    # the BookShelf.date_added arm (fork #220) alongside the composite keyset
    # and (when active) the magic-shelf membership arm.
    if magic_shelf_arm_active and device_entitlement_recovery_filter is not None:
        inner_cursor_filter_with_bookshelf = or_(
            ub.BookShelf.date_added > cursor_lm,
            composite_keyset_books_only,
            magic_shelf_arm,
            device_entitlement_recovery_filter,
        )
    elif magic_shelf_arm_active:
        inner_cursor_filter_with_bookshelf = or_(
            ub.BookShelf.date_added > cursor_lm,
            composite_keyset_books_only,
            magic_shelf_arm,
        )
    elif device_entitlement_recovery_filter is not None:
        inner_cursor_filter_with_bookshelf = or_(
            ub.BookShelf.date_added > cursor_lm,
            composite_keyset_books_only,
            device_entitlement_recovery_filter,
        )
    else:
        inner_cursor_filter_with_bookshelf = or_(
            ub.BookShelf.date_added > cursor_lm,
            composite_keyset_books_only,
        )

    # The sync-all branch does not join BookShelf, but personal-library mode
    # has its own membership timestamp. A book selected from the global
    # archive is commonly older than the device cursor, so its delivery key is
    # max(Books.last_modified, UserLibraryBook.added_at). Filtering AND ordering
    # on that same composite key is load-bearing: folding added_at into a cursor
    # while ordering only by Books.last_modified skips later membership rows
    # when a page is capped.
    library_delivery_key = func.max(
        normalized_books_last_modified(db.Books.last_modified),
        normalized_books_last_modified(ub.UserLibraryBook.added_at),
    )
    library_composite_keyset = delivery_keyset_after_cursor(
        library_delivery_key, cursor_lm, cursor_id)
    sync_all_keyset = (
        library_composite_keyset
        if personal_library_mode else composite_keyset_books_only
    )
    if magic_shelf_arm_active and device_entitlement_recovery_filter is not None:
        inner_cursor_filter_sync_all = or_(
            sync_all_keyset,
            magic_shelf_arm,
            device_entitlement_recovery_filter,
        )
    elif magic_shelf_arm_active:
        inner_cursor_filter_sync_all = or_(
            sync_all_keyset,
            magic_shelf_arm,
        )
    elif device_entitlement_recovery_filter is not None:
        inner_cursor_filter_sync_all = or_(
            sync_all_keyset,
            device_entitlement_recovery_filter,
        )
    else:
        inner_cursor_filter_sync_all = sync_all_keyset

    if only_kobo_shelves:
        changed_entries = calibre_db.session.query(db.Books,
                                                   ub.ArchivedBook.last_modified,
                                                   ub.BookShelf.date_added,
                                                   ub.ArchivedBook.is_archived,
                                                   ub.KoboReadingState)
        # Per-device sync state lives in the `x-kobo-synctoken` cursor
        # (the last_modified comparisons below). Don't filter by
        # KoboSyncedBooks here — that table is user-keyed, so it would
        # lock additional devices on the same user out of receiving
        # books another device already synced.
        #
        # Magic-shelf membership is enforced by the OUTER filter
        # (kobo_sync shelf OR magic-shelf) further down. The INNER
        # cursor includes a magic-shelf arm ONLY when the cache was
        # (re)built after the device's cursor, then advances cursor
        # past the cache's created_at — same termination guarantee as
        # #213 (cont_sync goes False once the cache stops moving) but
        # without the original "infinite loop" failure mode.
        changed_entries = (changed_entries
                           .join(db.Data).outerjoin(ub.ArchivedBook, and_(db.Books.id == ub.ArchivedBook.book_id,
                                                                          ub.ArchivedBook.user_id == current_user.id))
                           .outerjoin(ub.KoboReadingState, rstate_join)
                           .filter(inner_cursor_filter_with_bookshelf)
                           .filter(db.Data.format.in_(KOBO_FORMATS))
                           .filter(calibre_db.common_filters(allow_show_archived=True))
                           .order_by(normalized_books_last_modified(db.Books.last_modified))
                           .order_by(db.Books.id)
                           .outerjoin(ub.BookShelf, db.Books.id == ub.BookShelf.book_id)
                           .outerjoin(ub.Shelf, ub.Shelf.id == ub.BookShelf.shelf)
                           .filter(or_(
                               and_(
                                   ub.Shelf.user_id == current_user.id,
                                   ub.Shelf.kobo_sync.is_(True),
                               ),
                               db.Books.id.in_(magic_shelf_book_ids) if magic_shelf_book_ids else False
                           ))
                           .options(joinedload(db.Books.authors),
                                    joinedload(db.Books.publishers),
                                    joinedload(db.Books.series),
                                    joinedload(db.Books.languages),
                                    joinedload(db.Books.comments),
                                    joinedload(db.Books.data))
                           .distinct())
    else:
        changed_entries = calibre_db.session.query(db.Books,
                                                   ub.ArchivedBook.last_modified,
                                                   ub.ArchivedBook.is_archived,
                                                   ub.KoboReadingState)
        # Same per-device cursor invariant as the shelf branch above:
        # don't filter by KoboSyncedBooks (user-keyed, breaks multi-device).
        # The composite keyset (Books.last_modified, Books.id) is the per-device
        # throttle — without it, this branch returns every book on every sync
        # and keeps cont_sync True forever. The 'else' branch's SELECT does
        # not join BookShelf, so the date_added arm is dropped here. The
        # magic-shelf arm DOES participate (when active), letting magic-shelf
        # books with old Books.last_modified deliver in 'sync-all' mode too —
        # fixing the @recruiterguy regression in v4.0.147.
        if personal_library_mode:
            changed_entries = (changed_entries
                               .add_columns(
                                   ub.UserLibraryBook.added_at.label(
                                       "library_added_at"))
                               .join(
                                   ub.UserLibraryBook,
                                   and_(
                                       db.Books.id == ub.UserLibraryBook.book_id,
                                       ub.UserLibraryBook.user_id == current_user.id,
                                   )))
        changed_entries = (changed_entries
                           .join(db.Data).outerjoin(ub.ArchivedBook, and_(db.Books.id == ub.ArchivedBook.book_id,
                                                                          ub.ArchivedBook.user_id == current_user.id))
                           .outerjoin(ub.KoboReadingState, rstate_join)
                           .filter(inner_cursor_filter_sync_all)
                           .filter(calibre_db.common_filters(allow_show_archived=True))
                           .filter(db.Data.format.in_(KOBO_FORMATS))
                           .order_by(
                               library_delivery_key if personal_library_mode
                               else normalized_books_last_modified(
                                   db.Books.last_modified))
                           .order_by(db.Books.id)
                           .options(joinedload(db.Books.authors),
                                    joinedload(db.Books.publishers),
                                    joinedload(db.Books.series),
                                    joinedload(db.Books.languages),
                                    joinedload(db.Books.comments),
                                    joinedload(db.Books.data))
                           # `.join(db.Data)` yields ONE ROW PER KOBO-ELIGIBLE
                           # FORMAT, not per book, so a book holding both an
                           # EPUB and a KEPUB counts and paginates twice. The
                           # shelf branch above already ends `.distinct()`;
                           # this branch did not, and the asymmetry is the bug.
                           #
                           # Two consequences, both measured:
                           #   * `changed entries` below reports format rows,
                           #     not books -- which is why users report a
                           #     number "well past my library size" (#347,
                           #     #1634) and why that number is not evidence of
                           #     a loop.
                           #   * LIMIT applies to the multiplied rows while the
                           #     ORM uniquifies afterwards, so a page of
                           #     SYNC_ITEM_LIMIT delivers only ~half that many
                           #     books and the device needs twice the round
                           #     trips.
                           #
                           # Entitlements were never duplicated -- legacy Query
                           # uniquifies entity rows -- so this is a throughput
                           # and diagnostics defect, not data corruption.
                           .distinct())
    # Freeze the ordered membership before rendering. A count plus OFFSET is
    # not a snapshot: concurrent inserts can enter later offsets and concurrent
    # deletes can shift a captured row out of them. Fetching only these IDs lets
    # replay suppression top up safely without admitting or skipping rows.
    book_snapshot_ids = _capture_query_identities(
        changed_entries, db.Books.id,
    )
    book_count = len(book_snapshot_ids)
    log.debug("Kobo Sync: changed entries: {}".format(book_count))

    # The configured Calibre boolean column is the application's canonical
    # finished/not-finished value, while Kobo can only emit timestamped rows
    # from app.db.  Bridge changed Calibre books before freezing the state
    # frontier so a direct Calibre edit becomes a normal incremental
    # ChangedReadingState.  This also creates the missing state graph left by
    # older custom-column web toggles (#2144).
    reconcile_custom_read_column_for_kobo(
        book_snapshot_ids, sync_token.reading_state_last_modified,
    )

    reading_state_book_ids_emitted = []

    # Establish one ordered reading-state frontier before rendering any
    # entitlements. Reading states can travel either embedded in an entitlement
    # or as standalone ChangedReadingState commands, but the timestamp-only
    # token cannot represent holes between those two paths.  In particular, an
    # out-of-order recent state embedded on page one must not advance the token
    # past older states that did not fit in the standalone page (F-8cb0c9).
    #
    # Only rows in this oldest-first page may be embedded below.  Every row in
    # the page is then emitted exactly once (embedded or standalone), and the
    # cursor advances only to the maximum row in this fully-covered frontier.
    changed_reading_states = ub.session.query(ub.KoboReadingState)
    log.debug("Kobo Sync: rstate last modified: {}".format(
        sync_token.reading_state_last_modified))
    if only_kobo_shelves:
        changed_reading_states = changed_reading_states.outerjoin(
            ub.BookShelf,
            ub.KoboReadingState.book_id == ub.BookShelf.book_id,
        ).outerjoin(
            ub.Shelf,
            ub.Shelf.id == ub.BookShelf.shelf,
        ).filter(
            ub.KoboReadingState.last_modified
            > sync_token.reading_state_last_modified,
        ).filter(or_(
            and_(
                current_user.id == ub.Shelf.user_id,
                ub.Shelf.kobo_sync.is_(True),
            ),
            ub.KoboReadingState.book_id.in_(magic_shelf_book_ids)
            if magic_shelf_book_ids else False,
        )).distinct()
    else:
        changed_reading_states = changed_reading_states.filter(
            ub.KoboReadingState.last_modified
            > sync_token.reading_state_last_modified)

    changed_reading_states = changed_reading_states.filter(
        ub.KoboReadingState.user_id == current_user.id,
    ).order_by(ub.KoboReadingState.last_modified)
    log.debug("Kobo Sync: changed states: {}".format(
        changed_reading_states.count()))
    reading_state_page = changed_reading_states.limit(SYNC_ITEM_LIMIT).all()
    reading_state_page_ids = {
        reading_state.id for reading_state in reading_state_page
    }

    entitlement_fingerprint_updates = {}
    entitlement_change_basis_updates = {}
    suppressed_unchanged_book_ids = set()
    suppressed_deleted_entitlement_uuids = set()
    reseeded_shape_change_book_ids = set()
    reseeded_shape_change_deleted_uuids = set()
    delivered_book_identities = []
    rehydrate_book_ids = set()
    book_candidates_scanned = 0
    book_delivery_slots = 0
    book_selection_exhausted = book_count == 0
    last_scanned_book = None
    scanned_magic_book_ids = []

    for candidate_page in _bounded_query_pages(
            changed_entries,
            book_snapshot_ids,
            db.Books.id,
            lambda row: row.Books.id,
            SYNC_ITEM_LIMIT):
        try:
            known_entitlement_fingerprints = (
                kobo_sync_status.get_device_entitlement_fingerprints(
                    requesting_device_id,
                    [book.Books.id for book in candidate_page],
                )
                if requesting_device_id else {}
            )
        except Exception:
            _log_delivery_ledger_read_failure("live_entitlement")
            return _abort_sync_with_observability(
                503,
                requesting_device_id,
                sync_cursor_in,
                response_mode="live_entitlement_ledger_read_failed",
                capture_session=capture_session,
            )
        # Same-device acknowledged fingerprints are delivery state, not
        # optional diagnostics. They suppress exact absent/partial-token
        # replays and classify a genuine mismatch as Changed.
        prior_entitlement_fingerprints = known_entitlement_fingerprints
        for book in candidate_page:
            candidate_emitted_work = False
            kobo_reading_state = book.KoboReadingState  # None when no record exists yet
            entitlement = {
                "BookEntitlement": create_book_entitlement(
                    book.Books, archived=bool(book.is_archived),
                ),
                "BookMetadata": get_metadata(book.Books),
            }

            # A device may return a stale, partial, foreign, or absent token
            # after an interrupted sync or USB eject. Its cursors can then
            # select books already emitted to this same physical device. Layer
            # 2 suppresses only an exact payload replay from that device's
            # acknowledged ledger; another device has its own ledger, and any
            # real metadata, last_modified, or archive change alters this hash.
            entitlement_fingerprint = (
                _entitlement_fingerprint(entitlement)
                if requesting_device_id else None
            )
            entitlement_change_basis = _book_entitlement_change_basis(
                book.Books.last_modified,
                book.last_modified,
            )
            if replay_suppression_eligible:
                (
                    entitlement_is_unchanged,
                    shape_reseed,
                    refresh_fingerprint_record,
                ) = _entitlement_replay_decision(
                    prior_entitlement_fingerprints.get(book.Books.id),
                    entitlement_fingerprint,
                    entitlement_change_basis,
                )
            else:
                entitlement_is_unchanged = False
                shape_reseed = False
                refresh_fingerprint_record = False

            if (kobo_reading_state is not None
                    and kobo_reading_state.id in reading_state_page_ids):
                reading_state = get_kobo_reading_state_response(
                    book.Books, kobo_reading_state)
                new_reading_state_last_modified = max(
                    new_reading_state_last_modified,
                    kobo_reading_state.last_modified,
                )
                reading_state_book_ids_emitted.append(book.Books.id)
                if entitlement_is_unchanged:
                    # Replay suppression applies only to the byte-identical
                    # base entitlement. A state admitted to this response's
                    # ordered frontier must still be emitted independently.
                    sync_results.append({
                        "ChangedReadingState": {"ReadingState": reading_state}
                    })
                    candidate_emitted_work = True
                else:
                    entitlement["ReadingState"] = reading_state

            ts_created = get_kobo_created_ts(book)

            if entitlement_is_unchanged:
                suppressed_unchanged_book_ids.add(book.Books.id)
                if shape_reseed:
                    reseeded_shape_change_book_ids.add(book.Books.id)
                if refresh_fingerprint_record:
                    entitlement_fingerprint_updates[book.Books.id] = \
                        entitlement_fingerprint
                    entitlement_change_basis_updates[book.Books.id] = \
                        entitlement_change_basis
            else:
                known_record = known_entitlement_fingerprints.get(
                    book.Books.id,
                )
                if known_record is not None:
                    fingerprint_mismatch_reemitted += \
                        _observe_entitlement_reemit(
                            reemit_reasons,
                            known_record,
                            entitlement_fingerprint,
                            entitlement_change_basis,
                            eligible=replay_suppression_eligible,
                            is_cwng_token=sync_token.is_cwng_token,
                            prefix="live_",
                        )
                if book.Books.id not in prior_entitlement_fingerprints:
                    sync_results.append({"NewEntitlement": entitlement})
                else:
                    sync_results.append({"ChangedEntitlement": entitlement})
                candidate_emitted_work = True
                # Only a real delivery arms repair. A byte-identical replay
                # that #1925 suppresses (including a declared #1953 shape
                # reseed) must not mass-arm the user's position journal.
                if requesting_device_id:
                    rehydrate_book_ids.add(book.Books.id)
                    # The per-device ledger is the New-vs-Changed
                    # classification source even when suppression is disabled.
                    entitlement_fingerprint_updates[book.Books.id] = \
                        entitlement_fingerprint
                    entitlement_change_basis_updates[book.Books.id] = \
                        entitlement_change_basis

            new_books_last_modified = max(
                books_cursor_datetime(book.Books.last_modified),
                new_books_last_modified,
            )

            # Shelf membership and personal-library membership are additional
            # delivery clocks and must advance with every inspected candidate,
            # including an exact replay skipped while topping up this page.
            date_added = getattr(book, "date_added", None)
            if date_added is not None:
                if (hasattr(date_added, "replace")
                        and getattr(date_added, "tzinfo", None) is not None):
                    date_added = date_added.replace(tzinfo=None)
                new_books_last_modified = max(
                    date_added, new_books_last_modified)

            library_added_at = getattr(book, "library_added_at", None)
            if library_added_at is not None:
                if (hasattr(library_added_at, "replace")
                        and getattr(library_added_at, "tzinfo", None) is not None):
                    library_added_at = library_added_at.replace(tzinfo=None)
                new_books_last_modified = max(
                    library_added_at, new_books_last_modified)

            new_books_last_created = max(ts_created, new_books_last_created)
            delivered_book_identities.append(
                (book.Books.id, str(book.Books.uuid)))
            if (magic_shelf_book_ids
                    and book.Books.id in magic_shelf_book_ids):
                scanned_magic_book_ids.append(book.Books.id)
            last_scanned_book = book
            book_candidates_scanned += 1

            if candidate_emitted_work:
                book_delivery_slots += 1
                if book_delivery_slots >= SYNC_ITEM_LIMIT:
                    break
        if book_delivery_slots >= SYNC_ITEM_LIMIT:
            break
    else:
        book_selection_exhausted = True

    log.debug(
        "Kobo Sync: candidate scan total=%d scanned=%d deliverable=%d "
        "exhausted=%s",
        book_count,
        book_candidates_scanned,
        book_delivery_slots,
        book_selection_exhausted,
    )

    # Delivery state is deliberately not promoted here.  The exact page and
    # these bounded confirmation inputs are persisted below; the next request
    # promotes them only after presenting this page's returned token.

    # Magic-shelf sub-cursor: advance to the highest magic-shelf book id
    # emitted this round. magic_shelf_book_ids may be empty when the arm
    # wasn't active, in which case the comprehension is empty and we keep
    # the existing sub-cursor unchanged.
    #
    # CRITICAL: source from the LOCAL magic_shelf_last_id (which may have
    # been reset to -1 by the cache-rebuild detection above), NOT from
    # sync_token.magic_shelf_last_id. Otherwise: when the first batch after
    # a cache rebuild contains no magic books (e.g. it's full of regular
    # books that sort before the magic ones), the rebuild reset is
    # silently overwritten by the old token value, the membership_at is
    # advanced to match, and subsequent syncs never re-trigger the rebuild
    # detection — leaving low-id magic books undelivered (Greptile P on
    # PR #368).
    if scanned_magic_book_ids:
        new_magic_shelf_last_id = max(magic_shelf_last_id,
                                       max(scanned_magic_book_ids))
    else:
        new_magic_shelf_last_id = magic_shelf_last_id

    # Composite-keyset cursor: keep books_last_id aligned with
    # new_books_last_modified. The query order is (delivery time, id), where
    # delivery time is Books.last_modified normally and the maximum of book
    # modification/membership addition in personal sync-all mode.
    #   - If new_books_last_modified == the last row's delivery time, store the
    #     row id so the next sync walks past ties without dropping a capped page.
    #   - If new_books_last_modified got pushed past the batch's max ts (via the
    #     date_added fold from fork #220 or via the magic-shelf cache fold below),
    #     reset id to -1 — there are no books at the new ts in this batch, so any
    #     valid id passes the next sync's keyset arm.
    #   - If the batch was empty, keep the existing cursor id (no emission).
    if last_scanned_book is not None:
        last_book = last_scanned_book
        last_book_lm = books_cursor_datetime(last_book.Books.last_modified)
        last_library_added_at = getattr(last_book, "library_added_at", None)
        if last_library_added_at is not None:
            last_library_added_at = books_cursor_datetime(
                last_library_added_at)
            last_book_lm = max(last_book_lm, last_library_added_at)
        if new_books_last_modified == last_book_lm:
            new_books_last_id = last_book.Books.id
        else:
            new_books_last_id = -1
    else:
        new_books_last_id = sync_token.books_last_id

    # If the magic-shelf membership arm was active this round, advance the cursor
    # past the cache's created_at so the arm goes False next sync — same termination
    # guarantee as fork #213 (cont_sync goes False once cache.created_at stops
    # advancing), but without the original "infinite loop" failure mode. If the
    # cache.created_at advances cursor past the batch's max book ts, reset id to -1
    # (no books at the new ts in this batch).
    #
    # CRITICAL: only fire the fold when the bounded candidate snapshot was
    # exhausted. A response may inspect more than SYNC_ITEM_LIMIT candidates
    # while topping up past exact replays, so raw scan length no longer says
    # whether regular books remain. Deferring until exhaustion preserves the
    # same no-skip guarantee as the old partial-batch test.
    batch_drained = book_selection_exhausted
    if magic_shelf_arm_active and batch_drained:
        if magic_shelf_membership_added_at > new_books_last_modified:
            new_books_last_modified = magic_shelf_membership_added_at
            new_books_last_id = -1
            # Cache-rebuild epoch closed — reset the sub-cursor so the next
            # rebuild (which advances cache.created_at > cursor.lm again)
            # starts walking the magic books from id=0.
            new_magic_shelf_last_id = -1
        elif (magic_shelf_membership_added_at == new_books_last_modified
              and last_scanned_book is None):
            # Cache rebuilt but no books actually changed past the old cursor — still
            # advance to prevent the arm from firing again (idempotent re-trigger).
            new_books_last_id = -1
            new_magic_shelf_last_id = -1

    max_change = changed_entries.filter(ub.ArchivedBook.is_archived)\
        .filter(ub.ArchivedBook.user_id == current_user.id) \
        .order_by(func.datetime(ub.ArchivedBook.last_modified).desc()).first()

    max_change = max_change.last_modified if max_change else new_archived_last_modified

    new_archived_last_modified = max(new_archived_last_modified, max_change)

    # PR #248 established that Kobo firmware pins the request cursor whenever
    # `x-kobo-sync: continue` is present.  Comparing against the page cap fixed
    # partial pages, but a full page still formed a closed loop: firmware kept
    # the old token, so the same full page was returned forever.  End every
    # local session so the advanced composite cursor is persisted; the device
    # starts the next session with that returned token and drains the next page.
    cont_sync = False
    log.debug(
        "Kobo Sync: candidate rows after incoming cursor: %d", book_count,
    )
    # Do not set local continuation for a full reading-state page.  It has the
    # same firmware cursor-pinning semantics as the books signal above; ending
    # the session is what lets the returned reading-state cursor take effect.
    for kobo_reading_state in reading_state_page:
        if kobo_reading_state.book_id in reading_state_book_ids_emitted:
            continue
        book = calibre_db.session.query(db.Books).filter(db.Books.id == kobo_reading_state.book_id).one_or_none()
        if book:
            sync_results.append({
                "ChangedReadingState": {
                    "ReadingState": get_kobo_reading_state_response(book, kobo_reading_state)
                }
            })
            reading_state_book_ids_emitted.append(kobo_reading_state.book_id)
            new_reading_state_last_modified = max(new_reading_state_last_modified, kobo_reading_state.last_modified)

    # Re-download repair is independent of the opaque reading-state cursor.
    # Only latches which pre-date this request are eligible: work armed by an
    # entitlement/reset in this response must remain queued until a later sync,
    # after the device has had a chance to replace the bytes. Entitlements
    # delivered in this response are excluded even when their latch was older.
    # The cap bounds renderer migrations/account resets; unserved rows stay
    # latched and drain in book-id order on later requests.
    if requesting_device_id:
        pending_rehydrates = (
            ub.session.query(
                ub.DeviceReadingPosition,
                ub.KoboReadingState,
            )
            .join(
                ub.KoboReadingState,
                ub.KoboReadingState.book_id
                == ub.DeviceReadingPosition.book_id,
            )
            .filter(
                ub.DeviceReadingPosition.device_id
                == int(requesting_device_id),
                ub.DeviceReadingPosition.rehydrate_needed.is_(True),
                ub.DeviceReadingPosition.server_modified_at
                < rehydrate_request_cutoff,
                ub.KoboReadingState.user_id == current_user.id,
            )
            .order_by(ub.DeviceReadingPosition.book_id)
        )
        excluded_rehydrate_book_ids = (
            set(rehydrate_book_ids) | set(books_to_delete_ids)
        )
        if excluded_rehydrate_book_ids:
            pending_rehydrates = pending_rehydrates.filter(
                ub.DeviceReadingPosition.book_id.notin_(
                    excluded_rehydrate_book_ids,
                ),
            )
        pending_rehydrates = pending_rehydrates.limit(SYNC_ITEM_LIMIT).all()
        for position, kobo_reading_state in pending_rehydrates:
            book = calibre_db.session.query(db.Books).filter(
                db.Books.id == position.book_id,
            ).one_or_none()
            if book is None:
                continue
            if position.book_id not in reading_state_book_ids_emitted:
                sync_results.append({
                    "ChangedReadingState": {
                        "ReadingState": get_kobo_reading_state_response(
                            book, kobo_reading_state,
                        ),
                    },
                })
                reading_state_book_ids_emitted.append(position.book_id)
            rehydrate_positions_emitted.append(position)

    # Some Nickel versions acknowledge a repair state before materializing a
    # newly offered download, then recalculate the coarse book percentage as
    # the downloaded spine is installed. The acknowledging request is the
    # first server-visible boundary after that installation. Echo each state
    # cleared by this request once, after entitlement rendering, so the
    # download-time rewrite is repaired without depending on the opaque state
    # cursor. The acknowledged page contains at most SYNC_ITEM_LIMIT repair
    # rows, which bounds this query and response work. Do not consume the echo
    # when this response re-offers the same book; that entitlement arms a fresh
    # latch for a later request instead.
    if acknowledged_rehydrate_book_ids:
        echo_book_ids = [
            book_id for book_id in acknowledged_rehydrate_book_ids
            if book_id not in reading_state_book_ids_emitted
            and book_id not in rehydrate_book_ids
            and book_id not in books_to_delete_ids
        ]
        if echo_book_ids:
            try:
                echo_states = {
                    state.book_id: state
                    for state in ub.session.query(ub.KoboReadingState).filter(
                        ub.KoboReadingState.user_id == current_user.id,
                        ub.KoboReadingState.book_id.in_(echo_book_ids),
                    ).all()
                }
                for book_id in echo_book_ids:
                    kobo_reading_state = echo_states.get(book_id)
                    if kobo_reading_state is None:
                        continue
                    book = calibre_db.session.query(db.Books).filter(
                        db.Books.id == book_id,
                    ).one_or_none()
                    if book is None:
                        continue
                    sync_results.append({
                        "ChangedReadingState": {
                            "ReadingState": get_kobo_reading_state_response(
                                book, kobo_reading_state,
                            ),
                        },
                    })
                    reading_state_book_ids_emitted.append(book_id)
                    confirmation_echo_book_ids.append(book_id)
            except Exception:
                _rollback_after_sync_failure()
                try:
                    log.exception(
                        "Kobo Sync failed "
                        "reason=rehydrate_confirmation_echo_failed",
                    )
                except Exception:  # noqa: BLE001 - preserve retryable 503
                    pass
                return _abort_sync_with_observability(
                    503,
                    requesting_device_id,
                    sync_cursor_in,
                    response_mode="rehydrate_confirmation_echo_failed",
                    capture_session=capture_session,
                )

    sync_shelves(sync_token, sync_results, only_kobo_shelves)

    # Always emit DeletedTags for magic shelves that should NOT be on
    # the device — covers two distinct cases:
    #   (a) per-shelf kobo_sync flag was just flipped to False (user
    #       toggled a specific magic shelf off in the UI), or
    #   (b) global config_kobo_sync_magic_shelves flag was flipped off
    #       (admin disabled the whole feature). In case (b) every
    #       magic shelf the user owns needs a tombstone, even ones
    #       still marked kobo_sync=True, because the device may have
    #       synced them while the global flag was on.
    # Without this, toggling either flag off leaves orphan magic-shelf
    # entries on previously-synced devices that retry DELETE forever
    # (B1 retry loop). Wire-confirmed against the live
    # "Test_E2E_Discovered" magic-shelf during the 2026-05-17 capture.
    deletable_magic_shelves = ub.session.query(ub.MagicShelf).filter_by(
        user_id=current_user.id,
    )
    if config.config_kobo_sync_magic_shelves:
        deletable_magic_shelves = deletable_magic_shelves.filter_by(kobo_sync=False)
    # else: global flag off, emit DeletedTag for all magic shelves
    for shelf in deletable_magic_shelves.all():
        sync_results.append({
            "DeletedTag": {
                "Tag": {
                    "Id": shelf.uuid,
                    "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified)
                }
            }
        })

    # Add magic shelves as collections (only when feature is enabled)
    if config.config_kobo_sync_magic_shelves:
        magic_shelves = ub.session.query(ub.MagicShelf)\
            .filter_by(user_id=current_user.id, kobo_sync=True)\
            .all()

        new_tags_last_modified = sync_token.tags_last_modified

        for shelf in magic_shelves:
            books, _ = magic_shelf.get_books_for_magic_shelf(
                shelf.id, page=1, page_size=None
            )

            new_tags_last_modified = max(shelf.last_modified, new_tags_last_modified)

            tag = create_kobo_tag_magic(shelf, books)
            if not tag:
                continue

            if shelf.created > sync_token.tags_last_modified:
                log.debug("Syncing new magic shelf %s to Kobo device", shelf.name)
                sync_results.append({
                    "NewTag": tag
                })
            else:
                log.debug("Syncing changed magic shelf %s to Kobo device", shelf.name)
                sync_results.append({
                    "ChangedTag": tag
                })

        sync_token.tags_last_modified = new_tags_last_modified

    # Emit device-compatible removal tombstones for books hard-deleted from CW.
    # (B3 fix — closes the orphan-book-on-device gap from the 2026-05-17
    # MITM capture). editbooks.delete_whole_book captures (user_id,
    # book_uuid, deleted_at) into kobo_deleted_book before tearing down
    # the metadata.db row; here we play those tombstones back to each
    # affected device as an archived ChangedEntitlement. The timestamp is an
    # advisory high-watermark; a missing acknowledged row for this physical
    # device is also delivery work, including after Full Sync clears the
    # ledger. Page-cap with SYNC_ITEM_LIMIT so a mass reannouncement cannot
    # exceed the deletion response bound.
    #
    # Keep the timestamp arm on the original incoming cursor, NOT the local
    # new_archived_last_modified. The latter may already have been advanced by
    # an ArchivedBook row and would mask a legitimate tombstone between those
    # two values.
    cursor_archive_lm = sync_token.archive_last_modified
    deletion_recovery_filter = (
        ub.KoboDeletedBook.deleted_at > cursor_archive_lm
    )
    if requesting_device_id:
        acknowledged_deletion_uuids = select(
            ub.KoboDeviceDeletedEntitlement.book_uuid,
        ).where(
            ub.KoboDeviceDeletedEntitlement.device_id
            == int(requesting_device_id),
        )
        deletion_recovery_filter = or_(
            deletion_recovery_filter,
            ~ub.KoboDeletedBook.book_uuid.in_(
                acknowledged_deletion_uuids,
            ),
        )
    pending_deletions = (
        ub.session.query(ub.KoboDeletedBook)
        .filter(ub.KoboDeletedBook.user_id == current_user.id)
        .filter(deletion_recovery_filter)
        .order_by(
            ub.KoboDeletedBook.deleted_at,
            ub.KoboDeletedBook.book_uuid,
        )
    )
    deletion_snapshot_ids = _capture_query_identities(
        pending_deletions, ub.KoboDeletedBook.id,
    )
    deletion_candidate_count = len(deletion_snapshot_ids)
    deleted_fingerprint_updates = {}
    deleted_change_basis_updates = {}
    deletion_candidates_scanned = 0
    deletion_delivery_slots = 0
    deletion_selection_exhausted = deletion_candidate_count == 0

    for deletion_page in _bounded_query_pages(
            pending_deletions,
            deletion_snapshot_ids,
            ub.KoboDeletedBook.id,
            lambda tombstone: tombstone.id,
            SYNC_ITEM_LIMIT):
        deletion_page_uuids = [
            str(tombstone.book_uuid) for tombstone in deletion_page
        ]
        if replay_suppression_eligible:
            # This ledger read decides wire behavior and therefore must not use
            # the diagnostic fail-open path. Exact acknowledged tombstones are
            # suppressed for every token shape, including absent/partial.
            try:
                known_deleted_fingerprints = \
                    kobo_sync_status.get_device_deleted_entitlement_fingerprints(
                    requesting_device_id,
                    deletion_page_uuids,
                )
            except Exception:
                _log_delivery_ledger_read_failure("deleted_entitlement")
                return _abort_sync_with_observability(
                    503,
                    requesting_device_id,
                    sync_cursor_in,
                    response_mode="deleted_entitlement_ledger_read_failed",
                    capture_session=capture_session,
                )
        else:
            known_deleted_fingerprints = _diagnostic_ledger_lookup(
                kobo_sync_status.get_device_deleted_entitlement_fingerprints,
                requesting_device_id,
                deletion_page_uuids,
                scope="removed_non_suppressing",
            )
        prior_deleted_fingerprints = known_deleted_fingerprints
        for tombstone in deletion_page:
            book_uuid = str(tombstone.book_uuid)
            entitlement = {
                "BookEntitlement": create_deleted_book_entitlement(
                    book_uuid, tombstone.deleted_at),
                "BookMetadata": create_deleted_book_metadata(book_uuid),
            }
            fingerprint = (
                _entitlement_fingerprint(entitlement)
                if requesting_device_id else None
            )
            deleted_change_basis = _deleted_entitlement_change_basis(
                tombstone.deleted_at,
            )
            if replay_suppression_eligible:
                (
                    entitlement_is_unchanged,
                    shape_reseed,
                    refresh_fingerprint_record,
                ) = _entitlement_replay_decision(
                    prior_deleted_fingerprints.get(book_uuid),
                    fingerprint,
                    deleted_change_basis,
                )
            else:
                entitlement_is_unchanged = False
                shape_reseed = False
                refresh_fingerprint_record = False
            if entitlement_is_unchanged:
                suppressed_deleted_entitlement_uuids.add(book_uuid)
                if shape_reseed:
                    reseeded_shape_change_deleted_uuids.add(book_uuid)
                if refresh_fingerprint_record:
                    deleted_fingerprint_updates[book_uuid] = fingerprint
                    deleted_change_basis_updates[book_uuid] = \
                        deleted_change_basis
            else:
                known_record = known_deleted_fingerprints.get(book_uuid)
                if known_record is not None:
                    fingerprint_mismatch_reemitted += \
                        _observe_entitlement_reemit(
                            reemit_reasons,
                            known_record,
                            fingerprint,
                            deleted_change_basis,
                            eligible=replay_suppression_eligible,
                            is_cwng_token=sync_token.is_cwng_token,
                            prefix="removed_",
                        )
                sync_results.append({"ChangedEntitlement": entitlement})
                deletion_delivery_slots += 1
                if requesting_device_id:
                    deleted_fingerprint_updates[book_uuid] = fingerprint
                    deleted_change_basis_updates[book_uuid] = \
                        deleted_change_basis
            ta = tombstone.deleted_at
            if (hasattr(ta, "replace")
                    and getattr(ta, "tzinfo", None) is not None):
                ta = ta.replace(tzinfo=None)
            new_archived_last_modified = max(
                ta, new_archived_last_modified)
            deletion_candidates_scanned += 1
            if deletion_delivery_slots >= SYNC_ITEM_LIMIT:
                break
        if deletion_delivery_slots >= SYNC_ITEM_LIMIT:
            break
    else:
        deletion_selection_exhausted = True

    log.debug(
        "Kobo Sync: deletion scan total=%d scanned=%d deliverable=%d "
        "exhausted=%s",
        deletion_candidate_count,
        deletion_candidates_scanned,
        deletion_delivery_slots,
        deletion_selection_exhausted,
    )
    # Two-way removals and repair-latch consumption are also delivery
    # acknowledgments.  They remain unchanged until the returned token comes
    # back, so a lost response can replay the same commands and repair states.

    # Likewise, never set local continuation for deletion pages.  The returned
    # archive cursor must be persisted before the next page can be selected;
    # pinning the old cursor would just replay these tombstones indefinitely.

    # update last created timestamp to distinguish between new and changed entitlements
    if not cont_sync:
        sync_token.books_last_created = new_books_last_created
    sync_token.books_last_modified = new_books_last_modified
    sync_token.books_last_id = new_books_last_id
    sync_token.magic_shelf_last_id = new_magic_shelf_last_id
    # Persist the cache-rebuild timestamp so the next sync can detect a
    # newer rebuild and reset the sub-cursor (Greptile P on PR #367).
    if magic_shelf_membership_added_at is not None:
        sync_token.magic_shelf_membership_at = max(
            sync_token.magic_shelf_membership_at,
            magic_shelf_membership_added_at,
        )
    sync_token.archive_last_modified = new_archived_last_modified
    sync_token.reading_state_last_modified = new_reading_state_last_modified

    # Even a cursor-identical page needs a distinct acknowledgment token (for
    # example, a DeletedTag-only response).  This nonce is tiny and the device
    # treats the token opaquely; the exact serialized token stays server-side.
    sync_token.delivery_epoch = secrets.token_hex(16)
    response = generate_sync_response(sync_token, sync_results)
    sync_cursor_out = _sync_cursor_summary(sync_token)
    observability = _sync_observability_fail_open(
        sync_results,
        suppressed_replay=(
            len(suppressed_unchanged_book_ids)
            + len(suppressed_deleted_entitlement_uuids)
        ),
        suppressed_unchanged=len(suppressed_unchanged_book_ids),
        suppressed_removed=len(suppressed_deleted_entitlement_uuids),
        reseeded_shape_change=(
            len(reseeded_shape_change_book_ids)
            + len(reseeded_shape_change_deleted_uuids)
        ),
        fingerprint_mismatch_reemitted=fingerprint_mismatch_reemitted,
        reemit_reasons=reemit_reasons,
        replay_suppression_enabled=replay_suppression_enabled,
        replay_suppression_eligible=replay_suppression_eligible,
    )

    if requesting_device_id:
        confirmation = {
            "payload_schema_version": ENTITLEMENT_PAYLOAD_SCHEMA_VERSION,
            "live_entitlements": {
                str(book_id): {
                    "fingerprint": fingerprint,
                    "change_basis": entitlement_change_basis_updates.get(
                        book_id,
                    ),
                }
                for book_id, fingerprint
                in entitlement_fingerprint_updates.items()
            },
            "synced_books": delivered_book_identities,
            "rehydrate_book_ids": sorted(rehydrate_book_ids),
            "deleted_entitlements": {
                book_uuid: {
                    "fingerprint": fingerprint,
                    "change_basis": deleted_change_basis_updates.get(book_uuid),
                }
                for book_uuid, fingerprint in deleted_fingerprint_updates.items()
            },
            "removed_book_ids": sorted(books_to_delete_ids),
            "observability": observability,
            "rehydrate_positions": [
                {
                    "id": position.id,
                    "server_modified_at": _pending_position_clock(
                        position.server_modified_at,
                    ),
                }
                for position in rehydrate_positions_emitted
            ],
            "confirmation_echo_book_ids": sorted(
                set(confirmation_echo_book_ids),
            ),
        }
        stored_headers = {
            header_name: header_value
            for header_name, header_value in response.headers.items()
            if header_name.lower() in {
                "x-kobo-sync",
                "x-kobo-sync-mode",
                "x-kobo-recent-reads",
            }
        }
        kobo_sync_status.stage_pending_sync_page(
            requesting_device_id,
            _sync_token_hash(raw_sync_token),
            response.headers[SyncToken.SyncToken.SYNC_TOKEN_HEADER],
            response.get_data(as_text=True),
            json.dumps(stored_headers, separators=(",", ":")),
            _serialize_pending_confirmation(confirmation),
        )
    else:
        # Authenticated production requests resolve a physical device. Keep the
        # historical fallback for synthetic callers that do not supply one.
        kobo_sync_status.add_synced_books_batch(
            delivered_book_identities, commit=False,
        )
        for position in rehydrate_positions_emitted:
            position.rehydrate_needed = False

    # This commit makes only the replayable response durable. Confirmed
    # delivery state was promoted at the beginning of this request (when its
    # predecessor token was presented) and lands atomically with this page.
    if ub.session_commit() is False:
        return _abort_sync_with_observability(
            503,
            requesting_device_id,
            sync_cursor_in,
            response_mode="commit_failed",
            capture_session=capture_session,
            outgoing_cursor=sync_cursor_out,
            observability=observability,
        )
    _mark_pending_page_reset_staged(False)
    _log_sync_observability(
        requesting_device_id,
        sync_cursor_in,
        sync_cursor_out,
        observability,
        response_mode="new_page",
        capture_session=capture_session,
    )
    return response


def _dispatch_sync_request():
    return _run_sync_with_pending_page_reset_boundary(HandleSyncRequest)


kobo.add_url_rule(
    "/v1/library/sync",
    endpoint="HandleSyncRequest",
    view_func=_dispatch_sync_request,
)


def generate_sync_response(sync_token, sync_results):
    extra_headers = {}
    if config.config_kobo_proxy:
        # Merge in sync results from the official Kobo store.
        try:
            store_response = make_request_to_kobo_store(sync_token)

            store_sync_results = store_response.json()
            sync_results += store_sync_results
            sync_token.merge_from_store_response(store_response)
            for header_name in (
                "x-kobo-sync",
                "x-kobo-sync-mode",
                "x-kobo-recent-reads",
            ):
                header_value = store_response.headers.get(header_name)
                if header_value is None:
                    continue
                # Kobo firmware pins the incoming token whenever `continue`
                # is present.  Local page selection depends on the device
                # persisting our returned token, so a store continuation must
                # never escape on the combined response.
                if (
                    header_name == "x-kobo-sync"
                    and str(header_value).strip().lower() == "continue"
                ):
                    continue
                extra_headers[header_name] = header_value

        except Exception as ex:
            log.error_or_exception("Failed to receive or parse response from Kobo's sync endpoint: {}".format(ex))
    sync_token.to_headers(extra_headers)

    # Track Kobo sync activity
    try:
        from cps.cwa_db_loader import load_cwa_db
        CWA_DB = load_cwa_db().CWA_DB
        import json as json_lib
        cwa_db = CWA_DB()
        cwa_db.log_activity(
            user_id=int(current_user.id),
            user_name=current_user.name,
            event_type='KOBO_SYNC',
            extra_data=json_lib.dumps({
                'books_synced': len(sync_results),
                'endpoint': '/v1/library/sync'
            })
        )
    except Exception as e:
        log.debug(f"Failed to log Kobo sync activity: {e}")

    # log.debug("Kobo Sync Content: {}".format(sync_results))
    # jsonify decodes the unicode string different to what kobo expects
    response = make_response(json.dumps(sync_results), extra_headers)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    _warn_sync_header_budget(response)
    return response


@kobo.route("/v1/library/<book_uuid>/metadata")
@requires_kobo_auth
@download_required
def HandleMetadataRequest(book_uuid):
    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')
    log.info("Kobo library metadata request received for book %s" % book_uuid)
    # Policy boundary: metadata GET. Symmetric with web/OPDS — books the
    # user can't see in the web UI shouldn't expose metadata via Kobo.
    book = calibre_db.get_book_by_uuid_for_kobo(book_uuid, enforce_policy=True)
    if not book or not book.data:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    metadata = get_metadata(book)
    response = make_response(json.dumps([metadata], ensure_ascii=False))
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


def get_download_url_for_book(book_id, book_format):
    endpoint_name = "kobo.download_book"
    endpoint_path = "download"
    if request.headers.get("x-kobo-deviceos") == "Android":
        endpoint_name = "kobo.redirect_download_book"
        endpoint_path = "redirect_download"

    if not current_app.wsgi_app.is_proxied:
        if ':' in request.host and not request.host.endswith(']'):
            host = "".join(request.host.split(':')[:-1])
        else:
            host = request.host

        return "{url_scheme}://{url_base}:{url_port}/kobo/{auth_token}/{endpoint_path}/{book_id}/{book_format}".format(
            url_scheme=request.scheme,
            url_base=host,
            url_port=config.config_external_port,
            auth_token=get_auth_token(),
            endpoint_path=endpoint_path,
            book_id=book_id,
            book_format=book_format.lower()
        )
    return url_for(
        endpoint_name,
        auth_token=kobo_auth.get_auth_token(),
        book_id=book_id,
        book_format=book_format.lower(),
        _external=True,
    )


def create_book_entitlement(book, archived):
    book_uuid = str(book.uuid)
    created = convert_to_kobo_timestamp_string(book.timestamp)
    return {
        "Accessibility": "Full",
        # ActivePeriod is entitlement data, not response-generation time.  A
        # wall-clock value made the payload mutate on every replay (#1925).
        "ActivePeriod": {"From": created},
        "Created": created,
        "CrossRevisionId": book_uuid,
        "Id": book_uuid,
        "IsRemoved": archived,
        "IsHiddenFromArchive": False,
        "IsLocked": False,
        "LastModified": convert_to_kobo_timestamp_string(book.last_modified),
        "OriginCategory": "Imported",
        "RevisionId": book_uuid,
        "Status": "Active",
    }


def create_deleted_book_entitlement(book_uuid, deleted_at):
    """Build the archive-shaped entitlement Kobo firmware honors for a hard
    delete.  A bare ``DeletedEntitlement`` removes store bookkeeping but can
    leave a side-loaded book in My Books.  This mirrors the working live-book
    shelf-removal shape while hiding a hard-deleted book from Archive, where it
    could no longer be downloaded."""
    book_uuid = str(book_uuid)
    timestamp = convert_to_kobo_timestamp_string(deleted_at)
    return {
        "Accessibility": "Full",
        "ActivePeriod": {"From": timestamp},
        "Created": timestamp,
        "CrossRevisionId": book_uuid,
        "Id": book_uuid,
        "IsRemoved": True,
        "IsHiddenFromArchive": True,
        "IsLocked": False,
        "LastModified": timestamp,
        "OriginCategory": "Imported",
        "RevisionId": book_uuid,
        "Status": "Active",
    }


def create_deleted_book_metadata(book_uuid):
    """Return a type-complete metadata companion for a deleted entitlement.

    The calibre row and file are already gone, so identity is the retained
    UUID and DownloadUrls is intentionally empty.  Kobo receives the same key
    types as a normal entitlement without being offered a dead download.
    """
    book_uuid = str(book_uuid)
    return {
        "Categories": ["00000000-0000-0000-0000-000000000001"],
        "ContributorRoles": [],
        "Contributors": None,
        "CoverImageId": book_uuid,
        "CrossRevisionId": book_uuid,
        "CurrentDisplayPrice": {"CurrencyCode": "USD", "TotalAmount": 0},
        "CurrentLoveDisplayPrice": {"TotalAmount": 0},
        "Description": None,
        "DownloadUrls": [],
        "EntitlementId": book_uuid,
        "ExternalIds": [],
        "Genre": "00000000-0000-0000-0000-000000000001",
        "IsEligibleForKoboLove": False,
        "IsInternetArchive": False,
        "IsPreOrder": False,
        "IsSocialEnabled": True,
        "Language": "en",
        "PhoneticPronunciations": {},
        "PublicationDate": None,
        "Publisher": {"Imprint": "", "Name": None},
        "RevisionId": book_uuid,
        "Series": {},
        "Subtitle": "",
        "Title": book_uuid,
        "WorkId": book_uuid,
    }


def current_time():
    return strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())


def get_description(book):
    if not book.comments:
        return None
    return book.comments[0].text


def get_author(book):
    if not book.authors:
        return {"Contributors": None}
    author_list = []
    autor_roles = []
    for author in book.authors:
        autor_roles.append({"Name": author.name})
        author_list.append(author.name)
    return {"ContributorRoles": autor_roles, "Contributors": author_list}


def get_publisher(book):
    if not book.publishers:
        return None
    return book.publishers[0].name


def get_series(book):
    if not book.series:
        return None
    return book.series[0].name


def get_subtitle(book):
    # Backport of janeczku PR #3358 (@dotknott): surface a per-book
    # Subtitle in Kobo sync metadata when the Calibre library has a
    # custom column labeled "subtitle". The upstream patch had three
    # null-handling bugs (.all()[0] IndexError when no column exists,
    # unreachable else branch, TypeError on None custom_column attr);
    # rewritten here for correct empty-result handling end-to-end.
    col = (calibre_db.session.query(db.CustomColumns)
                       .filter(db.CustomColumns.mark_for_delete == 0)
                       .filter(db.CustomColumns.datatype.notin_(db.cc_exceptions))
                       .filter(db.CustomColumns.label == 'subtitle')
                       .first())
    if col is None:
        return ""
    column_attr = getattr(book, 'custom_column_' + str(col.id), None)
    if not column_attr:
        return ""
    value = getattr(column_attr[0], 'value', None)
    return value or ""


def get_seriesindex(book):
    return book.series_index if isinstance(book.series_index, float) else 1


def get_language(book):
    if not book.languages:
        return 'en'
    return isoLanguages.get(part3=book.languages[0].lang_code).part1


def _normalize_cover_uuid(image_id):
    return normalize_cover_uuid(image_id)


_REQUESTING_DEVICE_ASPECT_G_KEY = "_kobo_requesting_device_aspect"


def _requesting_device_aspect():
    """Aspect preset for the Kobo making *this* request, or None.

    The padding aspect is a single instance-wide setting, which is wrong the
    moment a household owns two different Kobos: one of them gets covers shaped
    for the other. We already record every device's model (`Device.model`, fed
    by the `x-kobo-devicemodel` header on every authenticated Kobo request), so
    the information needed to shape covers correctly is present and was simply
    never consulted.

    Resolution order:
      1. the `x-kobo-devicemodel` header on this request -- the device speaking
         for itself, and the only source that is certainly about *this* device;
      2. the model recorded for this user's Kobo, when the header is absent and
         the answer is unambiguous (exactly one preset is implied).

    Returns None whenever the answer is not certain -- unknown model, no
    request context, no user, or more than one candidate device with no header
    to disambiguate. None means "keep the configured aspect", i.e. exactly
    today's behaviour, so this can only make covers more correct, never less.
    """
    if not has_request_context():
        return None

    resolved = None
    try:
        if hasattr(g, _REQUESTING_DEVICE_ASPECT_G_KEY):
            return getattr(g, _REQUESTING_DEVICE_ASPECT_G_KEY)

        header_model = request.headers.get("x-kobo-devicemodel")
        preset = cover_preview.preset_for_device_model(header_model)
        if preset:
            resolved = preset
        else:
            # No usable header. Fall back to what this user's Kobos told us
            # earlier, but only when their mapped models imply one preset.
            # Different presets with no header are ambiguous; the configured
            # value is the more honest answer.
            user_id = getattr(current_user, "id", None)
            if user_id:
                models = [
                    row[0]
                    for row in ub.session.query(ub.Device.model)
                    .filter(ub.Device.user_id == user_id,
                            ub.Device.kind == "kobo",
                            ub.Device.active.is_(True))
                    .all()
                ]
                candidates = {cover_preview.preset_for_device_model(m) for m in models}
                candidates.discard(None)
                if len(candidates) == 1:
                    resolved = candidates.pop()
    except Exception as exc:
        # Cover shaping must never be the reason a sync or a cover fetch fails.
        log.debug("Kobo Sync: could not resolve a per-device cover aspect: %s", exc)
        resolved = None

    # This function runs once per book on sync-metadata construction. Cache
    # both a preset and the fail-safe None for this request so an absent or
    # unrecognised header cannot turn into one Device query per book.
    try:
        setattr(g, _REQUESTING_DEVICE_ASPECT_G_KEY, resolved)
    except Exception as exc:
        # A cache failure must not make cover shaping fail. Resolution remains
        # usable for this call; the next call will simply resolve again.
        log.debug("Kobo Sync: could not cache the per-device cover aspect: %s", exc)
    return resolved


def _current_padding_settings():
    """Snapshot of the admin's Kobo-padding config, with the aspect narrowed to
    the requesting device when we can identify it.

    Centralized so the sync-metadata path and the cover-serving path agree on
    the same hash -- and note that agreement is what makes the per-device
    aspect safe: `settings_hash()` already folds in `target_aspect`, so two
    devices resolve to two different CoverImageIds and two different cache
    entries, with no cross-contamination and no extra bookkeeping."""
    configured = getattr(config, "config_kobo_cover_padding_aspect", "kobo_libra_color") or "kobo_libra_color"
    return cover_preview.CoverPreviewSettings(
        enabled=bool(getattr(config, "config_kobo_cover_padding_enabled", False)),
        target_aspect=_requesting_device_aspect() or configured,
        fill_mode=getattr(config, "config_kobo_cover_padding_fill_mode", "edge_mirror") or "edge_mirror",
        manual_color=getattr(config, "config_kobo_cover_padding_color", "") or "",
    )


def _get_cover_image_id(book):
    base_id = str(book.uuid)
    try:
        # A personal preference changes only the bytes returned by the
        # authenticated image endpoint. It must never change BookMetadata:
        # CoverImageId participates in the entitlement fingerprint, and
        # changing it makes a held book look like a new/changed entitlement.
        # HandleInitRequest versions the per-user image URL template instead,
        # refreshing the image without touching book metadata or device ledgers.
        cover_path = None
        if not config.config_use_google_drive:
            cover_path = os.path.join(config.get_book_path(), book.path, "cover.jpg")
        image_id = build_cover_image_id(
            base_id,
            use_google_drive=config.config_use_google_drive,
            last_modified=book.last_modified,
            cover_path=cover_path,
        )
        # When server-side padding is on, append its settings hash so a
        # device whose cached cover was rendered with old settings
        # re-fetches after the admin changes the aspect or fill style.
        padding = _current_padding_settings()
        if padding.enabled:
            image_id = f"{image_id}-p{padding.settings_hash()}"
        return image_id
    except Exception as exc:
        log.debug("Kobo Sync: failed to build cover image id for book %s: %s", book.id, exc)
        return base_id

def build_download_url(book, book_data, download_format, declared_format):
    # Preserve the v4.1.42 wire contract. Clara hardware recorded
    # ``___FileSize=0`` when Size was omitted, a visible regression affecting
    # 100 books. The value is the stable stored Data size even when the route
    # later generates or rewrites the delivered artifact; replay suppression,
    # not Size omission, is the proven de-download fix (#1925).
    return {
        "Format": declared_format,
        "Url": get_download_url_for_book(book.id, download_format),
        "Platform": "Generic",
        "DrmType": "None",
        "Size": book_data.uncompressed_size,
    }

def get_metadata(book):
    download_urls = []

    kepub_data = next((d for d in book.data if d.format == 'KEPUB'), None)
    epub_data  = next((d for d in book.data if d.format == 'EPUB'),  None)

    # Send kepub if kepub format is available or if deferred kepub conversion
    # is supported
    if kepub_data:
        book_data, dl_format = kepub_data, 'kepub'
    elif epub_data and config.config_kepubifypath and config.config_kobo_prefer_kepub:
        book_data, dl_format = epub_data, 'kepub'
    elif epub_data:
        book_data, dl_format = epub_data, 'epub'
    else:
        book_data = None

    if book_data:
        is_fixed_layout = False
        try:
            if get_epub_layout(book, book_data) == 'pre-paginated':
                is_fixed_layout = True
        except (zipfile.BadZipfile, FileNotFoundError) as e:
            log.error(e)
        if is_fixed_layout:
            # Only send EPUB3FL if the book is a fixed layout. This forces the device
            # to pick this download, regardless of the priority order in the firmware
            download_urls.append(build_download_url(book, book_data, dl_format, 'EPUB3FL'))
        else:
            if dl_format == 'kepub':
                download_urls.append(build_download_url(book, book_data, dl_format, 'KEPUB'))
            else:
                # Send both EPUB and EPUB3 for epub files, in case legacy devices only support
                # EPUB download urls
                download_urls.append(build_download_url(book, book_data, dl_format, 'EPUB3'))
                download_urls.append(build_download_url(book, book_data, dl_format, 'EPUB'))

    book_uuid = book.uuid
    cover_image_id = _get_cover_image_id(book)
    if cover_image_id != str(book_uuid):
        log.debug("Kobo Sync: cache-busting cover id for book %s: %s", book.id, cover_image_id)
    metadata = {
        "Categories": ["00000000-0000-0000-0000-000000000001", ],
        # "Contributors": get_author(book),
        "CoverImageId": cover_image_id,
        "CrossRevisionId": book_uuid,
        "CurrentDisplayPrice": {"CurrencyCode": "USD", "TotalAmount": 0},
        "CurrentLoveDisplayPrice": {"TotalAmount": 0},
        "Description": get_description(book),
        "DownloadUrls": download_urls,
        "EntitlementId": book_uuid,
        "ExternalIds": [],
        "Genre": "00000000-0000-0000-0000-000000000001",
        "IsEligibleForKoboLove": False,
        "IsInternetArchive": False,
        "IsPreOrder": False,
        "IsSocialEnabled": True,
        "Language": get_language(book),
        "PhoneticPronunciations": {},
        "PublicationDate": convert_to_kobo_timestamp_string(book.pubdate),
        "Publisher": {"Imprint": "", "Name": get_publisher(book), },
        "RevisionId": book_uuid,
        "Title": book.title,
        "Subtitle": get_subtitle(book),
        "WorkId": book_uuid,
        "Series": {},
    }
    metadata.update(get_author(book))

    series_name = get_series(book)
    if series_name:
        name = series_name
        try:
            metadata["Series"] = {
                "Name": series_name,
                "Number": get_seriesindex(book),        # ToDo Check int() ?
                "NumberFloat": float(get_seriesindex(book)),
                # Get a deterministic id based on the series name.
                "Id": str(uuid.uuid3(uuid.NAMESPACE_DNS, name)),
            }
        except Exception as e:
            print(e)
    return metadata


@csrf.exempt
@kobo.route("/v1/library/tags", methods=["POST", "DELETE"])
@requires_kobo_auth
# Creates a Shelf with the given items, and returns the shelf's uuid.
def HandleTagCreate():
    # catch delete requests, otherwise they are handled in the book delete handler
    if request.method == "DELETE":
        abort(405)
    name, items = None, None
    try:
        shelf_request = request.json
        name = shelf_request["Name"]
        items = shelf_request["Items"]
        if not name:
            raise TypeError
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags request.")
        abort(400, description="Malformed tags POST request. Data has empty 'Name', missing 'Name' or 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.name == name, ub.Shelf.user_id ==
                                              current_user.id).one_or_none()
    if shelf and not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to create shelf.")

    if not shelf:
        # Device-created shelves are Kobo-managed by definition; default
        # kobo_sync=True so subsequent syncs continue to emit NewTag and
        # the shelf round-trips across multi-device setups + survives a
        # factory reset. Users can untoggle via the shelf-edit UI later.
        shelf = ub.Shelf(user_id=current_user.id, name=name, uuid=str(uuid.uuid4()), kobo_sync=True)
        ub.session.add(shelf)

    items_unknown_to_calibre = add_items_to_shelf(items, shelf)
    if items_unknown_to_calibre:
        log.debug("Received request to add unknown books to a collection. Silently ignoring items.")
    if ub.session_commit() is False:
        return abort(503)
    return make_response(jsonify(str(shelf.uuid)), 201)


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>", methods=["DELETE", "PUT"])
@requires_kobo_auth
def HandleTagUpdate(tag_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        # Device-trailing: the shelf is unknown to CalibreWeb because it
        # was deleted server-side (or was a magic-shelf that no longer
        # syncs). The device only sees the absence; without a tombstone
        # in the sync response it pushes DELETE here every sync. Answering
        # 404 makes the device interpret the failure as transient and
        # retry forever, burning bandwidth indefinitely. Match the
        # HandleStateRequest pattern (line ~989) and
        # redirect_or_proxy_request() unconditionally so the loop ends,
        # regardless of config_kobo_proxy.
        log.debug("Received Kobo tag update for unknown collection %s — "
                  "answering via redirect_or_proxy_request to terminate "
                  "the device's retry loop", tag_id)
        return redirect_or_proxy_request()

    if request.method == "DELETE":
        if not shelf_lib.check_shelf_edit_permissions(shelf):
            abort(401, description="Error deleting Shelf")
        if not shelf_lib.delete_shelf_helper(shelf):
            return abort(503)
    else:
        name = None
        try:
            shelf_request = request.json
            name = shelf_request["Name"]
        except (KeyError, TypeError):
            log.debug("Received malformed v1/library/tags rename request.")
            abort(400, description="Malformed tags POST request. Data is missing 'Name' field")

        shelf.name = name
        ub.session.merge(shelf)
        if ub.session_commit() is False:
            return abort(503)
    return make_response(' ', 200)


# Adds items to the given shelf.
def add_items_to_shelf(items, shelf):
    book_ids_already_in_shelf = set([book_shelf.book_id for book_shelf in shelf.books])
    items_unknown_to_calibre = []
    for item in items:
        try:
            if item["Type"] != "ProductRevisionTagItem":
                items_unknown_to_calibre.append(item)
                continue

            # Policy boundary: shelf-add. Adding a denied/hidden book to a
            # Kobo-synced shelf would leak it to every other Kobo on the
            # same account. Treat as unknown-to-calibre so the device
            # silently drops it (existing items_unknown_to_calibre path).
            book = calibre_db.get_book_by_uuid_for_kobo(
                item["RevisionId"], enforce_policy=True,
            )
            if not book:
                items_unknown_to_calibre.append(item)
                continue

            book_id = book.id
            if book_id not in book_ids_already_in_shelf:
                shelf.books.append(ub.BookShelf(book_id=book_id))
        except KeyError:
            items_unknown_to_calibre.append(item)
    return items_unknown_to_calibre


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>/items", methods=["POST"])
@requires_kobo_auth
def HandleTagAddItem(tag_id):
    items = None
    try:
        tag_request = request.json
        items = tag_request["Items"]
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags/<tag_id>/items/delete request.")
        abort(400, description="Malformed tags POST request. Data is missing 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        log.debug("Received Kobo request on a collection unknown to CalibreWeb")
        abort(404, description="Collection isn't known to CalibreWeb")

    if not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to edit shelf.")

    items_unknown_to_calibre = add_items_to_shelf(items, shelf)
    if items_unknown_to_calibre:
        log.debug("Received request to add an unknown book to a collection. Silently ignoring item.")

    ub.session.merge(shelf)
    if ub.session_commit() is False:
        return abort(503)
    return make_response('', 201)


@csrf.exempt
@kobo.route("/v1/library/tags/<tag_id>/items/delete", methods=["POST"])
@requires_kobo_auth
def HandleTagRemoveItem(tag_id):
    items = None
    try:
        tag_request = request.json
        items = tag_request["Items"]
    except (KeyError, TypeError):
        log.debug("Received malformed v1/library/tags/<tag_id>/items/delete request.")
        abort(400, description="Malformed tags POST request. Data is missing 'Items' field")

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.uuid == tag_id,
                                              ub.Shelf.user_id == current_user.id).one_or_none()
    if not shelf:
        # Same device-trailing pattern as HandleTagUpdate: the shelf
        # vanished server-side between syncs but the device still
        # thinks it exists and is now trying to remove an item from it.
        # Answering 404 traps the device in a retry loop. Mirror the
        # HandleStateRequest pattern and redirect_or_proxy_request().
        log.debug(
            "Received Kobo tag-remove-item for unknown collection %s — "
            "answering via redirect_or_proxy_request to terminate "
            "the device's retry loop", tag_id)
        return redirect_or_proxy_request()

    if not shelf_lib.check_shelf_edit_permissions(shelf):
        abort(401, description="User is unauthaurized to edit shelf.")

    items_unknown_to_calibre = []
    for item in items:
        try:
            if item["Type"] != "ProductRevisionTagItem":
                items_unknown_to_calibre.append(item)
                continue

            # Destructive, user-initiated: shelf-remove. Never block on
            # policy filters — the user must be able to clean up shelves
            # even when the underlying book is now hidden / denied / etc.
            book = calibre_db.get_book_by_uuid_for_kobo(
                item["RevisionId"], enforce_policy=False,
            )
            if not book:
                items_unknown_to_calibre.append(item)
                continue

            shelf.books.filter(ub.BookShelf.book_id == book.id).delete()
        except KeyError:
            items_unknown_to_calibre.append(item)
    if ub.session_commit() is False:
        return abort(503)

    if items_unknown_to_calibre:
        log.debug("Received request to remove an unknown book to a collecition. Silently ignoring item.")

    return make_response('', 200)


# Add new, changed, or deleted shelves to the sync_results.
# Note: Public shelves that aren't owned by the user aren't supported.
def sync_shelves(sync_token, sync_results, only_kobo_shelves=False):
    """Stage shelf response effects for HandleSyncRequest's checked commit."""
    new_tags_last_modified = sync_token.tags_last_modified
    # transmit all archived shelfs independent of last sync (why should this matter?)
    for shelf in ub.session.query(ub.ShelfArchive).filter(ub.ShelfArchive.user_id == current_user.id):
        new_tags_last_modified = max(shelf.last_modified, new_tags_last_modified)
        sync_results.append({
            "DeletedTag": {
                "Tag": {
                    "Id": shelf.uuid,
                    "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified)
                }
            }
        })
        ub.session.delete(shelf)

    extra_filters = []
    if only_kobo_shelves:
        for shelf in ub.session.query(ub.Shelf).filter(
            func.datetime(ub.Shelf.last_modified) > sync_token.tags_last_modified,
            ub.Shelf.user_id == current_user.id,
            ub.Shelf.kobo_sync == False  # noqa: E712 -- SQLAlchemy column comparison
        ):
            sync_results.append({
                "DeletedTag": {
                    "Tag": {
                        "Id": shelf.uuid,
                        "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified)
                    }
                }
            })
        extra_filters.append(ub.Shelf.kobo_sync)

    shelflist = ub.session.query(ub.Shelf).outerjoin(ub.BookShelf).filter(
        or_(func.datetime(ub.Shelf.last_modified) > sync_token.tags_last_modified,
            func.datetime(ub.BookShelf.date_added) > sync_token.tags_last_modified),
        ub.Shelf.user_id == current_user.id,
        *extra_filters
    ).distinct().order_by(func.datetime(ub.Shelf.last_modified).asc())

    for shelf in shelflist:
        if not shelf_lib.check_shelf_view_permissions(shelf):
            continue

        new_tags_last_modified = max(shelf.last_modified, new_tags_last_modified)

        tag = create_kobo_tag(shelf)
        if not tag:
            continue

        if shelf.created > sync_token.tags_last_modified:
            sync_results.append({
                "NewTag": tag
            })
        else:
            sync_results.append({
                "ChangedTag": tag
            })
    sync_token.tags_last_modified = new_tags_last_modified


# Creates a Kobo "Tag" object from a ub.Shelf object
def create_kobo_tag(shelf):
    tag = {
        "Created": convert_to_kobo_timestamp_string(shelf.created),
        "Id": shelf.uuid,
        "Items": [],
        "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified),
        "Name": shelf.name,
        "Type": "UserTag"
    }
    for book_shelf in shelf.books:
        book = calibre_db.get_book(book_shelf.book_id)
        if not book:
            log.info("Book (id: %s) in BookShelf (id: %s) not found in book database",  book_shelf.book_id, shelf.id)
            continue
        tag["Items"].append(
            {
                "RevisionId": book.uuid,
                "Type": "ProductRevisionTagItem"
            }
        )
    return {"Tag": tag}

# Creates a Kobo "Tag" object from a ub.MagicShelf object
def create_kobo_tag_magic(shelf, books):
    tag = {
        "Created": convert_to_kobo_timestamp_string(shelf.created),
        "Id": shelf.uuid,
        "Items": [],
        "LastModified": convert_to_kobo_timestamp_string(shelf.last_modified),
        "Name": shelf.name,
        "Type": "UserTag"
    }
    for book in books:
        tag["Items"].append(
            {
                "RevisionId": book.uuid,
                "Type": "ProductRevisionTagItem"
            }
        )
    return {"Tag": tag}


@csrf.exempt
@kobo.route("/v1/library/<book_uuid>/state", methods=["GET", "PUT"])
@requires_kobo_auth
def HandleStateRequest(book_uuid):
    # Device-trailing: state GET/PUT. The book is already on the Kobo;
    # reading progress must keep syncing even if the book later becomes
    # hidden/denied (otherwise the device retries forever and the user
    # sees sync failures). Sync push handler is the policy boundary.
    book = calibre_db.get_book_by_uuid_for_kobo(book_uuid, enforce_policy=False)
    if not book or not book.data:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    kobo_reading_state = get_or_create_reading_state(book.id)

    if request.method == "GET":
        return jsonify([get_kobo_reading_state_response(book, kobo_reading_state)])
    else:
        update_results_response = {"EntitlementId": book_uuid}
        resolved_bookmark_accepted = False

        try:
            request_data = request.json
            request_reading_state = request_data["ReadingStates"][0]

            # Use the device's own timestamp so the GET response mirrors it back,
            # preventing the "newer PT" conflict popup. Official Kobo cloud does the same.
            lm_str = request_reading_state.get("LastModified")
            request_lm = parse_kobo_timestamp(lm_str)
            g.kobo_reading_state_lm = request_lm
            if not device_positions.timestamp_is_newer(
                    request_lm, kobo_reading_state.last_modified):
                # The ORM hook also touches the parent for accepted statistics
                # and status writes. Give it a monotonic clock even when the
                # device's observation is missing or trails the stored row.
                g.kobo_reading_state_lm = getattr(
                    kobo_reading_state, "last_modified", None)

            request_bookmark = request_reading_state["CurrentBookmark"]
            if request_bookmark:
                current_bookmark = kobo_reading_state.current_bookmark
                location = request_bookmark.get("Location")
                incoming_progress = request_bookmark.get("ProgressPercent")
                rehydrate_pending = device_positions.stage_position(
                    device_id=getattr(g, "annotation_origin_device_id", None),
                    book_id=book.id,
                    progress_percent=incoming_progress,
                    content_source_progress_percent=request_bookmark.get(
                        "ContentSourceProgressPercent",
                    ),
                    location_value=location["Value"] if location else None,
                    location_type=location["Type"] if location else None,
                    location_source=location["Source"] if location else None,
                    client_modified_at=request_lm,
                )

                bookmark_outcome = device_positions.advance_kobo_bookmark(
                    current_bookmark,
                    incoming_progress,
                    content_source_progress_percent=request_bookmark.get(
                        "ContentSourceProgressPercent",
                    ),
                    content_source_supplied=(
                        "ContentSourceProgressPercent" in request_bookmark
                    ),
                    location_value=location["Value"] if location else None,
                    location_type=location["Type"] if location else None,
                    location_source=location["Source"] if location else None,
                    location_supplied=bool(location),
                    incoming_clock=request_lm,
                    clock_accepts=True,
                    equal_accepts=True,
                    preserve_clock_when_missing=True,
                    block_lower_at_or_below=(
                        KOB0_COVER_RESET_PROGRESS_EPSILON
                        if rehydrate_pending else None
                    ),
                    session=ub.session,
                )
                resolved_bookmark_accepted = bookmark_outcome.accepted
                cover_reset_suppressed = bool(
                    not bookmark_outcome.accepted
                    and rehydrate_pending
                    and incoming_progress is not None
                    and incoming_progress <= KOB0_COVER_RESET_PROGRESS_EPSILON
                    and bookmark_outcome.percentage is not None
                    and bookmark_outcome.percentage > incoming_progress
                )
                if cover_reset_suppressed:
                    log.info(
                        "Kobo cover reset suppressed for device %s book %s "
                        "(rehydrate_pending=%s): %.2f%% < %.2f%%",
                        getattr(g, "annotation_origin_device_id", None),
                        book.id,
                        rehydrate_pending,
                        incoming_progress,
                        bookmark_outcome.percentage,
                    )
                update_results_response["CurrentBookmarkResult"] = {"Result": "Success"}

            request_statistics = request_reading_state["Statistics"]
            if request_statistics:
                statistics = kobo_reading_state.statistics
                if device_positions.timestamp_is_newer(
                        request_lm, statistics.last_modified):
                    spent = request_statistics.get("SpentReadingMinutes")
                    if spent is not None:
                        statistics.spent_reading_minutes = int(spent)
                    remaining = request_statistics.get("RemainingTimeMinutes")
                    if remaining is not None:
                        statistics.remaining_time_minutes = int(remaining)
                    _apply_kobo_last_modified(statistics, request_lm)
                update_results_response["StatisticsResult"] = {"Result": "Success"}

            request_status_info = request_reading_state["StatusInfo"]
            if request_status_info:
                book_read = kobo_reading_state.book_read_link
                new_book_read_status = get_ub_read_status(request_status_info["Status"])
                status_clock_accepted = device_positions.timestamp_is_newer(
                    request_lm, book_read.last_modified,
                )
                if (new_book_read_status != book_read.read_status
                        and status_clock_accepted):
                    if new_book_read_status == ub.ReadBook.STATUS_IN_PROGRESS:
                        book_read.times_started_reading += 1
                        book_read.last_time_started_reading = datetime.now(timezone.utc)
                    book_read.read_status = new_book_read_status
                    _apply_kobo_last_modified(book_read, request_lm)
                if (status_clock_accepted
                        and new_book_read_status == ub.ReadBook.STATUS_FINISHED
                        and not helper.set_custom_read_column_value(
                            book.id, True, source="Kobo read-status",
                        )):
                    ub.session.rollback()
                    return "", 500
                update_results_response["StatusInfoResult"] = {"Result": "Success"}
        except (KeyError, TypeError, ValueError, StatementError):
            log.debug("Received malformed v1/library/<book_uuid>/state request.")
            ub.session.rollback()
            abort(400, description="Malformed request data is missing 'ReadingStates' key")

        if resolved_bookmark_accepted:
            if request_bookmark and request_bookmark.get("ProgressPercent") is not None:
                accepted_progress = bookmark_outcome.percentage
                push_reading_state_to_hardcover(
                    current_user, book, accepted_progress,
                )
                share_kobo_progress_with_koreader(
                    current_user.id,
                    book.id,
                    accepted_progress,
                )

        ub.session.merge(kobo_reading_state)
        # #1318 again, on the reading-state path: ``session_commit()`` returns
        # False when it caught an OperationalError/InvalidRequestError and
        # rolled back, and its docstring is explicit that callers whose answer
        # depends on the write landing MUST check it. Answering "Success" for a
        # rolled-back write makes the device clear its queue and keeps the
        # server on the older position, with nothing anywhere reporting it.
        if not ub.session_commit():
            return "", 500
        return jsonify({
            "RequestResult": "Success",
            "UpdateResults": [update_results_response],
        })


def share_kobo_progress_with_koreader(user_id, book_id, percentage):
    """Publish a Kobo's reading percentage onto the carrier KOReader pulls from.

    #1425 gap 1. KOReader fetches ``KOSyncProgress`` over kosync; a Kobo sync
    wrote ``KoboBookmark`` and never that table, so a KOReader device had
    nothing to fetch and carried on from where *it* last was. Same shape as
    #1366, where the web reader was the missing producer.

    What transfers is the percentage, not the position. A Kobo reports a
    ``KoboSpan`` addressing the kepub *that device holds*, which KOReader's
    engine cannot resolve, so the row is stored percentage-only and served as
    ``position_kind: "percentage"``. Clients that have not advertised support
    for that are served nothing, exactly as before (see
    ``record_percentage_only_progress``).

    Best-effort, and deliberately so: a Kobo's own sync is the required write
    here, and it must not fail because the KOReader carrier could not be
    written. On failure the device keeps its state and the next PUT retries.

    :param user_id: Owner of the reading position.
    :param book_id: Calibre book id — the key ``update_progress`` converges on.
    :param percentage: Reading progress, 0-100, as the device reported it.
    :return: None
    """
    # Imported lazily: ``kosync`` imports this module for
    # ``push_reading_state_to_hardcover``, so a module-level import is a cycle.
    from .progress_syncing.protocols.kosync import record_percentage_only_progress

    try:
        # A SAVEPOINT only contains what is flushed after it, so the Kobo's own
        # bookmark, statistics and status writes have to be settled first or a
        # rollback here would take them with it (#1318, same precondition the
        # web reader path documents).
        ub.session_flush()
        with ub.begin_contained_nested(ub.session):
            record_percentage_only_progress(user_id, book_id, percentage, device="Kobo")
    except Exception as e:
        log.warning("Could not share Kobo progress with KOReader for user %s book %s: %s",
                    user_id, book_id, e)


def push_reading_state_to_hardcover(user, book: db.Books, progress_percentage: int):
    """
    Sync reading progress to Hardcover if enabled for the user and book is not blacklisted.

    Most exceptions are caught and logged so that issues with Hardcover do not prevent
    the Kobo from clearing its reading state sync queue.

    :param book: The book for which to sync reading progress.
    :param progress_percentage: Reading progress percentage.
    :return: None
    """

    if not config.hardcover_sync_enabled() or not bool(hardcover):
        return

    # Check if book is blacklisted from reading progress syncing
    book_blacklist = ub.session.query(ub.HardcoverBookBlacklist).filter(
        ub.HardcoverBookBlacklist.book_id == book.id).first()

    if book_blacklist and book_blacklist.blacklist_reading_progress:
        log.debug(f"Skipping reading progress sync for book {book.id} - blacklisted for reading progress")
        return

    try:
        hardcoverClient = hardcover.HardcoverClient(user.hardcover_token)
    except hardcover.MissingHardcoverToken:
        log.info(f"User {user.name} has no Hardcover token, not syncing reading progress to Hardcover")
        return
    except Exception as e:
        log.error(f"Failed to create Hardcover client for user {user.name}: {e}")
        return

    try:
        hardcoverClient.update_reading_progress(book.identifiers, progress_percentage)
    except Exception as e:
        log.error(f"Failed to update reading progress for book {book.id} in Hardcover: {e}",
                  exc_info=True)


def get_read_status_for_kobo(ub_book_read):
    enum_to_string_map = {
        None: "ReadyToRead",
        ub.ReadBook.STATUS_UNREAD: "ReadyToRead",
        ub.ReadBook.STATUS_FINISHED: "Finished",
        ub.ReadBook.STATUS_IN_PROGRESS: "Reading",
    }
    return enum_to_string_map[ub_book_read.read_status]


def reconcile_custom_read_column_for_kobo(book_ids, reading_state_cursor):
    """Mirror changed Calibre read markers into timestamped Kobo state rows.

    The Calibre column is boolean while ReadBook is tri-state.  A true marker
    always means FINISHED; false is intentionally ignored because it cannot
    distinguish UNREAD from a legitimate IN_PROGRESS value reported by a
    reader.  Work is limited to the already-selected entitlement candidates,
    so an incremental sync never scans the full library and no token is
    invalidated.
    """
    if not getattr(config, "config_read_column", 0) or not book_ids:
        return 0

    user_id = int(current_user.id)
    reconciled = 0
    reconciled_states = []
    chunk_size = 500
    for start in range(0, len(book_ids), chunk_size):
        chunk = book_ids[start:start + chunk_size]
        books = calibre_db.session.query(db.Books).filter(
            db.Books.id.in_(chunk),
        ).all()
        read_by_book = {
            row.book_id: row
            for row in ub.session.query(ub.ReadBook).filter(
                ub.ReadBook.user_id == user_id,
                ub.ReadBook.book_id.in_(chunk),
            ).all()
        }

        for book in books:
            try:
                finished = helper.custom_read_column_value(book)
            except (KeyError, AttributeError, IndexError):
                log.error(
                    "Kobo read-status: custom column No.%s does not exist "
                    "in calibre database",
                    config.config_read_column,
                )
                return reconciled

            # Sync-originated completion is deliberately sticky throughout
            # this subsystem: false cannot distinguish UNREAD from the richer
            # IN_PROGRESS device state, and must not erase it.
            if not finished:
                continue

            book_read = read_by_book.get(book.id)
            if book_read is None:
                book_read = ub.ReadBook(
                    user_id=user_id,
                    book_id=book.id,
                    read_status=ub.ReadBook.STATUS_FINISHED,
                )
                ub.session.add(book_read)
                read_by_book[book.id] = book_read
                status_changed = True
            else:
                status_changed = (
                    book_read.read_status != ub.ReadBook.STATUS_FINISHED
                )
                if status_changed:
                    book_read.read_status = ub.ReadBook.STATUS_FINISHED

            needs_state = status_changed or (
                book_read.kobo_reading_state is None
            )
            if not needs_state:
                continue
            if book_read.kobo_reading_state is None:
                state = ub.KoboReadingState(
                    user_id=user_id, book_id=book.id,
                )
                state.current_bookmark = ub.KoboBookmark()
                state.statistics = ub.KoboStatistics()
                book_read.kobo_reading_state = state
            reconciled_states.append(book_read.kobo_reading_state)
            reconciled += 1

    if reconciled:
        # Populate onupdate/default clocks before the ordered frontier query.
        ub.session.flush()
        # SQLite reloads DateTime columns as UTC-naive values, which is also
        # the sync-token cursor basis.  Newly flushed ORM objects retain the
        # aware Python defaults until expiration, so normalize this request's
        # instances before the frontier's max comparisons. Give bulk-created
        # rows distinct microseconds too: the token is timestamp-only, so
        # >100 equal clocks would otherwise be skipped after page one.
        clock = max(
            books_cursor_datetime(reading_state_cursor),
            datetime.now(timezone.utc).replace(tzinfo=None),
        )
        for offset, state in enumerate(reconciled_states, start=1):
            state_clock = clock + timedelta(microseconds=offset)
            state.last_modified = state_clock
            state.priority_timestamp = state_clock
        ub.session.flush()
        log.debug(
            "Kobo Sync: reconciled %d configured read-column states",
            reconciled,
        )
    return reconciled


def get_ub_read_status(kobo_read_status):
    string_to_enum_map = {
        None: None,
        "ReadyToRead": ub.ReadBook.STATUS_UNREAD,
        "Finished": ub.ReadBook.STATUS_FINISHED,
        "Reading": ub.ReadBook.STATUS_IN_PROGRESS,
    }
    return string_to_enum_map[kobo_read_status]


def parse_kobo_timestamp(value):
    """Parse a Kobo ``LastModified`` into an aware UTC datetime, or ``None``.

    Kobo sends ISO-8601 UTC, but **not** always to whole seconds: a Libra Colour
    on 5.10.226356 sends ``2026-08-17T17:56:53.254Z`` (#1706). The previous
    ``strptime(value, "%Y-%m-%dT%H:%M:%SZ")`` raised ValueError on the
    fractional part, which the caller's ``except`` turned into a 400 -- so one
    unexpected millisecond field failed the whole reading-state sync, and the
    error text blamed a missing ``ReadingStates`` key.

    Returns ``None`` rather than raising: this timestamp exists only to mirror
    the device's own value back so it does not raise a "newer progress" popup.
    Losing it costs a popup; failing the request costs the sync.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # fromisoformat only learned to accept a trailing "Z" in 3.11.
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _apply_kobo_last_modified(target, observed_clock):
    """Apply a valid device clock, or retain the row's accepted clock.

    The Kobo state columns have SQLAlchemy ``onupdate=now`` defaults. Merely
    declining to assign ``last_modified`` on a rejected clock is insufficient:
    changing another field would still replace it with server time. Marking the
    loaded value as explicitly supplied keeps that real stored observation in
    the UPDATE instead. Test doubles have no SQLAlchemy state and need no such
    handling.
    """
    if observed_clock is not None:
        target.last_modified = observed_clock
        return
    if hasattr(target, "_sa_instance_state"):
        from sqlalchemy.orm.attributes import flag_modified
        # Load an expired value before flagging it, then make SQLAlchemy include
        # that value instead of firing the column's ``onupdate`` default.
        target.last_modified
        flag_modified(target, "last_modified")


def get_or_create_reading_state(book_id):
    """Return the canonical KoboReadingState row for (current_user, book_id),
    creating ReadBook + KoboReadingState (+ child rows) if missing.

    Concurrency-safe: two simultaneous PUTs to /v1/library/<uuid>/state from
    the same device — which legitimately happen on wake-from-sleep — would
    otherwise race past the read-then-write check and both insert. The
    audit-2026-05-11 fix:

    1. Bottom-row creation uses INSERT ... ON CONFLICT(user_id, book_id) DO
       NOTHING via the sqlite dialect insert, so duplicate inserts no-op
       at the SQL layer.
    2. The (user_id, book_id) UNIQUE index added in
       ``migrate_kobo_unique_constraints`` backs the ON CONFLICT clause
       and guarantees post-insert SELECT returns exactly one row.
    3. We commit each create separately so a concurrent winner's insert
       is visible to our subsequent SELECT — the ON CONFLICT path makes
       this safe (no spurious IntegrityError to recover from).
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    user_id = int(current_user.id)

    # 1. ReadBook (book_read_link). Atomic insert-if-missing.
    rb_stmt = (
        sqlite_insert(ub.ReadBook)
        .values(user_id=user_id, book_id=book_id,
                read_status=ub.ReadBook.STATUS_UNREAD,
                times_started_reading=0)
        .on_conflict_do_nothing(index_elements=["user_id", "book_id"])
    )
    ub.session.execute(rb_stmt)
    ub.session.commit()
    book_read = (
        ub.session.query(ub.ReadBook)
        .filter(ub.ReadBook.user_id == user_id,
                ub.ReadBook.book_id == book_id)
        .one()
    )

    # 2. KoboReadingState. Same pattern — but we also need the matching
    #    KoboBookmark + KoboStatistics child rows. We create the parent
    #    first, then attach children via the ORM if they're missing.
    if not book_read.kobo_reading_state:
        krs_stmt = (
            sqlite_insert(ub.KoboReadingState)
            .values(user_id=user_id, book_id=book_id)
            .on_conflict_do_nothing(index_elements=["user_id", "book_id"])
        )
        ub.session.execute(krs_stmt)
        ub.session.commit()
        # Re-fetch via the relationship so SQLAlchemy's identity map picks
        # up the row just inserted (possibly by us, possibly by a racing
        # request). ``book_read.kobo_reading_state`` is a primaryjoin on
        # (user_id, book_id), so the row will resolve automatically.
        ub.session.refresh(book_read)

    kobo_reading_state = book_read.kobo_reading_state

    # 3. Child rows. These have an FK to KoboReadingState.id, so they
    #    can be created via ORM safely (the parent row is now stable).
    if kobo_reading_state.current_bookmark is None:
        kobo_reading_state.current_bookmark = ub.KoboBookmark()
    if kobo_reading_state.statistics is None:
        kobo_reading_state.statistics = ub.KoboStatistics()
    ub.session.commit()

    return kobo_reading_state


def get_kobo_reading_state_response(book, kobo_reading_state):
    return {
        "EntitlementId": book.uuid,
        "Created": convert_to_kobo_timestamp_string(book.timestamp),
        "LastModified": convert_to_kobo_timestamp_string(kobo_reading_state.last_modified),
        # AFAICT PriorityTimestamp is always equal to LastModified.
        "PriorityTimestamp": convert_to_kobo_timestamp_string(kobo_reading_state.priority_timestamp),
        "StatusInfo": get_status_info_response(kobo_reading_state.book_read_link),
        "Statistics": get_statistics_response(kobo_reading_state.statistics),
        "CurrentBookmark": get_current_bookmark_response(kobo_reading_state.current_bookmark),
    }


def get_status_info_response(book_read):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(book_read.last_modified),
        "Status": get_read_status_for_kobo(book_read),
        "TimesStartedReading": book_read.times_started_reading,
    }
    if book_read.last_time_started_reading:
        resp["LastTimeStartedReading"] = convert_to_kobo_timestamp_string(book_read.last_time_started_reading)
    return resp


def get_statistics_response(statistics):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(statistics.last_modified),
    }
    if statistics.spent_reading_minutes is not None:
        resp["SpentReadingMinutes"] = statistics.spent_reading_minutes
    if statistics.remaining_time_minutes is not None:
        resp["RemainingTimeMinutes"] = statistics.remaining_time_minutes
    return resp


def _clean_progress(value):
    """Return progress as int if it's a whole number, preserving Kobo device expectations."""
    if value is not None and value == int(value):
        return int(value)
    return value


def get_current_bookmark_response(current_bookmark):
    resp = {
        "LastModified": convert_to_kobo_timestamp_string(current_bookmark.last_modified),
    }
    if current_bookmark.progress_percent is not None:
        resp["ProgressPercent"] = _clean_progress(current_bookmark.progress_percent)
    if current_bookmark.content_source_progress_percent is not None:
        resp["ContentSourceProgressPercent"] = _clean_progress(current_bookmark.content_source_progress_percent)
    if current_bookmark.location_value:
        resp["Location"] = {
            "Value": current_bookmark.location_value,
            "Type": current_bookmark.location_type,
            "Source": current_bookmark.location_source,
        }
    return resp


def _serve_padded_cover_if_enabled(
    book_uuid, resolution, *, source=None, cache_identity=None, private=False,
):
    """Return a Response with the aspect-ratio-padded cover, or None when
    padding is disabled / not applicable / produced an error. Callers fall
    back to the normal helper.get_book_cover_with_uuid path on None.

    Padded variants live under the same thumbnails cache dir but with a
    `kobopad-...` filename prefix that encodes book uuid + resolution +
    source mtime + settings hash.
    """
    settings = _current_padding_settings()
    if not settings.enabled or not cover_preview.use_IM:
        return None

    source = source or helper.get_kobo_cover_source_path(book_uuid, resolution)
    if not source:
        return None
    src_dir, src_filename, src_full = source

    try:
        # Nanoseconds avoid two users sharing a stale padded cache entry when
        # their personal files are published in the same second.
        src_mtime = os.stat(src_full).st_mtime_ns
    except OSError:
        return None

    cache = FileSystem()
    cache_dir = cache.get_cache_dir(CACHE_TYPE_THUMBNAILS)
    cache_filename = cover_preview.cache_filename_for(
        cache_identity or book_uuid, resolution, src_mtime, settings,
    )

    target = cover_preview.pad_path_to_cache(
        src_full, cache_dir, cache_filename, settings,
    )
    if not target:
        log.debug("Kobo Sync: padding pipeline produced no file for %s; "
                  "falling back to unpadded cover", book_uuid)
        return None

    log.debug("Kobo Sync: serving padded cover %s", cache_filename)
    response = send_from_directory(cache_dir, cache_filename)
    if private:
        response.headers["Cache-Control"] = "private, no-cache"
        response.vary.add("Cookie")
    return response


@kobo.route("/<book_uuid>/<width>/<height>/<isGreyscale>/image.jpg", defaults={'Quality': ""})
@kobo.route("/<book_uuid>/<width>/<height>/<Quality>/<isGreyscale>/image.jpg")
@requires_kobo_auth
def HandleCoverImageRequest(book_uuid, width, height, Quality, isGreyscale):
    book_uuid = _normalize_cover_uuid(book_uuid)
    user_library.mark_response_user_specific()
    try:
        if int(height) > 1000:
            resolution = COVER_THUMBNAIL_LARGE
        elif int(height) > 500:
            resolution = COVER_THUMBNAIL_MEDIUM
        else:
            resolution = COVER_THUMBNAIL_SMALL
    except ValueError:
        log.error("Requested height %s of book %s is invalid" % (height, book_uuid))
        resolution = COVER_THUMBNAIL_SMALL

    book = calibre_db.get_book_by_uuid_for_kobo(book_uuid, enforce_policy=True)
    override = None
    if book is not None:
        override = user_cover.override_for_user(
            getattr(current_user, "id", None), book.id,
        )
    if override is not None:
        personal_path = user_cover.path_for_row(override)
        personal_source = (
            os.path.dirname(personal_path),
            os.path.basename(personal_path),
            personal_path,
        )
        padded_response = _serve_padded_cover_if_enabled(
            book_uuid,
            resolution,
            source=personal_source,
            # The padding cache truncates filesystem mtimes to seconds. Fold
            # the immutable preference version into its private namespace so
            # two rapid replacements cannot reuse the first rendered image.
            cache_identity="{}-user-{}-{}".format(
                book_uuid,
                override.user_id,
                user_cover.version_token(override),
            ),
            private=True,
        )
        if padded_response is not None:
            return padded_response
        return user_cover.send_override(override)

    padded_response = _serve_padded_cover_if_enabled(book_uuid, resolution)
    if padded_response is not None:
        return padded_response

    book_cover = helper.get_book_cover_with_uuid(book_uuid, resolution=resolution)
    if book_cover:
        log.debug("Serving local cover image of book %s" % book_uuid)
        return book_cover

    if not config.config_kobo_proxy:
        log.debug("Returning 404 for cover image of unknown book %s" % book_uuid)
        # additional proxy request make no sense, -> direct return
        return abort(404)

    log.debug("Redirecting request for cover image of unknown book %s to Kobo" % book_uuid)
    return redirect(KOBO_IMAGEHOST_URL +
                    "/{book_uuid}/{width}/{height}/false/image.jpg".format(book_uuid=book_uuid,
                                                                           width=width,
                                                                           height=height), 307)


@kobo.route("")
def TopLevelEndpoint():
    return make_response(jsonify({}))


@csrf.exempt
@kobo.route("/v1/library/<book_uuid>", methods=["DELETE"])
@requires_kobo_auth
def HandleBookDeletionRequest(book_uuid):
    log.info("Kobo book delete request received for book %s", book_uuid)
    # Destructive, user-initiated: book DELETE. Never block on policy
    # filters — user must be able to remove a book from their Kobo
    # regardless of current hidden / denied / language state.
    book = calibre_db.get_book_by_uuid_for_kobo(book_uuid, enforce_policy=False)
    if not book:
        log.info("Book %s not found in database", book_uuid)
        return redirect_or_proxy_request()

    book_id = book.id
    # If the user has shelf sync enabled, do nothing.
    # The book will be removed from the device on the next sync.
    if current_user.kobo_only_shelves_sync:
        pass
    # Otherwise, archive the book if the user has permission to see archived books.
    elif current_user.check_visibility(32768):
        kobo_sync_status.change_archived_books(book_id, True, commit=False)

    # The archive marker and delivery-ledger removal describe one device
    # action. Stage both and let this answer-bearing route own the single
    # checked commit, so neither a partial write nor a rollback earns a 204.
    kobo_sync_status.remove_synced_book(book_id, commit=False)
    if ub.session_commit() is False:
        return abort(503)
    return "", 204


# TODO: Implement the following routes
@csrf.exempt
@kobo.route("/v1/library/<dummy>", methods=["DELETE", "GET", "POST"])
@kobo.route("/v1/library/<dummy>/preview", methods=["POST"])
def HandleUnimplementedRequest(dummy=None):
    log.debug("Unimplemented Library Request received: %s (%s)",
              request.base_url,
              'forwarded to Kobo Store' if config.config_kobo_proxy else 'returning empty response')
    return redirect_or_proxy_request()


# TODO: Implement the following routes
@csrf.exempt
@kobo.route("/v1/user/loyalty/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/user/profile", methods=["GET", "POST"])
@kobo.route("/v1/user/wishlist", methods=["GET", "POST"])
@kobo.route("/v1/user/recommendations", methods=["GET", "POST"])
@kobo.route("/v1/analytics/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/assets", methods=["GET"])
def HandleUserRequest(dummy=None):
    log.debug("Unimplemented User Request received: %s (%s)",
              request.base_url,
              'forwarded to Kobo Store' if config.config_kobo_proxy else 'returning empty response')
    return redirect_or_proxy_request()


@csrf.exempt
@kobo.route("/v1/user/loyalty/benefits", methods=["GET"])
def handle_benefits():
    if config.config_kobo_proxy:
        return redirect_or_proxy_request()
    else:
        return make_response(jsonify({"Benefits": {}}))


@csrf.exempt
@kobo.route("/v1/analytics/gettests", methods=["GET", "POST"])
def handle_getests():
    if config.config_kobo_proxy:
        return redirect_or_proxy_request()
    else:
        testkey = request.headers.get("X-Kobo-userkey", "")
        return make_response(jsonify({"Result": "Success", "TestKey": testkey, "Tests": {}}))


@csrf.exempt
@kobo.route("/v1/products/<dummy>/prices", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/recommendations", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/nextread", methods=["GET", "POST"])
@kobo.route("/v1/products/<dummy>/reviews", methods=["GET", "POST"])
@kobo.route("/v1/products/featured/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/featured/", methods=["GET", "POST"])
@kobo.route("/v1/products/books/external/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/series/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/products/books/<dummy>/", methods=["GET", "POST"])
@kobo.route("/v1/products/dailydeal", methods=["GET", "POST"])
@kobo.route("/v1/products/deals", methods=["GET", "POST"])
@kobo.route("/v1/products/featuredforkoboplus/")
@kobo.route("/v1/products", methods=["GET", "POST"])
@kobo.route("/v1/affiliate", methods=["GET", "POST"])
@kobo.route("/v1/deals", methods=["GET", "POST"])
@kobo.route("/v1/categories/<dummy>", methods=["GET", "POST"])
@kobo.route("/v1/categories/<dummy>/featured", methods=["GET", "POST"])
@kobo.route("/v1/categories/<dummy>/products")
def HandleProductsRequest(dummy=None):
    log.debug("Unimplemented Products Request received: %s (%s)",
              request.base_url,
              'forwarded to Kobo Store' if config.config_kobo_proxy else 'returning empty response')
    return redirect_or_proxy_request()


def make_calibre_web_auth_response():
    # As described in kobo_auth.py, CalibreWeb doesn't make use practical use of this auth/device API call for
    # authentation (nor for authorization). We return a dummy response just to keep the device happy.
    content = request.get_json()
    AccessToken = base64.b64encode(os.urandom(24)).decode('utf-8')
    RefreshToken = base64.b64encode(os.urandom(24)).decode('utf-8')
    return make_response(
        jsonify(
            {
                "AccessToken": AccessToken,
                "RefreshToken": RefreshToken,
                "TokenType": "Bearer",
                "TrackingId": str(uuid.uuid4()),
                "UserKey": content.get('UserKey', ""),
            }
        )
    )


def make_calibre_web_oauth_response():
    # Provide a dummy OAuth token response for unregistered devices or
    # when Kobo Store proxying is disabled.
    content = request.get_json(silent=True) or {}
    access_token = base64.b64encode(os.urandom(24)).decode('utf-8')
    refresh_token = base64.b64encode(os.urandom(24)).decode('utf-8')
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": content.get("scope", ""),
        "user_id": str(current_user.id) if current_user and not current_user.is_anonymous else content.get("user_id", ""),
        # Include legacy field names used by some Kobo requests
        "AccessToken": access_token,
        "RefreshToken": refresh_token,
        "TokenType": "Bearer",
    }
    return make_response(jsonify(payload))


@csrf.exempt
@kobo.route("/v1/auth/device", methods=["POST"])
@requires_kobo_auth
def HandleAuthRequest():
    log.debug('Kobo Auth request')
    if config.config_kobo_proxy:
        try:
            return redirect_or_proxy_request()
        except Exception:
            log.error("Failed to receive or parse response from Kobo's auth endpoint. Falling back to un-proxied mode.")
    return make_calibre_web_auth_response()



@csrf.exempt
@kobo.route('/oauth/.well-known/openid-configuration', methods=['GET', 'POST'])
@requires_kobo_auth
def HandleOidcDiscovery():
    base_url = url_for("kobo.HandleOauthRequest",
                       auth_token=get_auth_token(),
                       _external=True).rsplit("/oauth", 1)[0]
    payload = {
        'issuer': base_url,
        'authorization_endpoint': base_url + '/oauth/authorize',
        'token_endpoint': base_url + '/oauth/token',
        'userinfo_endpoint': base_url + '/oauth/userinfo',
        'response_types_supported': ['code'],
        'subject_types_supported': ['public'],
        'id_token_signing_alg_values_supported': ['RS256'],
    }
    return make_response(jsonify(payload))

@csrf.exempt
@kobo.route("/oauth/token", methods=["GET", "POST"])
@kobo.route("/oauth/authorize", methods=["GET", "POST"])
@kobo.route("/oauth/refresh", methods=["GET", "POST"])
@kobo.route("/oauth/<path:subpath>", methods=["GET", "POST"])
@requires_kobo_auth
def HandleOauthRequest(subpath=None):
    log.debug("Kobo OAuth request: %s", request.path)
    return make_calibre_web_oauth_response()


@kobo.route("/v1/initialization")
@requires_kobo_auth
def HandleInitRequest():
    log.info('Init')
    user_library.mark_response_user_specific()

    kobo_resources = None
    if config.config_kobo_proxy:
        try:
            store_response = make_request_to_kobo_store()
            store_response_json = store_response.json()

            # It is relatively important to handle ExpiredToken errors here to trigger re-authentication.
            # Kobo uses this endpoint to assure that the auth token is valid and recover from errors elsewhere.
            # If this does not work properly users may get stuck with an expired token and no way to re-authenticate.
            # The handling here has been kept minimal because some users may not wish to auth with Onestore,
            # but Kobo requires auth for more endpoints than used to be the case, and it perhaps does not make sense
            # for users to enable proxy requests to the Kobo Store without a working Kobo account.

            if rs := store_response_json.get("ResponseStatus", {}):
                if ec := rs.get("ErrorCode", ""):
                    msg = rs.get("Message", "(No message provided)")
                    if ec == "ExpiredToken":
                        log.info(f"Kobo Store session expired: {msg}. Triggering re-authentication.")
                        return make_proxy_response(store_response)
                    log.warning(f"Kobo: Kobo Store initialization returned error code {ec}: {msg}")
            if "Resources" in store_response_json:
                kobo_resources = store_response_json["Resources"]
            else:
                log.error("Kobo: Kobo Store initialization response missing 'Resources' field.")
        except Exception as e:
            log.error_or_exception(f"Failed to receive or parse response from Kobo's init endpoint: {e}")
    if not kobo_resources:
        log.debug("Using fallback Kobo resource definitions")
        kobo_resources = NATIVE_KOBO_RESOURCES()

    # A personal cover is image state, not entitlement state. Version the
    # authenticated image URL template so Kobo refreshes its image cache after
    # a set/clear, without changing BookMetadata.CoverImageId or emitting a
    # New/ChangedEntitlement for a book the device already holds.
    cover_resource_version = user_cover.kobo_resource_version_for_user(
        getattr(current_user, "id", None),
    )
    cover_resource_query = (
        {"pc": cover_resource_version} if cover_resource_version else {}
    )

    if not current_app.wsgi_app.is_proxied:
        log.debug('Kobo: Received unproxied request, changed request port to external server port')
        if ':' in request.host and not request.host.endswith(']'):
            host = "".join(request.host.split(':')[:-1])
        else:
            host = request.host
        calibre_web_url = "{url_scheme}://{url_base}:{url_port}".format(
            url_scheme=request.scheme,
            url_base=host,
            url_port=config.config_external_port
        )
        log.debug('Kobo: Received unproxied request, changed request url to %s', calibre_web_url)
        kobo_resources["image_host"] = calibre_web_url
        kobo_resources["image_url_quality_template"] = unquote(calibre_web_url +
                                                               url_for("kobo.HandleCoverImageRequest",
                                                                       auth_token=kobo_auth.get_auth_token(),
                                                                       book_uuid="{ImageId}",
                                                                       width="{Width}",
                                                                       height="{Height}",
                                                                       Quality='{Quality}',
                                                                       isGreyscale='isGreyscale',
                                                                       **cover_resource_query))
        kobo_resources["image_url_template"] = unquote(calibre_web_url +
                                                       url_for("kobo.HandleCoverImageRequest",
                                                               auth_token=kobo_auth.get_auth_token(),
                                                               book_uuid="{ImageId}",
                                                               width="{Width}",
                                                               height="{Height}",
                                                               isGreyscale='false',
                                                               **cover_resource_query))
        # Route reading-services (annotations + reading state) through CWNG
        # whenever Kobo sync is on — not just when Hardcover is enabled.
        # The annotation handler captures a local copy then ALWAYS proxies
        # the request on to Kobo's real reading services, so device-side
        # data is never withheld. Previously this was gated on Hardcover,
        # so with Hardcover off the device sent annotations straight to
        # Kobo and CWNG never saw them — the live-Kobo capture from #305
        # sub-project (2) was a no-op on the wire. (Found via real-device
        # test 2026-05-24.)
        if config.config_kobo_sync or (config.config_hardcover_annotations_sync and bool(hardcover)):
            kobo_resources["reading_services_host"] = calibre_web_url
        kobo_resources["library_sync"] = calibre_web_url + url_for("kobo.HandleSyncRequest",
                                                                    auth_token=kobo_auth.get_auth_token())
    else:
        kobo_resources["image_host"] = url_for("web.index", _external=True).strip("/")
        kobo_resources["image_url_quality_template"] = unquote(url_for("kobo.HandleCoverImageRequest",
                                                                       auth_token=kobo_auth.get_auth_token(),
                                                                       book_uuid="{ImageId}",
                                                                       width="{Width}",
                                                                       height="{Height}",
                                                                       Quality='{Quality}',
                                                                       isGreyscale='isGreyscale',
                                                                       _external=True,
                                                                       **cover_resource_query))
        kobo_resources["image_url_template"] = unquote(url_for("kobo.HandleCoverImageRequest",
                                                               auth_token=kobo_auth.get_auth_token(),
                                                               book_uuid="{ImageId}",
                                                               width="{Width}",
                                                               height="{Height}",
                                                               isGreyscale='false',
                                                               _external=True,
                                                               **cover_resource_query))
        # See note above — redirect reading-services to CWNG whenever Kobo
        # sync is on so live annotation capture works without Hardcover.
        if config.config_kobo_sync or (config.config_hardcover_annotations_sync and bool(hardcover)):
            kobo_resources["reading_services_host"] = url_for("web.index", _external=True).strip("/")
        kobo_resources["library_sync"] = url_for("kobo.HandleSyncRequest",
                                                  auth_token=kobo_auth.get_auth_token(),
                                                  _external=True)

    # When not proxying Kobo Store requests, point oauth_host to CWA and
    # serve dummy OAuth responses for unregistered devices.
    if not config.config_kobo_proxy:
        oauth_token_url = url_for("kobo.HandleOauthRequest",
                                  auth_token=kobo_auth.get_auth_token(),
                                  _external=True)
        kobo_resources["oauth_host"] = oauth_token_url.rsplit("/oauth", 1)[0] + "/oauth"

    response = make_response(jsonify({"Resources": kobo_resources}))
    response.headers["x-kobo-apitoken"] = "e30="

    return response


@kobo.route("/download/<book_id>/<book_format>")
@requires_kobo_auth
@download_required
def download_book(book_id, book_format):
    return get_download_link(book_id, book_format, "kobo")


@kobo.route("/redirect_download/<book_id>/<book_format>")
@requires_kobo_auth
@download_required
def redirect_download_book(book_id, book_format):
    return redirect(url_for(
        "kobo.download_book",
        auth_token=kobo_auth.get_auth_token(),
        book_id=book_id,
        book_format=book_format.lower(),
        _external=True,
    ))


def NATIVE_KOBO_RESOURCES():
    return {
        "account_page": "https://www.kobo.com/account/settings",
        "account_page_rakuten": "https://my.rakuten.co.jp/",
        "add_device": "https://storeapi.kobo.com/v1/user/add-device",
        "add_entitlement": "https://storeapi.kobo.com/v1/library/{RevisionIds}",
        "affiliaterequest": "https://storeapi.kobo.com/v1/affiliate",
        "assets": "https://storeapi.kobo.com/v1/assets",
        "audiobook": "https://storeapi.kobo.com/v1/products/audiobooks/{ProductId}",
        "audiobook_detail_page": "https://www.kobo.com/{region}/{language}/audiobook/{slug}",
        "audiobook_get_credits": "https://www.kobo.com/{region}/{language}/audiobooks/plans",
        "audiobook_landing_page": "https://www.kobo.com/{region}/{language}/audiobooks",
        "audiobook_preview": "https://storeapi.kobo.com/v1/products/audiobooks/{Id}/preview",
        "audiobook_purchase_withcredit": "https://storeapi.kobo.com/v1/store/audiobook/{Id}",
        "audiobook_subscription_management": "https://www.kobo.com/{region}/{language}/account/subscriptions",
        "audiobook_subscription_orange_deal_inclusion_url": "https://authorize.kobo.com/inclusion",
        "audiobook_subscription_purchase": "https://www.kobo.com/{region}/{language}/checkoutoption/21C6D938-934B-4A91-B979-E14D70B2F280",
        "audiobook_subscription_tiers": "https://www.kobo.com/{region}/{language}/checkoutoption/21C6D938-934B-4A91-B979-E14D70B2F280",
        "authorproduct_recommendations": "https://storeapi.kobo.com/v1/products/books/authors/recommendations",
        "autocomplete": "https://storeapi.kobo.com/v1/products/autocomplete",
        "blackstone_header": {
            "key": "x-amz-request-payer",
            "value": "requester"
        },
        "book": "https://storeapi.kobo.com/v1/products/books/{ProductId}",
        "book_detail_page": "https://www.kobo.com/{region}/{language}/ebook/{slug}",
        "book_detail_page_rakuten": "https://books.rakuten.co.jp/rk/{crossrevisionid}",
        "book_landing_page": "https://www.kobo.com/ebooks",
        "book_subscription": "https://storeapi.kobo.com/v1/products/books/subscriptions",
        "browse_history": "https://storeapi.kobo.com/v1/user/browsehistory",
        "categories": "https://storeapi.kobo.com/v1/categories",
        "categories_page": "https://www.kobo.com/ebooks/categories",
        "category": "https://storeapi.kobo.com/v1/categories/{CategoryId}",
        "category_featured_lists": "https://storeapi.kobo.com/v1/categories/{CategoryId}/featured",
        "category_products": "https://storeapi.kobo.com/v1/categories/{CategoryId}/products",
        "checkout_borrowed_book": "https://storeapi.kobo.com/v1/library/borrow",
        "client_authd_referral": "https://authorize.kobo.com/api/AuthenticatedReferral/client/v1/getLink",
        "configuration_data": "https://storeapi.kobo.com/v1/configuration",
        "content_access_book": "https://storeapi.kobo.com/v1/products/books/{ProductId}/access",
        "customer_care_live_chat": "https://v2.zopim.com/widget/livechat.html?key=Y6gwUmnu4OATxN3Tli4Av9bYN319BTdO",
        "daily_deal": "https://storeapi.kobo.com/v1/products/dailydeal",
        "deals": "https://storeapi.kobo.com/v1/deals",
        "delete_entitlement": "https://storeapi.kobo.com/v1/library/{Ids}",
        "delete_tag": "https://storeapi.kobo.com/v1/library/tags/{TagId}",
        "delete_tag_items": "https://storeapi.kobo.com/v1/library/tags/{TagId}/items/delete",
        "device_auth": "https://storeapi.kobo.com/v1/auth/device",
        "device_refresh": "https://storeapi.kobo.com/v1/auth/refresh",
        "dictionary_host": "https://ereaderfiles.kobo.com",
        "discovery_host": "https://discovery.kobobooks.com",
        "ereaderdevices": "https://storeapi.kobo.com/v2/products/EReaderDeviceFeeds",
        "dropbox_link_account_poll": "https://authorize.kobo.com/{region}/{language}/LinkDropbox",
        "dropbox_link_account_start": "https://authorize.kobo.com/LinkDropbox/start",
        "eula_page": "https://www.kobo.com/termsofuse?style=onestore",
        "exchange_auth": "https://storeapi.kobo.com/v1/auth/exchange",
        "external_book": "https://storeapi.kobo.com/v1/products/books/external/{Ids}",
        "facebook_sso_page": "https://authorize.kobo.com/signin/provider/Facebook/login?returnUrl=http://kobo.com/",
        "featured_list": "https://storeapi.kobo.com/v1/products/featured/{FeaturedListId}",
        "featured_lists": "https://storeapi.kobo.com/v1/products/featured",
        "free_books_page": {
            "EN": "https://www.kobo.com/{region}/{language}/p/free-ebooks",
            "FR": "https://www.kobo.com/{region}/{language}/p/livres-gratuits",
            "IT": "https://www.kobo.com/{region}/{language}/p/libri-gratuiti",
            "NL": "https://www.kobo.com/{region}/{language}/List/bekijk-het-overzicht-van-gratis-ebooks/QpkkVWnUw8sxmgjSlCbJRg",
            "PT": "https://www.kobo.com/{region}/{language}/p/livros-gratis"
        },
        "fte_feedback": "https://storeapi.kobo.com/v1/products/ftefeedback",
        "funnel_metrics": "https://storeapi.kobo.com/v1/funnelmetrics",
        "get_download_keys": "https://storeapi.kobo.com/v1/library/downloadkeys",
        "get_download_link": "https://storeapi.kobo.com/v1/library/downloadlink",
        "get_tests_request": "https://storeapi.kobo.com/v1/analytics/gettests",
        "giftcard_epd_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem-ereader",
        "giftcard_redeem_url": "https://www.kobo.com/{storefront}/{language}/redeem",
        "googledrive_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkcloudstorage/provider/google_drive",
        "gpb_flow_enabled": "False",
        "help_page": "https://www.kobo.com/help",
        "image_host": "https://cdn.kobo.com/book-images/",
        "image_url_quality_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/{Quality}/{IsGreyscale}/image.jpg",
        "image_url_template": "https://cdn.kobo.com/book-images/{ImageId}/{Width}/{Height}/false/image.jpg",
        "instapaper_enabled": "True",
        "instapaper_env_url": "https://www.instapaper.com/api/kobo",
        "instapaper_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkinstapaper",
        "kobo_audiobooks_credit_redemption": "True",
        "kobo_audiobooks_enabled": "True",
        "kobo_audiobooks_orange_deal_enabled": "True",
        "kobo_audiobooks_subscriptions_enabled": "True",
        "kobo_display_price": "True",
        "kobo_dropbox_link_account_enabled": "True",
        "kobo_google_tax": "False",
        "kobo_googledrive_link_account_enabled": "True",
        "kobo_nativeborrow_enabled": "True",
        "kobo_onedrive_link_account_enabled": "False",
        "kobo_onestorelibrary_enabled": "False",
        "kobo_privacyCentre_url": "https://www.kobo.com/privacy",
        "kobo_redeem_enabled": "True",
        "kobo_shelfie_enabled": "False",
        "kobo_subscriptions_enabled": "True",
        "kobo_superpoints_enabled": "True",
        "kobo_wishlist_enabled": "True",
        "library_book": "https://storeapi.kobo.com/v1/user/library/books/{LibraryItemId}",
        "library_items": "https://storeapi.kobo.com/v1/user/library",
        "library_metadata": "https://storeapi.kobo.com/v1/library/{Ids}/metadata",
        "library_prices": "https://storeapi.kobo.com/v1/user/library/previews/prices",
        "library_search": "https://storeapi.kobo.com/v1/library/search",
        "library_sync": "https://storeapi.kobo.com/v1/library/sync",
        "love_dashboard_page": "https://www.kobo.com/{region}/{language}/kobosuperpoints",
        "love_points_redemption_page": "https://www.kobo.com/{region}/{language}/KoboSuperPointsRedemption?productId={ProductId}",
        "magazine_landing_page": "https://www.kobo.com/emagazines",
        "more_sign_in_options": "https://authorize.kobo.com/signin?returnUrl=http://kobo.com/#allProviders",
        "notebooks": "https://storeapi.kobo.com/api/internal/notebooks",
        "notifications_registration_issue": "https://storeapi.kobo.com/v1/notifications/registration",
        "oauth_host": "https://oauth.kobo.com",
        "password_retrieval_page": "https://www.kobo.com/passwordretrieval.html",
        "personalizedrecommendations": "https://storeapi.kobo.com/v2/users/personalizedrecommendations",
        "pocket_link_account_start": "https://authorize.kobo.com/{region}/{language}/linkpocket",
        "post_analytics_event": "https://storeapi.kobo.com/v1/analytics/event",
        "ppx_purchasing_url": "https://purchasing.kobo.com",
        "privacy_page": "https://www.kobo.com/privacypolicy?style=onestore",
        "product_nextread": "https://storeapi.kobo.com/v1/products/{ProductIds}/nextread",
        "product_prices": "https://storeapi.kobo.com/v1/products/{ProductIds}/prices",
        "product_recommendations": "https://storeapi.kobo.com/v1/products/{ProductId}/recommendations",
        "product_reviews": "https://storeapi.kobo.com/v1/products/{ProductIds}/reviews",
        "products": "https://storeapi.kobo.com/v1/products",
        "productsv2": "https://storeapi.kobo.com/v2/products",
        "provider_external_sign_in_page": "https://authorize.kobo.com/ExternalSignIn/{providerName}?returnUrl=http://kobo.com/",
        "purchase_buy": "https://www.kobo.com/checkoutoption/",
        "purchase_buy_templated": "https://www.kobo.com/{region}/{language}/checkoutoption/{ProductId}",
        "quickbuy_checkout": "https://storeapi.kobo.com/v1/store/quickbuy/{PurchaseId}/checkout",
        "quickbuy_create": "https://storeapi.kobo.com/v1/store/quickbuy/purchase",
        "rakuten_token_exchange": "https://storeapi.kobo.com/v1/auth/rakuten_token_exchange",
        "rating": "https://storeapi.kobo.com/v1/products/{ProductId}/rating/{Rating}",
        "reading_services_host": "https://readingservices.kobo.com",
        "reading_state": "https://storeapi.kobo.com/v1/library/{Ids}/state",
        "redeem_interstitial_page": "https://www.kobo.com",
        "registration_page": "https://authorize.kobo.com/signup?returnUrl=http://kobo.com/",
        "related_items": "https://storeapi.kobo.com/v1/products/{Id}/related",
        "remaining_book_series": "https://storeapi.kobo.com/v1/products/books/series/{SeriesId}",
        "rename_tag": "https://storeapi.kobo.com/v1/library/tags/{TagId}",
        "review": "https://storeapi.kobo.com/v1/products/reviews/{ReviewId}",
        "review_sentiment": "https://storeapi.kobo.com/v1/products/reviews/{ReviewId}/sentiment/{Sentiment}",
        "shelfie_recommendations": "https://storeapi.kobo.com/v1/user/recommendations/shelfie",
        "sign_in_page": "https://auth.kobobooks.com/ActivateOnWeb",
        "social_authorization_host": "https://social.kobobooks.com:8443",
        "social_host": "https://social.kobobooks.com",
        "store_home": "www.kobo.com/{region}/{language}",
        "store_host": "www.kobo.com",
        "store_newreleases": "https://www.kobo.com/{region}/{language}/List/new-releases/961XUjtsU0qxkFItWOutGA",
        "store_search": "https://www.kobo.com/{region}/{language}/Search?Query={query}",
        "store_top50": "https://www.kobo.com/{region}/{language}/ebooks/Top",
        "subs_landing_page": "https://www.kobo.com/{region}/{language}/plus",
        "subs_management_page": "https://www.kobo.com/{region}/{language}/account/subscriptions",
        "subs_plans_page": "https://www.kobo.com/{region}/{language}/plus/plans",
        "subs_purchase_buy_templated": "https://www.kobo.com/{region}/{language}/Checkoutoption/{ProductId}/{TierId}",
        "subscription_publisher_price_page": "https://www.kobo.com/{region}/{language}/subscriptionpublisherprice",
        "tag_items": "https://storeapi.kobo.com/v1/library/tags/{TagId}/Items",
        "tags": "https://storeapi.kobo.com/v1/library/tags",
        "taste_profile": "https://storeapi.kobo.com/v1/products/tasteprofile",
        "terms_of_sale_page": "https://authorize.kobo.com/{region}/{language}/terms/termsofsale",
        "update_accessibility_to_preview": "https://storeapi.kobo.com/v1/library/{EntitlementIds}/preview",
        "use_one_store": "True",
        "user_loyalty_benefits": "https://storeapi.kobo.com/v1/user/loyalty/benefits",
        "user_platform": "https://storeapi.kobo.com/v1/user/platform",
        "user_profile": "https://storeapi.kobo.com/v1/user/profile",
        "user_ratings": "https://storeapi.kobo.com/v1/user/ratings",
        "user_recommendations": "https://storeapi.kobo.com/v1/user/recommendations",
        "user_reviews": "https://storeapi.kobo.com/v1/user/reviews",
        "user_wishlist": "https://storeapi.kobo.com/v1/user/wishlist",
        "userguide_host": "https://ereaderfiles.kobo.com",
        "wishlist_page": "https://www.kobo.com/{region}/{language}/account/wishlist"
    }
