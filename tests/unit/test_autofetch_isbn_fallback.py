# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-fetch retries an ISBN-capable provider by the book's ISBN.

A title search misses when the library spells the title differently from the
catalog — a Czech book stored as "Babicka" instead of "Babička". Providers that
declare ``SUPPORTS_ISBN_QUERY`` get a second search with ``isbn:<isbn>``; the
result still has to clear the same confidence gate.
"""

import json
from types import SimpleNamespace

import pytest

import cps.metadata_helper as m

pytestmark = pytest.mark.unit


def _record(title, isbn=None):
    return SimpleNamespace(title=title, authors=["Božena Němcová"],
                           identifiers={"isbn": isbn} if isbn else {})


def _provider(answers, supports_isbn=True):
    calls = []

    def search(query, generic_cover, locale):
        calls.append(query)
        return answers.get("isbn" if query.startswith("isbn:") else "title", [])

    provider = SimpleNamespace(__id__="p", __name__="p", active=True, search=search)
    if supports_isbn:
        provider.SUPPORTS_ISBN_QUERY = True
    return provider, calls


@pytest.fixture
def run(monkeypatch):
    applied = []

    def _run(provider, isbn="978-80-8182-096-0", *, title="Babicka", author="Bozena Nemcova"):
        identifiers = [SimpleNamespace(type="isbn", val=isbn)] if isbn else []
        book = SimpleNamespace(id=1, title=title,
                               authors=[SimpleNamespace(name=author)],
                               identifiers=identifiers)
        session = SimpleNamespace(close=lambda: None, rollback=lambda: None)
        cdb = SimpleNamespace(session=session, get_book=lambda _id: book)
        monkeypatch.setattr(m.db, "CalibreDB", type("CDB", (), {
            "session_factory": True,
            "__new__": lambda cls, *a, **k: cdb,
        }))
        settings = {
            "auto_metadata_fetch_enabled": True,
            "metadata_provider_hierarchy": json.dumps([provider.__id__]),
        }
        monkeypatch.setattr(m, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
        monkeypatch.setattr(m, "metadata_providers", [provider])
        monkeypatch.setattr(m, "_apply_metadata_to_book",
                            lambda _book, meta, _cdb: applied.append(meta) or True)
        return m.fetch_and_apply_metadata(1)

    _run.applied = applied
    return _run


@pytest.mark.parametrize(("isbn", "expected"), [
    ("978-80-8182-096-0", "9788081820960"),
    ("80-7205-048-6", "8072050486"),
])
def test_empty_title_search_falls_back_to_isbn(run, isbn, expected):
    hit = _record("Babička", expected)
    provider, calls = _provider({"isbn": [hit]})
    assert run(provider, isbn=isbn) is True
    assert calls == ["Babicka Bozena Nemcova", f"isbn:{expected}"]
    assert run.applied == [hit]


def test_unconfident_title_result_falls_back_to_isbn(run):
    hit = _record("Babička", "9788081820960")
    provider, calls = _provider({"title": [_record("Úplně jiná kniha")], "isbn": [hit]})
    assert run(provider) is True
    assert calls[-1] == "isbn:9788081820960"
    assert run.applied == [hit]


def test_confident_title_result_does_not_trigger_isbn_search(run):
    hit = _record("Babička")
    provider, calls = _provider({"title": [hit]})
    assert run(provider) is True
    assert calls == ["Babicka Bozena Nemcova"]


def test_isbn_result_still_has_to_pass_the_confidence_gate(run):
    provider, calls = _provider({"isbn": [_record("Úplně jiná kniha")]})
    assert run(provider) is False
    assert calls == ["Babicka Bozena Nemcova", "isbn:9788081820960"]
    assert run.applied == []


def test_provider_without_isbn_support_is_not_retried(run):
    provider, calls = _provider({}, supports_isbn=False)
    assert run(provider) is False
    assert calls == ["Babicka Bozena Nemcova"]


def test_book_without_isbn_is_not_retried(run):
    provider, calls = _provider({})
    assert run(provider, isbn=None) is False
    assert calls == ["Babicka Bozena Nemcova"]


def test_invalid_book_isbn_is_not_sent(run):
    provider, calls = _provider({})
    assert run(provider, isbn="978-80-8182-096-1") is False
    assert calls == ["Babicka Bozena Nemcova"]


def test_databazeknih_retries_by_isbn_after_the_title_misses(run):
    """Drive auto-fetch through the real provider and observe the captured edition applied."""
    from cps.metadata_provider import databazeknih
    from tests.unit.test_databazeknih_provider import _FakeResponse, _fixture, _provider

    base = databazeknih.BASE_URL
    isbn_url = f"{base}/search?q=9788081820960"
    book_path = "/prehled-knihy/henry-chinaski-postovy-urad-499580"
    book_url = f"{base}{book_path}"
    details_url = f"{base}/book-detail-more-info/499580"
    provider = _provider(databazeknih, {
        isbn_url: _FakeResponse(isbn_url, status=302, location=book_path),
        book_url: _FakeResponse(book_url, _fixture("book_499580.html")),
        details_url: _FakeResponse(details_url, _fixture("more_info_499580.html")),
    })

    assert run(provider, title="Postovy urad", author="Charles Bukowski") is True
    assert provider.session.calls[:3] == [
        f"{base}/search?q=Postovy%20urad%20Charles%20Bukowski", isbn_url, book_url,
    ]
    assert [record.title for record in run.applied] == ["Poštový úrad"]
