# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 0 gate evaluation and privacy-safe observability."""

from __future__ import annotations

import logging
import os
import threading
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


def schema_capable(engine) -> bool:
    """Fail closed when any Stage 0 schema capability is absent."""
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not REQUIRED_TABLES <= tables or "annotation" not in tables:
            return False
        columns = {column["name"] for column in inspector.get_columns("annotation")}
        if not REQUIRED_ANNOTATION_COLUMNS <= columns:
            return False
        if "user" not in tables or "settings" not in tables:
            return False
        if "kobo_two_way_annotation_sync" not in {
            column["name"] for column in inspector.get_columns("user")
        }:
            return False
        if "config_kobo_two_way_annotation_sync" not in {
            column["name"] for column in inspector.get_columns("settings")
        }:
            return False
        for table_name, required in REQUIRED_INDEXES.items():
            actual = {index["name"] for index in inspector.get_indexes(table_name)}
            if not required <= actual:
                return False
        with engine.connect() as connection:
            triggers = {
                row[0] for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='kobo_annotation_book_state'"
                )
            }
        if not REQUIRED_TRIGGERS <= triggers:
            return False
        return True
    except Exception:
        log.exception("Kobo Stage 0 schema capability check failed")
        return False


def emergency_override_disables(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    value = environ.get("CWNG_KOBO_TWO_WAY_ANNOTATIONS")
    return value is not None and value.strip().lower() in {"0", "false", "off", "no"}


def gates_allow(settings, user, book_state, *, schema_ready) -> bool:
    """Evaluate the local Kobo annotation wire-authority gate."""
    if emergency_override_disables():
        return False
    if not schema_ready:
        return False
    if not bool(getattr(settings, "config_kobo_two_way_annotation_sync", False)):
        return False
    if not bool(getattr(user, "kobo_two_way_annotation_sync", False)):
        return False
    return getattr(book_state, "authority_status", None) == "authoritative"
