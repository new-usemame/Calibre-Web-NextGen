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
* **Free-text device fields are charset-checked, and user labels are hashed.**
  ``platform`` and ``firmware_version`` come from the client; a value outside a
  conservative character set is replaced with ``(redacted)``. ``model`` is shown
  only for kinds where the hardware reports it (Kobo, browser reader). For
  KOReader it is a label someone chose, so only a short hash is shown.
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
import os
import re
import sqlite3

from .support_pack_state import _error, _open_readonly

MAX_DEVICES_LISTED = 200
MAX_KOSYNC_DEVICES = 50
MAX_TASK_CLASSES = 30

# Device kinds whose ``model`` is reported by the hardware or set by the server
# ("Kobo Clara BW", "CWNG web reader"). For every other kind (KOReader) the
# model is a label the client or user chose, so it can hold a person's name and
# is reported as a short hash only.
_MODEL_REPORTED_KINDS = frozenset({"kobo", "webreader"})

_DEVICE_COLUMNS = (
    "id", "public_id", "user_id", "kind", "model", "platform", "firmware_version",
    "active", "created_by", "first_seen_at", "last_seen_at", "share_place",
    "align_percent", "cwng_header_seen_at",
)
# Conservative: letters, digits, space and a few separators. No "@" (emails),
# no "/" or "\" (paths).
_SAFE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9 ._+():\-]{1,80}$")
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


def _safe_text(value):
    """Client-supplied label if it passes a conservative charset, else a marker."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if _SAFE_TEXT_PATTERN.match(text) else "(redacted)"


def _utc(value):
    """Stored UTC timestamp as ``YYYY-MM-DDTHH:MM:SSZ``, or None."""
    match = _TIMESTAMP_PATTERN.match(str(value)) if value is not None else None
    return f"{match.group(1)}T{match.group(2)}Z" if match else None


def _columns(conn, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def _grouped(conn, sql: str) -> dict:
    """Run a ``key, count[, extra]`` GROUP BY query; ``{}`` if the table is
    missing or the query fails, so one absent table cannot sink a section."""
    try:
        return {row[0]: row[1:] for row in conn.execute(sql)}
    except sqlite3.Error:
        return {}


def _open_app_db(app_db_path):
    if not app_db_path or not os.path.isfile(app_db_path):
        return None
    return _open_readonly(app_db_path)


# ---------------------------------------------------------------- devices

def collect_devices(app_db_path) -> dict:
    """Reading devices (Kobo, browser reader) from the ``device`` table."""
    conn = _open_app_db(app_db_path)
    if conn is None:
        return {"exists": False}
    try:
        present = _columns(conn, "device")
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

        user_labels: dict = {}
        devices = []
        for values in rows:
            row = dict(zip(wanted, values))
            device_id = row.get("id")
            user_label = user_labels.setdefault(row.get("user_id"), f"u{len(user_labels) + 1}")
            pos = positions.get(device_id, (0, 0))
            kind = _safe_text(row.get("kind"))
            model_raw = row.get("model")
            model_reported = kind in _MODEL_REPORTED_KINDS
            devices.append({
                "ref": short_ref("dev", row.get("public_id", device_id)),
                "user": user_label,
                "kind": kind,
                "model": _safe_text(model_raw) if model_reported else None,
                "model_ref": (short_ref("mdl", model_raw)
                              if not model_reported and model_raw else None),
                "platform": _safe_text(row.get("platform")),
                "firmware_version": _safe_text(row.get("firmware_version")),
                "active": bool(row.get("active")) if "active" in row else None,
                "created_by": _safe_text(row.get("created_by")),
                "share_place": bool(row.get("share_place")) if "share_place" in row else None,
                "align_percent": bool(row.get("align_percent")) if "align_percent" in row else None,
                "cwng_header_seen": bool(row.get("cwng_header_seen_at")) if "cwng_header_seen_at" in row else None,
                "first_seen_at": _utc(row.get("first_seen_at")),
                "last_seen_at": _utc(row.get("last_seen_at")),
                "reading_positions": pos[0] or 0,
                "rehydrate_pending": int(pos[1] or 0),
                "share_place_nudges_pending": (nudges.get(device_id) or (0,))[0],
            })

        # Aggregates cover every device, not just the listed ones.
        total, active, shared, users = conn.execute(
            "SELECT COUNT(*), "
            + ("SUM(CASE WHEN active THEN 1 ELSE 0 END)" if "active" in present else "NULL") + ", "
            + ("SUM(CASE WHEN share_place THEN 1 ELSE 0 END)" if "share_place" in present else "NULL") + ", "
            "COUNT(DISTINCT user_id) FROM device"
        ).fetchone()
        by_kind = _grouped(conn, "SELECT kind, COUNT(*) FROM device GROUP BY kind")
        return {
            "exists": True,
            "summary": {
                "total": total,
                "active": int(active) if active is not None else None,
                "share_place_enabled": int(shared) if shared is not None else None,
                "users_with_devices": users,
                "by_kind": {str(_safe_text(k)): v[0] for k, v in sorted(by_kind.items(), key=lambda kv: str(kv[0]))},
                "listed": len(devices),
                "truncated": truncated,
            },
            "devices": devices,
        }
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
            present = _columns(conn, "user")
            has_mail = "kindle_mail" in present
            has_subject = "kindle_mail_subject" in present
            total, with_mail, with_subject = conn.execute(
                'SELECT COUNT(*), '
                + ("SUM(CASE WHEN COALESCE(kindle_mail, '') != '' THEN 1 ELSE 0 END)" if has_mail else "NULL") + ", "
                + ("SUM(CASE WHEN COALESCE(kindle_mail_subject, '') != '' THEN 1 ELSE 0 END)" if has_subject else "NULL")
                + ' FROM "user"'
            ).fetchone()
            out["users"] = {
                "exists": True,
                "total": total,
                "with_ereader_email": int(with_mail) if with_mail is not None else None,
                "with_custom_subject": int(with_subject) if with_subject is not None else None,
            }
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
