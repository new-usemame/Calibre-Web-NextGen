# SPDX-License-Identifier: GPL-3.0-or-later
"""The New UI's advanced search can filter on custom columns (#2365).

The classic form posts ``custom_column_<id>[_low|_high|_start|_end]`` fields and
the shared builder filters on them; the New UI's JSON search never carried them,
so custom columns vanished from advanced search when a user switched UIs. The
API now translates a ``custom`` map into those same builder keys. What must hold:
a column the user is not allowed to see is never searched, and a value the
builder would choke on (it strptime()s dates and float()s ratings) is dropped
rather than turning the whole search into a 500.
"""
from types import SimpleNamespace

import pytest

from cps.api.search import _custom_column_options, _json_to_term

pytestmark = pytest.mark.unit


def col(id, datatype, name="c", display='{}'):
    return SimpleNamespace(id=id, datatype=datatype, name=name,
                           get_display_dict=lambda: __import__("json").loads(display))


COLUMNS = [col(1, "bool", "Cover updated"), col(2, "int", "Pages"), col(3, "datetime", "Finished on"),
           col(4, "text", "Source"), col(5, "rating", "My stars"), col(6, "float", "Score"),
           col(7, "enumeration", "Status", '{"enum_values": ["todo", "done"]}'),
           col(8, "composite", "Built")]


def custom_of(term):
    return {k: v for k, v in term.items() if k.startswith("custom_column_")}


def test_each_column_type_reaches_the_builder_in_its_classic_form_shape():
    term = _json_to_term({"custom": {
        "custom_column_1": "True",
        "custom_column_2_low": "100", "custom_column_2_high": "400",
        "custom_column_3_start": "2026-01-01", "custom_column_3_end": "2026-09-01",
        "custom_column_4": "gutenberg",
        "custom_column_5": "4",
        "custom_column_6_low": "2.5",
        "custom_column_7": "done",
    }}, COLUMNS)
    assert custom_of(term) == {
        "custom_column_1": "True",
        "custom_column_2_low": 100, "custom_column_2_high": 400,
        "custom_column_3_start": "2026-01-01", "custom_column_3_end": "2026-09-01",
        "custom_column_4": "gutenberg",
        "custom_column_5": "4",
        "custom_column_6_low": 2.5,
        "custom_column_7": "done",
    }


def test_yes_no_empty_means_the_flag_was_never_set():
    """The builder reads "" as "flag unset"; a URL cannot carry "" as a choice."""
    assert custom_of(_json_to_term({"custom": {"custom_column_1": "Empty"}}, COLUMNS)) == {"custom_column_1": ""}


def test_a_column_the_user_cannot_see_is_never_searched():
    """``columns`` is get_cc_columns(filter_config_custom_read=True): a column
    hidden from this user (or deleted) must not become a filter via the API."""
    term = _json_to_term({"custom": {"custom_column_99": "secret", "custom_column_8": "x"}}, COLUMNS)
    assert custom_of(term) == {}


@pytest.mark.parametrize("field,value", [
    ("custom_column_3_start", "yesterday"),     # strptime would raise in the builder
    ("custom_column_3_end", "2026-13-40"),
    ("custom_column_5", "lots"),                # float() would raise in the builder
    ("custom_column_5", "9"),                   # outside 1-5 stars
    ("custom_column_2_low", "ten"),
    ("custom_column_2", "5"),                   # a number column has no single-value field
    ("custom_column_4_low", "a"),               # a text column has no range
    ("custom_column_1", "maybe"),
    ("custom_column_4", "   "),
], ids=lambda v: str(v))
def test_values_the_builder_cannot_use_are_dropped(field, value):
    assert custom_of(_json_to_term({"custom": {field: value}}, COLUMNS)) == {}


def test_a_payload_without_custom_columns_is_unchanged():
    for payload in ({}, {"custom": None}, {"custom": ["custom_column_1"]}, {"custom": "x"}):
        assert custom_of(_json_to_term(payload, COLUMNS)) == {}


def test_options_list_searchable_columns_with_their_enum_values():
    options = _custom_column_options(COLUMNS)
    assert [o["id"] for o in options] == [1, 2, 3, 4, 5, 6, 7]  # composite has no search input
    assert options[-1] == {"id": 7, "name": "Status", "datatype": "enumeration", "enum_values": ["todo", "done"]}


def test_the_summary_line_names_the_yes_no_choice_not_the_raw_term(monkeypatch):
    from cps.api import search as api_search
    monkeypatch.setattr(api_search, "_", lambda s: s)
    columns = [col(1, "bool", "Finished"), col(4, "text", "Source")]
    assert api_search._humanize_bool_criteria(
        "Finished: True + Source: True + Title: dune", columns) == "Finished: Yes + Source: True + Title: dune"
    assert api_search._humanize_bool_criteria("Finished: ", columns) == "Finished: Empty"
    assert api_search._humanize_bool_criteria("Finished: False", columns) == "Finished: No"
