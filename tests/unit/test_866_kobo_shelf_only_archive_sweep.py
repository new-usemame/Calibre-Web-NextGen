# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the shelf-only Kobo archive sweep (fork #866 / #1008).

``update_on_sync_shelfs`` runs when a user turns on "Sync only selected shelves
to Kobo". It archives everything already synced to their device that their
Kobo-sync shelves do not make eligible, so the device drops those books on the
next sync.

The pre-#1008 query decided membership with:

    .join(BookShelf, KoboSyncedBooks.book_id == BookShelf.book_id, isouter=True)
    .join(Shelf, Shelf.user_id == user_id, isouter=True)
    .filter(or_(Shelf.kobo_sync == 0, Shelf.kobo_sync == None))

There is no ``Shelf.id == BookShelf.shelf`` condition — the Shelf join is on the
owner alone. So a single ordinary shelf anywhere in the user's account pairs
with EVERY synced book and satisfies ``kobo_sync == 0``, and books that ARE on
the Kobo-sync shelf get archived off the device. Reproduced live before the fix:
a book on a kobo_sync shelf was archived purely because an unrelated plain shelf
existed on the same account.

These use a real in-memory SQLAlchemy session rather than mocks — the defect was
in the join semantics, which only a real engine can catch.
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
def sweep(session):
    """``update_on_sync_shelfs`` bound to the in-memory session, with the
    magic-shelf lookup stubbed (covered separately below) and archiving
    recorded instead of routed through ``current_user``."""
    from cps import kobo_sync_status as mod

    archived = []

    def _fake_archive(book_id, state=None, message=None):
        archived.append(book_id)
        return True

    def _run(magic=(set(), True)):
        with patch.object(mod, "ub", _UbProxy(session)), \
             patch.object(mod, "change_archived_books", _fake_archive), \
             patch("cps.kobo.get_magic_shelf_book_ids_for_kobo", return_value=magic):
            mod.update_on_sync_shelfs(1)
        return archived

    return _run


class _UbProxy:
    """``ub`` with the module's session swapped for the in-memory one."""

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        if name == "session_commit":
            return lambda *a, **kw: self.session.commit()
        return getattr(ub, name)


def test_a_book_on_the_kobo_sync_shelf_survives_the_sweep_866(session, sweep):
    """The reporter's shape: one Kobo-sync shelf, one ordinary shelf. The book
    on the Kobo-sync shelf must stay on the device."""
    kobo_shelf = _shelf(session, "KoboShelf", kobo_sync=1)
    _shelf(session, "PlainShelf", kobo_sync=0)
    _put_on_shelf(session, 2, kobo_shelf)
    _synced(session, 2, 3)

    archived = sweep()

    assert 2 not in archived, "archived a book that is ON the Kobo-sync shelf"
    assert archived == [3]
    remaining = {r.book_id for r in
                 session.query(ub.KoboSyncedBooks).filter_by(user_id=1).all()}
    assert remaining == {2}


def test_a_book_on_both_shelf_types_survives_866(session, sweep):
    """Membership in ANY Kobo-sync shelf wins — being on an ordinary shelf too
    is not a reason to pull the book off the device."""
    kobo_shelf = _shelf(session, "KoboShelf", kobo_sync=1)
    plain = _shelf(session, "PlainShelf", kobo_sync=0)
    _put_on_shelf(session, 5, kobo_shelf)
    _put_on_shelf(session, 5, plain)
    _synced(session, 5)

    assert sweep() == []


def test_books_on_no_kobo_sync_shelf_are_archived_866(session, sweep):
    """The sweep still does its job — that is the point of the setting."""
    _shelf(session, "PlainShelf", kobo_sync=0)
    _synced(session, 7, 8)

    assert sorted(sweep()) == [7, 8]
    assert session.query(ub.KoboSyncedBooks).filter_by(user_id=1).count() == 0


def test_another_users_kobo_shelf_does_not_protect_our_books_866(session, sweep):
    """Eligibility is per-user: someone else's Kobo-sync shelf must not keep
    our synced book on our device."""
    other = _shelf(session, "TheirKoboShelf", kobo_sync=1, user_id=2)
    _put_on_shelf(session, 9, other)
    _synced(session, 9)

    assert sweep() == [9]


def test_magic_shelf_membership_protects_a_book_866(session, sweep):
    """Kobo-sync magic shelves deliver books too, so the sweep must spare them
    — otherwise a magic-shelf user loses the books they selected."""
    _shelf(session, "PlainShelf", kobo_sync=0)
    _synced(session, 11, 12)

    assert sweep(magic=({11}, True)) == [12]


def test_unreliable_magic_membership_archives_nothing_866(session, sweep):
    """#468 fail-safe: a failed membership query must not look like an empty
    shelf. Skip the sweep rather than archive books the user selected."""
    _shelf(session, "PlainShelf", kobo_sync=0)
    _synced(session, 13, 14)

    assert sweep(magic=(set(), False)) == []
    assert session.query(ub.KoboSyncedBooks).filter_by(user_id=1).count() == 2


def test_shelf_archive_rows_are_not_duplicated_on_repeat_866(session, sweep):
    """Toggling the setting off and on repeatedly used to append a fresh
    archive row per non-synced shelf every time."""
    _shelf(session, "PlainShelf", kobo_sync=0)
    _synced(session, 15)

    sweep()
    sweep()
    sweep()

    assert session.query(ub.ShelfArchive).filter_by(user_id=1).count() == 1
