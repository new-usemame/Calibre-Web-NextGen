# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The cover picker searches sources by title and author, not by a bare ISBN.

Measured on the household instance 2026-09-10 for the same book (an obscure
print edition's ISBN stored as its only ISBN): searching the 15 enabled
sources with the ISBN alone made 3 answer with 17 candidates; searching with
"Nineteen Eighty-Four George Orwell" made 6 answer with 61. Twelve sources
are text catalogues or scrapers that cannot resolve an ISBN they do not stock,
so the ISBN-first query turned a good book into "1 of 15 sources answered".
The ISBN still reaches the sources that use it natively (the Amazon CDN
probe takes every stored ISBN and ASIN separately).
"""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _book(title, authors=(), identifiers=()):
    return SimpleNamespace(
        title=title,
        authors=[SimpleNamespace(name=a) for a in authors],
        identifiers=[SimpleNamespace(type=t, val=v) for t, v in identifiers],
    )


def test_query_is_title_and_author_even_when_an_isbn_is_stored():
    from cps.cover_picker import _book_query_for_search, _book_isbns

    book = _book("Nineteen Eighty-Four", ["George Orwell"],
                 [("goodreads", "6277598"), ("isbn", "9781906147440"), ("mobi-asin", "B075LSTQ6P")])
    assert _book_query_for_search(book) == "Nineteen Eighty-Four George Orwell"
    # The ISBN is not lost: it feeds the identifier-based cover probes.
    assert _book_isbns(book) == ["9781906147440"]


def test_query_without_authors_is_the_title_alone():
    from cps.cover_picker import _book_query_for_search

    assert _book_query_for_search(_book("Bartleby, the Scrivener")) == "Bartleby, the Scrivener"
