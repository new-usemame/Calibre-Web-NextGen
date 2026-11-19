# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for HardcoverHandler — stateless push/delete + idempotency."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from cps.services.annotation_sync.hardcover import HardcoverHandler
from tests.fixtures.mock_hardcover_client import MockHardcoverClient


class FakeUser:
    def __init__(self, hardcover_token="tok", id=1):
        self.hardcover_token = hardcover_token
        self.id = id


def _book_with_isbn(book_id=1):
    return SimpleNamespace(
        id=book_id,
        title=f"Book {book_id}",
        identifiers=[SimpleNamespace(type="isbn", val="9780000000000")],
    )


class FakeAnnotation:
    """Mimics cps.ub.Annotation fields used by the handler."""

    def __init__(self, **kw):
        defaults = {
            "id": 1, "annotation_id": "kobo-uuid-001",
            "highlighted_text": "hello", "note_text": "note",
            "highlight_color": "yellow", "chapter_progress": 0.5,
            "source": "kobo",
        }
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)
        self._sync_target_row = kw.get("_sync_target_row")

    def sync_target(self, name):
        if self._sync_target_row and self._sync_target_row.target == name:
            return self._sync_target_row
        return None


def _make_handler(client, config_on=True, blacklisted=False, not_found_exc=None):
    return HardcoverHandler(
        client_factory=lambda token: client,
        config_getter=lambda: config_on,
        book_identifiers_getter=lambda b: {"isbn": "9780000000000"},
        blacklist_check=lambda book_id: blacklisted,
        not_found_exception=not_found_exc,
    )


def test_push_first_time_uses_add_journal_entry():
    client = MockHardcoverClient(add_response={"id": 999})
    h = _make_handler(client)
    result = h.push(FakeAnnotation(), _book_with_isbn(), FakeUser())
    assert result.status == "synced"
    assert result.target_record_id == "999"
    assert client.calls[0][0] == "add"


def test_push_existing_uses_update_journal_entry():
    """If annotation has an existing sync_target with record id, call update."""
    client = MockHardcoverClient(update_response={"id": 555})
    h = _make_handler(client)
    existing = SimpleNamespace(target="hardcover", target_record_id="555", status="synced")
    ann = FakeAnnotation(_sync_target_row=existing)
    result = h.push(ann, _book_with_isbn(), FakeUser())
    assert result.status == "synced"
    assert result.target_record_id == "555"
    assert client.calls[0][0] == "update"


def test_push_failed_on_empty_response():
    client = MockHardcoverClient(add_response={})
    h = _make_handler(client)
    result = h.push(FakeAnnotation(), _book_with_isbn(), FakeUser())
    assert result.status == "failed"
    assert result.error_message is not None


def test_push_catches_exception_as_failed():
    client = MockHardcoverClient(add_raises=RuntimeError("boom"))
    h = _make_handler(client)
    result = h.push(FakeAnnotation(), _book_with_isbn(), FakeUser())
    assert result.status == "failed"
    assert "boom" in (result.error_message or "")


def test_push_skipped_when_no_text():
    client = MockHardcoverClient()
    h = _make_handler(client)
    ann = FakeAnnotation(highlighted_text=None, note_text=None)
    result = h.push(ann, _book_with_isbn(), FakeUser())
    assert result.status == "failed"
    assert "no text" in (result.error_message or "").lower()
    assert client.calls == []


def test_push_skipped_when_blacklisted():
    client = MockHardcoverClient()
    h = _make_handler(client, blacklisted=True)
    result = h.push(FakeAnnotation(), _book_with_isbn(), FakeUser())
    assert result.status == "failed"
    assert "blacklisted" in (result.error_message or "").lower()
    assert client.calls == []


def test_push_skipped_when_no_identifiers():
    client = MockHardcoverClient()
    h = HardcoverHandler(
        client_factory=lambda token: client,
        config_getter=lambda: True,
        book_identifiers_getter=lambda b: {},  # no identifiers
        blacklist_check=lambda book_id: False,
    )
    result = h.push(FakeAnnotation(), _book_with_isbn(), FakeUser())
    assert result.status == "failed"
    assert "identifier" in (result.error_message or "").lower()


def test_delete_returns_tombstone_on_success():
    client = MockHardcoverClient(delete_response=123)
    h = _make_handler(client)
    st = SimpleNamespace(target_record_id="123", target="hardcover", status="synced")
    result = h.delete(st, FakeUser())
    assert result.status == "tombstone"
    assert result.target_record_id == "123"


def test_delete_treats_not_found_as_tombstone():
    class NotFound(Exception):
        pass
    client = MockHardcoverClient(delete_raises=NotFound("404"))
    h = _make_handler(client, not_found_exc=NotFound)
    st = SimpleNamespace(target_record_id="999", target="hardcover", status="synced")
    result = h.delete(st, FakeUser())
    assert result.status == "tombstone"
    assert "already deleted" in (result.error_message or "").lower()


def test_delete_failed_on_unrelated_exception():
    client = MockHardcoverClient(delete_raises=RuntimeError("network"))
    h = _make_handler(client)
    st = SimpleNamespace(target_record_id="123", target="hardcover", status="synced")
    result = h.delete(st, FakeUser())
    assert result.status == "failed"
    assert "network" in (result.error_message or "")


def test_is_enabled_requires_user_token_and_global_config():
    client = MockHardcoverClient()
    h = _make_handler(client, config_on=True)
    assert h.is_enabled(FakeUser(hardcover_token="t")) is True
    assert h.is_enabled(FakeUser(hardcover_token=None)) is False
    h2 = _make_handler(client, config_on=False)
    assert h2.is_enabled(FakeUser(hardcover_token="t")) is False


@pytest.mark.parametrize("source, tag", [
    ("kobo", "Kobo"),
    ("koreader", "KOReader"),
    ("webreader", "Web reader"),
    ("crosspoint", "CrossPoint"),
    ("textquote", None),
    (None, None),
])
def test_push_tags_the_entry_with_the_reader_of_its_source(source, tag):
    client = _RecordingHardcoverClient()
    result = _make_handler(client).push(
        FakeAnnotation(source=source), _book_with_isbn(), FakeUser())
    assert result.status == "synced", result.error_message
    assert [t["tag"] for t in client.sent[-1]["tags"]] == (
        ["CWA"] + ([tag] if tag else []) + ["yellow"])


def _handler_with_percent(client, percent_getter):
    return HardcoverHandler(
        client_factory=lambda token: client,
        config_getter=lambda: True,
        book_identifiers_getter=lambda b: {"isbn": "9780000000000"},
        blacklist_check=lambda book_id: False,
        book_percent_getter=percent_getter,
    )


def test_push_without_a_payload_sends_the_book_percent_of_the_annotation():
    client = MockHardcoverClient(add_response={"id": 1})
    seen = []
    h = _handler_with_percent(client, lambda book, ann, span: seen.append(ann) or 37.5)
    ann = FakeAnnotation(source="koreader")
    h.push(ann, _book_with_isbn(), FakeUser())
    assert seen == [ann]
    assert client.calls[0][2]["progress_percent"] == 37.5


def test_push_gives_the_payload_span_to_the_book_percent_and_never_sends_it_raw():
    client = MockHardcoverClient(add_response={"id": 1})
    seen = []
    h = _handler_with_percent(client, lambda book, ann, span: seen.append(span) or 50.36)
    span = {"chapterFilename": "OEBPS/c40.xhtml", "chapterProgress": 0.76}
    h.push(FakeAnnotation(), _book_with_isbn(), FakeUser(), payload={"location": {"span": span}})
    assert seen == [span]
    assert client.calls[0][2]["progress_percent"] == 50.36


def test_push_still_adds_the_entry_when_the_book_percent_fails():
    client = MockHardcoverClient(add_response={"id": 1})

    def broken(book, ann, span):
        raise ValueError("unreadable book")
    result = _handler_with_percent(client, broken).push(
        FakeAnnotation(source="koreader"), _book_with_isbn(), FakeUser())
    assert result.status == "synced"
    assert client.calls[0][2]["progress_percent"] is None


def test_book_percent_counts_the_text_before_the_xpointer(tmp_path, monkeypatch):
    from cps import annotations
    from cps.services.annotation_sync.hardcover import _default_book_percent
    from tests.unit.test_koreader_xpointer import _html_book
    epub = _html_book(tmp_path, ["<p>aaaa</p>", "<p>bbbb cccc</p>"])
    monkeypatch.setattr(annotations, "_book_format_path",
                        lambda book, fmt: str(epub) if fmt == "EPUB" else None)
    book = SimpleNamespace(id=1)
    # 4 characters in chapter 1, then 4 of the 8 in chapter 2: 8 of 12.
    ann = FakeAnnotation(start_xpointer="/body/DocFragment[2]/body/p/text().5")
    assert _default_book_percent(book, ann) == 66.67
    assert _default_book_percent(book, FakeAnnotation(start_xpointer=None)) is None
    assert _default_book_percent(
        book, FakeAnnotation(start_xpointer="/body/DocFragment[9]/body/p/text().0")) is None


def test_book_percent_of_a_kobo_span_counts_the_chapters_before_it(tmp_path, monkeypatch):
    from cps import readingservices
    from cps.services.annotation_sync.hardcover import _default_book_percent
    from tests.unit.test_koreader_xpointer import _html_book
    (tmp_path / "b").mkdir()
    _html_book(tmp_path / "b", ["<p>aaaa</p>", "<p>bbbb</p>"])
    monkeypatch.setattr(readingservices.config, "get_book_path", lambda: str(tmp_path))
    book = SimpleNamespace(id=1, path="b", data=[SimpleNamespace(format="EPUB", name="book")])
    # Each chapter has 5 characters with its title "t". Half of chapter 2: 7.5 of 10.
    span = {"chapterFilename": "OEBPS/c1.html", "chapterProgress": 0.5}
    assert _default_book_percent(book, FakeAnnotation(), span) == 75.0
    assert _default_book_percent(book, FakeAnnotation(), {"chapterProgress": 0.5}) is None
    assert _default_book_percent(book, FakeAnnotation(), None) is None


class _RecordingHardcoverClient:
    """HardcoverClient with the network calls replaced."""

    def __new__(cls):
        from cps.services.hardcover import HardcoverClient

        class Client(HardcoverClient):
            def __init__(self):
                self.privacy = 1
                self.sent = []

            def get_user_book(self, ids):
                return {"book_id": 7, "edition": {"id": 8, "pages": 200}}

            def execute(self, query, variables=None):
                self.sent.append(variables)
                return {"insert_reading_journal": {"id": 1, "reading_journal": {"id": 1}}}
        return Client()


def test_journal_entry_tags_name_cwa_and_only_the_given_reader():
    client = _RecordingHardcoverClient()
    client.add_journal_entry({"isbn": "9780000000000"}, None, progress_percent=25.0,
                             highlighted_text="words", source_tag="KOReader")
    variables = client.sent[-1]
    assert [t["tag"] for t in variables["tags"]] == ["CWA", "KOReader"]
    assert variables["metadata"]["position"]["value"] == 50
    client.add_journal_entry({"isbn": "9780000000000"}, None, highlighted_text="words")
    assert [t["tag"] for t in client.sent[-1]["tags"]] == ["CWA"]


@pytest.fixture
def hardcover_epub(tmp_path, monkeypatch):
    """Two equal chapters; their halfway point is 75% of the book."""
    from cps import readingservices
    from tests.unit.test_koreader_xpointer import _html_book

    (tmp_path / "b").mkdir()
    path = _html_book(tmp_path / "b", ["<p>aaaa</p>", "<p>bbbb</p>"])
    monkeypatch.setattr(readingservices.config, "get_book_path", lambda: str(tmp_path))
    book = SimpleNamespace(id=1, path="b", data=[SimpleNamespace(format="EPUB", name="book")])
    return book, path


@pytest.mark.parametrize("progress", [-1, 1.01, float("inf"), float("-inf"), float("nan"),
                                      True, "0.5", None])
def test_invalid_chapter_progress_syncs_text_without_a_page(hardcover_epub, progress):
    """Bad device progress must neither invent a page nor block the quote."""
    book, _path = hardcover_epub
    client = _RecordingHardcoverClient()
    span = {"chapterFilename": "OEBPS/c1.html", "chapterProgress": progress}
    result = _make_handler(client).push(
        FakeAnnotation(), book, FakeUser(), {"location": {"span": span}})
    assert result.status == "synced", result.error_message
    assert client.sent[-1]["metadata"] is None
    assert "hello" in client.sent[-1]["entry"]


@pytest.mark.parametrize("payload", [
    {"location": "bad"}, {"location": [1]},
    {"location": {"span": "bad"}}, {"location": {"span": [1]}},
])
def test_malformed_location_syncs_text_without_a_page(payload):
    """The dispatcher preserves text with rejected location; its target must too."""
    client = _RecordingHardcoverClient()
    result = _make_handler(client).push(FakeAnnotation(), _book_with_isbn(), FakeUser(), payload)
    assert result.status == "synced", result.error_message
    assert client.sent[-1]["metadata"] is None


def test_kobo_page_uses_the_exact_chapter_not_a_filename_suffix(hardcover_epub):
    """An earlier intro-c1.html must not steal a highlight in c1.html."""
    import zipfile

    book, path = hardcover_epub
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["OEBPS/intro-c1.html"] = files.pop("OEBPS/c0.html")
    files["OEBPS/content.opf"] = files["OEBPS/content.opf"].replace(b"c0.html", b"intro-c1.html")
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    client = _RecordingHardcoverClient()
    span = {"chapterFilename": "c1.html", "chapterProgress": 0.5}
    result = _make_handler(client).push(
        FakeAnnotation(), book, FakeUser(), {"location": {"span": span}})
    assert result.status == "synced", result.error_message
    assert client.sent[-1]["metadata"]["position"] == {
        "type": "pages", "value": 150, "percent": 75.0, "possible": 200,
    }


@pytest.mark.parametrize("chapter_progress, percent, page", [(0, 50, 100), (0.5, 75, 150), (1, 100, 200)])
def test_valid_kobo_position_reaches_the_journal_request(hardcover_epub, chapter_progress, percent, page):
    """Exercise the real EPUB calculator, handler and GraphQL client together."""
    book, _path = hardcover_epub
    client = _RecordingHardcoverClient()
    span = {"chapterFilename": "OEBPS/c1.html", "chapterProgress": chapter_progress}
    result = _make_handler(client).push(
        FakeAnnotation(), book, FakeUser(), {"location": {"span": span}})
    assert result.status == "synced", result.error_message
    assert client.sent[-1]["metadata"]["position"] == {
        "type": "pages", "value": page, "percent": percent, "possible": 200,
    }


def test_xpointer_position_reaches_the_journal_request(hardcover_epub, monkeypatch):
    """A payload-free KOReader highlight must get a page in the actual request."""
    from cps import annotations

    book, path = hardcover_epub
    monkeypatch.setattr(annotations, "_book_format_path",
                        lambda book, fmt: str(path) if fmt == "EPUB" else None)
    client = _RecordingHardcoverClient()
    annotation = FakeAnnotation(source="koreader", start_xpointer="/body/DocFragment[2]/body/p/text().2")
    result = _make_handler(client).push(annotation, book, FakeUser())
    assert result.status == "synced", result.error_message
    assert client.sent[-1]["metadata"]["position"] == {
        "type": "pages", "value": 150, "percent": 75.0, "possible": 200,
    }
