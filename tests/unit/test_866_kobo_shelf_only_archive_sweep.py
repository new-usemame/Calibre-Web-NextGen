# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the "sync only selected shelves to Kobo" transition
(fork #866 / #1008).

``update_on_sync_shelfs`` runs when a user turns that setting on. It used to
also sweep books: for everything already synced to the device that it judged
ineligible, it wrote an ``ArchivedBook`` row and deleted the ``KoboSyncedBooks``
row. Three things were wrong with that.

1. Its membership query joined ``Shelf`` on ``user_id`` alone, never on
   ``Shelf.id == BookShelf.shelf``. One ordinary shelf anywhere in the account
   paired with EVERY synced book and satisfied ``kobo_sync == 0``, so books that
   WERE on the Kobo-sync shelf got swept. Reproduced live on a container.
2. ``KoboSyncedBooks`` is the *only* input the sync handler
   (``HandleSyncRequest``, cps/kobo.py) uses to emit the ``ChangedEntitlement``
   with ``archived=True`` that makes the device drop a book. Deleting the row up
   front means the device is never told — and a swept book is by definition
   outside the eligible set the handler queries, so nothing else picks it up.
   The books stay on the reader forever, which is @auspex's symptom.
3. The ``ArchivedBook`` rows hid those books from the user's own library in the
   web UI. A Kobo sync preference should not archive most of someone's library.

The handler already computes the same difference correctly, with the #468
fail-safe. So the transition now only records shelf tombstones and leaves book
reconciliation to the sync that can actually act on it.

Real in-memory SQLAlchemy session, not mocks — the original defect was in join
semantics and row lifetime, which only a real engine shows.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub

pytestmark = pytest.mark.unit


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _UbProxy:
    """``ub`` with the module's session swapped for the in-memory one."""

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        if name == "session_commit":
            return lambda *a, **kw: self.session.commit()
        return getattr(ub, name)


def _shelf(s, name, kobo_sync, user_id=1):
    shelf = ub.Shelf(name=name, user_id=user_id, kobo_sync=kobo_sync,
                     uuid=f"uuid-{name}", is_public=0)
    s.add(shelf)
    s.commit()
    return shelf


def _put_on_shelf(s, book_id, shelf):
    # ub's before_flush listener bumps ``BookShelf.ub_shelf.last_modified``, so
    # the relationship has to be populated, not just the FK column.
    link = ub.BookShelf(book_id=book_id, shelf=shelf.id, order=1)
    link.ub_shelf = shelf
    s.add(link)
    s.commit()


def _synced(s, *book_ids, user_id=1):
    for b in book_ids:
        s.add(ub.KoboSyncedBooks(user_id=user_id, book_id=b))
    s.commit()


@pytest.fixture
def transition(session):
    """``update_on_sync_shelfs`` bound to the in-memory session. Archiving is
    recorded rather than routed through ``current_user`` — the point of several
    tests below is that it is never called at all."""
    from cps import kobo_sync_status as mod

    archived = []

    def _run():
        with patch.object(mod, "ub", _UbProxy(session)), \
             patch.object(mod, "change_archived_books",
                          lambda book_id, state=None, message=None: archived.append(book_id)):
            mod.update_on_sync_shelfs(1)
        return archived

    return _run


def test_synced_rows_survive_so_the_device_gets_told_866(session, transition):
    """The tracking rows are the sync handler's only input for the removal
    command. Deleting them here strands the books on the device."""
    _shelf(session, "PlainShelf", kobo_sync=0)
    _synced(session, 7, 8)

    transition()

    remaining = {r.book_id for r in
                 session.query(ub.KoboSyncedBooks).filter_by(user_id=1).all()}
    assert remaining == {7, 8}, \
        "deleted the rows HandleSyncRequest needs to emit ChangedEntitlement(archived=True)"


def test_the_transition_does_not_archive_books_in_the_library_866(session, transition):
    """Turning on a Kobo sync preference must not hide books from the user's
    own library in the web UI."""
    kobo_shelf = _shelf(session, "KoboShelf", kobo_sync=1)
    _shelf(session, "PlainShelf", kobo_sync=0)
    _put_on_shelf(session, 2, kobo_shelf)
    _synced(session, 2, 3)

    assert transition() == []
    assert session.query(ub.ArchivedBook).count() == 0


def test_a_book_on_the_kobo_sync_shelf_is_never_touched_866(session, transition):
    """The reporter's shape: one Kobo-sync shelf, one ordinary shelf. The old
    query swept the Kobo-sync shelf's book purely because the ordinary shelf
    existed."""
    kobo_shelf = _shelf(session, "KoboShelf", kobo_sync=1)
    _shelf(session, "PlainShelf", kobo_sync=0)
    _put_on_shelf(session, 2, kobo_shelf)
    _synced(session, 2)

    transition()

    assert {r.book_id for r in session.query(ub.KoboSyncedBooks).all()} == {2}
    assert session.query(ub.ArchivedBook).count() == 0


def test_non_kobo_shelves_are_tombstoned_866(session, transition):
    """The half that IS this function's job: the device has to drop the
    collections for shelves that are no longer synced."""
    _shelf(session, "PlainA", kobo_sync=0)
    _shelf(session, "PlainB", kobo_sync=0)
    _shelf(session, "KoboShelf", kobo_sync=1)

    transition()

    archived_uuids = {r.uuid for r in
                      session.query(ub.ShelfArchive).filter_by(user_id=1).all()}
    assert archived_uuids == {"uuid-PlainA", "uuid-PlainB"}


def test_another_users_shelves_are_left_alone_866(session, transition):
    _shelf(session, "TheirPlainShelf", kobo_sync=0, user_id=2)

    transition()

    assert session.query(ub.ShelfArchive).count() == 0


def test_shelf_archive_rows_are_not_duplicated_on_repeat_866(session, transition):
    """Toggling the setting off and on repeatedly used to append a fresh
    archive row per non-synced shelf every time (a test account had built up
    47 rows for 2 shelves)."""
    _shelf(session, "PlainShelf", kobo_sync=0)

    transition()
    transition()
    transition()

    assert session.query(ub.ShelfArchive).filter_by(user_id=1).count() == 1


def test_handler_still_owns_book_reconciliation_866():
    """Source pin: the removal path must keep emitting the device command from
    KoboSyncedBooks before deleting the rows. If someone moves that logic, the
    transition above silently becomes a no-op for books."""
    import inspect
    from pathlib import Path

    kobo_py = (Path(__file__).resolve().parents[2] / "cps" / "kobo.py").read_text()
    assert "books_to_delete_ids" in kobo_py
    assert "compute_kobo_books_to_archive" in kobo_py
    assert "create_book_entitlement(book, archived=True)" in kobo_py

    import ast

    from cps import kobo_sync_status
    tree = ast.parse(inspect.getsource(kobo_sync_status.update_on_sync_shelfs))
    fn = tree.body[0]
    # Drop the docstring — it *describes* the removed behaviour by name.
    body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    code = "\n".join(ast.dump(node) for node in body)
    assert "KoboSyncedBooks" not in code, \
        "the setting transition must not touch synced-book tracking rows"
    assert "change_archived_books" not in code, \
        "the setting transition must not archive books in the library"
