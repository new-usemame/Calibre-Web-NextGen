# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for fork issue #667 (reporter @chloeroform).

Symptom: the new-UI (SPA) sidebar shows *every* magic (smart) shelf, ignoring
the per-user "Magic Shelves Visibility" settings in ``/me``. Unchecking a
magic shelf there writes a row to ``hidden_magic_shelf_templates``; the classic
sidebar (cps/__init__.py) and OPDS both filter on it, but the SPA endpoint
``GET /api/v1/magicshelves`` rolled its own ``ub.session.query(MagicShelf)``
with only an ownership/public clause — so hidden shelves reappeared in /app.

ROOT CAUSE: two implementations of "which magic shelves can this user see".
The canonical one, ``magic_shelf.get_visible_magic_shelves_for_user`` (also
used by admin.py and mirrored inline by the classic sidebar), applies the
hidden-template filtering; the SPA endpoint didn't call it.

FIX (cps/api/magicshelves.py): the endpoint now delegates to
``get_visible_magic_shelves_for_user`` for authenticated callers — one source
of truth. Anonymous callers (no user row to hide against) keep the public-only
path.

Two guards, matching the repo's pattern for app-init-bound endpoint functions
(behavioural model on a real in-memory session + AST/source pin — cf.
test_468_magic_shelf_membership_timestamp.py):

  1. Behavioural — the canonical engine excludes a hidden system-template
     shelf and a hidden public shelf while keeping the visible ones. This pins
     the contract the endpoint now depends on.
  2. Source-pin — ``list_magic_shelves`` calls
     ``get_visible_magic_shelves_for_user`` and no longer builds its own
     visibility query, so the wiring can't silently regress.
"""
import ast
import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


_API = pathlib.Path(__file__).resolve().parents[2] / "cps" / "api" / "magicshelves.py"


def _fresh_ub_session():
    """An isolated in-memory ub session with just the two tables we need."""
    from cps import ub

    engine = create_engine("sqlite://")
    ub.MagicShelf.__table__.create(engine)
    ub.HiddenMagicShelfTemplate.__table__.create(engine)
    return sessionmaker(bind=engine)()


@pytest.mark.unit
def test_visible_engine_filters_hidden_shelves(monkeypatch):
    """The canonical engine drops shelves the user hid, keeps the rest."""
    from cps import ub, magic_shelf

    sess = _fresh_ub_session()
    monkeypatch.setattr(ub, "session", sess)

    uid, other = 1, 2
    # A system-template shelf owned by the user whose template_key we will hide.
    tmpl_key = next(iter(magic_shelf.SYSTEM_SHELF_TEMPLATES))
    tmpl_name = magic_shelf.SYSTEM_SHELF_TEMPLATES[tmpl_key]["name"]

    hidden_sys = ub.MagicShelf(name=tmpl_name, is_system=True, is_public=0, user_id=uid)
    visible_own = ub.MagicShelf(name="My Reading Pile", is_system=False, is_public=0, user_id=uid)
    hidden_pub = ub.MagicShelf(name="Someone's Public", is_system=False, is_public=1, user_id=other)
    visible_pub = ub.MagicShelf(name="Kept Public", is_system=False, is_public=1, user_id=other)
    sess.add_all([hidden_sys, visible_own, hidden_pub, visible_pub])
    sess.commit()

    sess.add(ub.HiddenMagicShelfTemplate(user_id=uid, template_key=tmpl_key))
    sess.add(ub.HiddenMagicShelfTemplate(user_id=uid, shelf_id=hidden_pub.id))
    sess.commit()

    names = {s.name for s in magic_shelf.get_visible_magic_shelves_for_user(uid)}

    assert tmpl_name not in names, "hidden system-template shelf must be filtered out"
    assert "Someone's Public" not in names, "hidden public shelf must be filtered out"
    assert "My Reading Pile" in names, "the user's own shelf must stay visible"
    assert "Kept Public" in names, "a non-hidden public shelf must stay visible"


def _list_magic_shelves_body():
    tree = ast.parse(_API.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "list_magic_shelves":
            return ast.get_source_segment(_API.read_text(), node)
    raise AssertionError("list_magic_shelves not found in cps/api/magicshelves.py")


@pytest.mark.unit
def test_endpoint_delegates_to_canonical_engine():
    """/api/v1/magicshelves must route through the shared visibility engine."""
    body = _list_magic_shelves_body()
    assert "get_visible_magic_shelves_for_user" in body, (
        "list_magic_shelves must delegate to the canonical visibility engine "
        "so it honours hidden_magic_shelf_templates (#667)"
    )


@pytest.mark.unit
def test_endpoint_has_no_bespoke_visibility_query():
    """The authenticated path must not rebuild its own MagicShelf visibility
    query — that bypass is exactly what let hidden shelves through."""
    body = _list_magic_shelves_body()
    # The old bug: an or_()-based ownership/public filter fed straight to the
    # query. The public-only anon fallback (a plain is_public==1 filter) is
    # fine; a resurrected or_() visibility clause is the regression.
    assert "or_(" not in body, (
        "list_magic_shelves must not rebuild an or_() visibility filter; "
        "delegate to get_visible_magic_shelves_for_user instead (#667)"
    )
