# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for CWA #1307 — "books appear archived after sync and lose
annotations".

Reporters: @TheDarkSpock (CWA #1307), @bigbold1023 (who hit the same class of
bug in fork #468 and reported it again on our build). Symptom: intermittently,
after a Kobo sync, some already-downloaded books show the download arrow again;
re-downloading loses the annotations/highlights on them.

#468 closed the *loud* half of this — a magic-shelf membership query that RAISED
was swallowed as "the shelf is empty" and the books fell into the archive set.
This is the *silent* half, and it needs no exception at all:

``get_magic_shelf_book_ids_for_kobo`` collected membership by calling
``get_books_for_magic_shelf``, which takes the authoritative id list and then
hydrates it through a fresh ``CalibreDB`` session::

    books = cdb.session.query(db.Books).filter(db.Books.id.in_(page_ids)).all()
    book_map = {b.id: b for b in books}
    ordered_books = [book_map[bid] for bid in page_ids if bid in book_map]

The trailing ``if bid in book_map`` silently DROPS every id whose Books row that
session did not return. On a CWA build the calibre ``metadata.db`` is rewritten
underneath the app by auto-ingest, so a sync landing mid-reload gets a short
hydration — no exception, ``reliable`` stays ``True``, and the dropped books
leave ``allowed_book_ids``. The two-way-sync deletion path then emits
``ChangedEntitlement(archived=True)`` for them and deletes their
``KoboSyncedBooks`` rows; the device drops the local copy, re-downloads later as
a fresh entitlement, and the annotations go with the old copy. Intermittent, a
subset of books, annotation loss — exactly the reported shape.

Fix: the destructive path asks for the id LIST
(``get_book_ids_for_magic_shelf``), which is the membership. Hydration is
presentation and must not narrow a deletion decision.
"""

import ast
import inspect
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
KOBO_PY = REPO / "cps" / "kobo.py"
MAGIC_PY = REPO / "cps" / "magic_shelf.py"


def _load_func(path, name, glb=None):
    tree = ast.parse(path.read_text())
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if fn is None:
        raise AssertionError(f"{name} not found in {path}")
    ns = dict(glb or {})
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), ns)
    return ns[name]


class _LossyMagicShelf:
    """Stand-in for ``cps.magic_shelf`` where the shelf genuinely contains
    ``full_ids`` but object hydration only resolves ``hydrated_ids`` this round
    (metadata.db reload mid-sync). Neither call raises."""

    def __init__(self, full_ids, hydrated_ids):
        self.full_ids = list(full_ids)
        self.hydrated_ids = set(hydrated_ids)
        self.calls = []

    def get_book_ids_for_magic_shelf(self, shelf_id, raise_on_error=False, **kw):
        self.calls.append("ids")
        return list(self.full_ids), len(self.full_ids)

    def get_books_for_magic_shelf(self, shelf_id, page=1, page_size=None,
                                  raise_on_error=False, **kw):
        self.calls.append("books")
        book_map = {bid: mock.Mock(id=bid) for bid in self.full_ids
                    if bid in self.hydrated_ids}
        ordered = [book_map[bid] for bid in self.full_ids if bid in book_map]
        return ordered, len(ordered)


def _globals_with(magic_shelf_stub):
    config = mock.Mock()
    config.config_kobo_sync_magic_shelves = True
    shelf = mock.Mock()
    shelf.id = 11
    ub = mock.Mock()
    ub.session.query.return_value.filter_by.return_value.all.return_value = [shelf]
    log = mock.Mock()
    log.isEnabledFor.return_value = False
    return {"config": config, "ub": ub, "magic_shelf": magic_shelf_stub,
            "log": log, "logging": __import__("logging")}


# --------------------------------------------------------------------------
# The behavioural red/green
# --------------------------------------------------------------------------

def test_short_hydration_does_not_shrink_membership():
    """RED before the fix: book 3 is on the shelf but its Books row was not
    returned this round, so membership came back {1, 2} with reliable=True."""
    stub = _LossyMagicShelf(full_ids=[1, 2, 3], hydrated_ids={1, 2})
    f = _load_func(KOBO_PY, "get_magic_shelf_book_ids_for_kobo", _globals_with(stub))
    ids, reliable = f(1)
    assert reliable is True
    assert ids == {1, 2, 3}, (
        "magic-shelf membership must come from the id list, not from hydrated "
        "Book objects — a short hydration silently drops ids (CWA #1307)"
    )


def test_short_hydration_does_not_archive_the_dropped_book():
    """End of the chain: the dropped book must not reach the archive set."""
    stub = _LossyMagicShelf(full_ids=[1, 2, 3], hydrated_ids={1, 2})
    collect = _load_func(KOBO_PY, "get_magic_shelf_book_ids_for_kobo", _globals_with(stub))
    archive = _load_func(KOBO_PY, "compute_kobo_books_to_archive")

    allowed, reliable = collect(1)
    synced = {1, 2, 3}          # all three are on the device
    assert archive(synced, allowed, reliable) == set(), (
        "a book whose Books row was momentarily unreadable must not be archived "
        "off the device (CWA #1307 — forces a re-download and loses annotations)"
    )


def test_membership_still_reliable_and_correct_on_a_clean_round():
    stub = _LossyMagicShelf(full_ids=[7, 8], hydrated_ids={7, 8})
    f = _load_func(KOBO_PY, "get_magic_shelf_book_ids_for_kobo", _globals_with(stub))
    assert f(1) == ({7, 8}, True)


def test_genuine_query_failure_is_still_unreliable():
    """The #468 fail-safe must survive the switch to the id-list API."""
    magic_shelf = mock.Mock()
    magic_shelf.get_book_ids_for_magic_shelf.side_effect = Exception("database is locked")
    f = _load_func(KOBO_PY, "get_magic_shelf_book_ids_for_kobo", _globals_with(magic_shelf))
    ids, reliable = f(1)
    assert ids == set()
    assert reliable is False


def test_id_list_query_is_asked_to_raise():
    stub = _LossyMagicShelf(full_ids=[1], hydrated_ids={1})
    f = _load_func(KOBO_PY, "get_magic_shelf_book_ids_for_kobo", _globals_with(stub))
    f(1)
    assert stub.calls == ["ids"], (
        "the deletion path must query the id list only — hydrating Book objects "
        "per sync is both lossy and needless work"
    )


def test_collector_does_not_call_the_hydrating_helper():
    """Source pin: this is refactor-fragile, and re-introducing the hydrating
    call silently restores the bug (no test would raise)."""
    tree = ast.parse(KOBO_PY.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "get_magic_shelf_book_ids_for_kobo")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "get_book_ids_for_magic_shelf" in called
    assert "get_books_for_magic_shelf" not in called, (
        "get_magic_shelf_book_ids_for_kobo must not hydrate Book objects — "
        "hydration drops unresolvable ids and narrows a destructive decision"
    )


# --------------------------------------------------------------------------
# Why the hydrating helper is unsafe for this caller (documents the mechanism)
# --------------------------------------------------------------------------

def test_hydrating_helper_still_drops_unresolvable_ids():
    """Not a defect in get_books_for_magic_shelf — browse callers WANT to skip
    an unrenderable book. This pins the property so the reason the Kobo path
    must not use it stays visible."""
    src = MAGIC_PY.read_text()
    fn_src = src[src.index("def get_books_for_magic_shelf"):]
    fn_src = fn_src[:fn_src.index("\ndef ", 1)]
    assert "if bid in book_map" in fn_src, (
        "the hydrating helper's silent-drop was the #1307 mechanism; if it "
        "changed, revisit the Kobo membership collector's comment"
    )
