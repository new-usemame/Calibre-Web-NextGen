"""Behavioral contract for three-state custom-column restrictions (#1322)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub
from cps.services.restriction_columns import (
    BOOL_CHOICES,
    RESTRICTION_DATATYPES,
    bool_tokens_valid,
    normalize_bool_token,
    restriction_predicate,
)


pytestmark = pytest.mark.unit

BOOL_COLUMN_ID = 13220
ENUM_COLUMN_ID = 13221


@pytest.fixture(scope="module")
def restriction_columns():
    if BOOL_COLUMN_ID not in db.cc_classes or ENUM_COLUMN_ID not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes([
            SimpleNamespace(id=BOOL_COLUMN_ID, datatype="bool"),
            SimpleNamespace(id=ENUM_COLUMN_ID, datatype="enumeration"),
        ])
    return db.cc_classes[BOOL_COLUMN_ID], db.cc_classes[ENUM_COLUMN_ID]


@pytest.fixture
def restriction_world(restriction_columns):
    bool_value, enum_value = restriction_columns
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Books.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    books = []
    for book_id, title in enumerate(
            ("True value", "False value", "Null value", "Missing value"), 1):
        created = datetime.now(timezone.utc)
        book = db.Books(
            title, title, "Author", created, created, "1.0", created,
            f"restriction-{book_id}", 0, [], [],
        )
        book.id = book_id
        books.append(book)
    session.add_all(books)
    session.flush()
    session.add_all([
        bool_value(book=books[0].id, value=True),
        bool_value(book=books[1].id, value=False),
        bool_value(book=books[2].id, value=None),
        enum_value(value="True"),
        enum_value(value="1"),
    ])
    books[0].custom_column_13221.extend(
        session.query(enum_value).filter(enum_value.value == "True").all()
    )
    books[1].custom_column_13221.extend(
        session.query(enum_value).filter(enum_value.value == "1").all()
    )
    session.commit()
    yield session, books
    session.close()
    engine.dispose()


def _user(allowed="", denied=""):
    return SimpleNamespace(
        id=1,
        is_anonymous=True,
        filter_language=lambda: "all",
        list_allowed_tags=lambda: [""],
        list_denied_tags=lambda: [""],
        list_allowed_column_values=lambda: allowed.split(","),
        list_denied_column_values=lambda: denied.split(","),
        allowed_column_value=allowed,
        denied_column_value=denied,
        has_own_library=False,
    )


def _visible_ids(session, predicate):
    return [row.id for row in session.query(db.Books.id)
            .filter(predicate).order_by(db.Books.id)]


def test_boolean_restrictions_distinguish_false_from_undefined(restriction_world):
    session, books = restriction_world
    value_model = db.cc_classes[BOOL_COLUMN_ID]
    relationship = getattr(db.Books, f"custom_column_{BOOL_COLUMN_ID}")

    false_only = restriction_predicate(
        relationship, value_model, "bool", ["No"], [],
    )
    undefined_only = restriction_predicate(
        relationship, value_model, "bool", ["undefined"], [],
    )

    assert _visible_ids(session, false_only) == [books[1].id]
    assert _visible_ids(session, undefined_only) == [books[2].id, books[3].id]


def test_boolean_allow_and_deny_states_compose_without_aliasing(restriction_world):
    session, books = restriction_world
    value_model = db.cc_classes[BOOL_COLUMN_ID]
    relationship = getattr(db.Books, f"custom_column_{BOOL_COLUMN_ID}")

    deny_false = restriction_predicate(
        relationship, value_model, "bool", ["Yes", "No", "Undefined"],
        ["0"],
    )
    deny_undefined = restriction_predicate(
        relationship, value_model, "bool", ["Yes", "No", "Undefined"],
        ["undefined"],
    )

    assert _visible_ids(session, deny_false) == [
        books[0].id, books[2].id, books[3].id,
    ]
    assert _visible_ids(session, deny_undefined) == [books[0].id, books[1].id]


def test_text_and_enumeration_values_remain_literal_strings(restriction_world):
    session, books = restriction_world
    value_model = db.cc_classes[ENUM_COLUMN_ID]
    relationship = getattr(db.Books, f"custom_column_{ENUM_COLUMN_ID}")

    literal_true = restriction_predicate(
        relationship, value_model, "enumeration", ["True"], [],
    )
    literal_one = restriction_predicate(
        relationship, value_model, "text", ["1"], [],
    )

    assert _visible_ids(session, literal_true) == [books[0].id]
    assert _visible_ids(session, literal_one) == [books[1].id]


def test_invalid_boolean_restriction_fails_closed(restriction_world):
    session, _books = restriction_world
    value_model = db.cc_classes[BOOL_COLUMN_ID]
    relationship = getattr(db.Books, f"custom_column_{BOOL_COLUMN_ID}")

    predicate = restriction_predicate(
        relationship, value_model, "bool", ["Yes", "unexpected"], [],
    )

    assert _visible_ids(session, predicate) == []


@pytest.mark.parametrize("allowed,denied,expected", [
    ("undefined", "", [3, 4]),
    ("false", "", [2]),
    ("true,false,undefined", "false", [1, 3, 4]),
    ("", "undefined", [1, 2]),
    ("false,undefined", "undefined", [2]),
    ("true", "true", []),
    ("", "", [1, 2, 3, 4]),
    ("unexpected", "", []),
    ("", "unexpected", []),
])
def test_common_and_multi_user_policy_seams_apply_the_same_boolean_states(
        restriction_world, monkeypatch, allowed, denied, expected):
    metadata_session, books = restriction_world
    app_engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(app_engine)
    app_session = sessionmaker(bind=app_engine)()
    monkeypatch.setattr(db.ub, "session", app_session)
    user = _user(allowed=allowed, denied=denied)
    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = SimpleNamespace(config_restricted_column=BOOL_COLUMN_ID)
    monkeypatch.setattr(db, "current_user", user)

    common_visible = _visible_ids(metadata_session, cdb.common_filters())
    batch_visible = cdb.get_filtered_book_ids_for_users(
        [user],
        {user.id: {"archived": (), "hidden": (), "membership": ()}},
        {user.id: [book.id for book in books]},
    )[user.id]

    assert common_visible == expected
    assert batch_visible == frozenset(common_visible)
    app_session.close()
    app_engine.dispose()


def test_boolean_admin_token_contract_is_canonical_and_compatible():
    assert RESTRICTION_DATATYPES == ("text", "enumeration", "bool")
    assert BOOL_CHOICES == (
        ("true", "Yes"), ("false", "No"), ("undefined", "Undefined"),
    )
    assert [normalize_bool_token(value) for value in
            ("True", "YES", 1, "False", "no", 0, "Undefined")] == [
                "true", "true", "true", "false", "false", "false",
                "undefined",
            ]
    assert normalize_bool_token(None) is None
    assert bool_tokens_valid("")
    assert bool_tokens_valid([""])
    assert bool_tokens_valid("Yes,No,Undefined")
    assert not bool_tokens_valid("true,,no")
    assert not bool_tokens_valid("maybe")
