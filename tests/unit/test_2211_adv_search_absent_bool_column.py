# SPDX-License-Identifier: GPL-3.0-or-later
"""An advanced search that never mentions a Yes/No custom column must not filter on it (#2211).

The classic search form always posts every custom column ("Any" when untouched).
The New UI's JSON search never sends custom columns at all, so the builder saw
``None`` for each bool column and applied it as "value == False": on a library
with a Yes/No column, an empty search returned only the books with that flag
cleared, and a title search usually returned nothing.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import column

from cps import db, search


pytestmark = pytest.mark.unit

BOOL_COLUMN = SimpleNamespace(id=7, datatype="bool", name="Finished")


class RecordingQuery:
    def __init__(self):
        self.filters = []

    def filter(self, clause):
        self.filters.append(clause)
        return self


@pytest.fixture
def bool_column(monkeypatch):
    monkeypatch.setattr(db.Books, "custom_column_7", MagicMock(), raising=False)
    monkeypatch.setitem(db.cc_classes, 7, SimpleNamespace(value=column("value")))


@pytest.mark.parametrize("term", [{}, {"custom_column_7": "Any"}], ids=["absent", "any"])
def test_an_unconstrained_bool_column_adds_no_filter(bool_column, term):
    query = RecordingQuery()
    search.adv_search_custom_columns([BOOL_COLUMN], term, query)
    assert query.filters == []


@pytest.mark.parametrize("value", ["True", "False", ""], ids=["yes", "no", "unset"])
def test_a_chosen_bool_value_still_filters(bool_column, value):
    query = RecordingQuery()
    search.adv_search_custom_columns([BOOL_COLUMN], {"custom_column_7": value}, query)
    assert len(query.filters) == 1
