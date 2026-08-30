# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-library regression coverage for last-format deletion through both UIs."""

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import flask
import pytest
from flask_babel import Babel
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cps import constants, db, ub


pytestmark = pytest.mark.unit


class _TestCwaDB:
    cwa_settings = {"duplicate_detection_enabled": 1}

    def invalidate_duplicate_cache(self):
        return True


@pytest.fixture
def format_delete_server(tmp_path, monkeypatch):
    """Serve both real deletion views over one temporary Calibre library."""
    from cps import editbooks, helper
    from cps.api import edit as api_edit
    from cps import cwa_db_loader

    library = tmp_path / "library"
    library.mkdir()
    metadata_db = library / "metadata.db"

    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _attach_calibre(connection, _record):
        connection.execute("ATTACH DATABASE ? AS calibre", (str(metadata_db),))

    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    book = db.Books(
        "Last Format",
        "Last Format",
        "Author",
        now,
        db.Books.DEFAULT_PUBDATE,
        "1.0",
        now,
        "Author/Last Format (1)",
        0,
        [],
        [],
    )
    book.uuid = "last-format-test-book"
    user = ub.User(
        name="format-editor",
        email="format-editor@example.invalid",
        password="",
        role=(
            constants.ROLE_USER
            | constants.ROLE_EDIT
            | constants.ROLE_DELETE_BOOKS
            | constants.ROLE_BROWSE_GLOBAL
        ),
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add_all([book, user])
    session.flush()

    data_name = "Last Format - Author"
    format_file = library / book.path / f"{data_name}.epub"
    format_file.parent.mkdir(parents=True)
    format_file.write_bytes(b"the only copy of this book")

    shelf = ub.Shelf(name="Keep me", user_id=user.id, is_public=0)
    session.add_all(
        [
            db.Data(book.id, "EPUB", format_file.stat().st_size, data_name),
            shelf,
            ub.ReadBook(
                book_id=book.id,
                user_id=user.id,
                read_status=ub.ReadBook.STATUS_IN_PROGRESS,
            ),
            ub.UserLibraryBook(user_id=user.id, book_id=book.id),
        ]
    )
    session.flush()
    shelf_link = ub.BookShelf(book_id=book.id, shelf=shelf.id, order=1)
    shelf_link.ub_shelf = shelf
    session.add(shelf_link)
    session.commit()

    calibre = object.__new__(db.CalibreDB)
    calibre.session = session
    calibre.config = SimpleNamespace(config_restricted_column=0)

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(db, "current_user", user)
    monkeypatch.setattr(editbooks, "current_user", user)
    monkeypatch.setattr(api_edit, "current_user", user)
    monkeypatch.setattr(editbooks, "calibre_db", calibre)
    monkeypatch.setattr(api_edit, "calibre_db", calibre)
    monkeypatch.setattr(helper, "calibre_db", calibre)
    monkeypatch.setattr(editbooks.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(
        cwa_db_loader,
        "load_cwa_db",
        lambda: SimpleNamespace(CWA_DB=_TestCwaDB),
    )

    app = flask.Flask(__name__)
    app.secret_key = "format-delete-integration"
    app.testing = True
    Babel(app)
    app.add_url_rule(
        "/admin/book/<int:book_id>",
        endpoint="edit-book.show_edit_book",
        view_func=lambda book_id: str(book_id),
    )
    # Authentication plumbing is intentionally unwrapped; the views' real role
    # checks, shared visibility lookup, storage operation, and SQL are retained.
    app.add_url_rule(
        "/delete/<int:book_id>/<string:book_format>",
        endpoint="classic-delete-format",
        view_func=inspect.unwrap(editbooks.delete_book_ajax),
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/v1/books/<int:book_id>/formats/<fmt>/delete",
        endpoint="api-delete-format",
        view_func=inspect.unwrap(api_edit.delete_format),
        methods=["POST"],
    )

    def request(surface):
        client = app.test_client()
        if surface == "classic":
            return client.post(
                f"/delete/{book.id}/EPUB", data={"location": f"/book/{book.id}"}
            )
        return client.post(f"/api/v1/books/{book.id}/formats/epub/delete")

    yield SimpleNamespace(
        app=app,
        book_id=book.id,
        data_name=data_name,
        engine=engine,
        format_file=format_file,
        helper=helper,
        request=request,
        session=session,
        shelf_id=shelf.id,
        user=user,
    )

    session.close()
    engine.dispose()


def _assert_book_and_user_state_survive(server):
    server.session.expire_all()
    book = server.session.get(db.Books, server.book_id)
    assert book is not None
    assert book.title == "Last Format"
    assert (
        server.session.query(ub.BookShelf)
        .filter_by(book_id=server.book_id, shelf=server.shelf_id)
        .count()
        == 1
    )
    read = (
        server.session.query(ub.ReadBook)
        .filter_by(book_id=server.book_id, user_id=server.user.id)
        .one()
    )
    assert read.read_status == ub.ReadBook.STATUS_IN_PROGRESS


@pytest.mark.parametrize("surface", ["classic", "api"])
def test_last_format_success_keeps_book_shelf_and_read_state(
    format_delete_server, surface
):
    server = format_delete_server

    response = server.request(surface)

    assert response.status_code == (302 if surface == "classic" else 204)
    assert not server.format_file.exists()
    assert (
        server.session.query(db.Data)
        .filter_by(book=server.book_id, format="EPUB")
        .count()
        == 0
    )
    assert not list(server.format_file.parent.glob("*.quarantine"))
    _assert_book_and_user_state_survive(server)


@pytest.mark.parametrize("surface", ["classic", "api"])
def test_format_stage_failure_keeps_row_and_bytes(
    format_delete_server, surface, monkeypatch
):
    server = format_delete_server

    def fail_rename(_source, _target):
        raise OSError("simulated quarantine rename failure")

    monkeypatch.setattr(server.helper.os, "replace", fail_rename)
    response = server.request(surface)

    assert response.status_code == (302 if surface == "classic" else 500)
    assert server.format_file.read_bytes() == b"the only copy of this book"
    assert (
        server.session.query(db.Data)
        .filter_by(book=server.book_id, format="EPUB")
        .count()
        == 1
    )
    _assert_book_and_user_state_survive(server)


@pytest.mark.parametrize("surface", ["classic", "api"])
def test_format_commit_failure_restores_physical_file(
    format_delete_server, surface, monkeypatch
):
    server = format_delete_server

    def fail_commit():
        raise RuntimeError("simulated metadata commit failure")

    monkeypatch.setattr(server.session, "commit", fail_commit)
    response = server.request(surface)

    assert response.status_code == (302 if surface == "classic" else 500)
    assert server.format_file.read_bytes() == b"the only copy of this book"
    assert (
        server.session.query(db.Data)
        .filter_by(book=server.book_id, format="EPUB")
        .count()
        == 1
    )
    assert not list(server.format_file.parent.glob("*.quarantine"))
    _assert_book_and_user_state_survive(server)


@pytest.mark.parametrize("surface", ["classic", "api"])
def test_invisible_format_target_is_404_on_both_surfaces(format_delete_server, surface):
    server = format_delete_server
    server.session.query(ub.UserLibraryBook).filter_by(
        user_id=server.user.id, book_id=server.book_id
    ).delete()
    server.session.commit()

    response = server.request(surface)

    assert response.status_code == 404
    assert server.format_file.exists()
    assert (
        server.session.query(db.Data)
        .filter_by(book=server.book_id, format="EPUB")
        .count()
        == 1
    )
    _assert_book_and_user_state_survive(server)


@pytest.mark.parametrize("surface", ["classic", "api"])
def test_delete_role_gate_blocks_both_surfaces(format_delete_server, surface):
    server = format_delete_server
    server.user.role &= ~constants.ROLE_DELETE_BOOKS

    response = server.request(surface)

    assert response.status_code == (302 if surface == "classic" else 403)
    assert server.format_file.exists()
    assert (
        server.session.query(db.Data)
        .filter_by(book=server.book_id, format="EPUB")
        .count()
        == 1
    )
    _assert_book_and_user_state_survive(server)


def test_google_drive_stage_restores_remote_name_on_rollback(monkeypatch):
    from cps import helper

    moves = []
    cache_updates = []
    remote = {"id": "remote-1", "title": "Book.epub"}

    def move(g_file, title):
        moves.append(title)
        g_file["title"] = title

    monkeypatch.setattr(helper.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(
        helper.gd, "getFileFromEbooksFolder", lambda *_args, **_kwargs: remote
    )
    monkeypatch.setattr(helper.gd, "moveGdriveFileRemote", move)
    monkeypatch.setattr(
        helper.gd,
        "updateDatabaseOnEdit",
        lambda file_id, title: cache_updates.append((file_id, title)),
    )
    book = SimpleNamespace(
        id=1,
        path="Author/Book (1)",
        data=[SimpleNamespace(name="Book", format="EPUB")],
    )

    staged, error = helper.stage_book_format_delete(book, "/unused", "EPUB")
    restored, restore_error = staged.restore()

    assert error is None
    assert restored is True and restore_error is None
    assert moves[-1] == "Book.epub"
    assert cache_updates[-1] == ("remote-1", "Book.epub")


def test_google_drive_finalize_trashes_only_after_staging(monkeypatch):
    from cps import helper

    events = []

    class RemoteFile(dict):
        def Trash(self):
            events.append("trash")

    remote = RemoteFile(id="remote-1", title="Book.epub")

    def move(g_file, title):
        events.append(("rename", title))
        g_file["title"] = title

    monkeypatch.setattr(helper.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(
        helper.gd, "getFileFromEbooksFolder", lambda *_args, **_kwargs: remote
    )
    monkeypatch.setattr(helper.gd, "moveGdriveFileRemote", move)
    monkeypatch.setattr(helper.gd, "updateDatabaseOnEdit", lambda *_args: None)
    monkeypatch.setattr(
        helper.gd,
        "deleteDatabaseEntry",
        lambda file_id: events.append(("delete-cache", file_id)),
    )
    book = SimpleNamespace(
        id=1,
        path="Author/Book (1)",
        data=[SimpleNamespace(name="Book", format="EPUB")],
    )

    staged, error = helper.stage_book_format_delete(book, "/unused", "EPUB")
    finalized, finalize_error = staged.finalize()

    assert error is None
    assert finalized is True and finalize_error is None
    assert events[0][0] == "rename"
    assert events[1:] == ["trash", ("delete-cache", "remote-1")]


def test_google_drive_stage_failure_compensates_successful_remote_rename(monkeypatch):
    from cps import helper

    moves = []
    remote = {"id": "remote-1", "title": "Book.epub"}

    def move(g_file, title):
        moves.append(title)
        g_file["title"] = title

    def update(_file_id, title):
        if title != "Book.epub":
            raise RuntimeError("simulated cache update failure")

    monkeypatch.setattr(helper.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(
        helper.gd, "getFileFromEbooksFolder", lambda *_args, **_kwargs: remote
    )
    monkeypatch.setattr(helper.gd, "moveGdriveFileRemote", move)
    monkeypatch.setattr(helper.gd, "updateDatabaseOnEdit", update)
    book = SimpleNamespace(
        id=1,
        path="Author/Book (1)",
        data=[SimpleNamespace(name="Book", format="EPUB")],
    )

    staged, error = helper.stage_book_format_delete(book, "/unused", "EPUB")

    assert staged is None
    assert "cache update failure" in error
    assert moves[-1] == "Book.epub"
    assert remote["title"] == "Book.epub"
