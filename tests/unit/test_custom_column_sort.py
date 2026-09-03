import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Float, Integer
from sqlalchemy.orm import declarative_base

from cps import custom_column_sort
from cps.api import books, magicshelves
from cps import search

pytestmark = pytest.mark.unit


class ColumnConfig:
    def __init__(self, column_id, datatype="float", multiple=False, deleted=False):
        self.id = column_id
        self.datatype = datatype
        self.is_multiple = multiple
        self.mark_for_delete = deleted


def test_sortable_columns_only_returns_enabled_scalar_columns():
    config = SimpleNamespace(config_sortable_custom_columns="2,3,garbage")
    columns = [
        ColumnConfig(2, "float"),
        ColumnConfig(3, "int"),
        ColumnConfig(4, "datetime"),
        ColumnConfig(5, "text"),
        ColumnConfig(6, "float", multiple=True),
    ]

    assert [column.id for column in custom_column_sort.sortable_columns(columns, config)] == [2, 3]


def test_book_api_sorts_exclude_download_only_sorts():
    assert "hotasc" not in books._COMPATIBLE_BOOK_SORTS
    assert "hotdesc" not in books._COMPATIBLE_BOOK_SORTS
    assert "new" in books._COMPATIBLE_BOOK_SORTS


def test_magic_shelf_builtin_sorts_exclude_download_only_sorts():
    assert "hotasc" not in magicshelves._MAGIC_SHELF_BUILTIN_SORTS
    assert "hotdesc" not in magicshelves._MAGIC_SHELF_BUILTIN_SORTS
    assert "new" in magicshelves._MAGIC_SHELF_BUILTIN_SORTS


def test_resolve_rejects_unknown_or_not_configured_keys(monkeypatch):
    config = SimpleNamespace(config_sortable_custom_columns="2")
    monkeypatch.setattr(custom_column_sort.db, "cc_classes", {})

    columns = [ColumnConfig(2, "float")]
    assert custom_column_sort.resolve("cc-2-asc", config, columns) is None
    assert custom_column_sort.resolve("cc-3-desc", config, columns) is None
    assert custom_column_sort.resolve("cc-2-drop table", config, columns) is None


def test_resolve_returns_direct_model_and_deterministic_order(monkeypatch):
    base = declarative_base()

    class Books(base):
        __tablename__ = "books"
        id = Column(Integer, primary_key=True)

    class Difficulty(base):
        __tablename__ = "custom_column_2"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Float)

    monkeypatch.setattr(custom_column_sort.db, "Books", Books)
    monkeypatch.setattr(custom_column_sort.db, "cc_classes", {2: Difficulty})
    config = SimpleNamespace(config_sortable_custom_columns="2")

    model, order = custom_column_sort.resolve("cc-2-desc", config, [ColumnConfig(2, "float")])

    assert model is Difficulty
    assert len(order) == 3
    assert "custom_column_2.value DESC" in str(order[1])
    assert "books.id DESC" in str(order[2])


@pytest.mark.parametrize("column", [
    ColumnConfig(2, "text"),
    ColumnConfig(2, "float", multiple=True),
    ColumnConfig(2, "float", deleted=True),
])
def test_resolve_rejects_stale_or_unsupported_live_column(monkeypatch, column):
    config = SimpleNamespace(config_sortable_custom_columns="2")
    monkeypatch.setattr(custom_column_sort.db, "cc_classes", {2: object()})

    assert custom_column_sort.resolve("cc-2-asc", config, [column]) is None


def test_classic_searches_apply_the_resolved_custom_column_join_before_ordering():
    simple_search_source = inspect.getsource(search.render_search_results)
    advanced_search_source = inspect.getsource(search.render_adv_search_results)

    assert "db.Series, *custom_join" in simple_search_source
    assert "q = q.outerjoin(*custom_join)" in advanced_search_source
