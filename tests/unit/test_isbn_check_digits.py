# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ISBNs are checked by their check digit before they pick or enter a record.

Auto-fetch compared ISBNs after stripping separators only, so a book whose
ISBN-10 was 0441013597 never matched a provider's 9780441013593 for the same
edition, and two equal junk values (a typo copied between records, a
placeholder) matched each other. A provider's junk ISBN was also written into
the library as the book's identifier.
"""
from types import SimpleNamespace as NS

import pytest

import cps.metadata_helper as m
from cps.services.isbn import is_valid, isbn13
from tests.unit.test_metadata_autofetch_safety import _Book, _FakeCDB, _Ident, _meta, _patch_settings

pytestmark = pytest.mark.unit

DUNE_13 = "9780441013593"
DUNE_10 = "0441013597"


@pytest.mark.parametrize("value, expected", [
    (DUNE_13, DUNE_13),
    ("978-0-441-01359-3", DUNE_13),
    ("ISBN: 978 0 441 01359 3", DUNE_13),
    ("978‑0‑441‑01359‑3", DUNE_13),      # non-breaking hyphens, as copied from a web page
    (DUNE_10, DUNE_13),
    ("0-8044-2957-x", "9780804429573"),   # X check digit, lower case
    ("9780441013594", None),              # wrong check digit
    ("0441013598", None),
    ("9780000000000", None),              # placeholder
    ("4006381333931", None),              # a valid EAN-13 that is not an ISBN
    ("B00ABCDEFG", None),                 # an ASIN under "isbn"
    ("04410X3597", None),                 # X only allowed last
    ("", None),
    (None, None),
])
def test_isbn13(value, expected):
    assert isbn13(value) == expected
    assert is_valid(value) is (expected is not None)


def _cand(title, identifiers):
    return NS(title=title, authors=["Frank Herbert"], identifiers=identifiers)


def test_an_isbn10_on_the_book_matches_the_providers_isbn13():
    first = _cand("Dune", {"isbn": "9780593099322"})
    right = _cand("Dune", {"isbn": DUNE_13})
    assert m._select_metadata_result([first, right], DUNE_10,
                                     book_title="Dune", book_authors=["Frank Herbert"]) is right


def test_equal_junk_isbns_do_not_pick_a_record():
    junk = _cand("Dune Messiah", {"isbn": "9780000000000"})
    right = _cand("Dune", {})
    # Without the check the junk value matched itself and won over the title.
    assert m._select_metadata_result([junk, right], "978-0000000000",
                                     book_title="Dune", book_authors=["Frank Herbert"]) is right


def test_a_valid_isbn_wins_over_an_invalid_isbn13_on_the_book():
    book = _Book(identifiers=[_Ident("isbn13", "9782222222222"), _Ident("isbn", DUNE_10)])
    assert m._book_isbn(book) == DUNE_10


def test_a_provider_isbn_that_fails_its_check_digit_is_not_written(monkeypatch):
    _patch_settings(monkeypatch)
    book = _Book(identifiers=[])
    m._apply_metadata_to_book(
        book, _meta(identifiers={"isbn": "9780441013594", "isbn_10": DUNE_10, "goodreads": "234225"}),
        _FakeCDB())
    assert {i.type: i.val for i in book.identifiers} == {"isbn_10": DUNE_10, "goodreads": "234225"}


# --- the other places an ISBN picks something --------------------------------

def test_the_cover_booster_skips_an_isbn_that_fails_its_check_digit():
    from cps.services import cover_booster
    # The first value is the wrong-digit form of the second; only the valid one is used.
    assert cover_booster._isbn_from({"isbn": "9780441013594", "isbn_10": DUNE_10}) == DUNE_10
    assert cover_booster._isbn_from({"isbn": "9780000000000"}) is None
    assert cover_booster._to_isbn10("9780441013594") is None
    assert cover_booster._to_isbn10(DUNE_13) == DUNE_10


def test_the_cover_picker_probes_no_amazon_key_for_an_invalid_isbn(monkeypatch):
    from cps.services import cover_booster
    monkeypatch.setattr(cover_booster, "_AMAZON_CDN_ENABLED", True)
    assert cover_booster.amazon_cdn_keys(isbns=["0441013598", DUNE_13]) == [DUNE_10]


def test_apple_books_only_routes_a_valid_isbn_to_its_isbn_lookup():
    from cps.metadata_provider import apple_books
    provider = apple_books.AppleBooks()
    assert provider._looks_like_isbn("978-0-441-01359-3")
    assert not provider._looks_like_isbn("978-0-441-01359-4")


def test_kobo_keeps_only_a_valid_isbn():
    from cps.metadata_provider.kobo import Kobo
    provider = Kobo()
    assert provider._validate_isbn("978-0-441-01359-3") == DUNE_13
    assert provider._validate_isbn(DUNE_10) == DUNE_10
    assert provider._validate_isbn("9780441013594") is None
    assert provider._validate_isbn("ISBN:9780441013593") is None   # digits only, as before


def test_hardcover_scores_an_isbn10_against_its_isbn13_as_an_isbn_match():
    from cps.metadata_provider.hardcover import Hardcover
    record = NS(identifiers={"isbn": DUNE_13}, title="Dune", authors=["Frank Herbert"])
    assert Hardcover.calculate_confidence_score(record, "Dune", ["Frank Herbert"], query_isbn=DUNE_10) == (
        1.0, "ISBN exact match")
    junk = NS(identifiers={"isbn": "9780000000000"}, title="Other", authors=["Someone"])
    score, reason = Hardcover.calculate_confidence_score(junk, "Dune", ["Frank Herbert"], query_isbn="9780000000000")
    assert reason != "ISBN exact match"


def test_goodreads_other_books_hides_an_owned_book_whichever_isbn_form_each_side_holds(monkeypatch):
    pytest.importorskip("goodreads")
    from cps.services import goodreads_support

    def gr_book(isbn, gid, title):
        return NS(isbn=isbn, gid=gid, _book_dict={"title_without_series": title})

    owned = gr_book(DUNE_13, 1, "Dune: Deluxe Edition With Extras")
    other = gr_book("9780441172719", 2, "Children of Dune")
    library = [NS(title="Dune", identifiers=[NS(val=DUNE_10)])]
    monkeypatch.setattr(goodreads_support, "Levenshtein", None)   # judge by ISBN alone
    found = list(goodreads_support.get_other_books(NS(books=[owned, other]), library))
    assert found == [other]
