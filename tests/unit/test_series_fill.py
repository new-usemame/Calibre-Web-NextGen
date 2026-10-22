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


@pytest.mark.parametrize("a, b", [
    ("進撃の巨人 2", "ワンピース 2"),        # non-Latin letters are letters, not punctuation
    ("Re:Zero", "Re:Zero 外伝"),
])
def test_non_latin_names_keep_their_letters(a, b):
    assert m._series_key(a) != m._series_key(b)


def test_two_different_bracket_notes_are_two_series():
    cdb = _SeriesCDB(["Chronicles of Narnia (Chronological Order)", "Star Wars (Canon)"])
    assert m._library_series_like(cdb, "The Chronicles of Narnia (Publication Order)") is None
    assert m._library_series_like(cdb, "Star Wars (Legends)") is None
    assert m._library_series_like(cdb, "Star Wars").name == "Star Wars (Canon)"


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


def test_a_shared_stem_by_another_author_is_another_series():
    paolini = NS(name="The Inheritance Cycle",
                 books=[NS(authors=[NS(name="Christopher Paolini")])])
    cdb = _SeriesCDB([])
    cdb.session.query = lambda model: NS(all=lambda: [paolini])
    jemisin = _Book(authors=["N. K. Jemisin"])
    assert m._library_series_like(cdb, "Inheritance Trilogy", jemisin) is None
    assert m._library_series_like(cdb, "Inheritance Trilogy", _Book(authors=["Christopher Paolini"])) is paolini


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
    filled = m._find_missing_series(book, "Cross Justice James Patterson", "ibdb", hierarchy,
                                    enabled or {})
    return filled, book


def test_a_series_left_empty_is_found_at_the_next_series_provider(monkeypatch):
    ibdb = _Provider("ibdb", [_hit("", 0)], fills=False)
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    found, _ = _fill(monkeypatch, [ibdb, hardcover], ["ibdb", "google", "hardcover"])
    assert found == ("Alex Cross", 23)
    assert ibdb.queries == []          # the provider already applied is not asked again


def test_the_configured_order_comes_first_then_providers_it_leaves_out(monkeypatch):
    dnb = _Provider("dnb", [_hit("Alex Cross", 23)])
    kobo = _Provider("kobo", [_hit("", 0)])
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23.0)])
    filled, _ = _fill(monkeypatch, [dnb, kobo, hardcover], ["ibdb", "kobo", "hardcover"])
    assert filled and kobo.queries and hardcover.queries and dnb.queries == []


def test_outside_the_order_only_hardcover_and_kobo_are_asked(monkeypatch):
    # A stock order (google, openlibrary, ibdb, dnb, douban) names no
    # scraper. Amazon and the like throttle bots, so the fill must not start
    # scraping them for every book without a series; Hardcover and Kobo carry
    # series data and are asked after the order.
    amazon = _Provider("amazon", [_hit("Alex Cross", 23)])
    amazon_jp = _Provider("amazon_jp", [_hit("Alex Cross", 23)])
    kobo = _Provider("kobo", [_hit("Alex Cross", 23)])
    hardcover = _Provider("hardcover", [])
    filled, book = _fill(monkeypatch, [amazon, amazon_jp, hardcover, kobo], ["ibdb", "dnb"])
    assert filled == ("Alex Cross", 23) and hardcover.queries and kobo.queries
    assert amazon.queries == [] and amazon_jp.queries == []


def test_a_different_book_is_not_a_source_of_series(monkeypatch):
    wrong = _Provider("hardcover", [_hit("Women's Murder Club", 7, title="7th Heaven")])
    filled, book = _fill(monkeypatch, [wrong], ["hardcover"])
    assert filled is None


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
    assert m._find_missing_series(book, "q", "ibdb", ["hardcover"], {}) is None
    assert hardcover.queries == []


def _record(title="Cross Justice"):
    """A complete provider record with no series, as IBDb or Google return."""
    return NS(title=title, authors=["James Patterson"], description="", publisher="", tags=[],
              publishedDate=None, rating=None, identifiers={}, cover=None, series="", series_index=None)


def _auto_fetch(monkeypatch, providers, hierarchy):
    """Run fetch_and_apply_metadata for one ingested book against fakes."""
    _patch_settings(monkeypatch, auto_metadata_fetch_enabled=True,
                    metadata_provider_hierarchy=json.dumps(hierarchy))
    monkeypatch.setattr(m, "metadata_providers", providers)
    book = _book()
    book.title = "Cross Justice"
    cdb = _SeriesCDB([])
    cdb.get_book = lambda book_id: book
    cdb.session.close = lambda: None
    monkeypatch.setattr(m.db, "CalibreDB", lambda *a, **k: cdb)
    monkeypatch.setattr(m.db.CalibreDB, "session_factory", object(), raising=False)
    return m.fetch_and_apply_metadata(book.id), book


def test_auto_fetch_fills_the_series_the_matched_record_lacked(monkeypatch, _no_file_embed):
    ibdb = _Provider("ibdb", [_record()], fills=False)
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    applied, book = _auto_fetch(monkeypatch, [ibdb, hardcover], ["ibdb", "hardcover"])
    assert applied and [s.name for s in book.series] == ["Alex Cross"]
    assert book.series_index == "23.0" and len(hardcover.queries) == 1
    # One apply, one file-embed record: two records written in the same second
    # share a file name, and the second one dropped the first one's cover flag.
    assert _no_file_embed == [{"metadata": True}]


def test_auto_fetch_stops_at_the_first_record_even_when_the_fill_breaks(monkeypatch):
    # Before the guard, an exception in the fill sent the provider loop on to
    # apply the next provider's whole record over the first one.
    def broken_gate(results, *args, **kwargs):
        if results and results[0].title == "Cross Justice" and results[0].series:
            raise AttributeError("result without authors")
        return results[0] if results else None
    monkeypatch.setattr(m, "_select_metadata_result", broken_gate)
    ibdb = _Provider("ibdb", [_record()], fills=False)
    hardcover = _Provider("hardcover", [_hit("Alex Cross", 23)])
    other = _Provider("google", [_record("A Different Record")], fills=False)
    applied, book = _auto_fetch(monkeypatch, [ibdb, hardcover, other], ["ibdb", "google"])
    assert applied and book.title == "Cross Justice" and other.queries == []


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
    assert m._find_missing_series(_book(), "q", "kobo", ["kobo", "hardcover"], {})
    assert kobo.queries == [] and hardcover.queries


def test_a_provider_that_is_off_by_default_is_asked_only_when_turned_on(monkeypatch):
    goodreads = _Provider("goodreads", [_hit("Alex Cross", 23)])
    filled, _ = _fill(monkeypatch, [goodreads], ["ibdb", "goodreads"])
    assert not filled and goodreads.queries == []
    filled, _ = _fill(monkeypatch, [goodreads], ["ibdb", "goodreads"], enabled={"goodreads": True})
    assert filled and goodreads.queries


def test_a_failing_fill_never_reaches_the_provider_loop(monkeypatch):
    # The fill runs inside fetch_and_apply_metadata's per-provider try, after
    # the main record was committed and before the loop's break. Anything it
    # raised there made the loop move on and apply the NEXT provider's whole
    # record over the one just written. It must report "no series" instead.
    def broken_gate(*args, **kwargs):
        raise AttributeError("result without authors")
    monkeypatch.setattr(m, "_select_metadata_result", broken_gate)
    filled, book = _fill(monkeypatch, [_Provider("hardcover", [_hit("Alex Cross", 23)])], ["hardcover"])
    assert filled is None
