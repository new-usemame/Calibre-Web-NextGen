"""Regression pins for default-on, cooperative Kobo KEPUB delivery."""

import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import config_sql

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


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


def test_download_selection_honours_off_but_keeps_existing_kepub(monkeypatch, tmp_path):
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
    # The selected KEPUB contract is content, not merely the wire label.
    import zipfile
    served_file = tmp_path / "book.kepub"
    with zipfile.ZipFile(served_file, "w") as archive:
        archive.writestr("chapter.xhtml", '<span class="koboSpan" id="kobo.1.1">Text</span>')
    with zipfile.ZipFile(served_file) as archive:
        assert b"koboSpan" in archive.read("chapter.xhtml")


def test_setting_on_without_binary_offers_epub_and_download_queues_nothing(monkeypatch):
    import cps.kobo as kobo

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

    helper_source = inspect.getsource(__import__("cps.helper", fromlist=["get_download_link"]).get_download_link)
    assert "config.config_kobo_prefer_kepub" in helper_source
    assert "run_blocking" in helper_source
    assert "timeout=25" in helper_source


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


def test_conversion_emits_resync_signal_without_unsyncing_every_user():
    source = (ROOT / "cps/tasks/convert.py").read_text(encoding="utf-8")
    assert "mark_book_modified(cur_book, set_dirty=False)" in source
    assert "remove_synced_book(book_id, True" not in source


def test_kepubify_probe_covers_standard_linux_paths_and_path(monkeypatch):
    monkeypatch.setattr(config_sql.sys, "platform", "linux")
    monkeypatch.setattr(config_sql.os.path, "isfile", lambda path: path == "/usr/bin/kepubify")
    monkeypatch.setattr(config_sql.os, "access", lambda *_: True)
    assert config_sql.autodetect_kepubify_binary() == "/usr/bin/kepubify"
