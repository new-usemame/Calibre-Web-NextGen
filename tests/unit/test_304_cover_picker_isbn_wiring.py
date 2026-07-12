"""Regression pins for #304's book-ISBN-to-cover-picker route wiring."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_SOURCE = (REPO_ROOT / "cps" / "cover_picker.py").read_text(encoding="utf-8")


def test_candidate_route_passes_stored_book_isbns_to_service():
    assert "def _book_isbns(book)" in BLUEPRINT_SOURCE
    assert "book_isbns=_book_isbns(book)" in BLUEPRINT_SOURCE
    assert '("isbn", "isbn_10", "isbn_13")' in BLUEPRINT_SOURCE
