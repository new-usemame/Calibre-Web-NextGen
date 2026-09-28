# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral regression coverage for #467 and the adjacent #635 UI drift.

The rule engine, Classic QueryBuilder, and React builder used to carry three
independent field/operator lists.  The v4.1.11 implementation added relative
date support to the engine and Classic UI but omitted the React list, so users
of the New UI could not select Publication Date or Date Added.  A later source
token test added those two strings to React, but left the duplicated contract
and every other #635 parity gap intact.

These tests pin one backend-owned schema and prove that its two relative-date
rules select real database rows, rather than merely compiling an expression.
"""
from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, magic_shelf
from cps.api import magicshelves


@pytest.mark.unit
def test_canonical_schema_covers_engine_and_dynamic_columns():
    schema = magic_shelf.build_rule_schema(
        languages={"eng": "English"},
        custom_columns=[
            {"id": 71, "label": "Pages", "datatype": "int"},
            {"id": 72, "label": "Mood", "datatype": "enumeration", "enum_values": ["Calm", "Tense"]},
        ],
    )

    fields = {field["id"]: field for field in schema["fields"]}
    native_ids = {field["id"] for field in schema["fields"] if not field["id"].startswith("custom_column_")}

    assert native_ids == set(magic_shelf.FIELD_MAP), (
        "every engine field must come from the same schema served to Classic and the SPA"
    )
    assert fields["pubdate"]["label"] == "Publication Date"
    assert fields["timestamp"]["label"] == "Date Added"
    assert fields["last_modified"]["label"] == "Last Modified"
    assert fields["language"]["values"] == {"eng": "English"}
    assert fields["custom_column_71"]["type"] == "integer"
    assert fields["custom_column_72"]["values"] == {"Calm": "Calm", "Tense": "Tense"}

    for field_id in ("pubdate", "timestamp", "last_modified"):
        assert fields[field_id]["type"] == "datetime", (
            "QueryBuilder only exposes relative-date operators to datetime fields"
        )
        assert "in_last_days" in fields[field_id]["operators"]
        assert "not_in_last_days" in fields[field_id]["operators"]
    assert "in_last_days" not in fields["title"]["operators"]
    assert "in_last_days" not in fields["custom_column_71"]["operators"]

    operator_ids = {operator["type"] for operator in schema["operators"]}
    assert {"in_last_days", "not_in_last_days"} <= operator_ids
    relative_operators = {
        operator["type"]: operator for operator in schema["operators"]
        if operator["type"] in {"in_last_days", "not_in_last_days"}
    }
    assert all(operator["apply_to"] == ["datetime"] for operator in relative_operators.values())
    assert all(set(field["operators"]) <= operator_ids for field in schema["fields"])


@pytest.mark.unit
def test_rule_schema_route_requires_an_authenticated_user():
    source = inspect.getsource(magicshelves.magic_shelf_rule_schema)
    assert "@user_login_required" in source


def _books_session():
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    return sessionmaker(bind=engine)()


@pytest.mark.unit
@pytest.mark.parametrize("field_id", ["timestamp", "pubdate", "last_modified"])
def test_relative_date_rules_filter_real_rows(field_id):
    session = _books_session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recent = db.Books(
        title="Recent", sort="Recent", author_sort="Author", path="recent",
        timestamp=now - timedelta(days=7), pubdate=now - timedelta(days=14),
        series_index=1.0, last_modified=now, has_cover=0, authors=[], tags=[],
    )
    old = db.Books(
        title="Old", sort="Old", author_sort="Author", path="old",
        timestamp=now - timedelta(days=90), pubdate=now - timedelta(days=120),
        series_index=1.0, last_modified=now - timedelta(days=60), has_cover=0, authors=[], tags=[],
    )
    session.add_all([recent, old])
    session.commit()

    in_window = magic_shelf.build_filter_from_rule({
        "id": field_id, "operator": "in_last_days", "value": "30",
    })
    outside_window = magic_shelf.build_filter_from_rule({
        "id": field_id, "operator": "not_in_last_days", "value": "30",
    })

    assert [title for (title,) in session.query(db.Books.title).filter(in_window).all()] == ["Recent"]
    assert [title for (title,) in session.query(db.Books.title).filter(outside_window).all()] == ["Old"]
