# SPDX-License-Identifier: GPL-3.0-or-later
"""KOSync's device gate and timed journal writes. No helper commits here.

Legacy clients keep the protocol's existing furthest-place arbitration. The
identified path compares clocks with the common place arbiter and protects
that verdict with a SQL compare-and-swap, including first-writer races.
"""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flask import request
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from .. import logger, ub
from . import device_registry
from .place_arbiter import Report, report_wins

log = logger.create()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def client_clock(raw, received_at):
    """Freeze invalid/future clocks at receipt, so they cannot mature later."""
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    try:
        epoch = float(raw)
        if not math.isfinite(epoch) or epoch < 946684800:  # 2000-01-01 UTC
            return None
        stamp = datetime.fromtimestamp(epoch, timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return stamp if stamp <= received_at + timedelta(minutes=5) else None


def request_device(user_id, body=None, *, observe=False):
    """Return (device, identified, raw_id); GET resolution is read-only.

    Header identity takes precedence. A headerless body can identify only a
    reader already observed with the CWNG header, i.e. built-in Progress sync
    on the same physical reader. Registration failure retains compatibility.
    """
    body = body if isinstance(body, dict) else {}
    header = request.headers.get("X-CWNG-Device-ID")
    raw_id = header or body.get("device_id")
    if not raw_id:
        return None, False, None
    name = (request.headers.get("X-CWNG-Device-Name") if header else None) or body.get("device")
    device = None
    if observe:
        try:
            internal = device_registry.register_koreader_device_best_effort(
                user_id=user_id, device_id=raw_id, device_name=name,
                header_seen=bool(header),
            )
        except Exception:
            log.warning("Best-effort KOReader registration failed at the place gate", exc_info=True)
            internal = None
        if internal is not None:
            device = ub.session.get(ub.Device, internal, populate_existing=True)
    else:
        device = device_registry.find_koreader_device(user_id=user_id, device_id=raw_id)
    identified = device is not None and (bool(header) or device_registry.place_control(device) == "per_device")
    return device, identified, raw_id


def latest_write(model, identity, values, *, client_column, server_column,
                 candidate, present_column=None, session=None):
    """Apply one arbiter verdict atomically; rejudge only a concurrent winner.

    Clocks form the optimistic concurrency token. SQLite serializes the
    UPDATE/INSERT; if another writer won before it, its new clocks are read
    and judged instead of overwriting a newer report using an old verdict.
    A NULL ``server_column`` reads the client clock as the receipt (main's
    key). When ``present_column`` is NULL the stored pair is ignored, so the
    UPDATE also requires it still NULL (spec section 13a): a concurrent
    first observation sharing the old pair cannot be overwritten.
    """
    s = session if session is not None else ub.session
    table = model.__table__
    terms = [table.c[key] == value for key, value in identity.items()]
    while True:
        current = s.execute(select(table).where(*terms)).mappings().first()
        clock = None
        if current is not None:
            clock = current[server_column] or current[client_column]
        report = Report(current[client_column], clock) if clock is not None else None
        absent = (current is not None and present_column is not None
                  and current[present_column] is None)
        if absent:
            report = None
        if not report_wins(candidate, report):
            return False
        if current is None:
            result = s.execute(insert(table).values(**identity, **values)
                               .on_conflict_do_nothing(index_elements=list(identity)))
        else:
            tokens = [table.c[key] == current[key] for key in (client_column, server_column)]
            if absent:
                tokens.append(table.c[present_column].is_(None))
            result = s.execute(update(table).where(*terms, *tokens).values(**values))
        if result.rowcount == 1:
            s.expire_all()
            return True


def own_position(device_id, book_id):
    if not book_id:
        return None
    return ub.session.query(ub.DeviceReadingPosition).filter_by(
        device_id=device_id, book_id=book_id).populate_existing().first()


def journal(device_id, book_id, document, progress, percentage, report):
    from .koreader_position import KOREADER_LOCATION_TYPE, is_xpointer
    from ..progress_syncing.protocols.kosync import read_status_for_percentage
    values = dict(
        location_source=document,
        location_type=(KOREADER_LOCATION_TYPE if is_xpointer(progress)
                       else "koreader_page" if progress is not None else None),
        location_value=progress, progress_percent=percentage,
        client_modified_at=report.client_at, server_modified_at=report.server_at,
    )
    identity = dict(device_id=device_id, book_id=book_id)
    accepted = latest_write(ub.DeviceReadingPosition, identity, values,
                            client_column="client_modified_at", server_column="server_modified_at",
                            candidate=report, present_column="progress_percent")
    if accepted:
        # Progress and explicit status are independent observations. A delayed
        # page turn can be the newest position but older than a status choice.
        # Spec section 13: the status observation is (raw client clock, its
        # own receipt), so a same-second tie goes to the later receipt.
        latest_write(ub.DeviceReadingPosition, identity,
                     dict(reading_status=read_status_for_percentage(percentage),
                          status_modified_at=report.client_at,
                          status_received_at=report.server_at),
                     client_column="status_modified_at", server_column="status_received_at",
                     present_column="reading_status", candidate=report)
    return accepted


def position_report(row):
    return Report(row.client_modified_at, row.server_modified_at)


def shared_report(row):
    return Report(row.client_modified_at, row.timestamp)


def own_record(device, row, raw_id, document):
    """A record-shaped private response; locators stay in their original file."""
    from ..progress_syncing.protocols.kosync import PERCENTAGE_ONLY_LOCATOR
    if row is None or row.progress_percent is None:
        return None
    return SimpleNamespace(
        progress=(row.location_value if row.location_source == document and row.location_value
                  else PERCENTAGE_ONLY_LOCATOR),
        percentage=row.progress_percent, device=device.display_name, device_id=raw_id,
        timestamp=row.server_modified_at,
    )
