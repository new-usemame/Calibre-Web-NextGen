# SPDX-License-Identifier: GPL-3.0-or-later
"""The edit picker must preserve stored text and enforce the editor's scope."""
from types import SimpleNamespace

from werkzeug.exceptions import HTTPException
import pytest
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from cps import app, db, editbooks

pytestmark = pytest.mark.unit


@pytest.fixture
def picker(monkeypatch):
    base = declarative_base()

    class Book(base):
        __tablename__ = "books"
        id = Column(Integer, primary_key=True)
        visible = Column(Boolean)

    class Value(base):
        __tablename__ = "custom_column_7"
        id = Column(Integer, primary_key=True)
        book = Column(Integer, ForeignKey("books.id"))
        value = Column(String)

    Book.custom_column_7 = relationship(Value)
    engine = create_engine("sqlite://")
    base.metadata.create_all(engine)
    db.CustomColumns.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    column = db.CustomColumns(id=7, label="shelf", name="Shelf", datatype="text", is_multiple=False,
                              mark_for_delete=False)
    session.add(column)
    session.add_all([Book(id=1, visible=True), Book(id=2, visible=False)])
    session.add_all([Value(book=1, value="Smith, Jr."), Value(book=1, value="QA. 76.."),
                     Value(book=2, value="Restricted value")])
    session.commit()
    calibre = SimpleNamespace(session=session, is_flat_cc_column=lambda _: True,
                              get_cc_columns=lambda *a, **kw: [column],
                              common_filters=lambda **kw: Book.visible.is_(True))
    monkeypatch.setattr(editbooks, "calibre_db", calibre)
    monkeypatch.setattr(db, "Books", Book)
    monkeypatch.setattr(db, "cc_classes", {7: Value})
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(role_edit=lambda: True,
                                                                 role_admin=lambda: False))
    def get_choices(column_id):
        with app.test_request_context(f"/ajax/getcctree/{column_id}"):
            try:
                # Login is supplied by the fixture. Keep the edit-role check.
                return app.make_response(editbooks.get_cc_tree.__wrapped__(column_id))
            except HTTPException as exc:
                return app.make_response(exc)

    yield get_choices, column, calibre
    session.close()
    engine.dispose()


def test_picker_returns_exact_values_only_from_editable_books(picker):
    """A single text value with a comma or dots must survive choosing unchanged."""
    get_choices, _, _ = picker
    response = get_choices(7)
    assert response.status_code == 200
    assert response.json == {"hierarchical": False, "is_multiple": False, "entries": [
        {"value": "QA. 76..", "depth": 0}, {"value": "Smith, Jr.", "depth": 0}]}


def test_picker_requires_edit_permission(picker, monkeypatch):
    get_choices, _, _ = picker
    monkeypatch.setattr(editbooks, "current_user", SimpleNamespace(role_edit=lambda: False,
                                                                 role_admin=lambda: False))
    assert get_choices(7).status_code == 403


@pytest.mark.parametrize("datatype", ["int", "comments", "bool", "enumeration"])
def test_picker_rejects_non_text_columns(picker, datatype):
    get_choices, column, _ = picker
    column.datatype = datatype
    assert get_choices(7).status_code == 404


def test_picker_rejects_unknown_and_ignored_columns(picker):
    get_choices, _, calibre = picker
    assert get_choices(999).status_code == 404
    calibre.get_cc_columns = lambda *a, **kw: []
    assert get_choices(7).status_code == 404


def test_hierarchical_picker_keeps_raw_values_and_tree_depth(picker):
    """Indentation can normalize paths; the selected value must remain exact."""
    get_choices, _, calibre = picker
    calibre.is_flat_cc_column = lambda _: False
    data = get_choices(7).json
    assert {"value": "QA. 76..", "depth": 1} in data["entries"]
    assert {"value": "Restricted value", "depth": 0} not in data["entries"]


def test_classic_editor_only_offers_picker_for_eligible_columns(picker, monkeypatch):
    """An ignored column may keep its text input, but must not offer a dead picker."""
    _, column, calibre = picker
    calibre.get_cc_columns = lambda *a, **kw: []
    calibre.get_hierarchical_column_ids = lambda: set()
    calibre.get_filtered_book = lambda *a, **kw: SimpleNamespace(id=1, languages=[], authors=[])
    calibre.order_authors = lambda books: []
    query = SimpleNamespace(first=lambda: None)
    query.filter = lambda *a: query
    monkeypatch.setattr(editbooks.ub, "session", SimpleNamespace(query=lambda *a: query))
    monkeypatch.setattr(editbooks.helper, "get_convert_options", lambda book: ([], []))
    monkeypatch.setattr(editbooks, "render_title_template", lambda template, **context: context)
    monkeypatch.setattr(editbooks, "_", lambda message: message)
    context = editbooks.render_edit_book(1)
    assert context["cc"] == [column]
    assert column.cc_picker_mode is None
