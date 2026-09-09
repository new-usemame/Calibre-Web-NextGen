# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account-wide browser attribution and preservation of pre-upgrade sources.

Old device rows remain as aliases, retaining their original public IDs and
transport telemetry. Only the canonical row participates in source selection.
The created_by markers are internal; neither is accepted from a client.
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import bindparam, text

ACCOUNT_BROWSER = "account-browser"
BROWSER_ALIAS = "browser-alias"
BROWSER_UNIQUE_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_account_browser ON device(user_id) "
    "WHERE kind = 'webreader' AND created_by = 'account-browser'"
)


def canonical_browser(device, session, ub):
    """Resolve old public IDs without crossing the original account boundary."""
    if device is None or device.kind != "webreader" or device.created_by != BROWSER_ALIAS:
        return device
    return session.query(ub.Device).filter_by(
        user_id=device.user_id, kind="webreader", created_by=ACCOUNT_BROWSER,
    ).one_or_none()


def _rows(conn, table, ids, order="id"):
    # Table/ordering are internal literals, never request values.
    return conn.execute(text(
        f"SELECT * FROM {table} WHERE device_id IN :ids ORDER BY {order}"
    ).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()


def _copy_latest(conn, table, key, clock, ids, canonical_id, *, combine_latch=False):
    """Copy whole latest observations; retain alias rows as historical evidence."""
    winners = {}
    latches = {}
    for row in _rows(conn, table, ids, f"{clock}, id"):
        winners[row[key]] = dict(row)
        if combine_latch:
            latches[row[key]] = latches.get(row[key], False) or bool(row["rehydrate_needed"])
    for identity, row in winners.items():
        row.pop("id")
        row["device_id"] = canonical_id
        if combine_latch:
            row["rehydrate_needed"] = latches[identity]
        columns = tuple(row)
        updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in (key, "device_id"))
        conn.execute(text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + c for c in columns)}) "
            f"ON CONFLICT(device_id, {key}) DO UPDATE SET {updates}"
        ), row)


def migrate_account_browser_source(engine):
    """Atomically consolidate existing sources, without touching shared progress.

Run after the annotation/device schema migrations. A completed account is
skipped on subsequent boots, so historical alias telemetry cannot overwrite
new reading activity. No device rows or annotation content are deleted.
"""
    with engine.begin() as conn:
        # Acquire SQLite's writer reservation before choosing canonical rows.
        # This also serializes simultaneous startup migrations.
        conn.execute(text("UPDATE device SET id=id WHERE 0"))
        conn.execute(text(BROWSER_UNIQUE_SQL))
        pending = conn.execute(text(
            "SELECT DISTINCT user_id FROM device WHERE kind='webreader' "
            "AND created_by NOT IN ('account-browser', 'browser-alias')"
            " UNION SELECT a.user_id FROM annotation a JOIN user u ON u.id=a.user_id "
            "WHERE a.source='webreader' AND a.origin_device_id IS NULL"
            " UNION SELECT b.user_id FROM bookmark b JOIN user u ON u.id=b.user_id "
            "WHERE NOT EXISTS (SELECT 1 FROM device d WHERE d.user_id=b.user_id "
            "AND d.kind='webreader' AND d.created_by='account-browser')"
        )).scalars().all()
        for user_id in pending:
            devices = conn.execute(text(
                "SELECT * FROM device WHERE user_id=:user AND kind='webreader' "
                "AND created_by != 'browser-alias' "
                "ORDER BY (created_by='account-browser') DESC, active DESC, id"
            ), {"user": user_id}).mappings().all()
            if not devices:
                # Older releases had shared browser bookmarks/annotations but
                # no device registry. Their existing reading activity is enough
                # to establish the account source; an unused account gets none.
                first, last = conn.execute(text(
                    "SELECT MIN(clock), MAX(clock) FROM ("
                    "SELECT created_at AS clock FROM annotation WHERE user_id=:user AND source='webreader' "
                    "UNION ALL SELECT server_modified_at FROM annotation WHERE user_id=:user AND source='webreader' "
                    "UNION ALL SELECT updated_at FROM bookmark WHERE user_id=:user) WHERE clock IS NOT NULL"
                ), {"user": user_id}).one()
                fallback = datetime.now(timezone.utc).replace(tzinfo=None)
                conn.execute(text(
                    "INSERT INTO device (public_id, user_id, kind, display_name, model, platform, "
                    "first_seen_at, last_seen_at, active, created_by) VALUES "
                    "(:public, :user, 'webreader', 'Browser', 'CWNG web reader', 'web', "
                    ":first, :last, 1, 'account-browser')"
                ), {"public": str(uuid.uuid4()), "user": user_id,
                    "first": first or fallback, "last": last or fallback})
                devices = conn.execute(text(
                    "SELECT * FROM device WHERE user_id=:user AND kind='webreader' "
                    "AND created_by='account-browser'"
                ), {"user": user_id}).mappings().all()
            canonical = devices[0]
            canonical_id = canonical["id"]
            ids = [d["id"] for d in devices]
            aliases = ids[1:]
            conn.execute(text(
                "UPDATE annotation SET origin_device_id=:canonical WHERE user_id=:user "
                "AND source='webreader' AND origin_device_id IS NULL"
            ), {"canonical": canonical_id, "user": user_id})
            label = canonical["display_name"]
            if canonical["created_by"] == "auto" and re.fullmatch(r"Web reader(?: \d+)?", label or ""):
                label = "Browser"
            conn.execute(text(
                "UPDATE device SET created_by='account-browser', display_name=:label, "
                "active=:active, first_seen_at=:first, last_seen_at=:last WHERE id=:id"
            ), {
                "id": canonical_id, "label": label,
                "active": any(d["active"] for d in devices),
                "first": min(d["first_seen_at"] for d in devices),
                "last": max(d["last_seen_at"] for d in devices),
            })
            if not aliases:
                continue
            # Raw SQL intentionally avoids ORM onupdate clocks: regrouping is
            # not a content edit or a new device-supplied annotation revision.
            for column in ("origin_device_id", "assigned_device_id", "last_editor_device_id"):
                conn.execute(text(
                    f"UPDATE annotation SET {column}=:canonical "
                    f"WHERE user_id=:user AND {column} IN :aliases"
                ).bindparams(bindparam("aliases", expanding=True)), {
                    "canonical": canonical_id, "user": user_id, "aliases": aliases,
                })
            _copy_latest(conn, "device_reading_position", "book_id", "server_modified_at",
                         ids, canonical_id, combine_latch=True)
            _copy_latest(conn, "annotation_device_state", "annotation_id", "updated_at",
                         ids, canonical_id)
            # A browser has no hardware delivery state. Keep the original
            # telemetry, but canonical routing intent follows the assignment.
            conn.execute(text(
                "UPDATE annotation_device_state SET desired=EXISTS "
                "(SELECT 1 FROM annotation a WHERE a.id=annotation_id "
                "AND a.user_id=:user AND a.assigned_device_id=:canonical) "
                "WHERE device_id=:canonical"
            ), {"user": user_id, "canonical": canonical_id})
            for row in _rows(conn, "device_retired_assignment", aliases):
                conn.execute(text(
                    "INSERT OR IGNORE INTO device_retired_assignment "
                    "(device_id, annotation_id, retired_at) VALUES (:device, :annotation, :retired)"
                ), {"device": canonical_id, "annotation": row["annotation_id"], "retired": row["retired_at"]})
            conn.execute(text(
                "UPDATE device SET active=0, created_by='browser-alias' WHERE id IN :aliases"
            ).bindparams(bindparam("aliases", expanding=True)), {"aliases": aliases})
