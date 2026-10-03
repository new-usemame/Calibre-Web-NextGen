# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 advanced search — the JSON→term translation layer.

The query builder itself (build_adv_search_query) is covered by cps/search.py's
suite and the container verification; here we pin the API's own translation of
the SPA payload into the term dict the builder consumes.
"""
import pytest
from cps.api.search import _json_to_term, _as_str_list, _READ_STATUS


@pytest.mark.unit
def test_as_str_list_coerces():
    assert _as_str_list(None) == []
    assert _as_str_list([]) == []
    assert _as_str_list([1, 2, 3]) == ["1", "2", "3"]
    assert _as_str_list("eng") == ["eng"]  # scalar -> single-item list


@pytest.mark.unit
def test_read_status_mapping():
    assert _json_to_term({"read_status": "all"})["read_status"] == "Any"
    assert _json_to_term({"read_status": "read"})["read_status"] == "True"
    assert _json_to_term({"read_status": "unread"})["read_status"] == "False"
    # unknown / missing -> Any (no filter)
    assert _json_to_term({})["read_status"] == "Any"
    assert _json_to_term({"read_status": "bogus"})["read_status"] == "Any"


@pytest.mark.unit
def test_term_always_has_list_fields():
    """Builders iterate the include_/exclude_ fields, so they must always be
    lists even when the payload omits them — else iterating None raises."""
    term = _json_to_term({})
    for key in (
        "include_tag", "exclude_tag", "include_serie", "exclude_serie",
        "include_language", "exclude_language", "include_extension",
        "exclude_extension", "include_shelf", "exclude_shelf",
    ):
        assert term[key] == [], f"{key} must default to []"


@pytest.mark.unit
def test_term_passes_text_and_ids():
    term = _json_to_term({
        "title": "dune",
        "authors": "herbert",
        "publisher": "chilton",
        "comments": "spice",
        "publishstart": "1960-01-01",
        "publishend": "1970-01-01",
        "rating_high": "5",
        "rating_low": "3",
        "include_tag": [11, 12],
        "exclude_serie": [3],
        "include_extension": ["EPUB"],
    })
    assert term["title"] == "dune"
    assert term["authors"] == "herbert"
    assert term["publisher"] == "chilton"
    assert term["comments"] == "spice"
    assert term["publishstart"] == "1960-01-01"
    assert term["publishend"] == "1970-01-01"
    # ratinghigh/ratinglow pass through verbatim (builder applies the upstream swap)
    assert term["ratinghigh"] == "5"
    assert term["ratinglow"] == "3"
    assert term["include_tag"] == ["11", "12"]
    assert term["exclude_serie"] == ["3"]
    assert term["include_extension"] == ["EPUB"]


@pytest.mark.unit
@pytest.mark.parametrize("status", ["in_progress", "did_not_finish", "on_hold"])
def test_exact_personal_status_survives_the_advanced_search_adapter(status):
    # The shared builder understands canonical statuses; the JSON adapter must
    # not silently broaden a selected pause filter to its Any fallback.
    assert _json_to_term({"read_status": status})["read_status"] == status


@pytest.mark.unit
def test_advanced_search_query_is_distinct():
    """The advanced-search query must be DISTINCT — build_adv_search_query always
    adds a BookShelf outerjoin, so a book on N shelves would otherwise be counted
    N times (total > items). Source-pin so the de-dup can't silently regress."""
    import inspect
    from cps.api import search as mod
    src = inspect.getsource(mod.advanced_search)
    assert ".distinct()" in src, "advanced_search must apply .distinct() to de-dup shelf-join rows"


@pytest.mark.unit
def test_criteria_read_status_humanized():
    """The raw 'Read Status = True/False' criterion is humanized for display."""
    import inspect
    from cps.api import search as mod
    src = inspect.getsource(mod.advanced_search)
    assert '"Read Status = \'True\'"' in src and '"Read"' in src
    assert '"Read Status = \'False\'"' in src and '"Unread"' in src
