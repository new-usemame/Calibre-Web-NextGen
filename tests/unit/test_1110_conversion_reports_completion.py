"""#1110: a finished conversion must be visible as finished.

The reporter saw "starting conversion" in the log and nothing after it, so a
conversion that had succeeded looked stalled. Success is now logged, and the
convert API hands back the queued task so the book page can watch it.
"""
import logging
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _fake_db(book, merged):
    class Query:
        def filter(self, *_):
            return self

        def one_or_none(self):
            return None

    class Session:
        def query(self, *_):
            return Query()

        def merge(self, row):
            merged.append(row)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class LocalDB:
        def __init__(self, **_):
            self.session = Session()

        def get_book(self, _book_id):
            return book

        def get_book_format(self, *_):
            return None

    return LocalDB


def test_successful_conversion_logs_that_it_finished(monkeypatch, caplog):
    from cps import helper  # noqa: F401  (import order: helper imports convert)
    from cps.tasks import convert

    book = SimpleNamespace(id=11368, title="Book", path="Author/Book", has_cover=False,
                           data=[SimpleNamespace(name="book")])
    merged = []
    checks = {"n": 0}

    def target_exists(_path):
        # absent before the converter runs, present after it
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(convert.db, "CalibreDB", _fake_db(book, merged))
    monkeypatch.setattr(convert.os.path, "isfile", target_exists)
    monkeypatch.setattr(convert.os.path, "exists", lambda *_: True)
    monkeypatch.setattr(convert.os.path, "getsize", lambda *_: 12)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.config, "config_converterpath", "/bin/ebook-convert", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", None, raising=False)
    task = convert.TaskConvert("/books/Author/Book/book", 11368, "convert",
                               {"old_book_format": "PDF", "new_book_format": "EPUB"}, None)
    monkeypatch.setattr(task, "_convert_calibre", lambda *_: (0, None))
    monkeypatch.setattr(convert.helper, "mark_book_format_materialised", lambda *_: None)

    with caplog.at_level(logging.INFO):
        assert task._convert_ebook_format() == "book.epub"

    finished = [r.getMessage() for r in caplog.records if "converted from" in r.getMessage()]
    assert finished == ["Book id 11368 converted from .pdf to .epub."]


def test_failed_conversion_does_not_log_success(monkeypatch, caplog):
    from cps import helper  # noqa: F401  (import order: helper imports convert)
    from cps.tasks import convert

    book = SimpleNamespace(id=7, title="Book", path="Author/Book", has_cover=False,
                           data=[SimpleNamespace(name="book")])
    monkeypatch.setattr(convert.db, "CalibreDB", _fake_db(book, []))
    monkeypatch.setattr(convert.os.path, "isfile", lambda *_: False)
    monkeypatch.setattr(convert.os.path, "exists", lambda *_: True)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(convert.config, "config_converterpath", "/bin/ebook-convert", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", None, raising=False)
    task = convert.TaskConvert("/books/Author/Book/book", 7, "convert",
                               {"old_book_format": "PDF", "new_book_format": "EPUB"}, None)
    monkeypatch.setattr(task, "_convert_calibre", lambda *_: (1, "boom"))

    with caplog.at_level(logging.INFO):
        assert task._convert_ebook_format() is None

    assert not [r for r in caplog.records if "converted from" in r.getMessage()]
    assert task.error == "boom"


def test_convert_book_format_hands_back_the_queued_task(monkeypatch, tmp_path):
    from cps import helper

    (tmp_path / "Author" / "Book").mkdir(parents=True)
    (tmp_path / "Author" / "Book" / "book.pdf").write_bytes(b"%PDF")
    book = SimpleNamespace(id=5, path="Author/Book", title="Book")
    added = []
    monkeypatch.setattr(helper.calibre_db, "get_book", lambda _id: book)
    monkeypatch.setattr(helper.calibre_db, "get_book_format",
                        lambda *_: SimpleNamespace(name="book"))
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper, "url_for", lambda *_a, **_k: "/book/5")
    monkeypatch.setattr(helper.WorkerThread, "add", lambda user, task: added.append(task))

    queued = []
    assert helper.convert_book_format(5, str(tmp_path), "PDF", "EPUB", "alice",
                                      queued_tasks=queued) is None
    assert queued == added and len(queued) == 1
