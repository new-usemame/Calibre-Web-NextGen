"""Regression coverage for New UI Date added / Last modified parity (#878)."""

from datetime import datetime, timezone
from types import SimpleNamespace


def _book():
    return SimpleNamespace(
        id=8, title="Dates", series_index=None, has_cover=0, pubdate=None,
        timestamp=datetime(2025, 4, 3, 2, 1, tzinfo=timezone.utc),
        last_modified=datetime(2026, 7, 13, 21, 0, tzinfo=timezone.utc),
        authors=[], series=[], data=[], tags=[], languages=[], publishers=[],
        identifiers=[], comments=[],
    )


def test_list_item_surfaces_library_timestamps():
    from cps.api.serializers import serialize_book_list_item

    out = serialize_book_list_item(_book())
    assert out["date_added"] == "2025-04-03T02:01:00+00:00"
    assert out["last_modified"] == "2026-07-13T21:00:00+00:00"


def test_detail_surfaces_library_timestamps():
    from cps.api.serializers import serialize_book_detail

    out = serialize_book_detail(_book())
    assert out["date_added"] == "2025-04-03T02:01:00+00:00"
    assert out["last_modified"] == "2026-07-13T21:00:00+00:00"


def test_missing_library_timestamps_are_null():
    from cps.api.serializers import serialize_book_list_item

    book = _book()
    book.timestamp = None
    book.last_modified = None
    out = serialize_book_list_item(book)
    assert out["date_added"] is None
    assert out["last_modified"] is None
