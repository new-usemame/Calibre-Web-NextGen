# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Saved cover designs: the reader's own presets, and the library's.

A preset is a name and a design. The shipped ones are a starting point, not the
vocabulary: a designer whose presets cannot be added to or removed from is a
row of buttons somebody else chose. So this module gives every user their own
list, lets an admin publish one to the whole instance, and — importantly — lets
any user hide a preset they cannot delete, because a dropdown full of designs
they will never use is the same problem in a different shape.

Three kinds of id arrive on the wire, and which one it is decides who may do
what to it:

``classic``        a builtin. Nobody can edit or delete it; a delete hides it
                   for the user who asked, and ``restore`` brings it back.
``user-12``        a row someone saved for themselves. Only its owner ever sees
                   it, so only its owner can touch it.
``library-12``     a row an admin published. Everyone sees it; its owner and any
                   admin can edit or delete it for real, and everyone else can
                   hide it for themselves.

Designs are stored as JSON and re-resolved on the way out, leniently: a preset
saved months ago must still open after a font is uninstalled, so an id that no
longer exists falls back rather than taking the whole catalogue down.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy.exc import SQLAlchemyError

from .. import logger, ub
from .cover_generator import (
    CoverGenerationError, MAX_PRESET_NAME_LENGTH, MAX_PRESETS_PER_USER, PRESETS,
    builtin_presets, resolve_design,
)

log = logger.create()

# A saved preset's id is its scope and its row, so the route can tell at a glance
# whether it is looking at a builtin (no separator) or a row, without a lookup.
_SAVED_ID = re.compile(r"^(user|library)-([0-9]{1,12})$")

# Control characters in a name would land in the SPA's dropdown and in the admin
# settings page; the design is already length-capped by its own validator.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

MAX_DESIGN_BYTES = 8 * 1024


def _session():
    return ub.session


def preset_id(row) -> str:
    return "%s-%s" % (row.scope, row.id)


def parse_preset_id(value: str):
    """``(scope, row id)`` for a saved preset, or ``(None, None)`` for a builtin."""
    match = _SAVED_ID.match(str(value or ""))
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def validate_name(value) -> str:
    name = str(value or "").strip()
    if not name:
        raise CoverGenerationError("invalid_name", "A preset needs a name.")
    if len(name) > MAX_PRESET_NAME_LENGTH:
        raise CoverGenerationError(
            "invalid_name", "A preset name can be at most %s characters." % MAX_PRESET_NAME_LENGTH)
    if _CONTROL_CHARACTERS.search(name):
        raise CoverGenerationError("invalid_name", "That preset name has characters we can't store.")
    return name


def _serialise_design(design, binaries_dir: str = "") -> str:
    """Validate a design the same way a preview does, then store it resolved.

    Storing the resolved design rather than the fragment the client sent is what
    makes a preset stable: it keeps meaning the same cover when the shipped
    defaults change underneath it.
    """
    spec = resolve_design(design, strict=True, binaries_dir=binaries_dir)
    encoded = json.dumps(spec.to_dict(), separators=(",", ":"))
    if len(encoded) > MAX_DESIGN_BYTES:  # pragma: no cover - a resolved design is ~500 bytes
        raise CoverGenerationError("invalid_design", "That design is too large to save.")
    return encoded


def _row_to_dict(row, binaries_dir: str = "") -> dict:
    try:
        stored = json.loads(row.design)
    except (TypeError, ValueError):
        log.warning("cover_design_presets: preset %s has unreadable JSON", row.id)
        stored = {}
    # Lenient on the way out: a font uninstalled since this was saved falls back
    # to a generic rather than making the whole catalogue fail to load.
    design = resolve_design(stored, strict=False, binaries_dir=binaries_dir).to_dict()
    return {"id": preset_id(row), "name": row.name, "label": row.name, "design": design,
            "builtin": False, "scope": row.scope, "position": row.position}


def hidden_keys(user_id: int) -> set:
    """The preset ids this user has hidden."""
    try:
        rows = _session().query(ub.HiddenCoverDesignPreset).filter(
            ub.HiddenCoverDesignPreset.user_id == user_id).all()
    except SQLAlchemyError as error:  # pragma: no cover - defensive
        log.warning("cover_design_presets: could not read hidden presets: %s", error)
        _session().rollback()
        return set()
    return {row.preset_key for row in rows}


def _visible_rows(user_id: int) -> list:
    session = _session()
    return session.query(ub.CoverDesignPreset).filter(
        (ub.CoverDesignPreset.scope == "library")
        | (ub.CoverDesignPreset.user_id == user_id)
    ).order_by(ub.CoverDesignPreset.position, ub.CoverDesignPreset.id).all()


def list_presets(user_id: int, binaries_dir: str = "") -> dict:
    """``{"presets": [...], "hidden": [...]}`` — builtin, then library, then own.

    That order is the one the panel shows: the designs everybody has, then the
    ones this library added, then the reader's own at the bottom where they
    accumulate.
    """
    hidden = hidden_keys(user_id)
    presets = [entry for entry in builtin_presets(binaries_dir) if entry["id"] not in hidden]
    try:
        rows = _visible_rows(user_id)
    except SQLAlchemyError as error:  # pragma: no cover - defensive
        log.warning("cover_design_presets: could not read presets: %s", error)
        _session().rollback()
        rows = []
    library = [row for row in rows if row.scope == "library"]
    own = [row for row in rows if row.scope != "library"]
    for row in library + own:
        if preset_id(row) in hidden:
            continue
        presets.append(_row_to_dict(row, binaries_dir))
    return {"presets": presets, "hidden": sorted(hidden)}


def _row(user_id: int, scope: str, row_id: int):
    session = _session()
    row = session.query(ub.CoverDesignPreset).filter(
        ub.CoverDesignPreset.id == row_id,
        ub.CoverDesignPreset.scope == scope).first()
    if row is None:
        raise CoverGenerationError("unknown_preset", "That preset no longer exists.")
    # A user preset that is not yours is not yours to know about, let alone edit.
    if scope == "user" and row.user_id != user_id:
        raise CoverGenerationError("unknown_preset", "That preset no longer exists.")
    return row


def create_preset(user_id: int, is_admin: bool, name, design, scope: str = "user",
                  binaries_dir: str = "") -> dict:
    scope = "library" if str(scope or "user") == "library" else "user"
    if scope == "library" and not is_admin:
        raise CoverGenerationError(
            "forbidden", "Only an administrator can save a design for the whole library.")
    clean_name = validate_name(name)
    encoded = _serialise_design(design, binaries_dir)

    session = _session()
    try:
        existing = session.query(ub.CoverDesignPreset).filter(
            ub.CoverDesignPreset.user_id == user_id).count()
    except SQLAlchemyError as error:  # pragma: no cover - defensive
        session.rollback()
        raise CoverGenerationError("storage_failed", str(error))
    if existing >= MAX_PRESETS_PER_USER:
        raise CoverGenerationError(
            "too_many_presets",
            "You already have %s saved designs; delete one to save another."
            % MAX_PRESETS_PER_USER)

    row = ub.CoverDesignPreset(user_id=user_id, scope=scope, name=clean_name,
                               design=encoded, position=existing)
    session.add(row)
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        if "unique" in str(error).lower():
            raise CoverGenerationError("duplicate_name", "You already have a design with that name.")
        raise CoverGenerationError("storage_failed", str(error))
    return _row_to_dict(row, binaries_dir)


def update_preset(user_id: int, is_admin: bool, identifier: str, name=None, design=None,
                  binaries_dir: str = "") -> dict:
    scope, row_id = parse_preset_id(identifier)
    if scope is None:
        raise CoverGenerationError(
            "builtin_preset", "The designs that come with Calibre-Web can't be renamed.")
    row = _row(user_id, scope, row_id)
    if row.user_id != user_id and not (scope == "library" and is_admin):
        raise CoverGenerationError("forbidden", "That design belongs to somebody else.")

    if name is not None:
        row.name = validate_name(name)
    if design is not None:
        row.design = _serialise_design(design, binaries_dir)
    row.updated_at = datetime.now(timezone.utc)

    session = _session()
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        if "unique" in str(error).lower():
            raise CoverGenerationError("duplicate_name", "You already have a design with that name.")
        raise CoverGenerationError("storage_failed", str(error))
    return _row_to_dict(row, binaries_dir)


def _hide(user_id: int, key: str) -> None:
    session = _session()
    existing = session.query(ub.HiddenCoverDesignPreset).filter(
        ub.HiddenCoverDesignPreset.user_id == user_id,
        ub.HiddenCoverDesignPreset.preset_key == key).first()
    if existing is not None:
        return
    session.add(ub.HiddenCoverDesignPreset(user_id=user_id, preset_key=key))
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise CoverGenerationError("storage_failed", str(error))


def delete_preset(user_id: int, is_admin: bool, identifier: str) -> str:
    """Delete or hide *identifier*; returns "deleted" or "hidden"."""
    scope, row_id = parse_preset_id(identifier)
    if scope is None:
        if identifier not in PRESETS:
            raise CoverGenerationError("unknown_preset", "That preset no longer exists.")
        _hide(user_id, identifier)
        return "hidden"

    row = _row(user_id, scope, row_id)
    if row.user_id == user_id or (scope == "library" and is_admin):
        session = _session()
        session.delete(row)
        try:
            session.commit()
        except SQLAlchemyError as error:  # pragma: no cover - defensive
            session.rollback()
            raise CoverGenerationError("storage_failed", str(error))
        return "deleted"
    _hide(user_id, identifier)
    return "hidden"


def restore_preset(user_id: int, identifier: str, binaries_dir: str = "") -> dict:
    """Un-hide a preset this user hid. Idempotent."""
    session = _session()
    rows = session.query(ub.HiddenCoverDesignPreset).filter(
        ub.HiddenCoverDesignPreset.user_id == user_id,
        ub.HiddenCoverDesignPreset.preset_key == identifier).all()
    for row in rows:
        session.delete(row)
    if rows:
        try:
            session.commit()
        except SQLAlchemyError as error:  # pragma: no cover - defensive
            session.rollback()
            raise CoverGenerationError("storage_failed", str(error))

    scope, row_id = parse_preset_id(identifier)
    if scope is None:
        entry = next((item for item in builtin_presets(binaries_dir)
                      if item["id"] == identifier), None)
        if entry is None:
            raise CoverGenerationError("unknown_preset", "That preset no longer exists.")
        return entry
    return _row_to_dict(_row(user_id, scope, row_id), binaries_dir)


def reorder_presets(user_id: int, identifiers: Sequence[str]) -> None:
    """Persist the reader's own ordering of their saved presets."""
    session = _session()
    positions = {}
    for index, identifier in enumerate(identifiers or ()):
        scope, row_id = parse_preset_id(identifier)
        if scope is not None:
            positions[row_id] = index
    if not positions:
        return
    try:
        rows = session.query(ub.CoverDesignPreset).filter(
            ub.CoverDesignPreset.id.in_(list(positions))).all()
        for row in rows:
            if row.user_id == user_id:
                row.position = positions[row.id]
        session.commit()
    except SQLAlchemyError as error:  # pragma: no cover - defensive
        session.rollback()
        raise CoverGenerationError("storage_failed", str(error))
