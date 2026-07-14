# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recorded-fixture and failure-contract tests for #303 and #315."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from cps.metadata_constants import metadata_provider_enabled
from cps.metadata_provider.bolcom import BolCom
from cps.metadata_provider.goodreads import Goodreads


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "metadata"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("provider_id", ["goodreads", "bolcom"])
def test_best_effort_providers_default_off_but_explicit_choice_wins(provider_id):
    assert metadata_provider_enabled(provider_id, {}) is False
    assert metadata_provider_enabled(provider_id, {provider_id: True}) is True
    assert metadata_provider_enabled(provider_id, {provider_id: False}) is False


def test_goodreads_scraper_does_not_advertise_dead_api_key_signup():
    source = (Path(__file__).resolve().parents[2] / "cps" / "search_metadata.py").read_text()
    registry = source.split("PROVIDER_KEY_REGISTRY = {", 1)[1].split("\n}\n", 1)[0]
    assert '"goodreads"' not in registry


def test_goodreads_recorded_search_and_book_fixture():
    links = Goodreads._parse_search(fixture("goodreads_search.html"))
    assert links == ["https://www.goodreads.com/book/show/4671.The_Great_Gatsby"]
    record = Goodreads._parse_book(fixture("goodreads_book.html"), links[0], "generic.svg")
    assert record.title == "The Great Gatsby"
    assert record.authors == ["F. Scott Fitzgerald"]
    assert record.identifiers == {"goodreads": "4671", "isbn": "9780743273565"}
    assert record.cover.endswith("gatsby.jpg")
    assert record.rating == 4
    assert record.series == "Modern Library"
    assert record.series_index == 1.0


def test_bolcom_recorded_search_and_book_fixture():
    links = BolCom._parse_search(fixture("bolcom_search.html"))
    assert links == ["https://www.bol.com/nl/nl/p/de-ontdekking-van-de-hemel/1001004001234567/"]
    record = BolCom._parse_book(fixture("bolcom_book.html"), links[0], "generic.svg")
    assert record.title == "De ontdekking van de hemel"
    assert record.authors == ["Harry Mulisch"]
    assert record.identifiers == {"bol": "1001004001234567", "isbn": "9789023460005"}
    assert record.cover.startswith("https://media.s-bol.com/")
    assert record.rating == 5
    assert record.languages == ["nld"]


@pytest.mark.parametrize("provider", [Goodreads, BolCom])
@pytest.mark.parametrize("bad", [None, "", "not html", 7, [], {}])
def test_malformed_empty_and_wrong_type_pages_are_none_or_empty(provider, bad):
    assert provider._parse_search(bad) == []
    assert provider._parse_book(bad, "https://example.invalid/book", "generic.svg") is None


@pytest.mark.parametrize("provider", [Goodreads(), BolCom()])
@pytest.mark.parametrize("query", [None, "", "   ", 123])
def test_negative_queries_do_not_touch_network(provider, query):
    with patch("requests.Session.get") as get:
        assert provider.search(query) == []
    get.assert_not_called()


@pytest.mark.parametrize("provider", [Goodreads(), BolCom()])
@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_network_down_and_timeout_gracefully_return_empty(provider, failure):
    with patch("requests.Session.get", side_effect=failure):
        assert provider.search("a real title") == []


@pytest.mark.parametrize("provider,search_fixture", [
    (Goodreads(), "goodreads_search.html"),
    (BolCom(), "bolcom_search.html"),
])
def test_product_fetch_failure_does_not_escape_or_return_partial_garbage(provider, search_fixture):
    search_response = Mock(text=fixture(search_fixture))
    search_response.raise_for_status.return_value = None
    with patch("requests.Session.get", side_effect=[search_response, requests.Timeout("slow")]):
        assert provider.search("book") == []
