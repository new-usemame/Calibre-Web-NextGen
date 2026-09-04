# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated Magic Shelf ordering for configured Calibre custom columns."""

from dataclasses import dataclass
import re
from typing import Any, Iterable

from sqlalchemy import case
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import InstrumentedAttribute

from . import calibre_db, db, logger
from .sort_orders import BOOK_SORT_ORDERS, DEFAULT_SORT


log = logger.create()

# This is the only datatype allowlist. Admin choices, menu options, and the
# request-time resolver all flow through eligible_columns().
ELIGIBLE_DATATYPES = frozenset(("int", "float", "datetime"))

_CUSTOM_SORT_KEY = re.compile(r"cc-([0-9]+)-(asc|desc)\Z")
_MAGIC_SHELF_BUILTIN_SORTS = frozenset(BOOK_SORT_ORDERS).difference(
    ("hotasc", "hotdesc")
)
_MAX_COLUMN_ID = (1 << 63) - 1
_COLUMNS_NOT_PROVIDED = object()


@dataclass(frozen=True)
class ResolvedMagicShelfSort:
    """A canonical sort key and only the trusted SQLAlchemy objects it needs."""

    key: str
    order_by: tuple[Any, ...]
    join: tuple[Any, ...] = ()
    persistable: bool = True


def _parse_column_id(value: Any) -> int | None:
    """Parse only IDs that can fit in Calibre's signed SQLite INTEGER key."""
    text = str(value)
    if not text.isascii() or not text.isdecimal() or len(text) > 19:
        return None
    column_id = int(text)
    return column_id if column_id <= _MAX_COLUMN_ID else None


def configured_column_ids(config) -> frozenset[int]:
    """Read the persisted comma-separated IDs, ignoring malformed old values."""
    raw = getattr(config, "config_sortable_custom_columns", "") or ""
    parsed = (_parse_column_id(value) for value in raw.split(","))
    return frozenset(column_id for column_id in parsed if column_id is not None)


def _is_eligible(column) -> bool:
    return (
        getattr(column, "datatype", None) in ELIGIBLE_DATATYPES
        and not bool(getattr(column, "is_multiple", False))
        and not bool(getattr(column, "mark_for_delete", False))
    )


def eligible_columns(columns: Iterable[Any]) -> list[Any]:
    """Return live scalar numeric/date definitions in their supplied order."""
    return [column for column in columns if _is_eligible(column)]


def configured_columns(columns: Iterable[Any], config) -> list[Any]:
    """Return eligible live definitions selected by the administrator."""
    configured = configured_column_ids(config)
    return [
        column for column in eligible_columns(columns)
        if getattr(column, "id", None) in configured
    ]


def _query_columns(query):
    """Execute a definition query without flushing unrelated pending writes."""
    session = calibre_db.session
    with session.no_autoflush:
        return query.all()


def load_eligible_columns() -> list[Any] | None:
    """Load selectable definitions, or ``None`` when the library is unavailable."""
    try:
        query = calibre_db.session.query(db.CustomColumns).filter(
            db.CustomColumns.datatype.in_(ELIGIBLE_DATATYPES),
            db.CustomColumns.is_multiple.is_(False),
            db.CustomColumns.mark_for_delete.is_(False),
        ).order_by(db.CustomColumns.name, db.CustomColumns.id)
        return eligible_columns(_query_columns(query))
    except (SQLAlchemyError, AttributeError):
        log.warning("Sortable custom-column definitions unavailable", exc_info=True)
        return None


def load_configured_columns(config) -> list[Any] | None:
    """Load configured definitions, or ``None`` when the library is unavailable."""
    configured = configured_column_ids(config)
    if not configured:
        return []
    try:
        query = calibre_db.session.query(db.CustomColumns).filter(
            db.CustomColumns.id.in_(configured)
        ).order_by(db.CustomColumns.name, db.CustomColumns.id)
        return configured_columns(_query_columns(query), config)
    except (SQLAlchemyError, AttributeError):
        log.warning("Configured custom-column definitions unavailable", exc_info=True)
        return None


def persist_configured_columns(
        config, requested_ids: Iterable[Any], columns: Iterable[Any] | None) -> str:
    """Persist requested IDs, preserving state if the allowlist could not load."""
    if columns is None:
        return getattr(config, "config_sortable_custom_columns", "") or ""
    allowed = {column.id for column in eligible_columns(columns)}
    selected = set()
    for raw_value in requested_ids:
        column_id = _parse_column_id(raw_value)
        if column_id is not None and column_id in allowed:
            selected.add(column_id)
    selected = sorted(selected)
    serialized = ",".join(str(column_id) for column_id in selected)
    config.config_sortable_custom_columns = serialized
    return serialized


def custom_sort_options(config, columns=_COLUMNS_NOT_PROVIDED) -> list[dict[str, str]]:
    """Build the server-owned custom choices consumed by both frontends."""
    if columns is _COLUMNS_NOT_PROVIDED:
        columns = load_configured_columns(config)
    if columns is None:
        return []
    options = []
    for column in configured_columns(columns, config):
        options.extend((
            {"value": f"cc-{column.id}-asc", "label": f"{column.name} ↑"},
            {"value": f"cc-{column.id}-desc", "label": f"{column.name} ↓"},
        ))
    return options


def _default_sort(*, persistable=True) -> ResolvedMagicShelfSort:
    return ResolvedMagicShelfSort(
        DEFAULT_SORT,
        tuple(BOOK_SORT_ORDERS[DEFAULT_SORT]),
        persistable=persistable,
    )


def resolve_magic_shelf_sort(
        sort_key, config, columns=_COLUMNS_NOT_PROVIDED) -> ResolvedMagicShelfSort:
    """Map a request key to trusted SQLAlchemy ordering, or the safe default.

    Custom keys must name an admin-enabled ID, a currently live eligible
    definition, and the ORM model already built from that Calibre schema. The
    request string is never treated as a SQL identifier or SQL expression.
    """
    if not isinstance(sort_key, str):
        return _default_sort()
    if sort_key in _MAGIC_SHELF_BUILTIN_SORTS:
        return ResolvedMagicShelfSort(sort_key, tuple(BOOK_SORT_ORDERS[sort_key]))

    match = _CUSTOM_SORT_KEY.fullmatch(sort_key)
    if match is None:
        return _default_sort()
    column_id = _parse_column_id(match.group(1))
    if column_id is None:
        return _default_sort()
    direction = match.group(2)
    if column_id not in configured_column_ids(config):
        return _default_sort()

    if columns is _COLUMNS_NOT_PROVIDED:
        columns = load_configured_columns(config)
    if columns is None:
        return _default_sort(persistable=False)
    live_column = next(
        (column for column in columns if getattr(column, "id", None) == column_id),
        None,
    )
    if live_column is None or not _is_eligible(live_column):
        return _default_sort()

    model = db.cc_classes.get(column_id)
    book_column = getattr(model, "book", None)
    value_column = getattr(model, "value", None)
    if not isinstance(book_column, InstrumentedAttribute) \
            or not isinstance(value_column, InstrumentedAttribute):
        return _default_sort()

    if direction == "desc":
        value_order = value_column.desc()
        id_order = db.Books.id.desc()
    else:
        value_order = value_column.asc()
        id_order = db.Books.id.asc()

    # SQLite puts NULL first for ascending order. A leading boolean makes
    # absent rows and explicit NULL values last in both directions.
    order_by = (
        case((value_column.is_(None), 1), else_=0).asc(),
        value_order,
        id_order,
    )
    return ResolvedMagicShelfSort(
        f"cc-{column_id}-{direction}",
        order_by,
        (model, db.Books.id == book_column),
    )
