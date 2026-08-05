"""Regression pins for default-on, cooperative Kobo KEPUB delivery."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, has_app_context, has_request_context
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import config_sql

pytestmark = pytest.mark.unit


def test_upgrade_and_fresh_install_default_prefer_kepub_on():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO settings (id) VALUES (1)"))
    session = sessionmaker(bind=engine)()
    config_sql._migrate_table(session, config_sql._Settings)
    assert session.execute(text("SELECT config_kobo_prefer_kepub FROM settings")).scalar() == 1

    fresh_engine = create_engine("sqlite:///:memory:")
    config_sql._Base.metadata.create_all(fresh_engine)
    fresh = sessionmaker(bind=fresh_engine)()
    fresh.add(config_sql._Settings())
    fresh.commit()
    assert fresh.query(config_sql._Settings).one().config_kobo_prefer_kepub is True


def test_download_selection_honours_off_but_keeps_existing_kepub(monkeypatch):
    import cps.kobo as kobo

    monkeypatch.setattr(kobo, "get_epub_layout", lambda *_: None)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda book_id, fmt: fmt)
    monkeypatch.setattr(kobo, "_get_cover_image_id", lambda book: str(book.uuid))
    monkeypatch.setattr(kobo, "get_subtitle", lambda book: None)
    monkeypatch.setattr(kobo.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_prefer_kepub", False, raising=False)

    epub = SimpleNamespace(format="EPUB", uncompressed_size=10)
    kepub = SimpleNamespace(format="KEPUB", uncompressed_size=12)
    base = dict(id=7, uuid="book-uuid", title="T", authors=[], series=[], series_index=1,
                tags=[], comments=[], pubdate=None, timestamp=None, last_modified=None,
                languages=[], publishers=[], identifiers=[])
    epub_metadata = kobo.get_metadata(SimpleNamespace(data=[epub], **base))
    assert {item["Format"] for item in epub_metadata["DownloadUrls"]} == {"EPUB", "EPUB3"}

    kepub_metadata = kobo.get_metadata(SimpleNamespace(data=[epub, kepub], **base))
    assert kepub_metadata["DownloadUrls"][0]["Format"] == "KEPUB"
    assert kepub_metadata["DownloadUrls"][0]["Size"] == 12


def test_setting_on_without_binary_offers_epub_and_download_queues_nothing(monkeypatch):
    import cps.kobo as kobo
    from cps.tasks import kepub_backfill

    monkeypatch.setattr(kobo, "get_epub_layout", lambda *_: None)
    monkeypatch.setattr(kobo, "get_download_url_for_book", lambda book_id, fmt: fmt)
    monkeypatch.setattr(kobo, "_get_cover_image_id", lambda book: str(book.uuid))
    monkeypatch.setattr(kobo, "get_subtitle", lambda book: None)
    monkeypatch.setattr(kobo.config, "config_kepubifypath", "", raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_prefer_kepub", True, raising=False)
    epub = SimpleNamespace(format="EPUB", uncompressed_size=10)
    book = SimpleNamespace(id=7, uuid="book-uuid", data=[epub], title="T", authors=[], series=[],
                           series_index=1, tags=[], comments=[], pubdate=None, timestamp=None,
                           last_modified=None, languages=[], publishers=[], identifiers=[])
    metadata = kobo.get_metadata(book)
    assert {item["Format"] for item in metadata["DownloadUrls"]} == {"EPUB", "EPUB3"}
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(kepub_backfill.WorkerThread, "add", lambda *_args, **_kwargs: pytest.fail("queued"))
    assert kepub_backfill.enqueue_kepub_backfill() is False


def test_cold_download_keeps_flask_context_while_conversion_is_prepared(monkeypatch):
    """Only Event.wait may leave the request greenlet; Flask work may not."""
    import cps.helper as helper

    epub = SimpleNamespace(format="EPUB", name="book", uncompressed_size=10)
    kepub = SimpleNamespace(format="KEPUB", name="book", uncompressed_size=12)
    book = SimpleNamespace(id=7, title="Book", path="Author/Book", authors=[])
    kepub_lookups = 0

    def get_format(_book_id, fmt):
        nonlocal kepub_lookups
        if fmt == "EPUB":
            return epub
        kepub_lookups += 1
        return None if kepub_lookups == 1 else kepub

    def context_sensitive_conversion(*_args, **kwargs):
        assert has_app_context()
        assert has_request_context()
        assert kwargs["blocking"] is True
        assert kwargs["timeout"] == 25
        return None

    monkeypatch.setattr(helper.calibre_db, "get_filtered_book", lambda *_args, **_kwargs: book)
    monkeypatch.setattr(helper.calibre_db, "get_book_format", get_format)
    monkeypatch.setattr(helper, "convert_book_format", context_sensitive_conversion)
    monkeypatch.setattr(helper, "do_download_file", lambda *_args: "served")
    monkeypatch.setattr(helper, "get_valid_filename", lambda value, **_: value)
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(is_authenticated=False, role_admin=lambda: False))
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(helper.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: "/books", raising=False)

    app = Flask(__name__)
    with app.test_request_context("/kobo/token/download/7/kepub"):
        assert helper.get_download_link(7, "kepub", "kobo") == "served"


def test_backfill_is_composite_idempotent_preserves_sync_rows_and_skips_gdrive(monkeypatch):
    from cps.tasks import kepub_backfill

    formats = {1: {"EPUB"}, 2: {"EPUB", "KEPUB"}}
    sync_rows = [(1,), (2,)]

    class Query:
        def distinct(self): return self
        def all(self): return list(sync_rows)

    class AppSession:
        def query(self, *_): return Query()
        def close(self): pass

    class CalibreDB:
        def __init__(self, **_): self.session = SimpleNamespace(close=lambda: None)
        def get_book(self, book_id): return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))
        def get_book_format(self, book_id, fmt):
            return SimpleNamespace(format=fmt, name="book", uncompressed_size=1) if fmt in formats[book_id] else None

    conversions = []
    class Conversion:
        def __init__(self, _path, book_id, *_args): self.book_id, self.error = book_id, None
        def _convert_ebook_format(self):
            conversions.append(self.book_id)
            formats[self.book_id].add("KEPUB")
            return "book.kepub"

    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", AppSession)
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", CalibreDB)
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    monkeypatch.setattr(kepub_backfill, "get_epub_layout", lambda *_: None)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "save", lambda: None, raising=False)

    kepub_backfill.TaskKepubBackfill().run(None)
    kepub_backfill.TaskKepubBackfill().run(None)
    assert conversions == [1]
    assert sync_rows == [(1,), (2,)]

    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", True)
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", lambda **_: pytest.fail("gdrive must not open calibre DB"))
    kepub_backfill.TaskKepubBackfill().run(None)


def test_backfill_continues_after_per_book_oserror_and_completes(monkeypatch):
    from cps.tasks import kepub_backfill

    class Query:
        def distinct(self): return self
        def all(self): return [(1,), (2,)]

    class AppSession:
        def query(self, *_): return Query()
        def close(self): pass

    class CalibreDB:
        def __init__(self, **_): self.session = SimpleNamespace(close=lambda: None)
        def get_book(self, book_id): return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))
        def get_book_format(self, book_id, fmt):
            return SimpleNamespace(format=fmt, name="book") if fmt == "EPUB" else None

    attempted = []

    class Conversion:
        def __init__(self, _path, book_id, *_args): self.book_id, self.error = book_id, None
        def _convert_ebook_format(self):
            attempted.append(self.book_id)
            if self.book_id == 1:
                raise OSError("read-only library")
            return "book.kepub"

    saved = []
    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", AppSession)
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", CalibreDB)
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    monkeypatch.setattr(kepub_backfill, "get_epub_layout", lambda *_: None)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_completed", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "save", lambda: saved.append(True), raising=False)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert attempted == [1, 2]
    assert task.failed == 1
    assert task.converted == 1
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is True
    assert saved == [True]


def test_conversion_advances_modified_without_touching_synced_rows(monkeypatch):
    from cps.tasks import convert

    old_modified = datetime(2020, 1, 1, tzinfo=timezone.utc)
    book = SimpleNamespace(id=1, title="Book", path="Author/Book",
                           last_modified=old_modified,
                           data=[SimpleNamespace(name="book")])
    synced_rows = [(1, 1), (2, 1)]
    merged = []

    class Query:
        def filter(self, *_): return self
        def one_or_none(self): return None

    class Session:
        def query(self, *_): return Query()
        def merge(self, row): merged.append(row)
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    class LocalDB:
        def __init__(self, **_): self.session = Session()
        def get_book(self, _book_id): return book
        def get_book_format(self, *_): return None

    file_checks = 0
    def target_exists(_path):
        nonlocal file_checks
        file_checks += 1
        return file_checks > 1

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.os.path, "isfile", target_exists)
    monkeypatch.setattr(convert.os.path, "getsize", lambda *_: 12)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    task = convert.TaskConvert("/books/Author/Book/book", 1, "convert",
                               {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None)
    monkeypatch.setattr(task, "_convert_kepubify", lambda *_: (0, None))

    assert task._convert_ebook_format() == "book.kepub"
    assert book.last_modified > old_modified
    assert synced_rows == [(1, 1), (2, 1)]
    assert len(merged) == 1


def test_truncated_kepub_is_not_adopted_as_database_format(monkeypatch, tmp_path):
    from cps.tasks import convert

    book = SimpleNamespace(id=1, title="Book", path="Author/Book",
                           data=[SimpleNamespace(name="book")])
    merged = []

    class Query:
        def filter(self, *_): return self
        def one_or_none(self): return None

    class Session:
        def query(self, *_): return Query()
        def merge(self, row): merged.append(row)
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    class LocalDB:
        def __init__(self, **_): self.session = Session()
        def get_book(self, _book_id): return book
        def get_book_format(self, *_): return None

    file_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    (tmp_path / "book.kepub").write_bytes(b"PK truncated")

    monkeypatch.setattr(convert.db, "CalibreDB", LocalDB)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    task = convert.TaskConvert(str(file_path), 1, "convert",
                               {"old_book_format": "EPUB", "new_book_format": "KEPUB"}, None)
    conversion_attempted = []
    monkeypatch.setattr(task, "_convert_kepubify",
                        lambda *_: (conversion_attempted.append(True) or 1, "failed"))

    assert task._convert_ebook_format() is None
    assert conversion_attempted == [True]
    assert merged == []


def test_download_serves_epub_immediately_while_backfill_is_in_flight(monkeypatch):
    import cps.helper as helper
    from cps.tasks import kepub_backfill

    epub = SimpleNamespace(format="EPUB", name="book", uncompressed_size=10)
    book = SimpleNamespace(id=7, title="Book", path="Author/Book", authors=[])

    monkeypatch.setattr(helper.calibre_db, "get_filtered_book", lambda *_args, **_kwargs: book)
    monkeypatch.setattr(helper.calibre_db, "get_book_format",
                        lambda _book_id, fmt: epub if fmt == "EPUB" else None)
    monkeypatch.setattr(helper, "convert_book_format",
                        lambda *_args, **_kwargs: pytest.fail("download blocked on conversion"))
    monkeypatch.setattr(helper, "do_download_file", lambda *_args: "served")
    monkeypatch.setattr(helper, "get_valid_filename", lambda value, **_: value)
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(is_authenticated=False, role_admin=lambda: False))
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(helper.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(kepub_backfill, "_pending", True)

    app = Flask(__name__)
    with app.test_request_context("/kobo/token/download/7/kepub"):
        assert helper.get_download_link(7, "kepub", "kobo") == "served"


def test_startup_backfill_task_formats_without_flask_context():
    from cps.tasks.kepub_backfill import TaskKepubBackfill

    assert not has_app_context()
    assert isinstance(str(TaskKepubBackfill()), str)


def test_kepubify_probe_covers_standard_linux_paths_and_path(monkeypatch):
    monkeypatch.setattr(config_sql.sys, "platform", "linux")
    monkeypatch.setattr(config_sql.os.path, "isfile", lambda path: path == "/usr/bin/kepubify")
    monkeypatch.setattr(config_sql.os, "access", lambda *_: True)
    assert config_sql.autodetect_kepubify_binary() == "/usr/bin/kepubify"
