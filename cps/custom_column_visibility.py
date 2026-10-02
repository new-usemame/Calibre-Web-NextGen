# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user custom-column browse visibility: seeding and resolution.

A tag-like custom column (datatype ``text``/``enumeration``) is a browse
surface: it gets a classic sidebar entry, an OPDS root entry, and a column in
``/api/v1/columns``. Which of those a given user sees is stored per user in
``User.view_settings['cc_sidebar']['show_cc_<id>']`` and managed by the
"Show <column> Section" checkboxes on the profile page (``/me``).

Three rules govern the value, and everything in this module exists to enforce
them:

1. **Seed once.** At user creation and as a one-time backfill on upgrade, an
   explicit boolean is written for every browsable column. The seed value is
   *hierarchical -> visible, flat -> hidden*, unless the administrator's
   template (:func:`seed_default_visible`) says otherwise.
2. **Saved wins.** A stored value is never overridden -- not by the backfill,
   not by the template, not by a later save. The backfill writes *missing keys
   only*, which is what lets "apply to everyone" and "saved wins" both hold.
3. **No dynamic re-evaluation.** A column that later turns flat ->
   hierarchical changes nothing. The stored boolean is authoritative,
   permanently. This is why the detector below is consulted only while
   *seeding*, never while serving a request that already has a value.

:func:`is_cc_visible` is the single resolution point. Every surface that can
show or reach a column's content must go through it, so the classic sidebar,
the profile page, the SPA API, the classic browse route, the OPDS root and the
OPDS feed can never disagree about what a user may browse.

The ``stored is None`` branch -- reached only by an anonymous session, or by a
column created in Calibre after the backfill ran -- resolves to the seed
default. That is a one-off fallback, not a live configuration lookup.
"""

from typing import Any, Iterable

from . import calibre_db, config, db, logger, ub

log = logger.create()

# The datatypes that render as browsable tag-like lists/trees. Mirrors
# cps/render_template.py::get_custom_column_sidebar_entries and
# cps/api/columns.py::_BROWSABLE_DATATYPES; changing one means changing all.
BROWSABLE_DATATYPES = frozenset(("text", "enumeration"))

# The User.view_settings section holding per-column browse visibility.
CC_PAGE = "cc_sidebar"

# SQLite's signed INTEGER key bound, so a hostile form value cannot become a
# column id. Mirrors custom_column_sort._MAX_COLUMN_ID.
_MAX_COLUMN_ID = (1 << 63) - 1


def _key(col_id: Any) -> str:
    return "show_cc_%d" % col_id


def _parse_column_id(value: Any) -> int | None:
    """Parse only IDs that can fit in Calibre's signed SQLite INTEGER key."""
    text = str(value)
    if not text.isascii() or not text.isdecimal() or len(text) > 19:
        return None
    column_id = int(text)
    return column_id if column_id <= _MAX_COLUMN_ID else None


def _is_browsable(column: Any) -> bool:
    return (
        getattr(column, "datatype", None) in BROWSABLE_DATATYPES
        and not bool(getattr(column, "mark_for_delete", False))
    )


def browsable_columns(columns: Iterable[Any]) -> list:
    """The columns that carry a browse surface, in their supplied order."""
    return [column for column in columns if _is_browsable(column)]


def load_browsable_columns() -> list | None:
    """The browsable columns, or ``None`` when the library is not ready.

    ``None`` is load-bearing and distinct from ``[]``: it means "the Calibre
    database could not be read", so the backfill must skip rather than seed
    every column as hidden. ``[]`` means the library is readable and simply has
    no browsable columns.

    The definition list and the administrator's ``config_columns_to_ignore``
    filtering both come from ``get_cc_columns``, the same call the sidebar
    makes, so the seeded set and the rendered set cannot drift apart.
    """
    if not db.cc_classes:
        return None
    try:
        return browsable_columns(calibre_db.get_cc_columns(config))
    except Exception:
        log.warning("Browsable custom-column definitions unavailable", exc_info=True)
        return None


# --------------------------------------------------------------------------
# The administrator's seed template
# --------------------------------------------------------------------------

def configured_default_visible(config) -> frozenset | None:
    """The ids the administrator chose, or ``None`` when never configured.

    Tri-state, and the distinction is the whole point:

    * ``None``  -- the column was never configured, so the default is derived
      from the column's own hierarchy (hierarchical -> visible).
    * ``""``    -- the administrator saved the form with nothing ticked, so
      every column seeds hidden. This is a real choice, not an absence.
    * ``"1,3"`` -- only those ids seed visible.

    Legacy and fresh settings rows both read ``NULL`` here, which is what makes
    the feature migration-free: an install that has never been configured gets
    the documented hierarchical-only default, and an install that has been
    gets exactly what was saved.
    """
    raw = getattr(config, "config_default_cc_columns", None)
    if raw is None:
        return None
    parsed = (_parse_column_id(value) for value in (raw or "").split(","))
    return frozenset(column_id for column_id in parsed if column_id is not None)


def seed_default_visible(config, col_id) -> bool:
    """What to write for `col_id` when seeding a user.

    Read once, at seed time, and written to the user. It is deliberately not
    consulted on the request path for a user who already has a value -- that is
    rule 3.

    Fails closed. ``is_flat_cc_column`` reports a column with no ORM class as
    *not* flat, which would seed it visible -- the wrong way round, and exactly
    the case of a ``#goodreads_id``-shaped column. A column we cannot classify
    is not evidence of a hierarchy, so it seeds hidden.
    """
    configured = configured_default_visible(config)
    if configured is not None:
        return col_id in configured
    if col_id not in db.cc_classes:
        return False
    try:
        return not calibre_db.is_flat_cc_column(col_id)
    except Exception:
        log.debug("Hierarchy detection failed for column %s", col_id, exc_info=True)
        return False


def persist_configured_default_visible(config, requested_ids, columns) -> str:
    """Persist the administrator's selection, preserving it on load failure.

    Mirrors custom_column_sort.persist_configured_columns, including the
    behaviour that matters most: when the column definitions could not be
    loaded, an empty submission is *not* the same as "the administrator
    cleared the list", and the stored value is returned unchanged.
    """
    current = getattr(config, "config_default_cc_columns", None) or ""
    if columns is None:
        return current
    allowed = {getattr(column, "id", None) for column in columns}
    selected = set()
    for raw_value in requested_ids or ():
        column_id = _parse_column_id(raw_value)
        if column_id is not None and column_id in allowed:
            selected.add(column_id)
    serialized = ",".join(str(column_id) for column_id in sorted(selected))
    config.config_default_cc_columns = serialized
    return serialized


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def is_cc_visible(user, col_id) -> bool:
    """Whether `user` may see and browse custom column `col_id`.

    The single resolution point; every surface calls this. A stored value is
    returned as-is and is never re-resolved (rules 2 and 3). The seed default
    is reached only when no value was ever written -- an anonymous session, or
    a column created in Calibre after the backfill ran.

    ``stored is not False`` rather than ``bool(stored)`` is deliberate: it keeps
    the exact tri-state the three existing call sites used, so a malformed
    non-boolean left in ``view_settings`` degrades to "visible" (the legacy
    behaviour) instead of becoming truthy-by-accident.
    """
    try:
        stored = user.get_view_property(CC_PAGE, _key(col_id))
    except Exception:
        log.debug("Could not read cc visibility for column %s", col_id, exc_info=True)
        stored = None
    if stored is None:
        return seed_default_visible(config, col_id)
    return stored is not False


def seed_cc_visibility(user, columns, commit=False) -> int:
    """Write an explicit value for every browsable column with no stored key.

    Never overwrites an existing key -- that is rule 2, and it is what lets the
    same function serve both the signup paths and the upgrade backfill without
    the backfill destroying a choice somebody already made.

    Returns the number of keys written, so the caller can log and commit.
    """
    written = 0
    for column in columns:
        col_id = getattr(column, "id", None)
        if col_id is None:
            continue
        key = _key(col_id)
        try:
            stored = user.get_view_property(CC_PAGE, key)
        except Exception:
            stored = None
        if stored is not None:
            continue
        user.set_view_property(CC_PAGE, key, seed_default_visible(config, col_id),
                               commit=commit)
        written += 1
    return written


def backfill_existing_users() -> int:
    """Seed every user who has no stored value yet. Runs once, at startup.

    This is the step that makes the administrator's configuration reach the
    users the report is about. A creation-time seed alone leaves them untouched,
    so the configuration would be a silent no-op for every existing account.

    Two deliberate properties:

    * **Gated.** ``config_cc_visibility_seeded`` is set only after a successful
      pass, so an install that boots with an unreadable library retries on the
      next start instead of recording a partial migration.
    * **Missing keys only.** A user who already saved a choice keeps it.

    Anonymous sessions cannot be backfilled -- they have no row -- and reach the
    seed default through :func:`is_cc_visible` instead.
    """
    if getattr(config, "config_cc_visibility_seeded", False):
        return 0

    columns = load_browsable_columns()
    if columns is None:
        # The library is unreadable. Leave every user untouched and retry on
        # the next start; seeding from an empty column list would write False
        # for every column and hide the lot.
        log.info("[cc-visibility] library unavailable, deferring the cc visibility seed")
        return 0

    changed = 0
    users = 0
    try:
        for user in ub.session.query(ub.User).all():
            users += 1
            changed += seed_cc_visibility(user, columns)
    except Exception:
        log.error("[cc-visibility] seed failed, will retry on next start", exc_info=True)
        return 0

    config.config_cc_visibility_seeded = True
    config.save()
    if changed:
        log.info("[cc-visibility] seeded %s value(s) across %s user(s): %s",
                 changed, users, describe_seed(columns))
    else:
        log.info("[cc-visibility] nothing to seed across %s user(s)", users)
    return changed


def describe_seed(columns):
    """What was seeded, per column, for the log record.

    Worth logging explicitly. The seed is read once and then frozen for every
    user, so a verdict taken while the library was still settling becomes a
    permanent setting that nobody can audit later -- this line is the only
    record of why a column came out the way it did.
    """
    parts = []
    for column in columns:
        col_id = getattr(column, "id", None)
        if col_id is None:
            continue
        parts.append("%s=%s" % (column.name, "visible" if seed_default_visible(config, col_id)
                                else "hidden"))
    return ", ".join(parts) or "no browsable columns"
