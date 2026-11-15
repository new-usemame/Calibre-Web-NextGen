# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared Classic, New UI and OPDS custom-column visibility.

Only reader choices are stored in cc_sidebar/show_cc_<id>. Without a choice,
Calibre-configured hierarchical columns are shown and atomic columns hidden.
A one-time compatibility upgrade preserves previously visible legacy trees,
without writing hidden defaults that could outlive an empty library.
"""
from functools import wraps

from flask import abort
from sqlalchemy.exc import SQLAlchemyError

from . import calibre_db, config, db, logger, ub

log = logger.create()
BROWSABLE_DATATYPES = frozenset(("text", "enumeration"))
CC_PAGE = "cc_sidebar"


def retryable_column_reads(view):
    """Primary browse data must report unavailable reads, never empty content."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except SQLAlchemyError:
            abort(503, description="Custom-column data temporarily unavailable")
    return wrapped


def browsable_columns(columns):
    return [column for column in columns
            if column.datatype in BROWSABLE_DATATYPES
            and not bool(getattr(column, "mark_for_delete", False))]


def load_browsable_columns():
    """None means an unavailable library; an empty list is a readable one."""
    try:
        calibre_db.ensure_session()
        calibre_db.session.query(db.CustomColumns.id).first()
        return browsable_columns(calibre_db.get_cc_columns(config, fail_on_error=True))
    except Exception:
        log.warning("Custom-column definitions unavailable", exc_info=True)
        return None


def is_cc_visible(user, col_id, fail_on_error=False):
    """An explicit reader choice wins; otherwise resolve Calibre's mode."""
    try:
        key = "show_cc_%d" % col_id
        # Explicit Guest choices are shared across anonymous clients. Session
        # preferences may customize only columns without a saved Guest choice.
        stored = ub.UserBase.get_view_property(user, CC_PAGE, key) if user.is_anonymous else None
        if not isinstance(stored, bool):
            stored = user.get_view_property(CC_PAGE, key)
        if isinstance(stored, bool):
            return stored
        return col_id in calibre_db.get_hierarchical_column_ids(fail_on_error=True) if fail_on_error else col_id in calibre_db.get_hierarchical_column_ids()
    except Exception:
        if fail_on_error:
            raise
        log.debug("Could not resolve custom-column visibility", exc_info=True)
        return False


def save_cc_visibility(user, options, form):
    """Store only checkbox changes, leaving unsaved derived defaults live."""
    for option in options:
        key = "show_cc_%d" % option["id"]
        initial = form.get("initial_" + key)
        if initial not in ("true", "false"):
            continue
        selected = form.get(key) == "on"
        if selected != (initial == "true"):
            user.set_view_property(CC_PAGE, key, selected, commit=False)


def backfill_existing_users():
    """Preserve legacy visible columns once, never freezing a hidden default.

    Every signup path derives the same defaults without a seeding hook.
    Existing explicit choices survive this upgrade. Failed or unavailable
    library reads leave the flag unset so a later startup can retry.
    """
    if getattr(config, "config_cc_visibility_seeded", False):
        return 0
    if hasattr(config, "config_calibre_dir") and not config.config_calibre_dir:
        # No earlier library could have advertised a column. A later library
        # must use its current Calibre defaults, not be treated as an upgrade.
        if hasattr(config, "save_fields"):
            config.save_fields(config_cc_visibility_seeded=True)
        else:
            config.config_cc_visibility_seeded = True
            config.save()
        return 0
    columns = load_browsable_columns()
    if columns is None:
        return 0
    changed = 0
    try:
        legacy_ids = calibre_db.get_legacy_hierarchical_column_ids(ttl=0, fail_on_error=True)
        last_legacy_user_id = getattr(config, "config_cc_visibility_legacy_user_id", None)
        for user in ub.session.query(ub.User).all():
            if last_legacy_user_id is not None and user.id > last_legacy_user_id:
                continue
            for column in columns:
                key = "show_cc_%d" % column.id
                if column.id in legacy_ids and user.get_view_property(CC_PAGE, key) is None:
                    user.set_view_property(CC_PAGE, key, True, commit=False)
                    changed += 1
        ub.session.commit()
        if hasattr(config, "save_fields"):
            config.save_fields(config_cc_visibility_seeded=True)
        else:
            config.config_cc_visibility_seeded = True
            config.save()
    except Exception:
        ub.session.rollback()
        log.error("Custom-column compatibility upgrade failed; will retry", exc_info=True)
        return 0
    log.info("Preserved %s legacy custom-column visibility choices", changed)
    return changed
