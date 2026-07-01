"""Regression tests for #582 — identifiers (Goodreads, StoryGraph, Hardcover,
Amazon, ISBN…) render as plain text in the new UI's book detail; they used to be
clickable links in the classic UI. serialize_book_detail now emits a safe http(s)
link + a display label per identifier so the SPA can render an anchor.
"""
import pathlib
from types import SimpleNamespace

import pytest

from cps.db import Identifiers
from cps.api.serializers import serialize_book_detail


def _book(identifiers):
    # Minimal stub: serialize_book_detail reads most attrs via getattr(..., None);
    # only id/title/series_index are direct. has_cover=0 → cover_url None.
    return SimpleNamespace(id=1, title="T", series_index="1", has_cover=0,
                           identifiers=identifiers)


@pytest.mark.unit
def test_known_providers_get_links_and_labels():
    ids = [
        Identifiers("12345", "goodreads", 1),
        Identifiers("B00XYZ", "amazon", 1),
        Identifiers("9780306406157", "isbn", 1),
        Identifiers("some-slug", "storygraph", 1),
    ]
    out = {d["type"]: d for d in serialize_book_detail(_book(ids))["identifiers"]}
    assert out["goodreads"]["url"] == "https://www.goodreads.com/book/show/12345"
    assert out["goodreads"]["label"] == "Goodreads"
    assert out["amazon"]["url"] == "https://amazon.com/dp/B00XYZ"
    assert out["isbn"]["url"] == "https://www.worldcat.org/isbn/9780306406157"
    assert out["storygraph"]["url"] == "https://app.thestorygraph.com/books/some-slug"
    assert out["storygraph"]["label"] == "StoryGraph"


@pytest.mark.unit
def test_unknown_identifier_has_no_link():
    """An identifier type with no URL rule stays plain text (url None)."""
    ids = [Identifiers("xyz", "customthing", 1)]
    out = serialize_book_detail(_book(ids))["identifiers"][0]
    assert out["url"] is None
    assert out["val"] == "xyz"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,<script>x</script>"])
def test_dangerous_scheme_identifier_never_linked(bad):
    """A crafted javascript:/data: identifier must NOT become a clickable href —
    only real http(s) URLs are emitted (stricter than the classic template)."""
    ids = [Identifiers(bad, "evil", 1)]
    out = serialize_book_detail(_book(ids))["identifiers"][0]
    assert out["url"] is None
    assert out["val"] == bad


@pytest.mark.unit
def test_book_detail_renders_identifier_link():
    """The SPA book detail must render a linkable identifier as an anchor with a
    safe target/rel, and fall back to plain text otherwise."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "pages" / "BookDetail.tsx").read_text()
    assert "id.url" in src
    assert 'rel="noopener noreferrer"' in src
    assert "href={id.url}" in src
