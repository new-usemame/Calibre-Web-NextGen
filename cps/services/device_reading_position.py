# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Per-device reading-position journal and rehydrate latch (#1942 M3).

The user-level ``KoboReadingState`` graph remains the resolved carrier served
to Kobo, Hardcover, KOSync, and the book-detail UI. This module stores each
device's own observation without committing; request handlers retain ownership
of their existing transaction boundaries.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, case, false, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub


def _now():
    return datetime.now(timezone.utc)


def rehydrate_request_cutoff():
    """Return the server clock fence for one sync request.

    A latch whose ``server_modified_at`` is at or beyond this fence was armed
    during the current request.  It cannot be consumed until a later request,
    after the device has had a chance to apply the entitlement/download that
    may reset its local position.
    """
    return _now()


def timestamp_is_newer(candidate, current):
    """Compare SQLite clocks while tolerating naive/aware round trips."""
    if candidate is None:
        return False
    if current is None:
        return True
    if (candidate.tzinfo is None) != (current.tzinfo is None):
        candidate = candidate.replace(tzinfo=None)
        current = current.replace(tzinfo=None)
    return candidate > current


@dataclass(frozen=True)
class PositionWriteOutcome:
    """Database verdict and the position that actually survived arbitration."""

    accepted: bool
    percentage: object
    clock: object = None
    row_id: object = None


def conditional_position_update(
    *,
    model,
    identity,
    incoming_percentage,
    values,
    insert_values=None,
    conflict_columns=None,
    percentage_column=None,
    clock_column=None,
    incoming_clock=None,
    clock_accepts=False,
    equal_accepts=False,
    same_source_column=None,
    incoming_source=None,
    block_lower_at_or_below=None,
    session=None,
):
    """Atomically advance one reading-position row and return the DB verdict.

    The acceptance comparison lives wholly in the UPDATE's WHERE clause.  A
    row is writable when its percentage is NULL/lower, when an explicitly
    enabled source clock is newer, when an equal-value locator refresh is
    allowed, or when a named source is updating its own row.  ``rowcount`` is
    the verdict; the SELECT afterwards reports the value that actually won so
    callers derive status and fan-out from accepted state rather than input.

    When no row matches, ``INSERT .. ON CONFLICT DO NOTHING`` closes the
    first-writer race without surfacing a duplicate-key ``IntegrityError``.
    The app database is SQLite, so this uses its native SQLAlchemy insert.
    """
    s = session if session is not None else ub.session
    if percentage_column is None:
        percentage_column = model.percentage
    table = model.__table__

    identity_terms = [column == value for column, value in identity.items()]
    acceptance_terms = []
    if incoming_percentage is not None:
        acceptance_terms.extend([
            percentage_column.is_(None),
            percentage_column < incoming_percentage,
        ])
    if clock_accepts and clock_column is not None and incoming_clock is not None:
        acceptance_terms.append(or_(
            clock_column.is_(None),
            clock_column < incoming_clock,
        ))
    if equal_accepts and incoming_percentage is not None:
        acceptance_terms.append(percentage_column == incoming_percentage)
    if same_source_column is not None and incoming_source:
        acceptance_terms.append(same_source_column == incoming_source)

    acceptance = or_(*acceptance_terms) if acceptance_terms else false()
    if (block_lower_at_or_below is not None
            and incoming_percentage is not None
            and incoming_percentage <= block_lower_at_or_below):
        # A rehydrate latch may identify a cover reset. Keep the stored-value
        # comparison in SQL: a blank row/equal-forward write is harmless, but a
        # newer device clock cannot authorize a lower reset while armed.
        acceptance = and_(
            acceptance,
            or_(
                percentage_column.is_(None),
                percentage_column <= incoming_percentage,
            ),
        )

    update_values = dict(values)
    if incoming_percentage is not None:
        update_values[percentage_column.key] = incoming_percentage
    if clock_column is not None and incoming_clock is not None:
        update_values[clock_column.key] = case(
            (
                or_(clock_column.is_(None), clock_column < incoming_clock),
                incoming_clock,
            ),
            else_=clock_column,
        )

    statement = (
        update(table)
        .where(*identity_terms, acceptance)
        .values(**update_values)
    )
    result = s.execute(statement)
    accepted = result.rowcount == 1

    conflict_identity = identity
    if not accepted and insert_values is not None:
        insert_payload = dict(insert_values)
        if incoming_percentage is not None:
            insert_payload[percentage_column.key] = incoming_percentage
        if clock_column is not None and incoming_clock is not None:
            insert_payload[clock_column.key] = incoming_clock
        conflict_columns = tuple(conflict_columns or identity.keys())
        insert_statement = sqlite_insert(
            table,
        ).values(**insert_payload).on_conflict_do_nothing(
            index_elements=[column.name for column in conflict_columns],
        )
        insert_result = s.execute(insert_statement)
        accepted = insert_result.rowcount == 1
        conflict_identity = {
            column: insert_payload[column.key]
            for column in conflict_columns
        }
        if not accepted:
            # Another transaction inserted after our first UPDATE observed no
            # row. Re-run the same conditional UPDATE against that winner so a
            # concurrent higher first write is not spuriously discarded.
            retry_result = s.execute(statement)
            accepted = retry_result.rowcount == 1

    read_terms = [
        column == value for column, value in conflict_identity.items()
    ]
    selected = [percentage_column]
    if clock_column is not None:
        selected.append(clock_column)
    primary_key = tuple(table.primary_key.columns)
    if primary_key:
        selected.append(primary_key[0])
    row = s.execute(select(*selected).where(*read_terms)).first()
    if row is None and conflict_identity is not identity:
        row = s.execute(select(*selected).where(*identity_terms)).first()

    if row is None:
        return PositionWriteOutcome(False, None)
    mapping = row._mapping
    return PositionWriteOutcome(
        accepted=accepted,
        percentage=mapping[percentage_column],
        clock=mapping[clock_column] if clock_column is not None else None,
        row_id=mapping[primary_key[0]] if primary_key else None,
    )


def advance_kobo_bookmark(
    bookmark,
    incoming_percentage,
    *,
    content_source_progress_percent=None,
    content_source_supplied=False,
    location_source=None,
    location_type=None,
    location_value=None,
    location_supplied=False,
    incoming_clock=None,
    clock_accepts=False,
    equal_accepts=False,
    preserve_clock_when_missing=False,
    block_lower_at_or_below=None,
    session=None,
):
    """Apply the shared SQL arbiter to the resolved Kobo bookmark carrier."""
    s = session if session is not None else ub.session
    if getattr(bookmark, "id", None) is None:
        s.flush()

    now = _now()
    write_clock = incoming_clock
    if write_clock is None and not preserve_clock_when_missing:
        write_clock = now
    values = {
        "last_modified": (
            write_clock
            if write_clock is not None else ub.KoboBookmark.last_modified
        ),
    }
    if content_source_supplied:
        values["content_source_progress_percent"] = content_source_progress_percent
    if location_supplied:
        values.update({
            "location_source": location_source,
            "location_type": location_type,
            "location_value": location_value,
        })
    if incoming_percentage is not None and incoming_percentage > 0:
        values["created_at"] = case(
            (ub.KoboBookmark.created_at.is_(None), write_clock or now),
            else_=ub.KoboBookmark.created_at,
        )

    outcome = conditional_position_update(
        model=ub.KoboBookmark,
        identity={ub.KoboBookmark.id: bookmark.id},
        incoming_percentage=incoming_percentage,
        values=values,
        percentage_column=ub.KoboBookmark.progress_percent,
        clock_column=ub.KoboBookmark.last_modified,
        incoming_clock=write_clock,
        clock_accepts=clock_accepts,
        equal_accepts=equal_accepts,
        block_lower_at_or_below=block_lower_at_or_below,
        session=s,
    )

    if outcome.accepted:
        parent_id = bookmark.kobo_reading_state_id
        if parent_id is not None:
            parent_clock = outcome.clock or write_clock or now
            monotonic_clock = case(
                (
                    or_(
                        ub.KoboReadingState.last_modified.is_(None),
                        ub.KoboReadingState.last_modified < parent_clock,
                    ),
                    parent_clock,
                ),
                else_=ub.KoboReadingState.last_modified,
            )
            s.execute(
                update(ub.KoboReadingState)
                .where(ub.KoboReadingState.id == parent_id)
                .values(
                    last_modified=monotonic_clock,
                    priority_timestamp=monotonic_clock,
                )
            )

    if hasattr(bookmark, "_sa_instance_state"):
        s.expire(bookmark)
        parent = getattr(bookmark, "kobo_reading_state", None)
        if parent is not None:
            s.expire(parent)
    return outcome


def stage_position(
    *,
    device_id,
    book_id,
    progress_percent=None,
    content_source_progress_percent=None,
    location_source=None,
    location_type=None,
    location_value=None,
    cfi=None,
    client_modified_at=None,
    session=None,
):
    """Upsert one device observation and return its prior rehydrate latch.

    An ordinary position write never clears ``rehydrate_needed``. That latch
    belongs to the sync response which actually emits the repair and is
    cleared only in that request's checked commit.
    """
    if not device_id:
        return False
    s = session if session is not None else ub.session
    device_id = int(device_id)
    book_id = int(book_id)
    with s.no_autoflush:
        existing = s.query(ub.DeviceReadingPosition).filter(
            ub.DeviceReadingPosition.device_id == device_id,
            ub.DeviceReadingPosition.book_id == book_id,
        ).one_or_none()
        rehydrate_pending = bool(
            existing is not None and existing.rehydrate_needed,
        )

    now = _now()
    values = {
        "device_id": device_id,
        "book_id": book_id,
        "location_source": location_source,
        "location_type": location_type,
        "location_value": location_value,
        "progress_percent": progress_percent,
        "content_source_progress_percent": content_source_progress_percent,
        "cfi": cfi,
        "client_modified_at": client_modified_at,
        "server_modified_at": now,
        "rehydrate_needed": rehydrate_pending,
    }
    statement = sqlite_insert(ub.DeviceReadingPosition).values(**values)
    updates = {
        "location_source": statement.excluded.location_source,
        "location_type": statement.excluded.location_type,
        "location_value": statement.excluded.location_value,
        "progress_percent": statement.excluded.progress_percent,
        "content_source_progress_percent": (
            statement.excluded.content_source_progress_percent
        ),
        "cfi": statement.excluded.cfi,
        "server_modified_at": statement.excluded.server_modified_at,
    }
    # A rejected/missing device clock is not a newer observation and must not
    # erase the last usable ordering clock from this device's journal.
    if client_modified_at is not None:
        updates["client_modified_at"] = statement.excluded.client_modified_at
    s.execute(statement.on_conflict_do_update(
        index_elements=["device_id", "book_id"],
        set_=updates,
    ))
    return rehydrate_pending


def mark_rehydrate_needed(device_id, book_ids, *, session=None):
    """Arm selected books for one device, creating blank journal rows."""
    if not device_id:
        return 0
    normalized = sorted({int(book_id) for book_id in book_ids})
    if not normalized:
        return 0
    s = session if session is not None else ub.session
    now = _now()
    rows = [
        {
            "device_id": int(device_id),
            "book_id": book_id,
            "server_modified_at": now,
            "rehydrate_needed": True,
        }
        for book_id in normalized
    ]
    statement = sqlite_insert(ub.DeviceReadingPosition).values(rows)
    s.execute(statement.on_conflict_do_update(
        index_elements=["device_id", "book_id"],
        set_={
            "server_modified_at": statement.excluded.server_modified_at,
            "rehydrate_needed": True,
        },
    ))
    return len(normalized)


def mark_existing_positions_for_rehydrate(device_id, *, session=None):
    """Arm every existing journal row during the legacy synced-book reset."""
    if not device_id:
        return 0
    s = session if session is not None else ub.session
    return s.query(ub.DeviceReadingPosition).filter(
        ub.DeviceReadingPosition.device_id == int(device_id),
    ).update(
        {
            ub.DeviceReadingPosition.rehydrate_needed: True,
            ub.DeviceReadingPosition.server_modified_at: _now(),
        },
        synchronize_session=False,
    )
