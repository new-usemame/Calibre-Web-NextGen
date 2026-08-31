# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #303 — a throttled scrape must not read as "no such book".

The reporter found a book on the first click of Get Metadata and not on the
second, with nothing changed in between. Our Goodreads provider keeps no cache
and opens a fresh connection per search, so there was no state on our side that
could differ between the two clicks: the second request was refused upstream.

``Goodreads.search`` caught every ``requests.RequestException`` — including the
``HTTPError`` that ``raise_for_status()`` raises on 429/403 — and returned
``[]``. The search layer then saw zero results with no exception and labelled
the row through ``_classify_empty_provider``, whose default is "No results for
this query". Being throttled and the book not existing rendered identically.

Goodreads answers a genuine no-match with 200 and an empty result list, so a
status refusal never means "no such book". These tests pin that a refusal
reaches the search layer, and that the layer names it without sending a user
after an API key that does not exist for a keyless scraper.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from cps.metadata_provider.bolcom import BolCom
from cps.metadata_provider.goodreads import Goodreads

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "metadata"
SEARCH_META = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "cps", "search_metadata.py")
)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _classifier():
    """Extract ``_classify_provider_failure`` plus the registry it consults.

    ``cps/search_metadata.py`` cannot be imported in the unit env (it pulls
    ``cwa_db``, which only exists in the container). The registry is exec'd from
    the same source rather than restated here, so the test cannot drift from the
    real single source of truth for which providers take an API key.
    """
    tree = ast.parse(Path(SEARCH_META).read_text(encoding="utf-8"))
    wanted = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_classify_provider_failure")
        or (isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "PROVIDER_KEY_REGISTRY" for t in node.targets))
    ]
    assert len(wanted) == 2, "expected PROVIDER_KEY_REGISTRY and _classify_provider_failure"
    ns: dict = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), SEARCH_META, "exec"), ns)
    return ns["_classify_provider_failure"], ns["PROVIDER_KEY_REGISTRY"]


def http_error(status: int, url: str = "https://www.goodreads.com/search?q=x"):
    """An HTTPError shaped like the one ``raise_for_status()`` raises."""
    response = Mock(status_code=status)
    reason = {429: "Too Many Requests", 403: "Forbidden", 404: "Not Found"}[status]
    return requests.HTTPError(f"{status} Client Error: {reason} for url: {url}",
                              response=response)


def provider_stub(provider_id: str):
    return Mock(__id__=provider_id)


# --------------------------------------------------------------------------
# The provider must let a refusal out instead of degrading it to "no results".
# --------------------------------------------------------------------------

@pytest.mark.parametrize("provider", [Goodreads(), BolCom()])
@pytest.mark.parametrize("status", [429, 403])
def test_search_refusal_reaches_the_caller(provider, status):
    with patch("requests.Session.get", side_effect=http_error(status)):
        with pytest.raises(requests.HTTPError):
            provider.search("Nine Month Contract")


@pytest.mark.parametrize("provider,search_fixture", [
    (Goodreads(), "goodreads_search.html"),
    (BolCom(), "bolcom_search.html"),
])
def test_refused_book_pages_report_the_refusal_when_nothing_was_read(provider, search_fixture):
    """Search matched, every page was refused: that is not an empty result."""
    search_response = Mock(text=fixture(search_fixture))
    search_response.raise_for_status.return_value = None
    with patch("requests.Session.get", side_effect=[search_response, http_error(429)]):
        with pytest.raises(requests.HTTPError):
            provider.search("book")


def test_partial_book_page_refusal_still_returns_what_was_read():
    """A refusal must not throw away the records we did get."""
    search_response = Mock(
        text='<a href="/book/show/1.A"></a><a href="/book/show/4671.Gatsby"></a>')
    search_response.raise_for_status.return_value = None
    book_response = Mock(text=fixture("goodreads_book.html"))
    book_response.raise_for_status.return_value = None
    with patch("requests.Session.get",
               side_effect=[search_response, http_error(429), book_response]):
        records = Goodreads().search("book")
    assert [r.title for r in records] == ["The Great Gatsby"]


@pytest.mark.parametrize("provider", [Goodreads(), BolCom()])
@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_unreachable_still_degrades_quietly(provider, failure):
    """Unchanged contract: we could not reach them, which is not a refusal."""
    with patch("requests.Session.get", side_effect=failure):
        assert provider.search("a real title") == []


# --------------------------------------------------------------------------
# The search layer must name the refusal, and not misadvise a keyless scraper.
# --------------------------------------------------------------------------

def test_throttling_is_reported_as_rate_limited_in_plain_english():
    classify, _ = _classifier()
    status, message = classify(http_error(429), provider_stub("goodreads"))
    assert status == "rate_limited"
    # The message is rendered verbatim to the user by get_meta.js, so it must
    # read as a sentence and must not leak the request URL.
    assert "goodreads.com/search" not in message
    assert message.strip().endswith(".")


def test_keyless_scraper_refusal_is_blocked_not_a_missing_key():
    """A 403 tells a Hardcover user to fix their key. Goodreads has no key to
    fix — its API shut in 2020, which is why it is scraped at all."""
    classify, registry = _classifier()
    assert "goodreads" not in registry and "bolcom" not in registry
    for provider_id in ("goodreads", "bolcom"):
        status, message = classify(http_error(403), provider_stub(provider_id))
        assert status == "blocked", provider_id
        assert "api key" not in message.lower(), provider_id


def test_key_taking_provider_still_reports_a_missing_key():
    classify, registry = _classifier()
    assert "hardcover" in registry
    status, _ = classify(http_error(403), provider_stub("hardcover"))
    assert status == "missing_key"


def test_a_book_id_containing_403_is_not_mistaken_for_a_refusal():
    """``str(HTTPError)`` carries the request URL, and Goodreads book ids are
    plain integers — /book/show/4031234 contains "403". Matching the text alone
    turned an ordinary 404 into a refusal."""
    classify, _ = _classifier()
    exc = http_error(404, "https://www.goodreads.com/book/show/4031234.Some_Book")
    status, _ = classify(exc, provider_stub("goodreads"))
    assert status == "error"


def test_provider_argument_is_optional_and_preserves_legacy_classification():
    """cps/services/cover_picker.py defaults this callable to a one-arg lambda,
    so the second parameter must stay optional."""
    classify, _ = _classifier()
    assert classify(http_error(429))[0] == "rate_limited"
    assert classify(http_error(403))[0] == "missing_key"
    assert classify(ValueError("token missing"))[0] == "missing_key"
    assert classify(ValueError("something else"))[0] == "error"
