import datetime
from types import SimpleNamespace

from cps.api.serializers import serialize_book_detail, serialize_book_list_item

# Cover URLs are versioned by Books.last_modified so a replaced cover changes the
# URL rather than waiting out a cache entry — see
# tests/unit/test_cover_and_static_cache_headers.py for the policy this feeds.
LAST_MODIFIED = datetime.datetime(2026, 8, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
STAMP = str(int(LAST_MODIFIED.timestamp() * 1_000_000))


def _book(has_cover):
    return SimpleNamespace(
        id=42,
        title="Cover test",
        series_index=None,
        has_cover=has_cover,
        last_modified=LAST_MODIFIED,
        authors=[],
        series=[],
        data=[],
        tags=[],
        ratings=[],
        comments=[],
        languages=[],
        publishers=[],
        identifiers=[],
        pubdate=None,
    )


def test_coverless_book_serializers_return_none():
    book = _book(has_cover=0)

    assert serialize_book_list_item(book)["cover_url"] is None
    assert serialize_book_detail(book)["cover_url"] is None


def test_book_with_cover_serializers_return_cover_paths():
    book = _book(has_cover=1)

    assert serialize_book_list_item(book)["cover_url"] == f"/cover/42/sm?c={STAMP}"
    # NOT /og: that resolution maps to COVER_THUMBNAIL_ORIGINAL == 0, which is
    # falsy, so helper.get_book_cover_internal skips its thumbnail branch and
    # serves the unresized library file.
    assert serialize_book_detail(book)["cover_url"] == f"/cover/42/md?c={STAMP}"
