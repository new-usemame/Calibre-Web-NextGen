# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1254: every SPA list item names the shelves it sits on.

The classic grid drew a shelf tag on each cover; the new UI's cards had no way
to, because list items never carried membership. These run the real
``_rows_to_items`` against the real app-DB schema, so the visibility rule is
exercised in SQL rather than asserted about a mock.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit

READER, OTHER = 7, 8


def _book(book_id):
    return SimpleNamespace(
        id=book_id, title="Book %d" % book_id, authors=[], series=[],
        series_index=1.0, has_cover=0, data=[], tags=[],
        timestamp=None, last_modified=None,
    )


@pytest.fixture
def env(monkeypatch):
    from cps import ub
    from cps.api import books as mod

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    shelves = {
        1: ub.Shelf(id=1, name="Zebra", is_public=0, user_id=READER),
        2: ub.Shelf(id=2, name="Etagère Lolo", is_public=0, user_id=READER),
        3: ub.Shelf(id=3, name="Club picks", is_public=1, user_id=OTHER),
        4: ub.Shelf(id=4, name="Other's secret", is_public=0, user_id=OTHER),
    }
    links = []
    for book_id, shelf_id in [(10, 1), (10, 2), (10, 3), (10, 4), (11, 4), (99, 2)]:
        link = ub.BookShelf(book_id=book_id, shelf=shelf_id, order=1)
        link.ub_shelf = shelves[shelf_id]
        links.append(link)
    session.add_all([*shelves.values(), *links])
    session.commit()

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(mod, "config", SimpleNamespace(config_read_column=0))
    monkeypatch.setattr(mod, "read_statuses_for_books", lambda *a, **k: {})
    monkeypatch.setattr(mod.user_cover, "overrides_for_user", lambda *a, **k: {})

    def as_viewer(user_id):
        monkeypatch.setattr(mod, "current_user", SimpleNamespace(
            id=user_id, is_authenticated=True, is_anonymous=user_id is None))
        return {item["id"]: item["shelves"]
                for item in mod._rows_to_items([_book(10), _book(11), _book(12)])}

    yield as_viewer
    session.close()
    engine.dispose()


def test_items_name_the_viewers_shelves_in_shelf_order(env):
    shelves = env(READER)
    assert [s["name"] for s in shelves[10]] == ["Club picks", "Etagère Lolo", "Zebra"]
    assert shelves[10][1] == {"id": 2, "name": "Etagère Lolo"}
    assert shelves[12] == []


def test_another_users_private_shelf_never_appears(env):
    shelves = env(READER)
    assert "Other's secret" not in [s["name"] for s in shelves[10]]
    assert shelves[11] == []
    # The owner does see it.
    assert [s["name"] for s in env(OTHER)[11]] == ["Other's secret"]


def test_guest_sees_public_shelves_only(env):
    shelves = env(None)
    assert [s["name"] for s in shelves[10]] == ["Club picks"]
    assert shelves[11] == [] and shelves[12] == []


def test_unreadable_app_db_costs_the_tags_not_the_page(env, monkeypatch):
    """A failed membership query still returns the page, and rolls the shared
    session back so the next request is not left with a broken transaction."""
    from unittest.mock import MagicMock
    from sqlalchemy.exc import OperationalError
    from cps import ub

    broken = MagicMock()
    broken.query.side_effect = OperationalError("SELECT", {}, Exception("locked"))
    monkeypatch.setattr(ub, "session", broken)
    shelves = env(READER)
    assert shelves == {10: [], 11: [], 12: []}
    broken.rollback.assert_called_once_with()
