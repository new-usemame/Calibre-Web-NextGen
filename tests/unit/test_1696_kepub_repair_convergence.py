# -*- coding: utf-8 -*-
"""Regression coverage for terminal KEPUB repair dispositions (#1696)."""

import os
import zipfile
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
from cps.tasks import kepub_package_repair as repair_task


_CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0"
 xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/content.opf"
     media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_MISSING_MANIFEST_ITEM_OPF = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="kobo-js" href="../js/kobo.js"
     media-type="application/javascript"/>
  </manifest>
  <spine/>
</package>
"""


def _write_permanently_unsupported_package(path):
    """Write a readable package whose escaping manifest target does not exist."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER_XML)
        archive.writestr("OPS/content.opf", _MISSING_MANIFEST_ITEM_OPF)
        archive.comment = b"a"


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(
        engine,
        tables=[
            ub.NoticeEvent.__table__,
            ub.UserNoticeDelivery.__table__,
            ub.KepubPackageRepair.__table__,
            ub.KoboSyncedBooks.__table__,
        ],
    )
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()


class _FakeMetadataQuery:
    def __init__(self, entities, data, book):
        self.entities = entities
        self.data = data
        self.book = book

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def count(self):
        return 1

    def yield_per(self, _size):
        return iter([(self.data, self.book)])

    def all(self):
        if self.entities == (repair_task.db.Data.id,):
            return [(self.data.id,)]
        return [(self.data, self.book)]

    def one_or_none(self):
        return self.data, self.book


class _FakeMetadataSession:
    def __init__(self, data, book):
        self.data = data
        self.book = book

    def query(self, *entities):
        return _FakeMetadataQuery(entities, self.data, self.book)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _install_task_harness(monkeypatch, tmp_path, app_session, inspect_package):
    book_dir = tmp_path / "Author" / "Title"
    book_dir.mkdir(parents=True)
    package = book_dir / "book.kepub"
    _write_permanently_unsupported_package(package)
    book = SimpleNamespace(
        id=31,
        uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title="Unsupported book",
        path="Author/Title",
        last_modified=None,
    )
    data = SimpleNamespace(
        id=41,
        book=31,
        name="book",
        format="KEPUB",
        uncompressed_size=100,
    )
    metadata_session = _FakeMetadataSession(data, book)
    queued = []

    monkeypatch.setattr(
        repair_task.db,
        "CalibreDB",
        lambda *args, **kwargs: SimpleNamespace(session=metadata_session),
    )
    monkeypatch.setattr(
        repair_task.ub,
        "get_new_session_instance",
        lambda: app_session,
    )
    monkeypatch.setattr(
        repair_task.WorkerThread,
        "add",
        lambda _user, task, hidden=False: queued.append(task),
    )
    monkeypatch.setattr(
        repair_task.config,
        "config_use_google_drive",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        repair_task.config,
        "config_kobo_kepub_package_repair_version",
        0,
        raising=False,
    )
    monkeypatch.setattr(repair_task.config, "get_book_path", lambda: str(tmp_path))
    monkeypatch.setattr(repair_task.config, "save", lambda: None)
    monkeypatch.setattr(
        repair_task,
        "kepub_package_needs_normalization",
        inspect_package,
    )
    monkeypatch.setattr(
        repair_task,
        "normalize_kepub_package",
        lambda _path: pytest.fail("an unsupported package must not be normalized"),
    )
    monkeypatch.setattr(
        repair_task,
        "_backup_original",
        lambda *_args: pytest.fail("an unsupported package must not be backed up"),
    )
    monkeypatch.setattr(
        repair_task,
        "_sha256",
        lambda _path: pytest.fail("an unsupported package must never be hashed"),
    )
    monkeypatch.setattr(repair_task, "_pending", False)
    monkeypatch.setattr(repair_task, "_pending_owner", None)
    return package, queued


@pytest.mark.unit
def test_startup_repair_converges_with_permanently_unsupported_book(
    app_session, tmp_path, monkeypatch
):
    probes = []
    real_probe = repair_task.kepub_package_needs_normalization

    def counting_probe(path):
        probes.append(os.fspath(path))
        return real_probe(path)

    _package, queued = _install_task_harness(
        monkeypatch,
        tmp_path,
        app_session,
        counting_probe,
    )

    assert repair_task.enqueue_startup_kepub_package_repair() is True
    first_task = queued[-1]
    first_task.run(None)
    probes_after_first = len(probes)
    second_enqueued = repair_task.enqueue_startup_kepub_package_repair()
    if second_enqueued:
        queued[-1].run(None)

    observed = (
        repair_task.config.config_kobo_kepub_package_repair_version,
        second_enqueued,
        len(probes),
    )
    assert observed == (repair_task.REPAIR_VERSION, False, 1)
    assert len(probes) - probes_after_first == 0
    assert first_task.stat == STAT_FINISH_SUCCESS
    assert first_task.unsupported == 1
    assert "1 unsupported" in str(first_task.message)


@pytest.mark.unit
def test_unsupported_skip_invalidates_for_file_identity_and_repair_version(
    app_session, tmp_path, monkeypatch
):
    package = tmp_path / "unsupported.kepub"
    _write_permanently_unsupported_package(package)
    book = SimpleNamespace(
        id=31,
        uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title="Unsupported book",
        last_modified=None,
    )
    data = SimpleNamespace(book=31, uncompressed_size=100)
    probes = []
    real_probe = repair_task.kepub_package_needs_normalization

    def counting_probe(path):
        probes.append(os.fspath(path))
        return real_probe(path)

    def process(version):
        return repair_task.process_kepub_candidate(
            app_session=app_session,
            book=book,
            data=data,
            path=package,
            repair_version=version,
            inspect_package=counting_probe,
            normalize=lambda _path: pytest.fail(
                "an unsupported package must not be normalized"
            ),
            mark_modified=lambda _book: pytest.fail(
                "unsupported metadata must not be modified"
            ),
            commit_metadata=lambda: pytest.fail(
                "unsupported metadata must not be committed"
            ),
        )

    monkeypatch.setattr(
        repair_task,
        "_sha256",
        lambda _path: pytest.fail("an unsupported package must never be hashed"),
    )

    assert process(repair_task.REPAIR_VERSION) == "unsupported"
    assert process(repair_task.REPAIR_VERSION) == "unsupported"
    assert len(probes) == 1

    cached_stat = package.stat()
    with zipfile.ZipFile(package, "a") as archive:
        archive.comment = b"b"
    os.utime(package, ns=(cached_stat.st_atime_ns, cached_stat.st_mtime_ns))
    changed_stat = package.stat()
    assert changed_stat.st_size == cached_stat.st_size
    assert changed_stat.st_mtime_ns == cached_stat.st_mtime_ns
    assert changed_stat.st_ctime_ns != cached_stat.st_ctime_ns

    assert process(repair_task.REPAIR_VERSION) == "unsupported"
    assert len(probes) == 2

    assert process(repair_task.REPAIR_VERSION + 1) == "unsupported"
    assert len(probes) == 3
    rows = app_session.query(ub.KepubPackageRepair).all()
    assert len(rows) == 1
    assert rows[0].repair_version == repair_task.REPAIR_VERSION + 1
    assert rows[0].source_size == package.stat().st_size
    assert rows[0].source_mtime_ns == package.stat().st_mtime_ns
    assert rows[0].source_ctime_ns == package.stat().st_ctime_ns


@pytest.mark.unit
def test_truncated_short_read_is_retryable_and_creates_no_disposition(
    app_session, tmp_path
):
    package = tmp_path / "short-read.kepub"
    package.write_bytes(b"PK\x03\x04truncated-before-central-directory")
    book = SimpleNamespace(
        id=31,
        uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title="Temporarily short read",
        last_modified=None,
    )
    data = SimpleNamespace(book=31, uncompressed_size=100)

    result = repair_task.process_kepub_candidate(
        app_session=app_session,
        book=book,
        data=data,
        path=package,
        repair_version=repair_task.REPAIR_VERSION,
        inspect_package=repair_task.kepub_package_needs_normalization,
        normalize=lambda _path: pytest.fail("a retryable probe must not normalize"),
        mark_modified=lambda _book: pytest.fail(
            "a retryable probe must not modify metadata"
        ),
        commit_metadata=lambda: pytest.fail(
            "a retryable probe must not commit metadata"
        ),
    )

    assert result == "failed"
    assert app_session.query(ub.KepubPackageRepair).count() == 0


@pytest.mark.unit
def test_retryable_probe_failure_still_blocks_version_bump(
    app_session, tmp_path, monkeypatch
):
    def retryable_probe(_path):
        raise PermissionError("network share is temporarily unavailable")

    _package, queued = _install_task_harness(
        monkeypatch,
        tmp_path,
        app_session,
        retryable_probe,
    )

    assert repair_task.enqueue_startup_kepub_package_repair() is True
    task = queued[-1]
    task.run(None)

    assert task.stat == STAT_FAIL
    assert task.failed == 1
    assert task.unsupported == 0
    assert repair_task.config.config_kobo_kepub_package_repair_version == 0
    assert app_session.query(ub.KepubPackageRepair).count() == 0


@pytest.mark.unit
def test_unsupported_identity_columns_migrate_existing_app_db_idempotently():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE kepub_package_repair ("
                "id INTEGER PRIMARY KEY, occurrence_key VARCHAR(64) NOT NULL, "
                "book_id INTEGER NOT NULL, source_sha256 VARCHAR(64) NOT NULL, "
                "status VARCHAR(24) NOT NULL)"
            )
        )

    repair_task.ub.migrate_kepub_package_repair_disposition(engine, None)
    repair_task.ub.migrate_kepub_package_repair_disposition(engine, None)

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("kepub_package_repair")
    }
    assert {
        "source_size",
        "source_mtime_ns",
        "source_ctime_ns",
        "repair_version",
    } <= set(columns)
    assert columns["source_size"]["nullable"] is True
    assert columns["source_mtime_ns"]["nullable"] is True
    assert columns["source_ctime_ns"]["nullable"] is True
    assert columns["repair_version"]["nullable"] is True
