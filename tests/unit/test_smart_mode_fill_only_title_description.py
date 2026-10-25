# -*- coding: utf-8 -*-
"""Smart mode fills a missing title or description; it never replaces an existing one with a longer one.
A summary or study guide's title and blurb are nearly always longer than the book's, so "longer wins"
put another product's metadata on the real book (Calibre-Web-Automated issue #1588)."""

from tests.unit.test_metadata_autofetch_safety import _Book, _FakeCDB, _meta, _patch_settings, _List  # noqa: F401
import cps.metadata_helper as m


class _Comment:
    def __init__(self, text):
        self.text = text


def test_existing_title_kept_against_a_longer_one(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.title = "Outgrowing God"
    changed = m._apply_metadata_to_book(book, _meta(title="SUMMARY: Outgrowing God (Unofficial Summary and Analysis)"), _FakeCDB())
    assert changed is False
    assert book.title == "Outgrowing God"


def test_missing_title_is_filled(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.title = "Unknown"
    m._apply_metadata_to_book(book, _meta(title="Outgrowing God"), _FakeCDB())
    assert book.title == "Outgrowing God"


def test_existing_description_kept_against_a_longer_one(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.comments = _List([_Comment("The real blurb.")])
    changed = m._apply_metadata_to_book(book, _meta(description="Disclaimer: this is an unofficial summary. " * 5), _FakeCDB())
    assert changed is False
    assert book.comments[0].text == "The real blurb."


def test_normal_mode_still_overwrites_the_title(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=False)
    book = _Book()
    book.title = "Old"
    m._apply_metadata_to_book(book, _meta(title="New"), _FakeCDB())
    assert book.title == "New"


def test_blank_description_is_filled(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.comments = _List([_Comment("  \n")])
    m._apply_metadata_to_book(book, _meta(description="The real blurb."), _FakeCDB())
    assert book.comments[0].text == "The real blurb."


def test_markup_only_description_is_filled(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.comments = _List([_Comment("<div><p>&nbsp;</p></div>")])
    m._apply_metadata_to_book(book, _meta(description="The real blurb."), _FakeCDB())
    assert book.comments[0].text == "The real blurb."


def test_description_with_text_in_markup_is_kept(monkeypatch):
    _patch_settings(monkeypatch, auto_metadata_smart_application=True)
    book = _Book()
    book.comments = _List([_Comment("<p>The real blurb.</p>")])
    m._apply_metadata_to_book(book, _meta(description="A much longer unofficial summary blurb."), _FakeCDB())
    assert book.comments[0].text == "<p>The real blurb.</p>"
