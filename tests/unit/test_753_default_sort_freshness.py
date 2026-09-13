# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #753: the default library must be newest-first."""
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


@pytest.mark.unit
def test_default_books_api_requests_newest_first_and_returns_seeded_order():
    from cps.api import books as books_mod
    from cps.pagination import Pagination
    def book(book_id, title, timestamp):
        return SimpleNamespace(id=book_id, title=title, timestamp=timestamp,
            series_index="1.0", has_cover=0, authors=[], series=[], data=[])
    seeded = [
        book(1, "Old", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        book(3, "Newest", datetime(2024, 3, 1, tzinfo=timezone.utc)),
        book(2, "Middle", datetime(2024, 2, 1, tzinfo=timezone.utc)),
    ]
    def fill(_page, _model, _per_page, _filter, order, *_args):
        assert order == books_mod.SORT_MAP["new"]
        rows = sorted(seeded, key=lambda b: b.timestamp, reverse=True)
        wrapped = [SimpleNamespace(Books=b, is_archived=False, read_status=None) for b in rows]
        return wrapped, None, Pagination(1, 60, len(rows))
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        with patch.object(books_mod.calibre_db, "fill_indexpage", side_effect=fill), \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            response = inspect.unwrap(books_mod.list_books)()
    assert [item["title"] for item in json.loads(response.get_data(as_text=True))["items"]] == ["Newest", "Middle", "Old"]


@pytest.mark.unit
def test_catalog_initial_sort_and_accumulator_reset_are_fresh():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/pages/Catalog.tsx").read_text()
    assert "const defaultSort = defaultCatalogSort({ isSeries, isPlainLibrary })" in src
    # #640 extended the initializer: snapshot first, then (plain library only)
    # the persisted choice, then the contextual default — still a lazy useState,
    # so the first render is fresh and there is no post-mount correction.
    # These two are source pins and cannot execute the claim. What does execute
    # it: frontend/e2e/library-recent-sort.spec.ts asserts the menu's value at
    # first paint and that the listing request carried it, with no second
    # request correcting the sort afterwards.
    assert "useState(() =>\n    snap?.sort\n    ?? (isPlainLibrary ? readStoredSort() : undefined)\n    ?? defaultSort)" in src
    assert "if (!data || isPlaceholderData) return" in src
    reset = src.index("if (resetKey !== accKeyRef.current)")
    replace = src.index("setAllBooks(data.items)", reset)
    append = src.index("dedupAppend(prev, data.items)", replace)
    assert reset < replace < append
