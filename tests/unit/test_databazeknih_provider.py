# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tests for the Databáze knih (databazeknih.cz) metadata provider.

The parsers are exercised against captured, trimmed copies of the live pages
(`tests/fixtures/databazeknih/`, 2026-10): a Czech edition (Poštovní úřad,
Pragma 1996), its Slovak counterpart (Poštový úrad, Citadella 2017), the
"Více info…" fragments for both and a title search result list. The
provider's search() flow is then driven through a fake HTTP session so no
test touches the network.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from cps.metadata_provider import databazeknih

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "databazeknih"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dk():
    return databazeknih


@pytest.fixture(autouse=True)
def _stable_language_names(dk, monkeypatch):
    """Under the full suite the real cps.isoLanguages is loaded and turns
    'slk' into a localized name; pin it to the ISO code so assertions do not
    depend on which test module imported cps first."""
    monkeypatch.setattr(dk, "get_language_name", lambda _locale, code: code)


# -- pure helpers ------------------------------------------------------------

def test_normalize_strips_czech_and_slovak_diacritics(dk):
    assert dk.normalize("Poštový úrad") == "postovy urad"
    assert dk.normalize("Poštovní úřad") == "postovni urad"
    assert dk.normalize("Ľúbostné príbehy") == "lubostne pribehy"


@pytest.mark.parametrize("query, expected", [
    ("978-80-8182-096-0", "9788081820960"),
    ("isbn 80-7205-048-6", "8072050486"),
    ("Poštový úrad Bukowski", None),
    ("1984 George Orwell", None),
])
def test_extract_isbn(dk, query, expected):
    assert dk.extract_isbn(query) == expected


def test_book_id_from_url(dk):
    assert dk.book_id_from_url("/prehled-knihy/postovni-urad-1781") == ("postovni-urad-1781", "1781")
    assert dk.book_id_from_url(
        "https://www.databazeknih.cz/prehled-knihy/henry-chinaski-postovy-urad-499580?x=1"
    ) == ("henry-chinaski-postovy-urad-499580", "499580")
    assert dk.book_id_from_url("https://www.databazeknih.cz/vyhledavani/knihy?q=x") == (None, None)


def test_big_cover_candidate(dk):
    assert dk.big_cover_candidate(
        "https://www.databazeknih.cz/img/books/17_/1781/bmid_postovni-urad-4A8-1781.png?v=1"
    ) == "https://www.databazeknih.cz/img/books/17_/1781/big_postovni-urad-4A8-1781.png"
    assert dk.big_cover_candidate("https://x/img/big_a.jpg") is None
    assert dk.big_cover_candidate("") is None


# -- search results ------------------------------------------------------------

def test_search_results_parsed(dk):
    hits = dk.parse_search_results(_fixture("search_postovni_urad.html"))
    assert len(hits) == 8
    first = hits[0]
    assert first == {
        "id": "1781",
        "slug_id": "postovni-urad-1781",
        "title": "Poštovní úřad",
        "authors": ["Charles Bukowski"],
        "year": "1996",
        "url": "https://www.databazeknih.cz/prehled-knihy/postovni-urad-1781",
    }
    # Each edition appears once even though the row links it twice
    # (cover + title).
    assert len({h["id"] for h in hits}) == len(hits)


def test_search_ranking_prefers_author_match(dk):
    hits = dk.parse_search_results(_fixture("search_postovni_urad.html"))
    tokens = ["Poštovní", "úřad", "Thákur"]
    hits.sort(key=lambda h: dk.score_match(tokens, h), reverse=True)
    assert hits[0]["id"] == "13404"


# -- edition pages ---------------------------------------------------------------

def test_more_info_fragment(dk):
    info = dk.parse_more_info(_fixture("more_info_1781.html"))
    assert info["Originální název"] == "Post Office, 1971"
    assert info["Překlad"] == "Ladislav Šenkyřík"
    assert info["Počet stran"] == "136"
    assert info["Jazyk vydání"] == "český"
    assert dk.parse_more_info("") == {}


def test_czech_edition_page(dk):
    info = dk.parse_more_info(_fixture("more_info_1781.html"))
    data = dk.parse_book_page(
        _fixture("book_1781.html"),
        "https://www.databazeknih.cz/prehled-knihy/postovni-urad-1781",
        info,
    )
    assert data["title"] == "Poštovní úřad"
    assert data["authors"] == ["Charles Bukowski"]
    assert data["series"] == "Henry Chinaski"
    assert data["series_index"] == 1
    assert data["publisher"] == "Pragma"
    assert data["publishedDate"] == "1996-01-01"
    assert data["language"] == "ces"
    assert data["rating"] == 4
    assert data["identifiers"] == {"databazeknih": "postovni-urad-1781", "isbn": "8072050486"}
    assert data["tags"][:2] == ["Literatura světová", "Romány"]
    assert "prvotina" in data["tags"]
    # Full description, not the 300-char JSON-LD teaser, and without the
    # "… celý text" toggle link.
    assert "je velmi přesvědčivý" in data["description"]
    assert "celý text" not in data["description"]
    assert "Originální název: Post Office, 1971" in data["description"]
    assert "Překlad: Ladislav Šenkyřík" in data["description"]


def test_slovak_edition_page(dk):
    info = dk.parse_more_info(_fixture("more_info_499580.html"))
    data = dk.parse_book_page(
        _fixture("book_499580.html"),
        "https://www.databazeknih.cz/prehled-knihy/henry-chinaski-postovy-urad-499580",
        info,
    )
    assert data["title"] == "Poštový úrad"
    assert data["publisher"] == "Citadella (SK)"
    assert data["publishedDate"] == "2017-01-01"
    assert data["language"] == "slk"
    assert data["identifiers"]["isbn"] == "9788081820960"
    assert data["identifiers"]["databazeknih"] == "henry-chinaski-postovy-urad-499580"
    # Line breaks in the blurb become separate paragraphs.
    assert data["description"].count("<p>") >= 3
    assert "Nina Morgenstein" in data["description"]


# -- search() end to end with a fake session ------------------------------------------

class _FakeResponse:
    def __init__(self, url, text="", status=200, content_type="text/html", location=None):
        self.url = url
        self.text = text
        self.status_code = status
        self.ok = status < 400
        self.headers = {"content-type": content_type, "Location": location}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(response=self)


class _FakeSession:
    """Routes the provider's requests to fixtures."""

    BASE = "https://www.databazeknih.cz"

    def __init__(self, routes, head_ok=False):
        self.routes = routes
        self.head_ok = head_ok
        self.calls = []
        self.headers = {}

    def get(self, url, timeout=None, **_kw):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response(url) if callable(response) else response
        return _FakeResponse(url, status=404)

    def head(self, url, timeout=None, **_kw):
        self.calls.append(url)
        return _FakeResponse(url, status=200 if self.head_ok else 404, content_type="image/png")


def _provider(dk, routes, head_ok=False):
    provider = dk.DatabazeKnih()
    provider.session = _FakeSession(routes, head_ok=head_ok)
    return provider


def test_isbn_query_follows_redirect_to_edition(dk):
    base = _FakeSession.BASE
    book_url = f"{base}/prehled-knihy/henry-chinaski-postovy-urad-499580"
    provider = _provider(dk, {
        f"{base}/search?q=9788081820960": _FakeResponse(
            f"{base}/search?q=9788081820960", status=302, location=book_url.removeprefix(base)
        ),
        book_url: _FakeResponse(book_url, _fixture("book_499580.html")),
        f"{base}/book-detail-more-info/499580": _FakeResponse("", _fixture("more_info_499580.html")),
    })
    records = provider.search("978-80-8182-096-0")
    assert len(records) == 1
    record = records[0]
    assert record.title == "Poštový úrad"
    assert record.source.id == "databazeknih"
    assert record.url == book_url
    assert record.languages == ["slk"]


def test_auto_fetch_isbn_query_form_follows_redirect(dk):
    """The auto-fetch fallback sends "isbn:<isbn>" (SUPPORTS_ISBN_QUERY)."""
    assert dk.DatabazeKnih.SUPPORTS_ISBN_QUERY is True
    base = _FakeSession.BASE
    book_url = f"{base}/prehled-knihy/henry-chinaski-postovy-urad-499580"
    provider = _provider(dk, {
        f"{base}/search?q=9788081820960": _FakeResponse(
            f"{base}/search?q=9788081820960", status=302, location=book_url.removeprefix(base)
        ),
        book_url: _FakeResponse(book_url, _fixture("book_499580.html")),
        f"{base}/book-detail-more-info/499580": _FakeResponse("", _fixture("more_info_499580.html")),
    })
    records = provider.search("isbn:9788081820960")
    assert [r.url for r in records] == [book_url]
    assert provider.session.calls[0] == f"{base}/search?q=9788081820960"


def test_title_author_query_retries_without_author(dk):
    """The site searches titles only: 'Poštovní úřad Bukowski' yields
    nothing useful on the first try, so the provider drops the trailing
    word and searches again."""
    base = _FakeSession.BASE
    empty = "<html><body><div id='content'></div></body></html>"

    def search(url):
        if "Bukowski" in url:
            return _FakeResponse(f"{base}/vyhledavani/knihy?q=x", empty)
        return _FakeResponse(f"{base}/vyhledavani/knihy?q=x", _fixture("search_postovni_urad.html"))

    def book(url):
        if url.endswith("-1781"):
            return _FakeResponse(url, _fixture("book_1781.html"))
        return _FakeResponse(url, status=404)

    provider = _provider(dk, {
        f"{base}/search?q=": search,
        f"{base}/prehled-knihy/": book,
        f"{base}/book-detail-more-info/1781": _FakeResponse("", _fixture("more_info_1781.html")),
    }, head_ok=True)
    records = provider.search("Poštovní úřad Bukowski")
    search_calls = [c for c in provider.session.calls if "/search?q=" in c]
    assert len(search_calls) == 2
    # Bukowski's edition ranks first; editions that 404 are dropped.
    assert [r.title for r in records] == ["Poštovní úřad"]
    assert records[0].authors == ["Charles Bukowski"]
    # big_ cover variant is used when it exists.
    assert records[0].cover.endswith("/big_postovni-urad-4A8-1781.png")


def test_explicit_identifier_query(dk):
    base = _FakeSession.BASE
    provider = _provider(dk, {
        f"{base}/prehled-knihy/postovni-urad-1781": _FakeResponse(
            f"{base}/prehled-knihy/postovni-urad-1781", _fixture("book_1781.html")
        ),
        f"{base}/book-detail-more-info/1781": _FakeResponse("", _fixture("more_info_1781.html")),
    })
    records = provider.search("databazeknih:postovni-urad-1781")
    assert [r.identifiers["databazeknih"] for r in records] == ["postovni-urad-1781"]


def test_blocked_site_surfaces_as_provider_refusal(dk):
    base = _FakeSession.BASE
    provider = _provider(dk, {f"{base}/search": _FakeResponse(f"{base}/search", status=429)})
    with pytest.raises(dk.ProviderRefused) as exc:
        provider.search("Strach Karika")
    assert exc.value.status == "rate_limited"


def test_inactive_provider_returns_empty(dk):
    provider = _provider(dk, {})
    provider.set_status(False)
    assert provider.search("anything") == []
    assert provider.session.calls == []


def test_other_editions_parsed_from_more_info(dk):
    editions = dk.parse_other_editions(_fixture("more_info_1781.html"))
    assert editions == [{
        "id": "499580",
        "slug_id": "henry-chinaski-postovy-urad-499580",
        "title": "Poštový úrad",
        "url": "https://www.databazeknih.cz/prehled-knihy/henry-chinaski-postovy-urad-499580",
    }]
    assert dk.parse_other_editions("") == []


def test_slovak_title_found_through_czech_edition(dk):
    """The site's search does not find 'Poštový úrad' (it returns near
    spellings), but the Czech edition lists the Slovak one under "Další
    název". The Slovak edition matches the query better, so it is fetched
    and ranked first."""
    base = _FakeSession.BASE

    def book(url):
        if url.endswith("-1781"):
            return _FakeResponse(url, _fixture("book_1781.html"))
        if url.endswith("-499580"):
            return _FakeResponse(url, _fixture("book_499580.html"))
        return _FakeResponse(url, status=404)

    provider = _provider(dk, {
        f"{base}/search?q=": _FakeResponse(
            f"{base}/vyhledavani/knihy?q=x", _fixture("search_postovni_urad.html")
        ),
        f"{base}/prehled-knihy/": book,
        f"{base}/book-detail-more-info/1781": _FakeResponse("", _fixture("more_info_1781.html")),
        f"{base}/book-detail-more-info/499580": _FakeResponse("", _fixture("more_info_499580.html")),
    })
    records = provider.search("Poštový úrad Bukowski")
    assert [r.title for r in records] == ["Poštový úrad", "Poštovní úřad"]
    assert records[0].languages == ["slk"]
    assert records[0].publisher == "Citadella (SK)"


@pytest.fixture
def catalog(dk):
    base = dk.BASE_URL
    book_url = f"{base}/prehled-knihy/postovni-urad-1781"
    return _provider(dk, {
        f"{base}/search?q=": _FakeResponse(book_url, _fixture("book_1781.html")),
        f"{base}/book-detail-more-info/1781": _FakeResponse(
            f"{base}/book-detail-more-info/1781", _fixture("more_info_1781.html")
        ),
    })


def test_manual_search_contacts_catalog_only_after_user_picks_it(monkeypatch, catalog):
    """A fresh manual search sends zero catalog requests until the user turns it on."""
    from cps import app, cover_picker, search_metadata as search

    user = SimpleNamespace(view_settings={})
    monkeypatch.setattr(search, "current_user", user)
    monkeypatch.setattr(cover_picker, "current_user", user)
    monkeypatch.setattr(search, "cl", [catalog])
    monkeypatch.setattr(search, "_get_global_provider_enabled_map", lambda: {})
    monkeypatch.setattr(search, "_get_provider_hierarchy", lambda: [])
    monkeypatch.setattr(search, "boost_covers", lambda _: None)
    monkeypatch.setattr(search, "get_locale", lambda: "en")
    monkeypatch.setattr(search.ub, "session", SimpleNamespace(commit=lambda: None))

    def search_for_book():
        with app.test_request_context("/metadata/search", method="POST", data={"query": "Poštovní úřad"}):
            return inspect.unwrap(search.metadata_search)().get_json()

    assert search_for_book()["results"] == []
    assert not cover_picker._is_provider_enabled_for_user(catalog)

    provider_path = "/metadata/provider/databazeknih"
    with app.test_request_context(provider_path, method="POST", json={
        "id": "databazeknih", "value": False, "initial": False, "query": "Poštovní úřad",
    }):
        inspect.unwrap(search.metadata_change_active_provider)("databazeknih")
    assert catalog.session.calls == []

    with app.test_request_context(provider_path, method="POST", json={"id": "databazeknih", "value": True}):
        inspect.unwrap(search.metadata_change_active_provider)("databazeknih")

    payload = search_for_book()
    assert cover_picker._is_provider_enabled_for_user(catalog)
    assert catalog.session.calls
    assert [record["title"] for record in payload["results"]] == ["Poštovní úřad"]


@pytest.mark.parametrize(("settings", "expected"), [
    ({}, False),
    ({"metadata_providers_enabled": '{"databazeknih": true}'}, False),
    ({"metadata_provider_hierarchy": '["databazeknih"]'}, True),
    ({"metadata_provider_hierarchy": '["databazeknih"]',
      "metadata_providers_enabled": '{"databazeknih": false}'}, False),
])
def test_auto_fetch_contacts_catalog_only_in_enabled_order(monkeypatch, catalog, settings, expected):
    """Ingest only sends catalog requests when the saved order includes it and it is available."""
    from cps import metadata_helper as metadata

    book = SimpleNamespace(title="Poštovní úřad", authors=[SimpleNamespace(name="Charles Bukowski")], identifiers=[])
    session = SimpleNamespace(close=lambda: None, rollback=lambda: None)
    library = SimpleNamespace(session=session, get_book=lambda _book_id: book)
    monkeypatch.setattr(metadata.db, "CalibreDB", type("Library", (), {
        "session_factory": True,
        "__new__": lambda cls, **kwargs: library,
    }))
    monkeypatch.setattr(metadata, "CWA_DB", lambda: SimpleNamespace(
        get_cwa_settings=lambda: {"auto_metadata_fetch_enabled": True, **settings}
    ))
    monkeypatch.setattr(metadata, "metadata_providers", [catalog])
    applied = []
    monkeypatch.setattr(metadata, "_apply_metadata_to_book",
                        lambda _book, record, _library: applied.append(record.title) or True)

    assert metadata.fetch_and_apply_metadata(1) is expected
    assert bool(catalog.session.calls) is expected
    assert applied == (["Poštovní úřad"] if expected else [])


def test_catalog_search_does_not_follow_off_site_redirect(dk):
    url = f"{dk.BASE_URL}/search?q=9788081820960"
    provider = _provider(dk, {
        url: _FakeResponse(url, status=302, location="https://example.invalid/book"),
    })

    assert provider.search("9788081820960") == []
    assert provider.session.calls == [url]
