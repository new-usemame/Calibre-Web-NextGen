# -*- coding: utf-8 -*-
"""Change sets: preview rows, apply with per-book outcomes, undo.

A real SQLite calibre library (calibre's NOCASE unique names matter for the
case-only rename) and a real app.db for the set tables.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub
from cps.services import change_sets as cs
from cps.services import value_map as vm

pytestmark = pytest.mark.unit


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
        {"book": 1, "tag": 1}, {"book": 1, "tag": 3}, {"book": 2, "tag": 2}, {"book": 3, "tag": 4},
    ])
    session.execute(db.Publishers.__table__.insert(), [
        {"id": 1, "name": "Zebra Books", "sort": "Zebra Books"}, {"id": 2, "name": "Zebra", "sort": "Zebra"},
    ])
    session.execute(db.books_publishers_link.insert(), [
        {"book": 1, "publisher": 1}, {"book": 2, "publisher": 2},
    ])
    session.commit()
    return session


@pytest.fixture
def app_db():
    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine, tables=[
        ub.MetadataValueRule.__table__, ub.MetadataChangeSet.__table__, ub.MetadataChange.__table__])
    return sessionmaker(bind=engine)()


class Hooks:
    def __init__(self, fail_on=None):
        self.fail_on, self.marked, self.queued = fail_on, [], []

    def mark(self, book):
        if book.id == self.fail_on:
            raise RuntimeError("disk full")
        self.marked.append(book.id)

    def queue(self, book, changed):
        self.queued.append((book.id, changed))

    def kw(self):
        return {"mark_modified": self.mark, "queue_embed": self.queue}


def _tags(session):
    session.expire_all()
    return {b.id: [t.name for t in b.tags] for b in session.query(db.Books).order_by(db.Books.id)}


def _propose(library, app_db, field, raw):
    rules, errors = vm.clean_rules(field, raw)
    assert errors == []
    changes = vm.plan(library, vm.ValueMap.from_rules(rules), field)
    return cs.create(app_db, "cleanup", 1, changes)


def _statuses(app_db, change_set):
    return {r.book_id: r.status for r in cs.rows(app_db, change_set.id)}


def test_apply_writes_records_and_queues_after_commit(library, app_db):
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Thriller"], "General": []})
    hooks = Hooks()

    result = cs.apply(app_db, library, change_set.id, **hooks.kw())

    assert _tags(library) == {1: ["Thriller"], 2: ["Thriller"], 3: ["Dragons"]}
    assert library.query(db.Tags).filter(db.Tags.name == "Thriller").count() == 1
    assert sorted(result["removed_values"]) == ["Fiction / Thrillers", "General"]
    assert _statuses(app_db, change_set) == {1: "applied", 2: "applied"}
    assert hooks.marked == [1, 2] and [q[0] for q in hooks.queued] == [1, 2]
    assert app_db.get(ub.MetadataChangeSet, change_set.id).status == "applied"


def test_excluded_and_stale_books_are_skipped(library, app_db):
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Thriller"], "General": []})
    # Book 2 changes after the preview.
    book = library.get(db.Books, 2)
    book.tags.append(library.get(db.Tags, 4))
    library.commit()

    cs.apply(app_db, library, change_set.id, exclude=[1], **Hooks().kw())

    rows = {r.book_id: (r.status, r.error) for r in cs.rows(app_db, change_set.id)}
    assert rows == {1: ("skipped", "left out at apply"), 2: ("skipped", "changed since the preview")}
    assert _tags(library)[1] == ["Fiction / Thrillers", "General"]


def test_a_failing_book_is_reported_and_the_rest_apply(library, app_db):
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Suspense"], "General": [], "thriller": ["Mystery"]})

    result = cs.apply(app_db, library, change_set.id, **Hooks(fail_on=1).kw())

    rows = {r.book_id: (r.status, r.error) for r in cs.rows(app_db, change_set.id)}
    assert rows[1] == ("failed", "disk full") and rows[2] == ("applied", None)
    assert _tags(library)[1] == ["Fiction / Thrillers", "General"]
    assert result["written"] == 1


def test_undo_restores_and_skips_books_changed_since(library, app_db):
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Thriller"], "General": []})
    cs.apply(app_db, library, change_set.id, **Hooks().kw())
    # Book 2 is edited by hand after the apply.
    book = library.get(db.Books, 2)
    book.tags.append(library.get(db.Tags, 4))
    library.commit()

    cs.undo(app_db, library, change_set.id, **Hooks().kw())

    tags = _tags(library)
    assert sorted(tags[1]) == ["Fiction / Thrillers", "General"]
    assert sorted(tags[2]) == ["Dragons", "Thriller"]
    rows = {r.book_id: (r.status, r.error) for r in cs.rows(app_db, change_set.id)}
    assert rows == {1: ("undone", None), 2: ("skipped", "changed since the preview")}
    with pytest.raises(ValueError):
        cs.undo(app_db, library, change_set.id, **Hooks().kw())


def test_only_a_proposed_set_applies(library, app_db):
    change_set = _propose(library, app_db, "tags", {"General": []})
    cs.apply(app_db, library, change_set.id, **Hooks().kw())
    with pytest.raises(ValueError):
        cs.apply(app_db, library, change_set.id, **Hooks().kw())


def test_excluded_shared_tag_owner_is_never_recased(library, app_db):
    """A target's spelling is global; leaving its owner out must leave it intact."""
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Thriller"]})
    hooks = Hooks()
    cs.apply(app_db, library, change_set.id, exclude=[2], **hooks.kw())
    assert _tags(library)[2] == ["thriller"]
    assert _tags(library)[1] == ["Fiction / Thrillers", "General"]
    assert hooks.marked == [] and hooks.queued == []


def test_shared_case_change_rolls_back_all_owners_on_failure(library, app_db):
    """All owners of a shared spelling must succeed together, with no silent writes."""
    library.get(db.Books, 3).tags = [library.get(db.Tags, 2)]
    library.commit()
    change_set = _propose(library, app_db, "tags", {"thriller": ["Thriller"]})
    hooks = Hooks(fail_on=3)
    result = cs.apply(app_db, library, change_set.id, **hooks.kw())
    assert _tags(library)[2] == ["thriller"]
    assert _tags(library)[3] == ["thriller"]
    assert result["written"] == 0 and hooks.queued == []
    assert _statuses(app_db, change_set) == {2: "failed", 3: "failed"}


def test_accent_only_edit_since_preview_is_not_overwritten(library, app_db):
    """Matching rules fold accents; concurrency checks must preserve exact edits."""
    change_set = _propose(library, app_db, "tags", {"Dragons": ["Fantasy"]})
    library.get(db.Tags, 4).name = "Drágons"
    library.commit()
    cs.apply(app_db, library, change_set.id, **Hooks().kw())
    assert _tags(library)[3] == ["Drágons"]
    assert _statuses(app_db, change_set) == {3: "skipped"}


def test_undo_ignores_tag_relationship_order(library, app_db):
    """Tags are a set; reloading by row id must not make undo appear stale."""
    change_set = _propose(library, app_db, "tags", {"Fiction / Thrillers": ["Zebra", "Alpha"]})
    cs.apply(app_db, library, change_set.id, **Hooks().kw())
    library.expire_all()
    cs.undo(app_db, library, change_set.id, **Hooks().kw())
    assert sorted(_tags(library)[1]) == ["Fiction / Thrillers", "General"]
    assert _statuses(app_db, change_set) == {1: "undone"}


def test_apply_reuses_an_existing_non_ascii_target(library, app_db):
    """SQLite lower() is ASCII-only; exact accented targets still reuse their row."""
    library.get(db.Tags, 4).name = "Épopée"
    library.commit()
    change_set = _propose(library, app_db, "tags", {"General": ["Épopée"]})
    cs.apply(app_db, library, change_set.id, **Hooks().kw())
    assert sorted(_tags(library)[1]) == ["Fiction / Thrillers", "Épopée"]
    assert _statuses(app_db, change_set) == {1: "applied"}
