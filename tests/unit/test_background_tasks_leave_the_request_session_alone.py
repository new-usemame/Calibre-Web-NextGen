# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background tasks must never use the session that web requests share.

``ub.session`` is one SQLAlchemy ``Session`` shared by every request greenlet
on the thread that serves HTTP, and ``ConfigSQL`` persists through it.
WorkerThread is a real OS thread (this app never monkey-patches threading),
and a SQLAlchemy Session is not thread-safe.  Before this fix the KEPUB
backfill and package-repair tasks called ``config.save()`` from WorkerThread,
so a request landing during that commit failed with a 500:
"This session is in 'prepared' state".  A thread dump taken at the failure
showed the request's query refused on the serving thread while WorkerThread
sat inside ``config.save()`` -> ``Session.commit()`` of the same session.

The interleaving is made deterministic by pausing a thread's commit inside
the engine's ``commit`` event, just before the DBAPI COMMIT: the moment the
production thread dump caught.  Every wait is bounded, so a regression fails
in seconds instead of hanging the suite.
"""

import io
import os
import sqlite3
import threading
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from PIL import Image
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from cps import config_sql, helper, ub
from cps.progress_syncing import settings as kosync_settings
from cps.services import user_cover
from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
from cps.tasks import auto_send, kepub_backfill, kepub_package_repair, mail

pytestmark = pytest.mark.unit

_BOUND = 10  # seconds


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """A real app.db whose ``ub.session`` plays the requests' shared session.

    ``ConfigSQL`` is initialised on this (the serving) thread with that session,
    exactly as ``create_app`` does, and the tasks under test see it as ``config``.
    """
    previous_session, previous_path = ub.session, ub.app_DB_path
    db_path = tmp_path / "app.db"
    ub.init_db(str(db_path))
    key = Fernet.generate_key()
    config_sql.load_configuration(ub.session, key)
    config = config_sql.ConfigSQL()
    config.init_config(ub.session, key, None)
    for module in (kepub_backfill, kepub_package_repair, auto_send):
        monkeypatch.setattr(module, "config", config)
    try:
        yield SimpleNamespace(path=db_path, config=config)
    finally:
        engine = ub.session.get_bind()
        ub.session.close()
        engine.dispose()
        ub.session, ub.app_DB_path = previous_session, previous_path


def _settings_row(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute(
            "SELECT config_kobo_kepub_backfill_completed AS backfill_completed, "
            "config_kobo_kepub_package_repair_version AS repair_version, "
            "config_title_regex AS title_regex FROM settings").fetchone())


def _download_rows(db_path):
    with sqlite3.connect(db_path) as connection:
        return set(connection.execute("SELECT user_id, book_id FROM downloads").fetchall())


def _in_worker(fn):
    """Run ``fn`` on a separate OS thread, as WorkerThread runs a task."""
    outcome = {}

    def target():
        try:
            outcome["result"] = fn()
        except BaseException as error:  # reported by the test, never swallowed
            outcome["error"] = error

    thread = threading.Thread(target=target, name="WorkerThread-under-test")
    thread.start()
    return thread, outcome


def _finish(thread):
    thread.join(_BOUND)
    assert not thread.is_alive(), "the background task did not finish"


class _EmptyMetadataSession:
    """metadata.db with no KEPUB formats: the repair scan finds nothing to do."""

    def query(self, *_entities):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def join(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def close(self):
        return None


# Each writer arranges one real background task.  ``run`` is what WorkerThread
# executes; ``verify`` checks that the task's own write landed, both in app.db
# and in this process's config.

def _kepub_backfill_completion(app_db, monkeypatch):
    def verify():
        assert _settings_row(app_db.path)["backfill_completed"] == 1
        assert app_db.config.config_kobo_kepub_backfill_completed is True

    return SimpleNamespace(
        run=lambda: kepub_backfill.TaskKepubBackfill._persist_completion(True),
        verify=verify)


def _kepub_repair_completion(app_db, monkeypatch):
    monkeypatch.setattr(kepub_package_repair.db, "CalibreDB",
                        lambda *args, **kwargs: SimpleNamespace(session=_EmptyMetadataSession()))
    task = kepub_package_repair.TaskKepubPackageRepair()

    def verify():
        assert task.stat == STAT_FINISH_SUCCESS, task.error
        assert _settings_row(app_db.path)["repair_version"] == kepub_package_repair.REPAIR_VERSION
        assert app_db.config.config_kobo_kepub_package_repair_version == \
            kepub_package_repair.REPAIR_VERSION

    return SimpleNamespace(run=lambda: task.run(None), verify=verify, task=task)


def _auto_send_download_record(app_db, monkeypatch):
    reader = ub.session.query(ub.User).filter(ub.User.name == "admin").one()
    reader.kindle_mail = "reader@kindle.example"
    reader.auto_send_enabled = True
    ub.session.commit()
    reader_id = reader.id
    monkeypatch.setattr(auto_send.db, "CalibreDB", lambda *args, **kwargs: SimpleNamespace(
        get_book=lambda book_id: SimpleNamespace(id=book_id, title="Ingested book"),
        session=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(auto_send.helper, "check_send_to_ereader",
                        lambda book: [{"format": "Epub", "convert": 0}])
    sent_to = []
    monkeypatch.setattr(auto_send.helper, "send_mail",
                        lambda **kwargs: sent_to.append(kwargs["ereader_mail"]))
    task = auto_send.TaskAutoSend("Auto-sending", book_id=7, user_id=reader_id)

    def verify():
        assert task.stat == STAT_FINISH_SUCCESS, task.error
        assert sent_to == ["reader@kindle.example"]
        assert (reader_id, 7) in _download_rows(app_db.path)

    return SimpleNamespace(run=lambda: task.run(None), verify=verify, task=task)


def _config_invalidation(app_db, monkeypatch):
    def verify():
        assert app_db.config.db_configured is False

    return SimpleNamespace(
        run=lambda: app_db.config.invalidate("metadata.db is gone"), verify=verify)


_COMMITTING_WRITERS = [
    pytest.param(_kepub_backfill_completion, id="kepub-backfill-completion"),
    pytest.param(_kepub_repair_completion, id="kepub-repair-completion"),
    pytest.param(_auto_send_download_record, id="auto-send-download-record"),
]


@pytest.mark.parametrize("writer", _COMMITTING_WRITERS)
def test_request_is_served_while_a_background_task_commits(app_db, monkeypatch, writer):
    """A request that arrives mid-commit of a background task is answered.

    Drives the real task on its own thread and pauses its commit before the
    DBAPI COMMIT; meanwhile the serving thread runs the query every anonymous
    request starts with (``ub.Anonymous()`` -> ``loadSettings``).  Breaks if
    the task commits the requests' session: that query then raises
    "This session is in 'prepared' state", the production 500.
    """
    background = writer(app_db, monkeypatch)
    test_thread = threading.get_ident()
    in_commit, release = threading.Event(), threading.Event()

    def pause_background_commit(_connection):
        if threading.get_ident() != test_thread:
            in_commit.set()
            release.wait(_BOUND)

    event.listen(Engine, "commit", pause_background_commit)
    try:
        thread, outcome = _in_worker(background.run)
        assert in_commit.wait(_BOUND), "the background task never reached its commit"
        try:
            ub.Anonymous()
            request_error = None
        except Exception as error:
            request_error = error
        release.set()
        _finish(thread)
    finally:
        release.set()
        event.remove(Engine, "commit", pause_background_commit)

    assert request_error is None, (
        "a request failed while the background task was committing: %r" % (request_error,))
    assert "error" not in outcome, outcome.get("error")
    background.verify()


@pytest.mark.parametrize("writer", _COMMITTING_WRITERS + [
    pytest.param(_config_invalidation, id="config-invalidation"),
])
def test_background_task_never_commits_a_requests_unfinished_write(app_db, monkeypatch, writer):
    """A request's uncommitted write stays the request's to keep or discard.

    The serving thread stages a row in the requests' session without
    committing it, the background task runs to completion, then the request
    rolls back.  Breaks if the task commits the requests' session: the staged
    row is then already durable and the rollback cannot remove it.
    """
    background = writer(app_db, monkeypatch)
    ub.session.add(ub.Downloads(user_id=1, book_id=424242))

    thread, outcome = _in_worker(background.run)
    _finish(thread)
    ub.session.rollback()

    assert (1, 424242) not in _download_rows(app_db.path), (
        "the background task committed a write the request never committed")
    assert "error" not in outcome, outcome.get("error")
    background.verify()


def test_background_marker_save_does_not_persist_a_requests_unsaved_setting(app_db):
    """A background marker write persists that marker and nothing else.

    An admin form sets fields on the shared config before validating them; a
    task saving its marker at that moment must not make them durable.  Breaks
    if the task saves the whole in-memory config: ``save()`` writes every
    dirty field, including the request's unvalidated edit.
    """
    stored_regex = _settings_row(app_db.path)["title_regex"]
    app_db.config.config_title_regex = "request-edit-not-yet-validated"

    thread, outcome = _in_worker(
        lambda: kepub_backfill.TaskKepubBackfill._persist_completion(True))
    _finish(thread)

    assert "error" not in outcome, outcome.get("error")
    row = _settings_row(app_db.path)
    assert row["backfill_completed"] == 1
    assert row["title_regex"] == stored_regex


def test_a_settings_reload_keeps_a_background_marker_save(app_db):
    """Reloading settings on the serving thread reads what a task saved.

    An admin settings form that fails validation reloads the settings. The
    requests' session still holds the row it read at boot, so a reload that
    trusted it put the boot-time marker back in memory, and the task it
    guards would run again in this process. Breaks if ``load()`` reads the
    cached row instead of the database.
    """
    app_db.config.__dict__["_settings"] = None
    app_db.config.load()  # a warm boot leaves the row loaded
    assert app_db.config.config_kobo_kepub_backfill_completed is False

    thread, outcome = _in_worker(
        lambda: kepub_backfill.TaskKepubBackfill._persist_completion(True))
    _finish(thread)
    assert "error" not in outcome, outcome.get("error")

    app_db.config.load()

    assert app_db.config.config_kobo_kepub_backfill_completed is True
    assert _settings_row(app_db.path)["backfill_completed"] == 1


def test_settings_save_and_reload_work_after_the_session_lets_go_of_the_row(app_db):
    """A closed requests' session does not turn settings pages into errors.

    The session is closed when a failed rollback has to be abandoned, which
    detaches the settings row the configuration holds. Saving and reloading
    must still work and read what is stored. Breaks if ``load()`` refreshes
    the held row in place: that raises for a detached row on every request.
    """
    app_db.config.load()
    ub.session.close()

    app_db.config.config_title_regex = "saved-after-close"
    app_db.config.save()
    app_db.config.load()

    assert app_db.config.config_title_regex == "saved-after-close"
    assert _settings_row(app_db.path)["title_regex"] == "saved-after-close"


@pytest.mark.parametrize("operation", ["save", "load"])
def test_config_session_access_off_the_serving_thread_is_refused(app_db, operation):
    """``ConfigSQL.save()``/``load()`` refuse to run on a thread that does not serve requests.

    Both use the requests' session, so from any other thread they are the race
    this file is about; a future task that calls one must fail loudly in
    tests, not intermittently in production.  Breaks if the guard is removed:
    ``save`` then commits the setting from the worker thread.
    """
    stored_regex = _settings_row(app_db.path)["title_regex"]
    app_db.config.config_title_regex = "saved-from-a-worker"

    thread, outcome = _in_worker(getattr(app_db.config, operation))
    _finish(thread)

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    assert _settings_row(app_db.path)["title_regex"] == stored_regex


def test_save_fields_writes_only_plain_stored_settings(app_db):
    """A misspelt or encrypted setting name raises instead of silently not saving.

    Breaks if ``save_fields`` accepts any attribute name: SQLAlchemy keeps an
    unknown attribute on the object without writing it, so a misspelt marker
    would never persist and its task would re-run on every boot.  An encrypted
    (``*_e``) setting would be stored in clear text.
    """
    with pytest.raises(AttributeError):
        app_db.config.save_fields(config_kobo_kepub_backfil_completed=True)
    with pytest.raises(AttributeError):
        app_db.config.save_fields(mail_password_e="secret")
    assert _settings_row(app_db.path)["backfill_completed"] == 0

    app_db.config.save_fields(config_kobo_kepub_backfill_completed=True)
    assert _settings_row(app_db.path)["backfill_completed"] == 1
    assert app_db.config.config_kobo_kepub_backfill_completed is True


def test_repair_whose_marker_cannot_be_saved_reports_failure_and_runs_again(
        app_db, monkeypatch):
    """A failed marker write is a failed repair, and the next boot repairs again.

    The repair finishes its scan, then SQLite refuses the COMMIT of its version
    marker.  Breaks if the failure is swallowed, or the in-memory marker is
    published before the commit lands: the task then reports success and this
    process believes the repair is done while app.db says it is not.
    """
    background = _kepub_repair_completion(app_db, monkeypatch)
    queued = []
    monkeypatch.setattr(kepub_package_repair.WorkerThread, "add",
                        lambda _user, task, hidden=False: queued.append(task))
    monkeypatch.setattr(kepub_package_repair, "_pending", False)
    monkeypatch.setattr(kepub_package_repair, "_pending_owner", None)
    test_thread = threading.get_ident()

    def refuse_background_commit(_connection):
        if threading.get_ident() != test_thread:
            raise OperationalError("COMMIT", None, sqlite3.OperationalError("disk I/O error"))

    event.listen(Engine, "commit", refuse_background_commit)
    try:
        thread, outcome = _in_worker(background.run)
        _finish(thread)
    finally:
        event.remove(Engine, "commit", refuse_background_commit)

    assert "error" not in outcome, outcome.get("error")
    assert background.task.stat == STAT_FAIL
    assert "completion marker could not be saved" in str(background.task.error)
    assert _settings_row(app_db.path)["repair_version"] == 0
    assert app_db.config.config_kobo_kepub_package_repair_version == 0
    assert kepub_package_repair.enqueue_startup_kepub_package_repair() is True
    assert len(queued) == 1


def _jpeg(color):
    stream = io.BytesIO()
    Image.new("RGB", (12, 18), color).save(stream, "JPEG")
    return stream.getvalue()


def _epub(path, cover_bytes):
    container = (b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:'
                 b'xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                 b'</rootfiles></container>')
    package = (b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
               b'version="2.0"><metadata><meta name="cover" content="cover-image"/></metadata>'
               b'<manifest><item id="cover-image" href="images/cover.jpg" '
               b'media-type="image/jpeg"/><item id="chapter" href="chapter.xhtml" '
               b'media-type="application/xhtml+xml"/></manifest>'
               b'<spine><itemref idref="chapter"/></spine></package>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/images/cover.jpg", cover_bytes)
        archive.writestr("OEBPS/chapter.xhtml", b"<p>unchanged</p>")


def test_emailed_book_keeps_the_senders_cover_while_a_request_commits(
        app_db, tmp_path, monkeypatch):
    """Send to eReader attaches the sender's personal cover even mid-request.

    The e-mail task runs on WorkerThread and looks up the sender's cover choice
    in app.db.  Here it runs while a request on the serving thread is inside
    its own commit.  Breaks if the task reads through the requests' session:
    the lookup raises "prepared state", the task swallows it, and the reader
    receives the library cover instead of the one they chose.
    """
    global_cover, personal_cover = _jpeg("blue"), _jpeg("red")
    library = tmp_path / "library"
    (library / "Author" / "Book").mkdir(parents=True)
    _epub(library / "Author" / "Book" / "book.epub", global_cover)

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path / "config"))
    sender_id = ub.session.query(ub.User).filter(ub.User.name == "admin").one().id
    chosen = datetime(2026, 2, 1, tzinfo=timezone.utc)
    ub.session.add(ub.UserBookCover(user_id=sender_id, book_id=11, updated_at=chosen))
    ub.session.commit()
    cover_file = user_cover.path_for_row(
        SimpleNamespace(user_id=sender_id, book_id=11, updated_at=chosen))
    os.makedirs(os.path.dirname(cover_file))
    with open(cover_file, "wb") as stream:
        stream.write(personal_cover)

    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path / "deliveries"))
    monkeypatch.setattr(kosync_settings, "is_koreader_sync_enabled", lambda: False)
    monkeypatch.setattr(mail, "config", SimpleNamespace(
        get_book_path=lambda: str(library), config_use_google_drive=False,
        config_binariesdir="", config_embed_metadata=False))
    task = mail.TaskEmail("Book", "Author/Book", "book.epub", {}, "reader@kindle.example",
                          "Sending", "text", id=11, cover_user_id=sender_id)

    test_thread = threading.get_ident()
    outcome = {}

    def send_during_request_commit(_connection):
        if threading.get_ident() == test_thread and not outcome:
            thread, attached = _in_worker(
                lambda: task._get_attachment("Author/Book", "book.epub"))
            _finish(thread)
            outcome.update(attached)

    event.listen(Engine, "commit", send_during_request_commit)
    try:
        ub.session.add(ub.Downloads(user_id=sender_id, book_id=5))
        ub.session.commit()
    finally:
        event.remove(Engine, "commit", send_during_request_commit)

    assert "error" not in outcome, outcome.get("error")
    with zipfile.ZipFile(io.BytesIO(outcome["result"])) as attached:
        cover = attached.read("OEBPS/images/cover.jpg")
    assert cover != global_cover, "the e-mail carried the library cover, not the sender's"
    with Image.open(io.BytesIO(cover)) as image:
        red, _green, blue = image.resize((1, 1)).getpixel((0, 0))
    assert red > blue
