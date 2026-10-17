# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidecar tables in ``app.db`` for custom-column facts Calibre cannot store.

Calibre owns ``custom_columns`` and ``custom_column_<id>`` and their schema. Its
value table has no room for a curated display order:

.. code-block:: sql

    CREATE TABLE custom_column_3(
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        value TEXT NOT NULL COLLATE NOCASE, link TEXT NOT NULL DEFAULT '',
        UNIQUE(value))

So sibling order does not exist anywhere in the system -- ``hierarchy._finalize``
sorts alphabetically on every read -- and any feature that wants to reorder values
has nowhere to put the result. This module is that somewhere.

## Sidecar, not mirror

Only what Calibre *cannot* express is stored here:

============  ==========================================================
``cc_column``     app-side facts about a column -- currently just whether it
                  is hierarchical, as a tri-state.
``cc_value_order``curated sibling order. **An absent row means alphabetical**,
                  which is exactly today's behaviour.
============  ==========================================================

Calibre remains authoritative for existence, label, datatype, the value set and
every count. Nothing here duplicates those, because Calibre is routinely edited
outside Calibre-Web and a mirrored copy would be stale the moment it is -- and
would give "does this column exist?" two disagreeing answers.

## Which connection owns this

``CalibreDB.engine`` is an in-memory SQLite with *both* files attached
(``cps/db.py``): ``metadata.db`` as ``calibre``, ``app.db`` as ``app_settings``.
So these tables are reached through ``CalibreDB.session`` with an
``app_settings.`` prefix, which buys three things:

1. a read of column metadata and its Calibre counterpart is **one query**, not
   two sessions and a merge in Python;
2. a sidecar write can share the transaction with the Calibre write it belongs
   to -- so "rename the value" and "move its order row" commit or roll back
   together, and the failure mode where a rename silently loses a curated
   position is unrepresentable rather than merely tested-for;
3. those writes are already serialised by ``metadata_db_write_lock()``.

### DDL is the exception, and SQLite forces it

Rows cross the boundary freely; **schema objects do not.** Probed directly:

.. code-block:: sql

    CREATE TABLE app_settings.cc_value_order (...)          -- accepted
    CREATE INDEX ix ON app_settings.cc_value_order (...)     -- near ".": syntax error
    CREATE INDEX ix ON cc_value_order (...)                  -- no such table: main.x

SQLite resolves the table of a ``CREATE INDEX`` against ``main`` only, so an
index on a table in an attached database cannot be created from this connection
at all. ``CREATE TABLE`` *is* allowed, oddly enough.

So the split is forced and is also the right one:

* **DDL** goes through ``ub._run_ddl_with_retry(ub.engine, ...)`` -- app.db owns
  its own schema, and that helper already handles a locked database and
  duplicate-column errors the way every other app.db migration does.
* **Rows** go through ``CalibreDB.session`` with the ``app_settings.`` prefix,
  which is what buys the join and the shared transaction.

The cost of this module must never be fatal: without the sidecar the app still
works, it just cannot hold a curated order. Every entry point checks
:func:`sidecar_ready` first, and the many unit tests that stub a plain
in-memory session get a silent no-op.

Not stored, deliberately: counts (the displayed count is per-user filtered by
restricted shelves and own-library membership, so one cached number is correct
for nobody), ``is_leaf``/``has_children`` (derivable, and it rots), ``label`` and
``datatype`` (the join is free).
"""
from typing import NamedTuple

from sqlalchemy import text

from . import calibre_db, db, logger, ub

log = logger.create()

#: Schema the sidecar lives under. ``None`` when the running session has no
#: ``app_settings`` attachment, which is the signal to do nothing.
SIDECAR_SCHEMA = "app_settings"

#: SQLite's signed INTEGER key bound. A Calibre column id always fits it, so
#: anything larger is a hostile or malformed form value rather than a column.
#: Lives here because this module owns the tables whose keys these validate.
_MAX_COLUMN_ID = (1 << 63) - 1


def parse_column_id(value):
    """Parse only ids that can fit Calibre's signed SQLite INTEGER key.

    The admin form carries a column id in a FIELD NAME
    (``cc_hierarchy_mode_<id>``), so that name is attacker-controlled input like
    any other and must not reach a database unvalidated. ``isdigit()`` is not
    enough on its own: Python's ints are unbounded, so an id of
    ``99999999999999999999`` passes it and then fails at the driver.
    """
    text = str(value)
    if not text.isascii() or not text.isdecimal() or len(text) > 19:
        return None
    column_id = int(text)
    return column_id if column_id <= _MAX_COLUMN_ID else None


class HierarchyVerdict(NamedTuple):
    """A column's hierarchy answer, and where it came from.

    ``value`` is ``True``/``False`` when an administrator has declared it and
    ``None`` when the answer is still derived from the data -- which is the
    behaviour every install has today.
    """
    value: bool | None
    source: str          # 'override' | 'derived' | 'unavailable'


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def sidecar_ready() -> bool:
    """Whether the running session can see the sidecar tables.

    ``CalibreDB`` attaches ``app_settings`` on every real connection, but the
    unit tests that stub a plain in-memory session do not. Everything here is a
    no-op rather than an error in that case: a missing sidecar degrades to
    alphabetical order, which is the pre-feature behaviour.
    """
    try:
        session = calibre_db.session
        if session is None:
            return False
        attached = session.execute(text("PRAGMA database_list")).fetchall()
    except Exception:
        return False
    return any(row[1] == SIDECAR_SCHEMA for row in attached)


def _ensure_session():
    """Materialise the session, or return None when it cannot be had."""
    try:
        calibre_db.ensure_session()
    except Exception:
        log.debug("Sidecar: no Calibre session available", exc_info=True)
        return None
    return getattr(calibre_db, "session", None)


def _table(name: str) -> str:
    return "%s.%s" % (SIDECAR_SCHEMA, name)


def ensure_tables() -> bool:
    """Create the sidecar tables and their index if absent. Idempotent.

    DDL goes through ``ub.engine`` rather than ``CalibreDB.session`` because
    SQLite cannot ``CREATE INDEX`` on a table in an attached database from the
    attaching connection (see the module docstring). app.db owns its schema, so
    app.db creates it; ``CalibreDB.session`` still reads and writes the rows,
    which is what buys the cross-file join and the shared transaction.

    Returns True when the tables are present afterwards. A failure is logged and
    swallowed: without the sidecar the app still works, it just cannot hold a
    curated order.
    """
    statements = (        # is_hierarchy is a tri-state: NULL means "still derived from the data",
        # which is what every existing install keeps. No DEFAULT on purpose --
        # DEFAULT 0 would silently declare every column flat on creation.
        """
        CREATE TABLE IF NOT EXISTS cc_column (
            id            INTEGER NOT NULL PRIMARY KEY,
            is_hierarchy  BOOLEAN,
            reconciled_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cc_value_order (
            column_id   INTEGER NOT NULL,
            path        TEXT    NOT NULL,
            parent_path TEXT    NOT NULL,
            view_order  INTEGER NOT NULL,
            PRIMARY KEY (column_id, path)
        )
        """,
        # Serves "fetch the order for one parent", which is the only query the
        # read path ever needs. See parent_path in cc_sidecar_datamodel.md.
        """
        CREATE INDEX IF NOT EXISTS ix_cc_value_order_sibling
            ON cc_value_order (column_id, parent_path, view_order)
        """,
    )
    try:
        # ``ub.engine`` is a local inside ub.init_db, not a module attribute, so
        # the app.db engine is taken off the session's bind. That is the same
        # handle cps/__init__.py passes to ub.backfill_annotation_content_ids.
        engine = ub.session.get_bind()
        ub._run_ddl_with_retry(engine, statements)
    except Exception:
        log.warning("Custom-column sidecar tables could not be created; "
                    "curated value order is unavailable", exc_info=True)
        return False
    return True


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def hierarchy_verdict(col_id) -> HierarchyVerdict:
    """Is this column hierarchical, and is that declared or derived?

    Phase 1 only *reads* the column; nothing writes it yet, so every install
    answers ``derived``. That is deliberate: a stored verdict is inert until an
    administrator sets one, which is what makes this landable with no behaviour
    change at all.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return HierarchyVerdict(None, "unavailable")
    try:
        rows = session.execute(
            text("SELECT is_hierarchy FROM %s WHERE id = :id" % _table("cc_column")),
            {"id": col_id}).fetchall()
    except Exception:
        log.debug("Sidecar: hierarchy verdict unavailable for column %s", col_id,
                  exc_info=True)
        return HierarchyVerdict(None, "unavailable")
    if not rows:
        return HierarchyVerdict(None, "derived")
    value = rows[0][0]
    return HierarchyVerdict(None if value is None else bool(value),
                            "derived" if value is None else "override")


def stored_order(column_id) -> dict:
    """``{path: view_order}`` for one column. Empty means alphabetical.

    Reads the whole column rather than one parent because the caller -- the tree
    builder -- has every node in hand anyway and is about to sort them.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return {}
    try:
        rows = session.execute(
            text("SELECT path, view_order FROM %s WHERE column_id = :cid"
                 % _table("cc_value_order")),
            {"cid": column_id}).fetchall()
    except Exception:
        log.debug("Sidecar: stored order unavailable for column %s", column_id,
                  exc_info=True)
        return {}
    return {row[0]: row[1] for row in rows}


def sibling_order(column_id, parent_path) -> dict:
    """``{path: view_order}`` for one parent's children, via the index."""
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return {}
    try:
        rows = session.execute(
            text("SELECT path, view_order FROM %s "
                 "WHERE column_id = :cid AND parent_path = :parent "
                 "ORDER BY view_order" % _table("cc_value_order")),
            {"cid": column_id, "parent": parent_path or ""}).fetchall()
    except Exception:
        log.debug("Sidecar: sibling order unavailable for column %s parent %r",
                  column_id, parent_path, exc_info=True)
        return {}
    return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
#
# Everything below runs inside the caller's transaction -- the same
# CalibreDB.session the value rewrite is already on -- so a curated position and
# the value it belongs to commit or roll back TOGETHER. That is the whole reason
# the sidecar is reached through the Calibre connection rather than app.db's own:
# without it, a rename that dropped its order row would fail silently, leaving a
# value correctly renamed and quietly out of position.
#
# Positions are dense and per-parent, allocated like book_shelf_link.order
# (cps/shelf.py): append with max+1, move by swapping two integers, and leave a
# hole after a delete. Only relative order is ever compared, so holes cost
# nothing and nothing is ever renumbered.

def set_hierarchy_verdict(col_id, value):
    """Record an administrator's declared hierarchy answer for a column.

    ``value`` is ``True``, ``False`` or ``None``. ``None`` is not "clear the
    column" -- it is the third state, and the only way back to deriving the
    answer from the data, which is what every install does today. That
    distinction is the whole reason the column is nullable with no DEFAULT.

    Returns True when it was written. Never raises: this is called from an admin
    form, where failing over an optional preference would lose the rest of the
    submission too.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return False
    try:
        if not ensure_tables():
            return False
        session.execute(
            text("INSERT INTO %s (id, is_hierarchy, reconciled_at) "
                 "VALUES (:id, :value, CURRENT_TIMESTAMP) "
                 "ON CONFLICT(id) DO UPDATE SET is_hierarchy = :value, "
                 "reconciled_at = CURRENT_TIMESTAMP" % _table("cc_column")),
            {"id": col_id, "value": value})
        session.commit()
    except Exception:
        session.rollback()
        log.warning("Could not record the hierarchy verdict for column %s", col_id,
                    exc_info=True)
        return False
    log.info("[cc-sidecar] hierarchy verdict for column %s set to %s",
             col_id, "automatic" if value is None else value)
    # The cached derivation may now be wrong in either direction: a column
    # declared hierarchical must stop being read as flat, and vice versa.
    for attr in ("_hier_cache", "_legacy_hier_cache"):
        if hasattr(calibre_db, attr):
            setattr(calibre_db, attr, None)
    return True


def parent_of(path):
    """The parent path of a dotted path, or '' for a root.

    Local rather than ``hierarchy``'s business because this module must not
    import it: cc_sidecar is reached from cps.db, which hierarchy also imports.
    """
    if not path or "." not in path:
        return ""
    return path.rsplit(".", 1)[0]


def set_order_for_siblings(column_id, ordered_paths):
    """Write an explicit position for every path in ``ordered_paths``.

    The whole list is written, not just the one that moved. That is what takes
    the siblings the user arranged out of the alphabetical fallback: an
    untouched sibling between two positioned ones would otherwise sort itself
    back into the middle by name.

    No-op when the sidecar is unavailable. The caller is already inside a value
    write, so raising here would fail a rename for a feature the user may not
    even have enabled.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return 0
    written = 0
    for position, path in enumerate(ordered_paths):
        try:
            session.execute(
                text("INSERT INTO %s (column_id, path, parent_path, view_order) "
                     "VALUES (:cid, :path, :parent, :pos) "
                     "ON CONFLICT(column_id, path) DO UPDATE SET "
                     "parent_path = :parent, view_order = :pos"
                     % _table("cc_value_order")),
                {"cid": column_id, "path": path, "parent": parent_of(path),
                 "pos": position})
            written += 1
        except Exception:
            log.warning("Sidecar: could not record the order of %r", path,
                        exc_info=True)
            return written
    return written


def drop_order(column_id, paths):
    """Forget the curated position of these paths.

    Used by delete and by the source side of a merge, where the value itself is
    going away and an order row would be an orphan until the next reconcile.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return 0
    removed = 0
    for path in paths:
        try:
            result = session.execute(
                text("DELETE FROM %s WHERE column_id = :cid AND path = :path"
                     % _table("cc_value_order")),
                {"cid": column_id, "path": path})
            removed += result.rowcount or 0
        except Exception:
            log.warning("Sidecar: could not clear the order of %r", path,
                        exc_info=True)
    return removed


def carry_order(column_id, mapping):
    """Move curated positions along a value rewrite. Returns the number moved.

    ``mapping`` is ``{old_value: new_value}`` from ``plan_rename`` or
    ``plan_move`` -- the *stored* spellings, which is what the order rows are
    keyed on.

    This is the step that is easy to miss and impossible to notice when it is:
    every stored value is rewritten correctly, the user sees the rename they
    asked for, and the value has quietly jumped to the alphabetical end of its
    siblings. Hence its own function, its own test, and the shared transaction.

    Read, then delete, then reinsert. A row cannot be re-keyed in place when the
    new key already exists -- and a rename onto an existing value IS a merge, so
    that case is the rule rather than the exception.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready() or not mapping:
        return 0

    # Several old values can map onto one new one (a merge). The survivor keeps
    # the earliest curated position of any of its sources, so it lands where the
    # topmost source was rather than at the bottom of the group.
    wanted = {}
    for old, new in mapping.items():
        if old == new:
            continue
        try:
            rows = session.execute(
                text("SELECT view_order FROM %s "
                     "WHERE column_id = :cid AND path = :path"
                     % _table("cc_value_order")),
                {"cid": column_id, "path": old}).fetchall()
        except Exception:
            log.debug("Sidecar: could not read the order of %r", old, exc_info=True)
            continue
        if not rows:
            continue
        position = rows[0][0]
        if new not in wanted or position < wanted[new]:
            wanted[new] = position

    if not wanted:
        return 0

    # Clear the old keys before writing the new ones, so a rename that is also a
    # merge cannot collide with the row it is replacing.
    stale = [old for old, new in mapping.items() if old != new and old not in wanted]
    drop_order(column_id, [old for old, new in mapping.items()
                           if old != new and old in wanted] + stale)

    for new, position in sorted(wanted.items(), key=lambda kv: kv[1]):
        try:
            session.execute(
                text("INSERT INTO %s (column_id, path, parent_path, view_order) "
                     "VALUES (:cid, :path, :parent, :pos) "
                     "ON CONFLICT(column_id, path) DO UPDATE SET "
                     "parent_path = :parent, view_order = :pos"
                     % _table("cc_value_order")),
                {"cid": column_id, "path": new, "parent": parent_of(new),
                 "pos": position})
        except Exception:
            log.warning("Sidecar: could not carry the order of %r", new,
                        exc_info=True)
    return len(wanted)


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def _live_column_ids() -> set:
    """Every Calibre custom-column id, including composite and series.

    Deliberately not ``get_cc_columns()``: that applies the administrator's
    ``config_columns_to_ignore`` filter and the browsable-datatype filter, and a
    sidecar row is wanted for every column so that un-ignoring one needs no
    migration.
    """
    session = _ensure_session()
    if session is None:
        return set()
    try:
        rows = session.execute(text("SELECT id FROM calibre.custom_columns")).fetchall()
    except Exception:
        # A test session with no `calibre` attachment.
        try:
            rows = session.execute(text("SELECT id FROM custom_columns")).fetchall()
        except Exception:
            log.debug("Sidecar: Calibre columns unavailable", exc_info=True)
            return set()
    return {row[0] for row in rows}


def _ordered_column_ids() -> set:
    """Columns that actually have curated order rows.

    Reconcile only descends into these, which bounds its cost by *how many
    columns somebody has reordered* rather than by library size. On a fresh
    install this set is empty and reconcile does no value scanning at all.
    """
    session = calibre_db.session
    if session is None or not sidecar_ready():
        return set()
    try:
        rows = session.execute(
            text("SELECT DISTINCT column_id FROM %s" % _table("cc_value_order"))
        ).fetchall()
    except Exception:
        return set()
    return {row[0] for row in rows}


def _live_paths(column_id) -> set:
    """Every dotted path the live values of one column can produce.

    A path is not only a stored value: ``custom_column_x`` holding ``Arts.Mystery``
    also implies ``Arts``. So this walks each distinct value and adds every
    prefix, which is what makes pruning correct for synthesised parents.
    """
    session = calibre_db.session
    cc = db.cc_classes.get(column_id)
    if cc is None:
        return set()
    try:
        values = [row[0] for row in
                  session.execute(text('SELECT DISTINCT "value" FROM calibre.%s'
                                       % cc.__tablename__)).fetchall()]
    except Exception:
        log.debug("Sidecar: values unavailable for column %s", column_id, exc_info=True)
        return set()
    paths = set()
    for value in values:
        if not value:
            continue
        parts = [p for p in str(value).split(".") if p]
        for index in range(len(parts)):
            paths.add(".".join(parts[:index + 1]))
    return paths


def reconcile() -> dict:
    """Bring the sidecar back in line with the library.

    Four steps, and the order matters:

    1. **Upsert** a ``cc_column`` row for every Calibre column. Never touches
       ``is_hierarchy`` -- that is the app's fact, not Calibre's.
    2. **Delete** ``cc_column`` rows for columns Calibre no longer has.
    3. **Prune** ``cc_value_order`` rows whose path the library can no longer
       produce.
    4. Stamp ``reconciled_at``.

    Step 3 is the one that is easy to skip and expensive to skip. Without it, a
    value deleted in Calibre desktop leaves an orphan forever, and recreating a
    value with the same name silently inherits its old curated position -- which
    looks like the app remembering something the user never asked it to.

    Returns a small summary dict; safe to call when the sidecar is unavailable,
    in which case it reports that rather than raising.
    """
    summary = {"columns_added": 0, "columns_removed": 0, "orders_pruned": 0,
               "available": False}
    if not ensure_tables():
        return summary
    summary["available"] = True
    session = calibre_db.session
    live = _live_column_ids()

    # 1. upsert
    added = 0
    try:
        for column_id in sorted(live):
            result = session.execute(
                text("INSERT INTO %s (id, is_hierarchy, reconciled_at) "
                     "VALUES (:id, NULL, CURRENT_TIMESTAMP) "
                     "ON CONFLICT(id) DO UPDATE SET reconciled_at = CURRENT_TIMESTAMP"
                     % _table("cc_column")),
                {"id": column_id})
            if result.rowcount:
                added += 1
    except Exception:
        session.rollback()
        log.warning("Sidecar: column reconcile failed", exc_info=True)
        return summary
    summary["columns_added"] = added

    # 2. drop columns Calibre no longer has
    removed = 0
    try:
        known = {row[0] for row in
                 session.execute(text("SELECT id FROM %s" % _table("cc_column"))).fetchall()}
        for stale in sorted(known - live):
            session.execute(text("DELETE FROM %s WHERE id = :id" % _table("cc_column")),
                            {"id": stale})
            session.execute(
                text("DELETE FROM %s WHERE column_id = :id" % _table("cc_value_order")),
                {"id": stale})
            removed += 1
        session.commit()
    except Exception:
        session.rollback()
        log.warning("Sidecar: stale column cleanup failed", exc_info=True)
        return summary
    summary["columns_removed"] = removed

    # 3. prune order rows the library can no longer produce
    pruned = 0
    try:
        for column_id in sorted(_ordered_column_ids()):
            if column_id not in live:
                continue
            live_paths = _live_paths(column_id)
            if not live_paths:
                # A column whose value table we could not read. Leaving its order
                # alone is the safe direction: pruning on a failed read would
                # discard a user's curation because of our problem, not theirs.
                continue
            stored = stored_order(column_id)
            for path in sorted(set(stored) - live_paths):
                session.execute(
                    text("DELETE FROM %s WHERE column_id = :cid AND path = :path"
                         % _table("cc_value_order")),
                    {"cid": column_id, "path": path})
                pruned += 1
        session.commit()
    except Exception:
        session.rollback()
        log.warning("Sidecar: order prune failed", exc_info=True)
        return summary
    summary["orders_pruned"] = pruned

    if added or removed or pruned:
        log.info("[cc-sidecar] reconciled: %s column(s) added, %s removed, "
                 "%s stale order row(s) pruned", added, removed, pruned)
    return summary
