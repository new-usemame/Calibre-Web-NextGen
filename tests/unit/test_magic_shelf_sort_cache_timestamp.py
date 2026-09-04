# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo membership timestamps must be independent of Magic Shelf sort keys."""

from datetime import datetime
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


class _BookIdQuery:
    def __init__(self, book_ids):
        self.book_ids = book_ids

    def with_entities(self, _book_id_column):
        return self

    def all(self):
        return [(book_id,) for book_id in self.book_ids]


def test_identical_membership_under_another_sort_key_does_not_advance_kobo_timestamp(
        monkeypatch):
    from cps import calibre_db, kobo, magic_shelf, ub

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(
        engine,
        tables=[ub.MagicShelf.__table__, ub.MagicShelfCache.__table__],
    )
    session = sessionmaker(bind=engine)()
    original_created_at = datetime(2026, 9, 4, 1, 0, 0)
    try:
        session.add(ub.MagicShelf(
            id=7,
            name="On Kobo",
            user_id=42,
            kobo_sync=True,
        ))
        session.add(ub.MagicShelfCache(
            shelf_id=7,
            user_id=42,
            sort_param="stored",
            book_ids=[11, 22],
            total_count=2,
            created_at=original_created_at,
        ))
        session.commit()

        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(
            magic_shelf,
            "current_user",
            SimpleNamespace(is_authenticated=True, id=42),
        )
        monkeypatch.setattr(calibre_db, "_desktop_compat", False)
        monkeypatch.setattr(
            magic_shelf,
            "build_book_query_for_magic_shelf",
            lambda *_args, **_kwargs: (_BookIdQuery([22, 11]), SimpleNamespace()),
        )
        monkeypatch.setattr(
            kobo.config,
            "config_kobo_sync_magic_shelves",
            True,
            raising=False,
        )

        timestamp_before_resort = kobo.get_magic_shelf_membership_added_at(42)
        magic_shelf.get_book_ids_for_magic_shelf(
            7,
            sort_param="cc-12-asc",
        )

        cache_rows = session.query(ub.MagicShelfCache).filter_by(
            shelf_id=7,
            user_id=42,
        ).order_by(ub.MagicShelfCache.sort_param).all()
        assert [row.sort_param for row in cache_rows] == ["cc-12-asc", "stored"]
        assert {frozenset(row.book_ids) for row in cache_rows} == {frozenset((11, 22))}
        assert kobo.get_magic_shelf_membership_added_at(42) == timestamp_before_resort
    finally:
        session.close()
        engine.dispose()


def test_reverted_membership_starts_a_new_kobo_timestamp_generation(monkeypatch):
    from cps import calibre_db, kobo, magic_shelf, ub

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(
        engine,
        tables=[ub.MagicShelf.__table__, ub.MagicShelfCache.__table__],
    )
    session = sessionmaker(bind=engine)()
    original_created_at = datetime(2026, 8, 1, 0, 0, 0)
    membership = [2, 3]
    try:
        session.add(ub.MagicShelf(
            id=7,
            name="On Kobo",
            user_id=42,
            kobo_sync=True,
        ))
        for sort_param in ("stored", "cc-12-asc"):
            session.add(ub.MagicShelfCache(
                shelf_id=7,
                user_id=42,
                sort_param=sort_param,
                book_ids=[1, 2],
                total_count=2,
                created_at=original_created_at,
            ))
        session.commit()

        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(
            magic_shelf,
            "current_user",
            SimpleNamespace(is_authenticated=True, id=42),
        )
        monkeypatch.setattr(calibre_db, "_desktop_compat", False)
        monkeypatch.setattr(
            magic_shelf,
            "build_book_query_for_magic_shelf",
            lambda *_args, **_kwargs: (_BookIdQuery(membership), SimpleNamespace()),
        )
        monkeypatch.setattr(
            kobo.config,
            "config_kobo_sync_magic_shelves",
            True,
            raising=False,
        )

        magic_shelf.get_book_ids_for_magic_shelf(7, sort_param="new")
        changed_membership_at = kobo.get_magic_shelf_membership_added_at(42)
        assert changed_membership_at > original_created_at

        time.sleep(0.01)
        membership[:] = [1, 2]
        magic_shelf.get_book_ids_for_magic_shelf(7, sort_param="stored")

        reverted_membership_at = kobo.get_magic_shelf_membership_added_at(42)
        assert reverted_membership_at > changed_membership_at
        cache_rows = session.query(ub.MagicShelfCache).filter_by(
            shelf_id=7,
            user_id=42,
        ).all()
        assert [(row.sort_param, set(row.book_ids)) for row in cache_rows] == [
            ("stored", {1, 2}),
        ]
    finally:
        session.close()
        engine.dispose()
