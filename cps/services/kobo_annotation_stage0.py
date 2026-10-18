# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 0 gate evaluation and privacy-safe observability."""

from __future__ import annotations

import logging
import os
import threading
import weakref
from collections import Counter

from sqlalchemy import inspect


log = logging.getLogger(__name__)
_METRICS = Counter()
_METRICS_LOCK = threading.Lock()

REQUIRED_TABLES = {
    "kobo_annotation_materialization",
    "kobo_annotation_book_state",
    "kobo_device_book_annotation_state",
    "kobo_annotation_seed_capture",
    "kobo_annotation_seed_capture_page",
    "kobo_annotation_page_snapshot",
    "kobo_annotation_page_cursor",
    "kobo_opaque_content_present_guard",
}
REQUIRED_ANNOTATION_COLUMNS = {
    "annotation_type", "content_revision", "server_modified_at",
    "last_editor_device_id",
}
REQUIRED_BOOK_STATE_COLUMNS = {"ever_authoritative"}
REQUIRED_SEED_CAPTURE_COLUMNS = {"seed_kind"}
REQUIRED_INDEXES = {
    "kobo_annotation_materialization": {"ix_kam_serveable"},
    "kobo_annotation_book_state": {"ix_kabs_user_content", "ix_kabs_authority"},
    "kobo_device_book_annotation_state": {"ix_kdbas_book_ack"},
    "kobo_annotation_seed_capture": {"ix_kasc_book_time"},
    "kobo_annotation_seed_capture_page": {"ix_kascp_capture"},
    "kobo_annotation_page_snapshot": {"ix_kaps_expiry"},
    "kobo_annotation_page_cursor": {"ix_kapc_snapshot"},
}
REQUIRED_TRIGGERS = {
    "trg_kabs_opaque_present_sticky",
    "trg_kabs_opaque_present_guard_insert",
    "trg_kabs_opaque_present_record_insert",
    "trg_kabs_opaque_present_record_update",
}


def record_event(event, outcome, *, trace_id=None, user_id=None, book_id=None,
                 annotation_count=None, **_discarded_sensitive_fields):
    """Increment a bounded counter and log only structural identifiers.

    Unexpected keyword fields are deliberately ignored so a caller cannot
    accidentally put annotation text, notes, credentials, or payloads in logs.
    """
    event = str(event)[:64]
    outcome = str(outcome)[:64]
    with _METRICS_LOCK:
        _METRICS[(event, outcome)] += 1
    log.info(
        "kobo_annotation_stage0 event=%s outcome=%s trace_id=%s user_id=%s "
        "book_id=%s annotation_count=%s",
        event, outcome, trace_id, user_id, book_id, annotation_count,
    )


def metrics_snapshot():
    with _METRICS_LOCK:
        return dict(_METRICS)


def reset_metrics_for_testing():
    with _METRICS_LOCK:
        _METRICS.clear()


# Per-engine memo of the last capability verdict, keyed weakly so a disposed
# engine can be collected. Value is ``(schema_version, verdict)``.
_SCHEMA_CAPABLE_CACHE = weakref.WeakKeyDictionary()
_SCHEMA_CAPABLE_LOCK = threading.Lock()


def _sqlite_schema_version(engine):
    """Return SQLite's ``schema_version`` counter, or ``None`` if unavailable.

    SQLite bumps this on every DDL change (including from another process or
    worker), so it is a one-query fingerprint of "has the schema changed".
    """
    try:
        with engine.connect() as connection:
            return connection.exec_driver_sql("PRAGMA schema_version").scalar()
    except Exception:
        log.debug("Kobo Stage 0 schema_version unreadable; capability not cached", exc_info=True)
        return None


def invalidate_schema_capable_cache(engine=None) -> None:
    """Forget cached schema verdicts so the next call re-inspects.

    Pass an engine to drop just that one; omit it to drop all. Safe to call at
    any time. The schema_version check already catches schema changes, so this
    is an explicit signal for the Stage 0 migration rather than a requirement.
    """
    with _SCHEMA_CAPABLE_LOCK:
        if engine is None:
            _SCHEMA_CAPABLE_CACHE.clear()
        else:
            _SCHEMA_CAPABLE_CACHE.pop(engine, None)


def schema_capable(engine) -> bool:
    """Fail closed when any Stage 0 schema capability is absent.

    The verdict is memoised per engine and revalidated against SQLite's
    ``schema_version``, so a request normally costs one PRAGMA instead of a
    full introspection. Any schema change (migration, dropped trigger or
    index, another worker) alters the version and forces a fresh inspection;
    both ``True`` and ``False`` verdicts are therefore safe to cache. If the
    version cannot be read, nothing is cached.
    """
    version = _sqlite_schema_version(engine)
    if version is not None:
        with _SCHEMA_CAPABLE_LOCK:
            cached = _SCHEMA_CAPABLE_CACHE.get(engine)
        if cached is not None and cached[0] == version:
            return cached[1]
    verdict, cacheable = _inspect_schema_capable(engine)
    if version is not None and cacheable:
        with _SCHEMA_CAPABLE_LOCK:
            _SCHEMA_CAPABLE_CACHE[engine] = (version, verdict)
    return verdict


def _inspect_schema_capable(engine):
    """Run the full introspection. Returns ``(verdict, cacheable)``."""
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not REQUIRED_TABLES <= tables or "annotation" not in tables:
            return False, True
        columns = {column["name"] for column in inspector.get_columns("annotation")}
        if not REQUIRED_ANNOTATION_COLUMNS <= columns:
            return False, True
        book_state_columns = {
            column["name"]
            for column in inspector.get_columns("kobo_annotation_book_state")
        }
        if not REQUIRED_BOOK_STATE_COLUMNS <= book_state_columns:
            return False, True
        seed_capture_columns = {
            column["name"]
            for column in inspector.get_columns("kobo_annotation_seed_capture")
        }
        if not REQUIRED_SEED_CAPTURE_COLUMNS <= seed_capture_columns:
            return False, True
        if "user" not in tables or "settings" not in tables:
            return False, True
        if "kobo_two_way_annotation_sync" not in {
            column["name"] for column in inspector.get_columns("user")
        }:
            return False, True
        if "config_kobo_two_way_annotation_sync" not in {
            column["name"] for column in inspector.get_columns("settings")
        }:
            return False, True
        for table_name, required in REQUIRED_INDEXES.items():
            actual = {index["name"] for index in inspector.get_indexes(table_name)}
            if not required <= actual:
                return False, True
        with engine.connect() as connection:
            triggers = {
                row[0] for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='kobo_annotation_book_state'"
                )
            }
        if not REQUIRED_TRIGGERS <= triggers:
            return False, True
        return True, True
    except Exception:
        log.exception("Kobo Stage 0 schema capability check failed")
        return False, False


def emergency_override_disables(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    value = environ.get("CWNG_KOBO_TWO_WAY_ANNOTATIONS")
    return value is not None and value.strip().lower() in {"0", "false", "off", "no"}


def gate_failure_reason(settings, user, book_state, *, schema_ready):
    """Return a privacy-safe reason code, or ``None`` when gates pass."""
    if emergency_override_disables():
        return "emergency_override"
    if not schema_ready:
        return "schema_incomplete"
    if not bool(getattr(settings, "config_kobo_two_way_annotation_sync", False)):
        return "instance_gate_disabled"
    if not bool(getattr(user, "kobo_two_way_annotation_sync", False)):
        return "user_gate_disabled"
    status = getattr(book_state, "authority_status", None)
    if status != "authoritative":
        if status in {"unseeded", "seeding", "quarantined", "disabled"}:
            return f"authority_status_{status}"
        return "authority_state_missing_or_invalid"
    return None


def gates_allow(settings, user, book_state, *, schema_ready) -> bool:
    """Evaluate the local Kobo annotation wire-authority gate."""
    return gate_failure_reason(
        settings, user, book_state, schema_ready=schema_ready,
    ) is None
