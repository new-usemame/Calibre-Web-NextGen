# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Series and series order from more than the first provider that matches.

Auto-fetch applies the first provider whose result passes the match gate and
stops. Google Books, Open Library and IBDb never return a series, so with
any of them ahead of a provider that does, a book got no series at all. A
household library's order was ``ibdb, google, amazon, hardcover, kobo``:
Hardcover's series was never asked for whenever IBDb matched.

Also pinned here: a source's other spelling of a series the library already
has ("Dune Chronicles" for "Dune") reuses that series instead of starting a
second one, and fractional positions (24.5) survive Kobo, Amazon and
Goodreads parsing.
"""
import json
from types import SimpleNamespace as NS

import pytest
from bs4 import BeautifulSoup

import cps.metadata_helper as m
from cps.metadata_provider.amazon import Amazon
from cps.metadata_provider.goodreads import Goodreads
from cps.metadata_provider.kobo import Kobo
from tests.unit.test_metadata_autofetch_safety import _Book, _FakeCDB, _patch_settings

pytestmark = pytest.mark.unit


# --- series names -------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Dune", "Dune Chronicles"),
    ("Red Rising", "Red Rising Saga"),
    ("Alex Cross", "Alex Cross Series"),
    ("The Expanse", "Expanse"),
    ("Montréal Noir", "montreal noir"),
    ("The Chronicles of Narnia", "The Chronicles of Narnia (Publication Order)"),
    ("New Tales of the Vampires", "New Tales of the Vampires dylogy"),
])
def test_other_spellings_of_one_series_share_a_key(a, b):
    assert m._series_key(a) == m._series_key(b)


@pytest.mark.parametrize("a, b", [
    ("Alex Cross", "Detective Cross"),   # a real rename: a value-map rule, not a guess
    ("Dune", "Dune Messiah"),
])
def test_different_series_keep_different_keys(a, b):
    assert m._series_key(a) != m._series_key(b)


def test_a_kind_word_alone_is_kept():
    assert m._series_key("The Trilogy") == "trilogy"


class _SeriesCDB(_FakeCDB):
    def __init__(self, names):
        super().__init__()
        rows = [NS(name=n) for n in names]
        self.session.query = lambda model: NS(all=lambda: rows)


def test_a_new_spelling_reuses_the_librarys_series():
    found = m._library_series_like(_SeriesCDB(["Dune", "Alex Cross"]), "Dune Chronicles")
    assert found is not None and found.name == "Dune"


def test_two_library_series_with_one_key_reuse_neither():
    assert m._library_series_like(_SeriesCDB(["Dune", "The Dune Saga"]), "Dune Chronicles") is None


def test_the_main_apply_reuses_the_librarys_series(monkeypatch):
    _patch_settings(monkeypatch)
    book = _book()
    cdb = _SeriesCDB(["Red Rising"])
    m._apply_metadata_to_book(book, NS(
        title="", authors=[], description="", publisher="", tags=[], publishedDate=None, rating=None,
        identifiers={}, cover=None, series="Red Rising Saga", series_index=2), cdb)
    assert [s.name for s in book.series] == ["Red Rising"] and book.series_index == "2.0"


# --- the fill step ------------------------------------------------------------

class _Provider:
    def __init__(self, pid, results=(), fills=True, raises=None):
        self.__id__, self.__name__ = pid, pid
        self.FILLS_SERIES, self.active = fills, True
        self.results, self.raises, self.queries = list(results), raises, []

    def search(self, query, generic_cover="", locale="en"):
        self.queries.append(query)
        if self.raises:
            raise self.raises
        return self.results


def _hit(series, index, title="Cross Justice", authors=("James Patterson",)):
    return NS(title=title, authors=list(authors), identifiers={}, series=series, series_index=index)


def _book():
    book = _Book(authors=["James Patterson"])
    book.title = "Cross Justice"
    book.has_cover, book.last_modified, book.path = 0, None, "James Patterson/Cross Justice (1)"
    return book


@pytest.fixture(autouse=True)
def _no_file_embed(monkeypatch):
    queued = []
    monkeypatch.setattr(m, "_queue_metadata_enforcement", lambda book, changed: queued.append(changed))
    return queued


def _fill(monkeypatch, providers, hierarchy, enabled=None, **settings):
    _patch_settings(monkeypatch, **settings)
    monkeypatch.setattr(m, "metadata_providers", providers)
    book = _book()
    filled = m._fill_missing_series(book, "Cross Justice James Patterson", "ibdb", hierarchy,
                                    enabled or {}, _SeriesCDB([]))
    return filled, book


def test_a_series_left_empty_is_filled_from_the_next_series_provider(monkeypatch, _no_file_embed):
    ibdb = _Provider("ibdb", [_hit("", 0)], fills=False)
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    filled, book = _fill(monkeypatch, [ibdb, hardcover], ["ibdb", "google", "hardcover"])
    assert filled and [s.name for s in book.series] == ["Alex Cross"] and book.series_index == "23.0"
    assert ibdb.queries == []          # the provider already applied is not asked again
    # Only the series changed: the record's title was not applied.
    assert book.title == "Cross Justice"
    assert _no_file_embed == [{"metadata": True}]   # the file gets the series too


def test_the_configured_order_comes_first_then_providers_it_leaves_out(monkeypatch):
    wikidata = _Provider("wikidata", [_hit("Alex Cross", 23)])
    kobo = _Provider("kobo", [_hit("", 0)])
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23.0)])
    filled, _ = _fill(monkeypatch, [wikidata, kobo, hardcover], ["ibdb", "kobo", "hardcover"])
    assert filled and kobo.queries and hardcover.queries and wikidata.queries == []


def test_a_provider_outside_the_order_is_asked_last(monkeypatch):
    wikidata = _Provider("wikidata", [_hit("Alex Cross", 23)])
    hardcover = _Provider("hardcover", [])
    filled, book = _fill(monkeypatch, [wikidata, hardcover], ["ibdb", "hardcover"])
    assert filled and hardcover.queries and wikidata.queries
    assert [s.name for s in book.series] == ["Alex Cross"]


def test_a_different_book_is_not_a_source_of_series(monkeypatch):
    wrong = _Provider("hardcover", [_hit("Women's Murder Club", 7, title="7th Heaven")])
    filled, book = _fill(monkeypatch, [wrong], ["hardcover"])
    assert not filled and list(book.series) == []


def test_disabled_failing_and_non_series_providers_are_skipped(monkeypatch):
    off = _Provider("kobo", [_hit("Alex Cross", 23)])
    broken = _Provider("amazon", raises=RuntimeError("503"))
    plain = _Provider("google", [_hit("Alex Cross", 23)], fills=False)
    good = _Provider("hardcover", [_hit("Alex Cross", 23)])
    filled, _ = _fill(monkeypatch, [off, broken, plain, good], ["kobo", "amazon", "google", "hardcover"],
                      enabled={"kobo": False})
    assert filled and off.queries == [] and plain.queries == [] and broken.queries and good.queries


def test_at_most_three_providers_are_asked(monkeypatch):
    empty = [_Provider(f"p{i}", [_hit("", 0)]) for i in range(5)]
    filled, _ = _fill(monkeypatch, empty, [p.__id__ for p in empty])
    assert not filled and sum(1 for p in empty if p.queries) == m._SERIES_FILL_LIMIT


@pytest.mark.parametrize("settings, has_series", [
    ({"auto_metadata_update_series": False}, False),
    ({}, True),
])
def test_nothing_is_asked_when_series_are_off_or_already_set(monkeypatch, settings, has_series):
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    _patch_settings(monkeypatch, **settings)
    monkeypatch.setattr(m, "metadata_providers", [hardcover])
    book = _book()
    if has_series:
        book.series.append(NS(name="Alex Cross"))
    assert not m._fill_missing_series(book, "q", "ibdb", ["hardcover"], {}, _SeriesCDB([]))
    assert hardcover.queries == []


def test_auto_fetch_runs_the_fill_after_a_successful_apply():
    import inspect
    source = inspect.getsource(m.fetch_and_apply_metadata)
    applied = source.index("if _apply_metadata_to_book(book, metadata, calibre_db_instance):")
    assert source.index("_fill_missing_series(", applied) < source.index("break", applied)


# --- fractional positions in the parsers --------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Book 24 of 32: Alex Cross", ("Alex Cross", 24)),
    ("Book 24.5: Alex Cross", ("Alex Cross", 24.5)),
    ("Kindle Edition", (None, None)),
])
def test_amazon_series_bullet(text, expected):
    assert Amazon._series_from_bullet(text) == expected


def test_kobo_keeps_a_fractional_position():
    html = ('<div><h1 data-testid="title">Cross Kill</h1><dl>'
            '<dt data-testid="series-product-type-and-number">Book 23.5 -</dt>'
            '<dd data-testid="series-product-type-and-number">'
            '<a href="/ca/en/series/alex-cross"><span class="link--label">Alex Cross</span></a></dd>'
            '</dl></div>')
    assert Kobo()._parse_series_from_dom(BeautifulSoup(html, "html.parser")) == ("Alex Cross", 23.5)


def _goodreads_page(data):
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


def test_goodreads_reads_the_series_from_the_name_when_is_part_of_is_missing():
    record = Goodreads._parse_book(_goodreads_page({
        "@type": "Book", "name": "Cross Kill (Alex Cross, #23.5)",
        "author": [{"@type": "Person", "name": "James Patterson"}]}),
        "https://www.goodreads.com/book/show/1-cross-kill", "generic.svg")
    assert (record.title, record.series, record.series_index) == ("Cross Kill", "Alex Cross", 23.5)


def test_goodreads_is_part_of_wins_and_the_title_loses_the_suffix():
    record = Goodreads._parse_book(_goodreads_page({
        "@type": "Book", "name": "Red Rising (Red Rising Saga, #1)",
        "author": [{"@type": "Person", "name": "Pierce Brown"}],
        "isPartOf": {"@type": "BookSeries", "name": "Red Rising", "position": "1"}}),
        "https://www.goodreads.com/book/show/2-red-rising", "generic.svg")
    assert (record.title, record.series, record.series_index) == ("Red Rising", "Red Rising", 1.0)


def test_goodreads_leaves_a_title_without_the_suffix_alone():
    record = Goodreads._parse_book(_goodreads_page({
        "@type": "Book", "name": "Dune (40th Anniversary)",
        "author": [{"@type": "Person", "name": "Frank Herbert"}]}),
        "https://www.goodreads.com/book/show/3-dune", "generic.svg")
    assert record.title == "Dune (40th Anniversary)" and not record.series


def test_the_provider_already_applied_is_not_asked_again(monkeypatch):
    _patch_settings(monkeypatch)
    kobo = _Provider("kobo", [_hit("Alex Cross", 23)])
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    monkeypatch.setattr(m, "metadata_providers", [kobo, hardcover])
    assert m._fill_missing_series(_book(), "q", "kobo", ["kobo", "hardcover"], {}, _SeriesCDB([]))
    assert kobo.queries == [] and hardcover.queries
