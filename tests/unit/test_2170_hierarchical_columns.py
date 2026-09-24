# SPDX-License-Identifier: GPL-3.0-or-later
"""Hierarchical custom columns against a real library schema (#2170).

The detection scan runs on every advanced search that names a text custom
column, so it has to survive the libraries people actually have: ones that
also carry numeric, rating or yes/no columns, whose values are not strings.
"""
import itertools
import random

import flask
import pytest
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from cps import db, hierarchy


pytestmark = pytest.mark.unit


@pytest.fixture()
def mixed_library(monkeypatch):
    """A text column holding a hierarchy beside an int and a bool column."""
    base = declarative_base()

    class Subjects(base):
        __tablename__ = "custom_column_2"
        id = Column(Integer, primary_key=True)
        value = Column(String)

    class Pages(base):
        __tablename__ = "custom_column_3"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Integer)

    class Finished(base):
        __tablename__ = "custom_column_4"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Integer)

    engine = create_engine("sqlite://")
    db.CustomColumns.__table__.create(engine)
    for table in (Subjects, Pages, Finished):
        table.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2, 'subjects', 'Subjects', 'text', 1), (3, 'pages', 'Pages', 'int', 0), "
            "(4, 'finished', 'Finished', 'bool', 0)"))
        connection.execute(Subjects.__table__.insert(), [
            {"id": 1, "value": "Computers"}, {"id": 2, "value": "Computers.DB"}])
        connection.execute(Pages.__table__.insert(), [{"id": 1, "book": 1, "value": 320}])
        connection.execute(Finished.__table__.insert(), [{"id": 1, "book": 1, "value": 1}])

    monkeypatch.setattr(db, "cc_classes", {2: Subjects, 3: Pages, 4: Finished})
    monkeypatch.setattr(db.CalibreDB, "_hier_cache", None, raising=False)
    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    calibre.session = session
    try:
        yield calibre
    finally:
        session.close()
        engine.dispose()


def test_detection_ignores_non_text_columns_instead_of_crashing(mixed_library):
    assert mixed_library.get_hierarchical_column_ids() == {2}


def _pairwise(values):
    vset = {v.strip() for v in values if v}
    return any(other.startswith(v + ".") for v in vset for other in vset)


def test_linear_detection_agrees_with_the_pairwise_definition():
    """Every small value set over an alphabet that exercises the separator,
    spaces, near-miss prefixes and empty segments gives the same answer."""
    atoms = ["A", "A.B", "A B", "AB", "A.", ".A", "A..B", "B.A", " A", "A.B.C", "778.3", "778"]
    for size in range(0, 4):
        for combo in itertools.combinations(atoms, size):
            assert hierarchy.is_hierarchical_value_set(combo) == _pairwise(combo), combo
    rng = random.Random(2170)
    for _ in range(300):
        combo = rng.sample(atoms, rng.randint(1, len(atoms)))
        assert hierarchy.is_hierarchical_value_set(combo) == _pairwise(combo), combo


def test_a_pagination_link_names_its_own_page_not_the_current_one():
    """Views that take ?page= (the custom-column tree among them) must not have
    every numbered link rewritten back to the page being shown."""
    from cps.jinjia import url_for_other_page

    app = flask.Flask(__name__)
    app.add_url_rule("/custom_column/<int:column_id>/<path:category_path>",
                     "cc", lambda column_id, category_path: "")
    with app.test_request_context("/custom_column/5/Computers.DB?page=2&sort_param=abc"):
        link = url_for_other_page(3)
    assert "page=3" in link
    assert "page=2" not in link
    assert "sort_param=abc" in link


def test_every_node_lists_exactly_the_books_the_tree_counts(monkeypatch):
    """The tree counts and the node's book list come from one definition, so
    oddly spelled values (empty segments, spaces around dots, a slash, a
    different case) are listed where they are counted, and a book filed at
    two depths is counted once."""
    base = declarative_base()

    class Subjects(base):
        __tablename__ = "custom_column_9"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(String)

    rows = [(1, "Computers"), (2, "Computers.DB"), (3, "Computers..DB"),
            (4, " Fiction . Mystery "), (5, "AC/DC.Live"), (6, "computers.DB"),
            (7, "Computers.DB"), (7, "Computers.DB.SQL"), (8, "ComputersX"),
            (9, "Science.")]
    engine = create_engine("sqlite://")
    Subjects.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(Subjects.__table__.insert(), [
            {"id": i, "book": book, "value": value} for i, (book, value) in enumerate(rows, 1)])
    monkeypatch.setitem(db.cc_classes, 9, Subjects)
    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    try:
        tree = hierarchy.parse_tag_hierarchy(rows)

        def walk(nodes):
            for node in nodes:
                yield node
                yield from walk(node["children"])

        for node in walk(tree):
            listed = {book for (book,) in session.query(Subjects.book).filter(
                calibre.hierarchical_cc_filter(9, node)).distinct()}
            assert len(listed) == node["total_count"], node["path"]

        by_path = {node["path"]: node for node in walk(tree)}
        assert by_path["Computers.DB"]["total_count"] == 3      # books 2, 3, 7
        assert by_path["Computers"]["total_count"] == 4         # + book 1, not 6 or 8
        assert by_path["Fiction.Mystery"]["total_count"] == 1
        assert by_path["AC/DC.Live"]["total_count"] == 1
        assert by_path["Science"]["total_count"] == 1
    finally:
        session.close()
        engine.dispose()
