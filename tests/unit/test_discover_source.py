# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover-source privacy and predicate behavior (#1229)."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select


@pytest.mark.unit
def test_manual_shelf_membership_uses_attached_app_database():
    """Membership is an attached app.db subquery, not a main-db table join."""
    from cps import db
    from cps.services import discover_source

    source = SimpleNamespace(id=12)
    user = SimpleNamespace(id=4, is_authenticated=True, is_anonymous=False)
    predicate = discover_source._record_filter("shelf", source, user)

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS app_settings")
        connection.exec_driver_sql("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        # A tempting unqualified table name would bind to this same-named
        # shadow table on some connections and select the wrong book.
        connection.exec_driver_sql(
            "CREATE TABLE main.book_shelf_link "
            "(book_id INTEGER NOT NULL, shelf INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE app_settings.book_shelf_link "
            "(book_id INTEGER NOT NULL, shelf INTEGER NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO books VALUES (1), (2), (3)")
        connection.exec_driver_sql("INSERT INTO main.book_shelf_link VALUES (2, 12)")
        connection.exec_driver_sql(
            "INSERT INTO app_settings.book_shelf_link VALUES (1, 12), (3, 13)"
        )
        actual = connection.execute(select(db.Books.id).where(predicate)).scalars().all()

    assert actual == [1]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source_owner", "is_public", "viewer_id", "available"),
    [(7, 0, 7, True), (7, 0, 8, False), (7, 1, 8, True), (None, 0, 8, False)],
)
def test_source_record_is_owner_or_public_only(source_owner, is_public, viewer_id, available):
    from cps import ub
    from cps.services import discover_source

    record = SimpleNamespace(user_id=source_owner, is_public=is_public)

    class Query:
        def filter(self, *_criteria):
            return self

        def first(self):
            return record

    user = SimpleNamespace(id=viewer_id, is_authenticated=True, is_anonymous=False)
    with patch.object(ub, "session", SimpleNamespace(query=lambda _model: Query())):
        found = discover_source._source_record("shelf", 12, user)

    assert (found is not None) is available


@pytest.mark.unit
def test_smart_source_rejects_partial_or_empty_invalid_rule_trees():
    """One unsupported branch cannot be silently skipped into a broader feed."""
    from cps import db, magic_shelf
    from cps.services import discover_source

    valid = {"id": "title", "operator": "contains", "value": "paper"}
    invalid = {"id": "missing_field", "operator": "equal", "value": "x"}

    with patch.object(magic_shelf, "build_filter_from_rule", side_effect=[db.Books.id > 0, None]):
        assert discover_source._strict_smart_shelf_filter(
            {"condition": "AND", "rules": [valid, invalid]}, user_id=7
        ) is None

    with patch.object(magic_shelf, "build_filter_from_rule", return_value=db.Books.id > 0):
        assert discover_source._strict_smart_shelf_filter(
            {"condition": "XOR", "rules": [valid]}, user_id=7
        ) is None
        assert discover_source._strict_smart_shelf_filter(
            {"condition": "AND", "rules": []}, user_id=7
        ) is None


@pytest.mark.unit
def test_real_smart_rule_builder_invalid_branch_cannot_widen_source():
    """An unsupported saved rule is rejected alongside a valid rule."""
    from cps.services import discover_source

    predicate = discover_source._strict_smart_shelf_filter(
        {
            "condition": "OR",
            "rules": [
                {"id": "title", "operator": "contains", "value": "rare"},
                {"id": "not_a_field", "operator": "equal", "value": "anything"},
            ],
        },
        user_id=7,
    )

    assert predicate is None


@pytest.mark.unit
def test_hidden_public_smart_shelf_is_unavailable_to_discover():
    from cps import ub
    from cps.services import discover_source

    record = SimpleNamespace(id=19, user_id=3, is_public=1)

    class Query:
        def filter(self, *_criteria):
            return self

        def first(self):
            return record

    user = SimpleNamespace(id=8, is_authenticated=True, is_anonymous=False)
    with patch.object(ub, "session", SimpleNamespace(query=lambda _model: Query())), \
            patch.object(discover_source.magic_shelf, "get_visible_magic_shelves_for_user", return_value=[]):
        assert discover_source._source_record("smart", 19, user) is None


@pytest.mark.unit
def test_request_filter_cache_can_be_invalidated_after_a_preference_write():
    from flask import Flask
    from sqlalchemy import true
    from cps.services import discover_source

    user = SimpleNamespace(
        id=8, is_authenticated=True, is_anonymous=False,
        get_view_property=lambda _page, _key: "shelf:12",
    )
    record = SimpleNamespace(id=12)
    app = Flask(__name__)
    with app.test_request_context("/discover"):
        with patch.object(discover_source, "_source_record", return_value=record) as lookup, \
                patch.object(discover_source, "_record_filter", return_value=true()):
            assert discover_source.filter_for(user)[1] is True
            assert discover_source.filter_for(user)[1] is True
            assert lookup.call_count == 1

            discover_source.clear_filter_cache()
            assert discover_source.filter_for(user)[1] is True
            assert lookup.call_count == 2


@pytest.mark.unit
def test_missing_saved_source_is_an_explicit_empty_filter():
    """Deleted/private saved sources never degrade to an all-books predicate."""
    from cps import db
    from cps.services import discover_source

    user = SimpleNamespace(
        id=8,
        is_authenticated=True,
        is_anonymous=False,
        get_view_property=lambda page, prop: "shelf:12",
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO books VALUES (1), (2)")
        with patch.object(discover_source, "_source_record", return_value=None):
            predicate, available = discover_source.filter_for(user)
        rows = connection.execute(select(db.Books.id).where(predicate)).scalars().all()

    assert available is False
    assert rows == []


@pytest.mark.unit
def test_discover_source_commit_failure_rolls_back_and_returns_unavailable():
    from flask import Flask
    from sqlalchemy.exc import OperationalError
    from cps.api import discover_source as api

    events = []
    user = SimpleNamespace(set_view_property=lambda *args, **kwargs: events.append("set"))
    session = SimpleNamespace(
        commit=lambda: (_ for _ in ()).throw(OperationalError("commit", {}, RuntimeError("disk"))),
        rollback=lambda: events.append("rollback"),
    )
    app = Flask(__name__)
    with app.test_request_context(
        "/api/v1/account/discover-source", method="PUT", json={"source": "shelf:12"}
    ), patch.object(api, "current_user", user), \
            patch.object(api, "_require_real_user", return_value=None), \
            patch.object(api.discover_source, "validate_source", return_value="shelf:12"), \
            patch.object(api.ub, "session", session):
        response, status = api.account_discover_source()

    assert status == 503
    assert response.get_json()["error"]["code"] == "source_unavailable"
    assert events == ["set", "rollback"]
