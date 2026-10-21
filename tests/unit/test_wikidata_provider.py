# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wikidata provider: series (P179) and position (P1545) from the work's item.

Responses below have the shape of the live API (action=query&list=search,
then wbgetentities); no test touches the network.
"""
import pytest
import requests

from cps.metadata_provider import wikidata as wd
from cps.services.Metadata import ProviderRefused

pytestmark = pytest.mark.unit


def _item(qid, value):
    return {"mainsnak": {"datavalue": {"value": {"id": value}}}, "rank": "normal"}


def _series(value, ordinal=None, rank="normal"):
    statement = {"mainsnak": {"datavalue": {"value": {"id": value}}}, "rank": rank}
    if ordinal is not None:
        statement["qualifiers"] = {"P1545": [{"datavalue": {"value": ordinal}}]}
    return statement


def _labels(**names):
    return {qid: {"labels": {"en": {"value": name}}} for qid, name in names.items()}


WORK = {
    "labels": {"en": {"value": "Cross Kill"}},
    "claims": {
        "P50": [_item("Q1", "Q100")],
        "P179": [_series("Q200", "23.5")],
        "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2016-10-03T00:00:00Z", "precision": 11}}}}],
    },
}


class _Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def _api(monkeypatch, works, names, status=200):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append((params, headers))
        if status != 200:
            return _Response({}, status)
        if params.get("list") == "search":
            return _Response({"query": {"search": [{"title": qid} for qid in works]}})
        if "claims" in params.get("props", ""):
            return _Response({"entities": works})
        return _Response({"entities": names})
    monkeypatch.setattr(wd.requests, "get", get)
    return calls


def test_a_work_comes_back_with_its_series_and_fractional_position(monkeypatch):
    calls = _api(monkeypatch, {"Q10": WORK}, _labels(Q100="James Patterson", Q200="Alex Cross"))
    (record,) = wd.Wikidata().search("Cross Kill James Patterson")
    assert (record.title, record.authors, record.series, record.series_index) == (
        "Cross Kill", ["James Patterson"], "Alex Cross", 23.5)
    assert record.publishedDate == "2016-10-03" and record.identifiers == {"wikidata": "Q10"}
    search = calls[0][0]
    assert "haswbstatement:P50" in search["srsearch"]          # works only
    assert "github.com" in calls[0][1]["User-Agent"]           # Wikimedia UA policy: contact details
    assert len(calls) == 3                                      # search, works, names in one go


def test_a_preferred_series_wins_then_one_with_a_position():
    claims = {"P179": [_series("Q1"), _series("Q2", "1"), _series("Q3", "4", rank="preferred")]}
    assert wd.Wikidata._series(claims) == ("Q3", 4)
    claims = {"P179": [_series("Q1"), _series("Q2", "1"), _series("Q4", "2", rank="deprecated")]}
    assert wd.Wikidata._series(claims) == ("Q2", 1)


def test_a_work_with_no_series_has_none(monkeypatch):
    work = {"labels": {"en": {"value": "Standalone"}}, "claims": {"P50": [_item("Q1", "Q100")]}}
    _api(monkeypatch, {"Q11": work}, _labels(Q100="Someone"))
    (record,) = wd.Wikidata().search("Standalone Someone")
    assert record.series is None and record.series_index == 0


@pytest.mark.parametrize("time, precision, expected", [
    ("+1965-08-01T00:00:00Z", 11, "1965-08-01"),
    ("+1976-04-00T00:00:00Z", 10, "1976-04"),
    ("+1965-00-00T00:00:00Z", 9, "1965"),
])
def test_publication_dates_keep_their_precision(time, precision, expected):
    claims = {"P577": [{"mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}}}]}
    assert wd.Wikidata._first_date(claims) == expected


def test_rate_limiting_is_a_refusal_not_an_empty_result(monkeypatch):
    _api(monkeypatch, {}, {}, status=429)
    with pytest.raises(ProviderRefused) as refused:
        wd.Wikidata().search("Dune Frank Herbert")
    assert refused.value.status == "rate_limited"


def test_a_network_failure_is_an_empty_result(monkeypatch):
    def down(*_a, **_k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(wd.requests, "get", down)
    assert wd.Wikidata().search("Dune Frank Herbert") == []


def test_it_is_a_series_source():
    assert wd.Wikidata.FILLS_SERIES is True
