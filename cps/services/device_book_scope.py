# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stored device choices and their live, ownership-checked interpretation.

Selections survive shelf deletion in the JSON so the page can explain fallback.
They never mark an account shelf for sync or write any reading data.
"""

import json
from dataclasses import dataclass

from .. import ub

MODES = ("unset", "library", "my_library", "shelves")


class InvalidBookScope(ValueError):
    """A device choice the owner cannot make."""


def saved(device):
    raw = getattr(device, "book_scope", None)
    if raw is None:
        return {"mode": "unset", "shelf_ids": [], "magic_shelf_ids": []}
    return json.loads(raw)


def account_mode(user):
    from . import ereader_scope
    if ereader_scope.shelf_only(user):
        return "shelves"
    return "my_library" if ereader_scope.personal_library(user) else "library"


def validate(value, user, device, session):
    from . import ereader_scope
    if (device.kind not in ("kobo", "koreader") or not isinstance(value, dict)
            or value.get("mode") not in MODES):
        raise InvalidBookScope("invalid mode or device")
    mode = value["mode"]
    if mode == "my_library" and not ereader_scope.personal_library(user):
        raise InvalidBookScope("My Library is off")
    result = {"mode": mode, "shelf_ids": [], "magic_shelf_ids": []}
    if mode == "shelves":
        for key, model in (("shelf_ids", ub.Shelf), ("magic_shelf_ids", ub.MagicShelf)):
            ids = value.get(key, [])
            if (not isinstance(ids, list)
                    or any(type(sid) is not int or sid <= 0 for sid in ids)):
                raise InvalidBookScope("shelf ids must be positive integers")
            # Bind ownership, including public shelves, before accepting ids.
            owned = {row.id for row in session.query(model.id).filter(
                model.user_id == user.id)}
            if not set(ids) <= owned:
                raise InvalidBookScope("shelf unavailable")
            result[key] = sorted(set(ids))
        if not result["shelf_ids"] and not result["magic_shelf_ids"]:
            raise InvalidBookScope("choose a shelf")
    return result


@dataclass(frozen=True)
class Choice:
    mode: str
    shelves: tuple = ()
    magic_shelves: tuple = ()
    fallback: str = None

    @property
    def overrides_account(self):
        return self.mode != "unset" and self.fallback is None

    @property
    def revision_key(self):
        if not self.overrides_account:
            return None
        return [self.mode, [s.id for s in self.shelves],
                [s.id for s in self.magic_shelves]]


def resolve(user, device, *, session=None):
    session = session or ub.session
    if device is None or device.user_id != user.id:
        return Choice("unset")
    value = saved(device)
    mode = value["mode"]
    if mode != "shelves":
        return Choice(mode)
    selected = []
    unavailable = False
    for key, model in (("shelf_ids", ub.Shelf), ("magic_shelf_ids", ub.MagicShelf)):
        ids = value[key]
        rows = (session.query(model).filter(model.id.in_(ids))
                .order_by(model.id).all()) if ids else []
        unavailable |= any(row.user_id != user.id for row in rows)
        selected.append(tuple(row for row in rows if row.user_id == user.id))
    if not any(selected):
        return Choice(mode, fallback="shelf_unavailable" if unavailable else "shelf_deleted")
    # Partial loss keeps every remaining owned shelf, including an empty one.
    return Choice(mode, *selected)


def choices(user, session):
    from . import ereader_scope
    return {
        "shelves": [{"id": row.id, "name": row.name} for row in session.query(ub.Shelf)
                    .filter(ub.Shelf.user_id == user.id).order_by(ub.Shelf.name, ub.Shelf.id)],
        "magic_shelves": [{"id": row.id, "name": row.name} for row in session.query(ub.MagicShelf)
                          .filter(ub.MagicShelf.user_id == user.id)
                          .order_by(ub.MagicShelf.name, ub.MagicShelf.id)],
        "my_library": ereader_scope.personal_library(user),
    }


def payload(user, device, *, session=None, offered=None):
    if device.kind not in ("kobo", "koreader"):
        return None
    session = session or ub.session
    choice = resolve(user, device, session=session)
    account = account_mode(user)
    return {
        **saved(device), "account_mode": account,
        "effective_mode": choice.mode if choice.overrides_account else account,
        "fallback": {"reason": choice.fallback} if choice.fallback else None,
        "choices": offered if offered is not None else choices(user, session),
    }
