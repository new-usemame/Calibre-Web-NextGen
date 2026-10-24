# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Progress reaches Hardcover even when the user_book has no edition.

Books added by the auto-match, the review page or a shelf sync are inserted
with ``hardcover-id`` only, so Hardcover stores the user_book without an
edition and without a page count. Every progress push then logged "Hardcover
user_book has no edition page count; progress not synced" and stopped, and
the reader had to pick an edition by hand on hardcover.app for each book.
"""

from __future__ import annotations

import pytest

from cps.services.hardcover import HardcoverClient, STATUS_READING

pytestmark = pytest.mark.unit


class FakeApi:
    """Answers the client's GraphQL calls by recognising each query."""

    def __init__(self, editions, defaults=None, refuse_users_count=False, defaults_error=False):
        self.editions = editions
        self.defaults = defaults or {}
        self.refuse_users_count = refuse_users_count
        self.defaults_error = defaults_error
        self.calls = []

    def __call__(self, query, variables=None):
        self.calls.append((query, variables or {}))
        if "editions(where" in query:
            if self.refuse_users_count and "users_count" in query:
                raise Exception("GraphQL error: field 'users_count' not found")
            return {"editions": self.editions}
        if "default_ebook_edition_id" in query:
            if self.defaults_error:
                raise Exception("GraphQL error: field not found")
            return {"books": [self.defaults]}
        if "update_user_book(" in query:
            return {"update_user_book": {"error": None}}
        if "update_user_book_read" in query:
            return {"update_user_book_read": {"id": 1}}
        raise AssertionError(f"unexpected query: {query}")

    def read_update(self):
        return next(v for q, v in self.calls if "update_user_book_read" in q)

    def edition_saved(self):
        return [v["edition_id"] for q, v in self.calls if "update_user_book(" in q]


def _client(api, reads=({"id": 34, "started_at": "2026-10-01"},)):
    client = HardcoverClient.__new__(HardcoverClient)
    client.execute = api
    client.get_user_book = lambda ids: {
        "id": 12, "book_id": 9, "status_id": STATUS_READING,
        "edition": None, "user_book_reads": list(reads),
    }
    client.change_book_status = lambda book, status: book
    return client


EDITIONS = [
    {"id": 101, "pages": 400, "reading_format_id": 1, "users_count": 900},  # hardcover
    {"id": 202, "pages": 380, "reading_format_id": 4, "users_count": 50},   # e-book
    {"id": 303, "pages": 390, "reading_format_id": 4, "users_count": 300},  # e-book
]


def test_the_books_own_edition_identifier_wins():
    api = FakeApi(EDITIONS, defaults={"default_ebook_edition_id": 303})
    _client(api).update_reading_progress({"hardcover-id": "9", "hardcover-edition": "101"}, 50)

    assert api.read_update()["editionId"] == 101
    assert api.read_update()["pages"] == 200
    assert api.edition_saved() == [101]


def test_hardcovers_default_ebook_edition_comes_next():
    api = FakeApi(EDITIONS, defaults={"default_ebook_edition_id": 202,
                                      "default_physical_edition_id": 101})
    _client(api).update_reading_progress({"hardcover-id": "9"}, 50)

    assert api.read_update()["editionId"] == 202
    assert api.read_update()["pages"] == 190


def test_without_defaults_the_most_shelved_ebook_is_used():
    api = FakeApi(EDITIONS, defaults_error=True)
    _client(api).update_reading_progress({"hardcover-id": "9"}, 10)

    assert api.read_update()["editionId"] == 303
    assert api.read_update()["pages"] == 39


def test_a_schema_without_users_count_still_finds_an_edition():
    editions = [{k: v for k, v in e.items() if k != "users_count"} for e in EDITIONS]
    api = FakeApi(editions, defaults_error=True, refuse_users_count=True)
    _client(api).update_reading_progress({"hardcover-id": "9"}, 10)

    assert api.read_update()["editionId"] in (202, 303)


def test_no_edition_with_pages_keeps_the_old_behaviour():
    api = FakeApi([], defaults_error=True)
    _client(api).update_reading_progress({"hardcover-id": "9"}, 50)

    assert not any("update_user_book_read" in q for q, _ in api.calls)
    assert api.edition_saved() == []


def test_no_read_in_progress_writes_nothing():
    api = FakeApi(EDITIONS, defaults={"default_ebook_edition_id": 202})
    _client(api, reads=()).update_reading_progress({"hardcover-id": "9"}, 50)

    assert not any("update_user_book_read" in q for q, _ in api.calls)
