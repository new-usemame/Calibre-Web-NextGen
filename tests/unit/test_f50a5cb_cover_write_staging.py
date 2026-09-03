# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""F-50a5cb: cover bytes and metadata must cross one commit boundary safely."""

from contextlib import nullcontext
from datetime import datetime, timezone
import errno
import inspect
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import flask
from flask_babel import Babel
from PIL import Image
import pytest
import requests
from werkzeug.datastructures import FileStorage


pytestmark = pytest.mark.unit


OLD_COVER = b"the byte-identical previous cover"
OLD_MODIFIED = datetime(2020, 1, 2, tzinfo=timezone.utc)
NEW_MODIFIED = datetime(2026, 8, 18, tzinfo=timezone.utc)


def _jpeg_bytes(color=(21, 84, 160)):
    output = io.BytesIO()
    Image.new("RGB", (8, 11), color).save(output, format="JPEG")
    return output.getvalue()


def _storage(data=None):
    return FileStorage(
        stream=io.BytesIO(data or _jpeg_bytes()),
        filename="cover.jpg",
        content_type="image/jpeg",
    )


def _book(path="Author/Book (7)"):
    return SimpleNamespace(
        id=7,
        path=path,
        has_cover=0,
        last_modified=OLD_MODIFIED,
        title="Book",
        authors=[SimpleNamespace(name="Author")],
        identifiers=[],
        comments=[],
        publishers=[],
        tags=[],
        series=[],
        series_index="1.0",
        pubdate=None,
        ratings=[],
    )


def _install_local_library(monkeypatch, helper, tmp_path, book):
    library = tmp_path / "library"
    cover = library / book.path / "cover.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(OLD_COVER)
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library))
    return cover


class _FailingSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def merge(self, _book):
        return None

    def commit(self):
        self.commits += 1
        raise RuntimeError("simulated metadata commit failure")

    def rollback(self):
        self.rollbacks += 1


class _SuccessfulSession:
    def __init__(self, events):
        self.events = events

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        raise AssertionError("successful cover update must not roll back")


class _DiskFullImage:
    headers = {"content-type": "image/jpeg"}

    def save(self, filename):
        with open(filename, "wb") as target:
            target.write(b"short")
        raise OSError(errno.ENOSPC, "simulated disk full")


def _mark_modified(book, *args, **kwargs):
    book.last_modified = NEW_MODIFIED


def _editor():
    return SimpleNamespace(
        id=3,
        name="cover-editor",
        is_authenticated=True,
        is_anonymous=False,
        role_edit=lambda: True,
        role_admin=lambda: False,
    )


def test_short_write_stages_away_from_existing_cover(tmp_path, monkeypatch):
    """Pre-fix, _DiskFullImage.save receives cover.jpg and truncates OLD_COVER."""
    from cps import helper

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)

    staged, error = helper.save_cover(_DiskFullImage(), book.path)

    assert not staged
    assert error
    assert cover.read_bytes() == OLD_COVER
    assert not list(cover.parent.glob(".cover.jpg.cwng-*.stage"))


def test_undecodable_stage_is_rejected_without_touching_existing_cover(
    tmp_path, monkeypatch
):
    from cps import helper

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    junk = _storage(b"not actually a JPEG")

    staged, error = helper.save_cover(junk, book.path)

    assert not staged
    assert error
    assert cover.read_bytes() == OLD_COVER
    assert not list(cover.parent.glob(".cover.jpg.cwng-*.stage"))


@pytest.mark.parametrize("existing", [True, False], ids=["same-id-update", "insert"])
def test_google_drive_cover_publish_updates_same_id_or_inserts(
    tmp_path, monkeypatch, existing
):
    from cps import helper

    monkeypatch.setattr(helper.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))

    class RemoteFile(dict):
        def __init__(self, metadata):
            super().__init__(metadata)
            self.content_path = None
            self.uploads = 0

        def SetContentFile(self, path):
            assert Image.open(path).format == "JPEG"
            self.content_path = path

        def Upload(self):
            assert self.content_path
            self.uploads += 1

    previous = RemoteFile({"id": "cover-file-id", "title": "cover.jpg"}) if existing else None
    created = []
    def create_file(metadata):
        created.append(RemoteFile(metadata))
        return created[-1]
    drive = SimpleNamespace(CreateFile=MagicMock(side_effect=create_file))
    monkeypatch.setattr(
        helper.gd,
        "prepareCoverUpload",
        MagicMock(return_value=(drive, "book-folder-id", previous)),
    )

    staged, error = helper.save_cover(_storage(), "Author/Book (7)")

    assert staged and error is None
    assert previous is None or previous.uploads == 0
    published, publish_error = staged.publish()
    assert published and publish_error is None
    if existing:
        drive.CreateFile.assert_not_called()
        assert previous["id"] == "cover-file-id"
        assert previous.uploads == 1
    else:
        drive.CreateFile.assert_called_once_with({
            "title": "cover.jpg",
            "parents": [{"kind": "drive#fileLink", "id": "book-folder-id"}],
        })
        assert created[0].uploads == 1
    assert not list(tmp_path.glob(".cover.jpg.cwng-*.stage"))


def test_google_drive_publish_failure_uses_api_metadata_compensation(tmp_path, monkeypatch):
    from cps import helper, kobo_sync_status
    from cps.api import edit as api_edit

    book = _book()
    events = []
    session = _SequencedSession(events)
    app = flask.Flask(__name__)

    class FailingRemoteFile(dict):
        def SetContentFile(self, path):
            assert Image.open(path).format == "JPEG"
            events.append("remote-content")

        def Upload(self):
            events.append("remote-update")
            raise OSError("simulated Drive update failure")

    existing = FailingRemoteFile(id="same-cover-id", title="cover.jpg")
    drive = SimpleNamespace(CreateFile=MagicMock())
    monkeypatch.setattr(helper.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(helper.gd, "prepareCoverUpload",
                        lambda _path: (drive, "book-folder-id", existing))
    monkeypatch.setattr(api_edit, "current_user", _editor())
    monkeypatch.setattr(api_edit.calibre_db, "get_filtered_book", lambda *a, **k: book)
    monkeypatch.setattr(api_edit.calibre_db, "session", session)
    monkeypatch.setattr(api_edit, "book_cover_is_locked", lambda _book_id: False)
    monkeypatch.setattr(api_edit, "mark_book_modified", _mark_modified)
    monkeypatch.setattr(kobo_sync_status, "remove_synced_book", MagicMock())
    monkeypatch.setattr(api_edit, "replace_cover_thumbnail_cache", MagicMock())

    with app.test_request_context(
        "/api/v1/books/7/cover", method="POST",
        data={"file": (_storage().stream, "cover.jpg")},
        content_type="multipart/form-data",
    ):
        response, status = inspect.unwrap(api_edit.set_cover)(book.id)

    assert status == 500
    assert response.get_json()
    assert events == ["commit", "remote-content", "remote-update", "compensate"]
    assert existing["id"] == "same-cover-id"
    drive.CreateFile.assert_not_called()
    assert book.has_cover == 0
    assert book.last_modified == OLD_MODIFIED
    assert not list(tmp_path.glob(".cover.jpg.cwng-*.stage"))


@pytest.mark.parametrize("actual_format", ["PNG", "WEBP", "BMP"])
def test_decoded_non_jpeg_is_normalized_regardless_of_declared_mime(
    tmp_path, monkeypatch, actual_format
):
    from cps import helper

    output = io.BytesIO()
    Image.new("RGBA" if actual_format == "PNG" else "RGB", (8, 11), (10, 20, 30, 128)
              if actual_format == "PNG" else (10, 20, 30)).save(output, format=actual_format)
    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)

    staged, error = helper.save_cover(_storage(output.getvalue()), book.path)
    assert staged and error is None
    assert cover.read_bytes() == OLD_COVER
    assert staged.publish() == (True, None)
    with Image.open(cover) as decoded:
        assert decoded.format == "JPEG"
        decoded.load()


@pytest.mark.parametrize("variant", ["progressive", "cmyk", "exif"])
def test_jpeg_variants_are_fully_decoded_and_preserved(
    tmp_path, monkeypatch, variant
):
    from cps import helper

    output = io.BytesIO()
    image = Image.new("CMYK" if variant == "cmyk" else "RGB", (8, 11), 10)
    options = {"progressive": True} if variant == "progressive" else {}
    if variant == "exif":
        exif = Image.Exif()
        exif[274] = 6
        options["exif"] = exif
    image.save(output, format="JPEG", **options)
    original = output.getvalue()
    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)

    staged, error = helper.save_cover(_storage(original), book.path)
    assert staged and error is None
    assert staged.publish() == (True, None)
    assert cover.read_bytes() == original
    with Image.open(cover) as decoded:
        decoded.load()


def test_startup_scavenger_logs_and_removes_only_cover_stages(tmp_path, monkeypatch):
    from cps import helper

    library = tmp_path / "library"
    temp_dir = tmp_path / "temp"
    book_dir = library / "Author" / "Book (7)"
    book_dir.mkdir(parents=True)
    temp_dir.mkdir()
    canonical = book_dir / "cover.jpg"
    canonical.write_bytes(OLD_COVER)
    local_stage = book_dir / ".cover.jpg.cwng-local.stage"
    drive_stage = temp_dir / ".cover.jpg.cwng-drive.stage"
    unrelated = book_dir / ".cover.jpg.other.stage"
    for path in (local_stage, drive_stage, unrelated):
        path.write_bytes(b"staged")
    warnings = []
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(temp_dir))
    monkeypatch.setattr(helper.log, "warning", lambda message, *args: warnings.append(message % args))

    assert helper.scavenge_staged_cover_files() == 2
    assert not local_stage.exists()
    assert not drive_stage.exists()
    assert unrelated.exists()
    assert canonical.read_bytes() == OLD_COVER
    assert len(warnings) == 2
    assert all("metadata may already reference an unpublished cover" in warning for warning in warnings)
    from cps import create_app
    assert "helper.scavenge_staged_cover_files()" in inspect.getsource(create_app)


def test_classic_upload_commit_failure_keeps_cover_and_metadata(
    tmp_path, monkeypatch
):
    from cps import editbooks, helper

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    session = _FailingSession()
    app = flask.Flask(__name__)
    app.secret_key = "classic-cover-staging"
    Babel(app)
    app.add_url_rule(
        "/book/<int:book_id>",
        endpoint="web.show_book",
        view_func=lambda book_id: str(book_id),
    )

    monkeypatch.setattr(editbooks, "current_user", _editor())
    monkeypatch.setattr(editbooks.calibre_db, "get_filtered_book", lambda *a, **k: book)
    monkeypatch.setattr(editbooks.calibre_db, "session", session)
    monkeypatch.setattr(editbooks, "metadata_db_write_lock", nullcontext)
    monkeypatch.setattr(editbooks, "handle_author_on_edit", lambda *a, **k: (["Author"], False))
    for name in (
        "edit_book_ratings",
        "edit_book_series_index",
        "edit_book_comments",
        "edit_book_tags",
        "edit_book_series",
        "edit_book_publisher",
        "edit_book_languages",
        "edit_all_cc_data",
    ):
        monkeypatch.setattr(editbooks, name, lambda *a, **k: False)
    monkeypatch.setattr(editbooks, "identifier_list", lambda *a, **k: [])
    monkeypatch.setattr(editbooks, "modify_identifiers", lambda *a, **k: (False, False))
    monkeypatch.setattr(editbooks.helper, "mark_book_modified", _mark_modified)
    monkeypatch.setattr(editbooks.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(editbooks.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(editbooks.helper, "replace_cover_thumbnail_cache", MagicMock())

    with app.test_request_context(
        "/admin/book/7",
        method="POST",
        data={"authors": "Author", "btn-upload-cover": (_storage().stream, "cover.jpg")},
        content_type="multipart/form-data",
    ):
        response = editbooks.do_edit_book(book.id)

    assert response.status_code == 302
    assert session.rollbacks == 1
    assert cover.read_bytes() == OLD_COVER
    assert book.has_cover == 0
    assert book.last_modified == OLD_MODIFIED


def test_cover_picker_commit_failure_keeps_cover_and_metadata(
    tmp_path, monkeypatch
):
    from cps import cover_picker, helper

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    session = _FailingSession()
    app = flask.Flask(__name__)
    Babel(app)

    monkeypatch.setattr(cover_picker, "current_user", _editor())
    monkeypatch.setattr(cover_picker, "_load_book", lambda _book_id: book)
    monkeypatch.setattr(cover_picker, "_get_lock_state", lambda _book_id: False)
    monkeypatch.setattr(cover_picker.calibre_db, "session", session)
    monkeypatch.setattr(cover_picker.helper, "mark_book_modified", _mark_modified)
    monkeypatch.setattr(cover_picker.helper, "log_metadata_change", MagicMock())

    with app.test_request_context(
        "/book/7/cover/apply",
        method="POST",
        data={"file": (_storage().stream, "cover.jpg")},
        content_type="multipart/form-data",
    ):
        response = inspect.unwrap(cover_picker.cover_picker_apply)(book.id)

    assert response.status_code == 500
    assert session.rollbacks == 1
    assert cover.read_bytes() == OLD_COVER
    assert book.has_cover == 0
    assert book.last_modified == OLD_MODIFIED


def test_spa_cover_commit_failure_keeps_cover_and_metadata(tmp_path, monkeypatch):
    from cps import helper
    from cps.api import edit as api_edit

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    session = _FailingSession()
    app = flask.Flask(__name__)

    monkeypatch.setattr(api_edit, "current_user", _editor())
    monkeypatch.setattr(api_edit.calibre_db, "get_filtered_book", lambda *a, **k: book)
    monkeypatch.setattr(api_edit.calibre_db, "session", session)
    monkeypatch.setattr(api_edit, "book_cover_is_locked", lambda _book_id: False)
    monkeypatch.setattr(api_edit, "mark_book_modified", _mark_modified)

    with app.test_request_context(
        "/api/v1/books/7/cover",
        method="POST",
        data={"file": (_storage().stream, "cover.jpg")},
        content_type="multipart/form-data",
    ):
        response, status = inspect.unwrap(api_edit.set_cover)(book.id)

    assert status == 500
    assert session.rollbacks == 1
    assert cover.read_bytes() == OLD_COVER
    assert book.has_cover == 0
    assert book.last_modified == OLD_MODIFIED


def test_ingest_cover_commit_failure_keeps_cover_and_metadata(tmp_path, monkeypatch):
    from cps import helper, metadata_helper

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    session = _FailingSession()
    cdb = SimpleNamespace(session=session)
    settings = {
        "auto_metadata_smart_application": False,
        "auto_metadata_update_title": False,
        "auto_metadata_update_authors": False,
        "auto_metadata_update_description": False,
        "auto_metadata_update_publisher": False,
        "auto_metadata_update_tags": False,
        "auto_metadata_update_series": False,
        "auto_metadata_update_published_date": False,
        "auto_metadata_update_rating": False,
        "auto_metadata_update_identifiers": False,
        "auto_metadata_update_cover": True,
    }
    monkeypatch.setattr(
        metadata_helper,
        "CWA_DB",
        lambda: SimpleNamespace(get_cwa_settings=lambda: settings),
    )
    monkeypatch.setattr(helper, "book_cover_is_locked", lambda _book_id: False)
    monkeypatch.setattr(helper.cli_param, "allow_localhost", True)

    response = requests.Response()
    response.status_code = 200
    response.headers["content-type"] = "image/jpeg"
    response._content = _jpeg_bytes()
    response._content_consumed = True
    response.request = requests.Request(
        "GET", "https://covers.example/7.jpg"
    ).prepare()
    monkeypatch.setattr(helper.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(helper, "mark_book_modified", _mark_modified)

    metadata = SimpleNamespace(title="", cover="https://covers.example/7.jpg")
    assert metadata_helper._apply_metadata_to_book(book, metadata, cdb) is False

    assert session.rollbacks == 1
    assert cover.read_bytes() == OLD_COVER
    assert book.has_cover == 0
    assert book.last_modified == OLD_MODIFIED


def test_spa_success_commits_then_publishes_then_invalidates_thumbnail(
    tmp_path, monkeypatch
):
    from cps import helper
    from cps.api import edit as api_edit

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    events = []
    session = _SuccessfulSession(events)
    app = flask.Flask(__name__)
    real_replace = helper.os.replace

    def observed_replace(source, target):
        assert str(target) == str(cover)
        assert cover.read_bytes() == OLD_COVER
        events.append("publish")
        return real_replace(source, target)

    monkeypatch.setattr(helper.os, "replace", observed_replace)
    monkeypatch.setattr(api_edit, "current_user", _editor())
    monkeypatch.setattr(api_edit.calibre_db, "get_filtered_book", lambda *a, **k: book)
    monkeypatch.setattr(api_edit.calibre_db, "session", session)
    monkeypatch.setattr(api_edit, "book_cover_is_locked", lambda _book_id: False)
    monkeypatch.setattr(api_edit, "mark_book_modified", _mark_modified)
    monkeypatch.setattr(api_edit, "log_metadata_change", lambda *a, **k: events.append("enforce"))
    monkeypatch.setattr(api_edit, "replace_cover_thumbnail_cache", lambda *a, **k: events.append("thumb"))
    monkeypatch.setattr(api_edit, "cover_url_for", lambda *a, **k: "/cover/7/md")
    monkeypatch.setattr("cps.kobo_sync_status.remove_synced_book", lambda *a, **k: events.append("unsync"))

    with app.test_request_context(
        "/api/v1/books/7/cover",
        method="POST",
        data={"file": (_storage().stream, "cover.jpg")},
        content_type="multipart/form-data",
    ):
        response = inspect.unwrap(api_edit.set_cover)(book.id)

    assert response.status_code == 200
    assert events[:3] == ["commit", "publish", "enforce"]
    assert events.index("thumb") > events.index("publish")
    assert cover.read_bytes() == _jpeg_bytes()
    with Image.open(cover) as decoded:
        decoded.verify()
    assert not list(cover.parent.glob(".cover.jpg.cwng-*.stage"))


class _SequencedSession:
    def __init__(self, events, fail_compensation=False):
        self.events = events
        self.fail_compensation = fail_compensation
        self.commits = 0
        self.rollbacks = 0

    def merge(self, book):
        return book

    def add(self, _value):
        return None

    def commit(self):
        self.commits += 1
        self.events.append("commit" if self.commits == 1 else "compensate")
        if self.commits == 2 and self.fail_compensation:
            raise RuntimeError("simulated compensation failure")

    def rollback(self):
        self.rollbacks += 1


def _ingest_settings():
    return {
        "auto_metadata_smart_application": False,
        "auto_metadata_update_title": False,
        "auto_metadata_update_authors": False,
        "auto_metadata_update_description": False,
        "auto_metadata_update_publisher": False,
        "auto_metadata_update_tags": False,
        "auto_metadata_update_series": False,
        "auto_metadata_update_published_date": False,
        "auto_metadata_update_rating": False,
        "auto_metadata_update_identifiers": False,
        "auto_metadata_update_cover": True,
    }


def _invoke_surface(
    surface,
    tmp_path,
    monkeypatch,
    *,
    publish_failure=False,
    compensation_failure=False,
    housekeeping_failure=False,
):
    """Exercise a real cover entry point with observable transaction events."""
    from cps import helper, kobo_sync_status

    book = _book()
    cover = _install_local_library(monkeypatch, helper, tmp_path, book)
    events = []
    session = _SequencedSession(events, compensation_failure)
    app = flask.Flask(__name__)
    app.secret_key = "cover-surface-matrix"
    Babel(app)
    app.add_url_rule(
        "/book/<int:book_id>", endpoint="web.show_book", view_func=lambda book_id: str(book_id)
    )
    app.add_url_rule(
        "/cover/<int:book_id>/<resolution>",
        endpoint="web.get_cover",
        view_func=lambda book_id, resolution: str(book_id),
    )
    real_replace = helper.os.replace

    def observed_replace(source, target):
        assert str(target) == str(cover)
        assert cover.read_bytes() == OLD_COVER
        events.append("publish")
        if publish_failure:
            raise OSError(errno.EIO, "simulated atomic publish failure")
        return real_replace(source, target)

    def mark_modified(target_book, *args, **kwargs):
        events.append("mark")
        target_book.last_modified = NEW_MODIFIED
        mark_modified.calls.append((args, kwargs))

    mark_modified.calls = []

    def housekeeping(name):
        events.append(name)
        if housekeeping_failure:
            raise RuntimeError("simulated {} failure".format(name))

    monkeypatch.setattr(helper.os, "replace", observed_replace)
    monkeypatch.setattr(kobo_sync_status, "remove_synced_book", lambda *a, **k: housekeeping("kobo"))

    if surface == "classic":
        from cps import editbooks

        monkeypatch.setattr(editbooks, "current_user", _editor())
        monkeypatch.setattr(editbooks.calibre_db, "get_filtered_book", lambda *a, **k: book)
        monkeypatch.setattr(editbooks.calibre_db, "session", session)
        monkeypatch.setattr(editbooks, "metadata_db_write_lock", nullcontext)
        monkeypatch.setattr(editbooks, "handle_author_on_edit", lambda *a, **k: (["Author"], False))
        for name in (
            "edit_book_ratings", "edit_book_series_index", "edit_book_comments",
            "edit_book_tags", "edit_book_series", "edit_book_publisher",
            "edit_book_languages", "edit_all_cc_data",
        ):
            monkeypatch.setattr(editbooks, name, lambda *a, **k: False)
        monkeypatch.setattr(editbooks, "identifier_list", lambda *a, **k: [])
        monkeypatch.setattr(editbooks, "modify_identifiers", lambda *a, **k: (False, False))
        monkeypatch.setattr(editbooks.helper, "mark_book_modified", mark_modified)
        monkeypatch.setattr(editbooks.helper, "replace_cover_thumbnail_cache",
                            lambda *a, **k: housekeeping("thumb"))
        monkeypatch.setattr(editbooks, "_queue_duplicate_scan_after_change", lambda *a, **k: None)
        monkeypatch.setattr(editbooks.constants, "CWA_METADATA_CHANGE_LOGS_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(editbooks.config, "config_kobo_sync", False, raising=False)
        monkeypatch.setattr(editbooks.config, "config_use_google_drive", False, raising=False)
        with app.test_request_context(
            "/admin/book/7",
            method="POST",
            data={
                "authors": "Author",
                "detail_view": "1",
                "btn-upload-cover": (_storage().stream, "cover.jpg"),
            },
            content_type="multipart/form-data",
        ):
            result = editbooks.do_edit_book(book.id)
        succeeded = result.status_code == 302 and not publish_failure

    elif surface == "picker":
        from cps import cover_picker

        monkeypatch.setattr(cover_picker, "current_user", _editor())
        monkeypatch.setattr(cover_picker, "_load_book", lambda _book_id: book)
        monkeypatch.setattr(cover_picker, "_get_lock_state", lambda _book_id: False)
        monkeypatch.setattr(cover_picker.calibre_db, "session", session)
        monkeypatch.setattr(cover_picker.helper, "mark_book_modified", mark_modified)
        monkeypatch.setattr(cover_picker.helper, "log_metadata_change",
                            lambda *a, **k: housekeeping("enforce"))
        monkeypatch.setattr(cover_picker.helper, "replace_cover_thumbnail_cache",
                            lambda *a, **k: housekeeping("thumb"))
        with app.test_request_context(
            "/book/7/cover/apply", method="POST",
            data={"file": (_storage().stream, "cover.jpg")},
            content_type="multipart/form-data",
        ):
            result = inspect.unwrap(cover_picker.cover_picker_apply)(book.id)
        succeeded = result.status_code == 200

    elif surface == "api":
        from cps.api import edit as api_edit

        monkeypatch.setattr(api_edit, "current_user", _editor())
        monkeypatch.setattr(api_edit.calibre_db, "get_filtered_book", lambda *a, **k: book)
        monkeypatch.setattr(api_edit.calibre_db, "session", session)
        monkeypatch.setattr(api_edit, "book_cover_is_locked", lambda _book_id: False)
        monkeypatch.setattr(api_edit, "mark_book_modified", mark_modified)
        monkeypatch.setattr(api_edit, "log_metadata_change",
                            lambda *a, **k: housekeeping("enforce"))
        monkeypatch.setattr(api_edit, "replace_cover_thumbnail_cache",
                            lambda *a, **k: housekeeping("thumb"))
        monkeypatch.setattr(api_edit, "cover_url_for", lambda *a, **k: "/cover/7/md")
        with app.test_request_context(
            "/api/v1/books/7/cover", method="POST",
            data={"file": (_storage().stream, "cover.jpg")},
            content_type="multipart/form-data",
        ):
            result = inspect.unwrap(api_edit.set_cover)(book.id)
        succeeded = getattr(result, "status_code", None) == 200

    elif surface == "ingest":
        from cps import metadata_helper

        monkeypatch.setattr(metadata_helper, "CWA_DB",
                            lambda: SimpleNamespace(get_cwa_settings=_ingest_settings))
        monkeypatch.setattr(helper, "book_cover_is_locked", lambda _book_id: False)
        monkeypatch.setattr(helper.cli_param, "allow_localhost", True)
        response = requests.Response()
        response.status_code = 200
        response.headers["content-type"] = "image/jpeg"
        response._content = _jpeg_bytes()
        response._content_consumed = True
        response.request = requests.Request("GET", "https://covers.example/7.jpg").prepare()
        monkeypatch.setattr(helper.requests, "get", lambda *a, **k: response)
        monkeypatch.setattr(helper, "mark_book_modified", mark_modified)
        monkeypatch.setattr(helper, "log_metadata_change",
                            lambda *a, **k: housekeeping("enforce"))
        monkeypatch.setattr(helper, "replace_cover_thumbnail_cache",
                            lambda *a, **k: housekeeping("thumb"))
        metadata = SimpleNamespace(title="", cover="https://covers.example/7.jpg")
        result = metadata_helper._apply_metadata_to_book(
            book, metadata, SimpleNamespace(session=session)
        )
        succeeded = result is True
    else:  # pragma: no cover - test helper guard
        raise AssertionError(surface)

    return SimpleNamespace(
        book=book,
        cover=cover,
        events=events,
        session=session,
        mark_calls=mark_modified.calls,
        succeeded=succeeded,
    )


@pytest.mark.parametrize("surface", ["classic", "picker", "api", "ingest"])
@pytest.mark.parametrize("compensation_failure", [False, True], ids=["compensated", "compensation-fails"])
def test_atomic_publish_failure_compensates_each_surface(
    surface, compensation_failure, tmp_path, monkeypatch
):
    result = _invoke_surface(
        surface, tmp_path, monkeypatch,
        publish_failure=True,
        compensation_failure=compensation_failure,
    )

    assert result.succeeded is False
    assert result.cover.read_bytes() == OLD_COVER
    assert result.book.has_cover == 0
    assert result.book.last_modified == OLD_MODIFIED
    assert result.session.commits == 2
    assert result.events.index("commit") < result.events.index("publish") < result.events.index("compensate")
    assert not {"kobo", "thumb", "enforce"}.intersection(result.events)
    assert not list(result.cover.parent.glob(".cover.jpg.cwng-*.stage"))
    assert result.session.rollbacks == (1 if compensation_failure else 0)


@pytest.mark.parametrize("surface", ["classic", "picker", "ingest"])
def test_success_order_commit_publish_then_housekeeping(surface, tmp_path, monkeypatch):
    result = _invoke_surface(surface, tmp_path, monkeypatch)

    assert result.succeeded is True
    assert result.events.index("commit") < result.events.index("publish")
    for event in ({"kobo", "thumb"} if surface != "ingest" else {"enforce", "thumb"}):
        assert result.events.index("publish") < result.events.index(event)
    assert result.cover.read_bytes() == _jpeg_bytes()
    assert not list(result.cover.parent.glob(".cover.jpg.cwng-*.stage"))
    if surface == "classic":
        assert result.mark_calls == [((), {})]
        assert result.events.index("publish") < result.events.index("kobo")


@pytest.mark.parametrize("surface", ["classic", "picker", "api", "ingest"])
def test_housekeeping_failure_after_publish_does_not_reverse_success(
    surface, tmp_path, monkeypatch
):
    result = _invoke_surface(surface, tmp_path, monkeypatch, housekeeping_failure=True)

    assert result.succeeded is True
    assert result.cover.read_bytes() == _jpeg_bytes()
    assert result.session.commits == 1
    assert result.events.index("commit") < result.events.index("publish")
    assert "thumb" in result.events
