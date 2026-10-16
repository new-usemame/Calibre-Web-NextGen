# -*- coding: utf-8 -*-
"""Value map for list metadata (#2438, Discussion #2481): matching and plan."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db
from cps.services import value_map as vm

pytestmark = pytest.mark.unit


def _map(field, raw):
    rules, errors = vm.clean_rules(field, raw)
    assert errors == []
    return vm.ValueMap.from_rules(rules)


def test_fold_ignores_case_accents_and_spacing():
    assert vm.fold("  Ésotérisme ") == vm.fold("esoterisme")
    assert vm.fold("Fiction /  Thrillers") == vm.fold("fiction / thrillers")


def test_rename_split_drop_and_untouched():
    tags = _map("tags", {
        "Fiction / Thrillers": ["Thriller"],
        "Action & Adventure": ["Action", "Adventure"],
        "General": [],
    })
    assert tags.map_values(["fiction / thrillers", "Action & Adventure", "General", "Dragons"]) == [
        "Thriller", "Action", "Adventure", "Dragons"]


def test_target_is_the_canonical_spelling_and_duplicates_collapse():
    tags = _map("tags", {"Suspense": ["Thriller"]})
    assert tags.map_values(["thriller", "Suspense"]) == ["Thriller"]


def test_values_no_rule_covers_stay_as_they_are():
    # Two spellings that fold alike are only merged by a rule. With no rule they
    # are left alone; the page promises values no rule covers stay as they are.
    assert vm.ValueMap().map_values(["Montreal (Quebec)", "Montréal (Québec)"]) == [
        "Montreal (Quebec)", "Montréal (Québec)"]
    assert vm.ValueMap().map_values(["Dragons", "Dragons"]) == ["Dragons"]


def test_clean_rules_rejects_bad_input():
    rules, errors = vm.clean_rules("tags", [
        {"incoming": "", "targets": []},
        {"incoming": "A, B", "targets": ["A"]},
        {"incoming": "Crime", "targets": ["Thriller"]},
        {"incoming": "crime", "targets": ["Mystery"]},
    ])
    assert [r["incoming"] for r in rules] == ["Crime"]
    assert len(errors) == 3


def test_clean_rules_accepts_the_export_and_legacy_map_shapes():
    rules, errors = vm.clean_rules("tags", {"Suspense": "Thriller"})
    assert errors == [] and rules[0]["targets"] == ["Thriller"]


@pytest.fixture
def library():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(db.Books.__table__.insert(), [
        {"id": i, "title": f"Book {i}", "sort": f"Book {i}", "path": f"b{i}", "author_sort": ""}
        for i in (1, 2, 3)
    ])
    session.execute(db.Tags.__table__.insert(), [
        {"id": 1, "name": "Fiction / Thrillers"}, {"id": 2, "name": "thriller"},
        {"id": 3, "name": "General"}, {"id": 4, "name": "Dragons"},
    ])
    session.execute(db.books_tags_link.insert(), [
        {"book": 1, "tag": 1}, {"book": 1, "tag": 3},
        {"book": 2, "tag": 2},
        {"book": 3, "tag": 4},
    ])
    session.execute(db.Publishers.__table__.insert(), [
        {"id": 1, "name": "Zebra Books", "sort": "Zebra Books"}, {"id": 2, "name": "Zebra", "sort": "Zebra"},
    ])
    session.execute(db.books_publishers_link.insert(), [
        {"book": 1, "publisher": 1}, {"book": 2, "publisher": 2},
    ])
    session.commit()
    return session


def _tags(session):
    session.expire_all()
    return {b.id: [t.name for t in b.tags] for b in session.query(db.Books).order_by(db.Books.id)}


def test_plan_lists_only_books_that_change(library):
    tags = _map("tags", {"Fiction / Thrillers": ["Thriller"], "General": []})
    changes = vm.plan(library, tags, "tags")
    assert [(c["book_id"], c["old"], c["new"]) for c in changes] == [
        (1, ["Fiction / Thrillers", "General"], ["Thriller"]),
        (2, ["thriller"], ["Thriller"]),
    ]
    assert _tags(library)[1] == ["Fiction / Thrillers", "General"]  # nothing written


def test_library_values_counts_books(library):
    values = vm.library_values(library, "tags")
    assert {v["name"]: v["count"] for v in values} == {
        "Fiction / Thrillers": 1, "thriller": 1, "General": 1, "Dragons": 1}


def test_allow_list_rejects_unmatched_values_but_not_drops(monkeypatch):
    rules, _ = vm.clean_rules("tags", {"General": [], "Suspense": ["Thriller"]})
    monkeypatch.setattr(vm, "load_map", lambda session, field: vm.ValueMap.from_rules(rules))
    kept, rejected = vm.apply_value_map(
        "tags", ["General", "Suspense", "Dragons", "FIC030000", "thriller"],
        app_session=object(), allowed=["Dragons"])
    # Dropped by a rule: not rejected. A map target (Thriller) is allowed even
    # though no book in ``allowed`` carries it.
    assert kept == ["Thriller", "Dragons"]
    assert rejected == ["FIC030000"]


def test_without_an_allow_list_nothing_is_rejected(monkeypatch):
    monkeypatch.setattr(vm, "load_map", lambda session, field: vm.ValueMap())
    assert vm.apply_value_map("tags", ["Anything"], app_session=object()) == (["Anything"], [])
