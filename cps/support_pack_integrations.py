# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Per-integration sections for the support pack (fork issue #2728, PR 2).

``support_pack_state`` answers "what is this server like". These collectors
answer "what is it talking to": the reading devices, KOReader sync and the
send-to-eReader email path. They follow the same rules, so the pack stays safe
to paste into a public issue:

* **Allow-list, never a redaction pass.** Each query names the columns it
  reads. ``display_name`` (user-editable, may hold a person's name),
  ``book_scope``, device identities, document hashes, reading locations and
  every email address are never selected.
* **Short hashes, not identifiers.** A device is shown as ``dev_<8 hex>`` and
  an account as ``u1``, ``u2`` (order of first appearance). No tokens, no
  serials, no names, no addresses.
* **Device fields use known values and firmware uses a version format.**
  A Kobo's client-supplied model is shown only when it matches recognized
  hardware. KOReader labels appear as short hashes.
* **Read-only, tolerant of older schemas.** ``app.db`` is opened read-only,
  columns and tables that do not exist yet are skipped rather than failing,
  and a failing sub-query degrades to that field only.
* **Bounded.** At most ``MAX_DEVICES_LISTED`` devices and ``MAX_KOSYNC_DEVICES``
  KOReader devices are listed; aggregates always cover everything.

Every collector takes plain inputs (a database path, a settings dict, a list of
task tuples) so it can be tested without booting the app.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3

from . import support_pack_state
from .support_pack_state import _error, _open_readonly

MAX_DEVICES_LISTED = 200
MAX_KOSYNC_DEVICES = 50
MAX_TASK_CLASSES = 30

_DEVICE_KINDS = frozenset({"kobo", "koreader", "webreader"})
_DEVICE_PLATFORMS = frozenset({"Kobo", "nickel", "koreader", "web"})
_DEVICE_ORIGINS = frozenset({"auto", "account-browser"})
_MODEL_REPORTED_KINDS = frozenset({"kobo", "webreader"})
_KOBO_MODELS = frozenset({
    "Kobo Clara 2E", "Kobo Clara BW", "Kobo Clara Colour", "Kobo Clara Color",
    "Kobo Clara HD", "Kobo Elipsa", "Kobo Elipsa 2E", "Kobo Forma",
    "Kobo Libra 2", "Kobo Libra Colour", "Kobo Libra Color", "Kobo Libra H2O",
    "Kobo Nia", "Kobo Sage", "Kobo_spaBW",
})
_BROWSER_MODELS = frozenset({"CWNG web reader"})
_FIRMWARE_VERSION = re.compile(r"[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,8}")


_DEVICE_COLUMNS = (
    "id", "public_id", "user_id", "kind", "model", "platform", "firmware_version",
    "active", "created_by", "first_seen_at", "last_seen_at", "share_place",
    "align_percent", "cwng_header_seen_at",
)
_TIMESTAMP_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")

# CalibreTask stat codes (cps/services/worker.py).
_TASK_STATS = {0: "waiting", 1: "failed", 2: "started", 3: "success", 4: "ended", 5: "cancelled"}
_SERVER_TYPES = {0: "smtp", 1: "gmail_oauth"}
_ENCRYPTION = {0: "none", 1: "starttls", 2: "ssl"}


def short_ref(prefix: str, value) -> str:
    """Stable 8-hex label for an identifier. Never reversible in practice and
    never the identifier itself."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"


def _known_value(value, allowed):
    if value is None:
        return None
    return value if value in allowed else "(redacted)"


def _firmware_version(value):
    if value is None:
        return None
    return value if isinstance(value, str) and _FIRMWARE_VERSION.fullmatch(value) else "(redacted)"


def _utc(value):
    """Stored UTC timestamp as ``YYYY-MM-DDTHH:MM:SSZ``, or None."""
    match = _TIMESTAMP_PATTERN.match(str(value)) if value is not None else None
    return f"{match.group(1)}T{match.group(2)}Z" if match else None


class Unavailable:
    """Data that could not be read, as distinct from data that is empty.

    A locked or unreadable table must never look like "0 devices" or "0 pending":
    a support pack that reports healthy when it could not read anything is the
    worst outcome for a debugging tool. Sections show this as
    ``"unavailable": "<reason>"``."""

    def __init__(self, reason: str):
        self.reason = reason


def _reason(ex: BaseException) -> str:
    """A short, safe reason: the exception type, or a plain-English category for
    the few SQLite failures worth telling apart. Never the message itself."""
    message = str(ex).lower()
    if "locked" in message or "busy" in message:
        return "database locked"
    if "no such table" in message:
        return "table missing"
    if "no such column" in message:
        return "column missing"
    return type(ex).__name__


def _columns(conn, table: str):
    """The table's column names, or ``Unavailable`` if they could not be read.
    An empty set means the table genuinely does not exist."""
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error as ex:
        return Unavailable(_reason(ex))


def _grouped(conn, sql: str):
    """Run a ``key, count[, extra]`` GROUP BY query. ``{}`` means no rows;
    ``Unavailable`` means it could not be read (missing table, locked database)."""
    try:
        return {row[0]: row[1:] for row in conn.execute(sql)}
    except sqlite3.Error as ex:
        return Unavailable(_reason(ex))


def _json_size(obj) -> int:
    return len(json.dumps(obj, sort_keys=True, indent=2).encode("utf-8"))


def _fit_devices_to_budget(section: dict) -> None:
    """Trim the device list, never the summary, so the section fits its size cap.

    Without this the generic cap would replace the whole section with a
    truncation marker and lose the aggregates, which are the part that always
    covers every device. The largest list that fits is found by measuring the
    real output (a device nested in the list is indented further than on its
    own, so adding up per-device sizes undercounts)."""
    limit = support_pack_state.MAX_SECTION_BYTES - 1024  # headroom for formatting
    if _json_size(section) <= limit:
        return
    devices = section["devices"]
    low, high = 0, len(devices)  # the largest k with devices[:k] fitting is in [low, high)
    while low < high:
        middle = (low + high + 1) // 2
        if _json_size(dict(section, devices=devices[:middle])) <= limit:
            low = middle
        else:
            high = middle - 1
    del devices[low:]
    section["summary"]["listed"] = low
    section["summary"]["truncated"] = True
    section["summary"]["trimmed_for_size"] = True


def _open_app_db(app_db_path):
    if not app_db_path or not os.path.isfile(app_db_path):
        return None
    return _open_readonly(app_db_path)


# ---------------------------------------------------------------- devices

def collect_devices(app_db_path) -> dict:
    """Reading devices (Kobo, browser reader, KOReader) from the ``device`` table."""
    conn = _open_app_db(app_db_path)
    if conn is None:
        return {"exists": False}
    try:
        present = _columns(conn, "device")
        if isinstance(present, Unavailable):
            return {"exists": True, "unavailable": present.reason}
        if not present:
            return {"exists": True, "device_table": False}
        wanted = [c for c in _DEVICE_COLUMNS if c in present]
        select = ", ".join(wanted)
        rows = conn.execute(
            f"SELECT {select} FROM device ORDER BY id LIMIT ?", (MAX_DEVICES_LISTED + 1,)
        ).fetchall()
        truncated = len(rows) > MAX_DEVICES_LISTED
        rows = rows[:MAX_DEVICES_LISTED]

        positions = _grouped(
            conn,
            "SELECT device_id, COUNT(*), SUM(CASE WHEN rehydrate_needed THEN 1 ELSE 0 END) "
            "FROM device_reading_position GROUP BY device_id",
        )
        nudges = _grouped(
            conn, "SELECT device_id, COUNT(*) FROM kobo_share_place_nudge GROUP BY device_id"
        )
        # Anything that could not be read is reported as unavailable, with the
        # reason, and its per-device values are null: never a zero.
        unavailable: dict = {}
        if isinstance(positions, Unavailable):
            unavailable["reading_positions"] = unavailable["rehydrate_pending"] = positions.reason
        if isinstance(nudges, Unavailable):
            unavailable["share_place_nudges_pending"] = nudges.reason

        user_labels: dict = {}
        devices = []
        for values in rows:
            row = dict(zip(wanted, values))
            device_id = row.get("id")
            user_label = user_labels.setdefault(row.get("user_id"), f"u{len(user_labels) + 1}")
            if isinstance(positions, Unavailable):
                reading_positions = rehydrate_pending = None
            else:
                counted, pending = positions.get(device_id, (0, 0))
                reading_positions, rehydrate_pending = counted or 0, int(pending or 0)
            nudge_count = None if isinstance(nudges, Unavailable) else (nudges.get(device_id) or (0,))[0]
            kind = _known_value(row.get("kind"), _DEVICE_KINDS)
            model_raw = row.get("model")
            model_reported = kind in _MODEL_REPORTED_KINDS
            known_models = _KOBO_MODELS if kind == "kobo" else _BROWSER_MODELS
            devices.append({
                "ref": short_ref("dev", row.get("public_id", device_id)),
                "user": user_label,
                "kind": kind,
                "model": _known_value(model_raw, known_models) if model_reported else None,
                "model_ref": (short_ref("mdl", model_raw)
                              if kind == "koreader" and model_raw else None),
                "platform": _known_value(row.get("platform"), _DEVICE_PLATFORMS),
                "firmware_version": _firmware_version(row.get("firmware_version")),
                "active": bool(row.get("active")) if "active" in row else None,
                "created_by": _known_value(row.get("created_by"), _DEVICE_ORIGINS),
                "share_place": bool(row.get("share_place")) if "share_place" in row else None,
                "align_percent": bool(row.get("align_percent")) if "align_percent" in row else None,
                "cwng_header_seen": bool(row.get("cwng_header_seen_at")) if "cwng_header_seen_at" in row else None,
                "first_seen_at": _utc(row.get("first_seen_at")),
                "last_seen_at": _utc(row.get("last_seen_at")),
                "reading_positions": reading_positions,
                "rehydrate_pending": rehydrate_pending,
                "share_place_nudges_pending": nudge_count,
            })

        # Aggregates cover every device, not just the listed ones.
        total, active, shared, users = conn.execute(
            "SELECT COUNT(*), "
            + ("SUM(CASE WHEN active THEN 1 ELSE 0 END)" if "active" in present else "NULL") + ", "
            + ("SUM(CASE WHEN share_place THEN 1 ELSE 0 END)" if "share_place" in present else "NULL") + ", "
            "COUNT(DISTINCT user_id) FROM device"
        ).fetchone()
        grouped_kinds = _grouped(conn, "SELECT kind, COUNT(*) FROM device GROUP BY kind")
        if isinstance(grouped_kinds, Unavailable):
            unavailable["by_kind"] = grouped_kinds.reason
            by_kind = None
        else:
            by_kind = {}
            for raw_kind, (count,) in grouped_kinds.items():
                kind = _known_value(raw_kind, _DEVICE_KINDS) or "unknown"
                by_kind[kind] = by_kind.get(kind, 0) + count
        section = {
            "exists": True,
            "summary": {
                "total": total,
                "active": int(active) if active is not None else None,
                "share_place_enabled": int(shared) if shared is not None else None,
                "users_with_devices": users,
                "by_kind": by_kind,
                "listed": len(devices),
                "truncated": truncated,
            },
            "devices": devices,
        }
        if unavailable:
            section["unavailable"] = unavailable
        _fit_devices_to_budget(section)
        return section
    except Exception as ex:  # pylint: disable=broad-except
        return {"exists": True, **_error(ex)}
    finally:
        conn.close()


# --------------------------------------------------------------- koreader

def collect_koreader(app_db_path, sync_enabled=None) -> dict:
    """KOReader progress sync from ``kosync_progress``. Counts and times only:
    no document hashes, progress strings, CFIs or device names."""
    conn = _open_app_db(app_db_path)
    if conn is None:
        return {"exists": False}
    try:
        present = _columns(conn, "kosync_progress")
        if isinstance(present, Unavailable):
            return {"exists": True, "unavailable": present.reason, "sync_enabled": sync_enabled}
        if not present:
            return {"exists": True, "kosync_table": False, "sync_enabled": sync_enabled}
        total, users, devices, newest = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id), COUNT(DISTINCT device_id), MAX(timestamp) "
            "FROM kosync_progress"
        ).fetchone()
        out = {
            "exists": True,
            "sync_enabled": sync_enabled,
            "rows": total,
            "users": users,
            "identified_devices": devices,
            "newest_sync_at": _utc(newest),
        }
        if "client_modified_at" in present:
            out["rows_without_client_clock"] = conn.execute(
                "SELECT COUNT(*) FROM kosync_progress WHERE client_modified_at IS NULL"
            ).fetchone()[0]
        if "web_cfi" in present:
            out["rows_with_browser_position"] = conn.execute(
                "SELECT COUNT(*) FROM kosync_progress WHERE web_cfi IS NOT NULL"
            ).fetchone()[0]
        per_device = conn.execute(
            "SELECT device_id, COUNT(*), MAX(timestamp) FROM kosync_progress "
            "GROUP BY device_id ORDER BY MAX(timestamp) DESC LIMIT ?",
            (MAX_KOSYNC_DEVICES,),
        ).fetchall()
        out["devices"] = [
            {
                "ref": short_ref("kos", device_id) if device_id else "unidentified",
                "documents": count,
                "last_sync_at": _utc(last),
            }
            for device_id, count, last in per_device
        ]
        return out
    except Exception as ex:  # pylint: disable=broad-except
        return {"exists": True, **_error(ex)}
    finally:
        conn.close()


# ------------------------------------------------------------------ email

def _account_counts(conn) -> dict:
    """How many accounts exist and how many have an eReader email or a custom
    subject. Counts only: never an address or a subject."""
    present = _columns(conn, "user")
    if isinstance(present, Unavailable):
        return {"exists": True, "unavailable": present.reason}
    has_mail = "kindle_mail" in present
    has_subject = "kindle_mail_subject" in present
    total, with_mail, with_subject = conn.execute(
        'SELECT COUNT(*), '
        + ("SUM(CASE WHEN COALESCE(kindle_mail, '') != '' THEN 1 ELSE 0 END)" if has_mail else "NULL") + ", "
        + ("SUM(CASE WHEN COALESCE(kindle_mail_subject, '') != '' THEN 1 ELSE 0 END)" if has_subject else "NULL")
        + ' FROM "user"'
    ).fetchone()
    return {
        "exists": True,
        "total": total,
        "with_ereader_email": int(with_mail) if with_mail is not None else None,
        "with_custom_subject": int(with_subject) if with_subject is not None else None,
    }


def collect_email(mail_settings, worker_tasks, app_db_path) -> dict:
    """Send-to-eReader path: SMTP shape, how many accounts use it, and task
    outcomes since the process started. Never a host name, address, subject
    or task message (those can hold addresses, titles and paths)."""
    out: dict = {}
    if mail_settings is None:
        out["smtp"] = {"available": False}
    else:
        size = mail_settings.get("max_attachment_bytes")
        out["smtp"] = {
            "available": True,
            "configured": bool(mail_settings.get("configured")),
            "server_type": _SERVER_TYPES.get(mail_settings.get("server_type"), "unknown"),
            "port": mail_settings.get("port") if isinstance(mail_settings.get("port"), int) else None,
            "encryption": _ENCRYPTION.get(mail_settings.get("encryption"), "unknown"),
            "max_attachment_mib": round(size / (1024 * 1024), 1) if isinstance(size, (int, float)) else None,
        }

    conn = None
    try:
        conn = _open_app_db(app_db_path)
        if conn is None:
            out["users"] = {"exists": False}
        else:
            out["users"] = _account_counts(conn)
    except Exception as ex:  # pylint: disable=broad-except
        out["users"] = _error(ex)
    finally:
        if conn is not None:
            conn.close()

    if worker_tasks is None:
        out["tasks_since_start"] = {"available": False}
    else:
        counts: dict = {}
        for class_name, stat in worker_tasks:
            if class_name not in counts and len(counts) >= MAX_TASK_CLASSES:
                continue
            bucket = counts.setdefault(str(class_name), {label: 0 for label in _TASK_STATS.values()})
            bucket[_TASK_STATS.get(stat, "ended")] += 1
        out["tasks_since_start"] = {"available": True, "by_task": counts}
    return out
