# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user cover preferences stay isolated from global metadata and files."""
from datetime import datetime, timezone
import io
import inspect
import json
import os
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import zipfile
import zlib

from flask import Flask
from PIL import Image
import pytest
from sqlalchemy import create_engine, inspect as sa_inspect
from werkzeug.datastructures import FileStorage

from cps import ub
from cps.api.serializers import cover_url_for, serialize_book_detail
from cps.services import user_cover


def _jpeg(color):
    stream = io.BytesIO()
    Image.new("RGB", (12, 18), color).save(stream, "JPEG")
    return stream.getvalue()


def _png_header(width, height):
    """A tiny PNG container whose IHDR declares the requested dimensions."""
    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xffffffff,
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _book(book_id=11):
    return SimpleNamespace(
        id=book_id, title="Public test book", has_cover=1,
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        series=[], series_index=1, authors=[], data=[], tags=[], comments=[],
        languages=[], publishers=[], identifiers=[], rating=[], pubdate=None,
    )


@pytest.mark.unit
def test_migration_creates_composite_user_book_primary_key(tmp_path):
    engine = create_engine("sqlite:///{}".format(tmp_path / "app.db"))
    ub.User.__table__.create(engine)

    ub.migrate_user_book_cover_table(engine, None)
    ub.migrate_user_book_cover_table(engine, None)

    inspector = sa_inspect(engine)
    assert inspector.has_table("user_book_cover")
    assert inspector.get_pk_constraint("user_book_cover")["constrained_columns"] == [
        "user_id", "book_id",
    ]


@pytest.mark.unit
def test_serializer_selects_only_the_supplied_users_override(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    own = ub.UserBookCover(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    other = ub.UserBookCover(
        user_id=8, book_id=11,
        updated_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
    )
    book = _book()

    assert cover_url_for(book, "md", own).startswith(
        "/api/v1/books/11/my-cover/image?c=")
    assert cover_url_for(book, "md", other).startswith(
        "/api/v1/books/11/my-cover/image?c=")
    assert cover_url_for(book, "md") == "/cover/11/md?c=1767225600000000"

    detail = serialize_book_detail(book, cover_override=own)
    assert detail["using_my_cover"] is True
    assert detail["cover_url"].startswith("/api/v1/books/11/my-cover/image")
    assert detail["library_cover_url"] == "/cover/11/md?c=1767225600000000"
    assert detail["cover_srcset"] is None


@pytest.mark.unit
def test_upload_is_normalized_and_not_visible_until_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    raw = io.BytesIO()
    Image.new("RGBA", (10, 14), (20, 40, 60, 120)).save(raw, "PNG")
    raw.seek(0)

    updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    staged, error = user_cover.stage_upload(
        7, 11, updated_at,
        FileStorage(raw, filename="cover.png", content_type="image/png"),
    )

    target = user_cover.cover_path(7, 11, user_cover.version_token(updated_at))
    assert error is None
    assert staged is not None
    assert not os.path.exists(target)
    assert staged.publish() == (True, None)
    with Image.open(target) as saved:
        assert saved.format == "JPEG"
        assert saved.mode == "RGB"


@pytest.mark.unit
def test_encoded_limit_cannot_hide_oversized_decoded_dimensions(tmp_path, monkeypatch):
    from cps import helper

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    bomb = _png_header(9_000, 9_000)
    assert len(bomb) < 100
    updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

    staged, error = user_cover.stage_upload(
        7, 11, updated_at,
        FileStorage(io.BytesIO(bomb), filename="small.png", content_type="image/png"),
    )
    assert staged is None
    assert "usable cover image" in str(error)

    staged, error = helper.save_cover_from_filestorage(
        str(tmp_path / "global"), "cover.jpg",
        FileStorage(io.BytesIO(bomb), filename="small.png", content_type="image/png"),
    )
    assert staged is None
    assert "valid image" in str(error)


@pytest.mark.unit
def test_kobo_image_template_version_changes_without_touching_book_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    first = ub.UserBookCover(
        user_id=7,
        book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    second = ub.UserBookCover(
        user_id=7,
        book_id=12,
        updated_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
    )
    for row in (first, second):
        os.makedirs(user_cover.cover_directory(row.user_id), exist_ok=True)
        with open(user_cover.path_for_row(row), "wb") as cover_file:
            cover_file.write(_jpeg("red"))

    query = MagicMock()
    ordered = query.filter.return_value.order_by.return_value
    ordered.all.return_value = [first, second]
    session = MagicMock()
    session.query.return_value = query

    before = user_cover.kobo_resource_version_for_user(7, session=session)
    first.updated_at = datetime(2026, 2, 3, tzinfo=timezone.utc)
    with open(user_cover.path_for_row(first), "wb") as cover_file:
        cover_file.write(_jpeg("blue"))
    after_set = user_cover.kobo_resource_version_for_user(7, session=session)
    ordered.all.return_value = [second]
    after_clear = user_cover.kobo_resource_version_for_user(7, session=session)
    ordered.all.return_value = []

    assert before and after_set and after_clear
    assert len({before, after_set, after_clear}) == 3
    assert user_cover.kobo_resource_version_for_user(7, session=session) is None


@pytest.mark.unit
def test_startup_scavenger_removes_only_interrupted_personal_stages(tmp_path, monkeypatch):
    from cps import helper

    config_dir = tmp_path / "config"
    library_dir = tmp_path / "library"
    temp_dir = tmp_path / "temp"
    library_dir.mkdir()
    temp_dir.mkdir()
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library_dir))
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(temp_dir))

    updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    staged, error = user_cover.stage_upload(
        7, 11, updated_at,
        FileStorage(
            io.BytesIO(_jpeg("red")),
            filename="cover.jpg",
            content_type="image/jpeg",
        ),
    )
    assert staged is not None and error is None
    unrelated = config_dir / "user-covers" / "7" / ".notes.cwng-test.stage"
    unrelated.write_bytes(b"keep")

    assert helper.scavenge_staged_cover_files() == 1
    assert not os.path.exists(staged.staged_path)
    assert unrelated.read_bytes() == b"keep"
    assert not os.path.exists(
        user_cover.cover_path(7, 11, user_cover.version_token(updated_at)))


@pytest.mark.unit
def test_set_endpoint_writes_only_current_users_row(tmp_path, monkeypatch):
    from cps.api import actions

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    app = Flask(__name__)
    upload = (io.BytesIO(_jpeg("red")), "mine.jpg")
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    session = MagicMock()
    staged = MagicMock()
    events = []
    staged.publish.side_effect = lambda: (events.append("publish") or (True, None))
    session.commit.side_effect = lambda: events.append("commit")
    book = _book()

    with app.test_request_context(
        "/api/v1/books/11/my-cover", method="PUT",
        data={"file": upload}, content_type="multipart/form-data",
    ), patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "stage_upload", return_value=(staged, None)), \
            patch.object(actions.user_cover, "row_for_user", return_value=None) as lookup, \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions, "remove_synced_book") as remove_synced:
        response = inspect.unwrap(actions.set_my_book_cover)(11)

    body = json.loads(response.get_data())
    assert body["ok"] is True
    assert body["using_my_cover"] is True
    lookup.assert_called_once_with(7, 11)
    row = session.add.call_args.args[0]
    assert (row.user_id, row.book_id) == (7, 11)
    session.commit.assert_called_once()
    staged.publish.assert_called_once()
    assert events == ["publish", "commit"]
    remove_synced.assert_not_called()


@pytest.mark.unit
def test_publish_failure_never_commits_a_preference_row(monkeypatch):
    from cps.api import actions

    app = Flask(__name__)
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    session = MagicMock()
    staged = MagicMock()
    staged.publish.return_value = (False, "publish failed")

    with app.test_request_context(
        "/api/v1/books/11/my-cover", method="PUT",
        data={"file": (io.BytesIO(_jpeg("red")), "mine.jpg")},
        content_type="multipart/form-data",
    ), patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=_book()), \
            patch.object(actions.user_cover, "stage_upload", return_value=(staged, None)), \
            patch.object(actions.user_cover, "row_for_user", return_value=None), \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"):
        response, status = inspect.unwrap(actions.set_my_book_cover)(11)

    assert status == 500
    assert json.loads(response.get_data())["error"]["code"] == "save_failed"
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.unit
def test_db_failure_after_publish_keeps_old_row_and_bytes(tmp_path, monkeypatch):
    from cps.api import actions

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    old_updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    old_row = ub.UserBookCover(
        user_id=7, book_id=11, updated_at=old_updated_at,
    )
    os.makedirs(user_cover.cover_directory(7), exist_ok=True)
    old_path = user_cover.path_for_row(old_row)
    with open(old_path, "wb") as cover_file:
        cover_file.write(_jpeg("blue"))

    app = Flask(__name__)
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    session = MagicMock()
    session.commit.side_effect = RuntimeError("database unavailable")

    with app.test_request_context(
        "/api/v1/books/11/my-cover", method="PUT",
        data={"file": (io.BytesIO(_jpeg("red")), "mine.jpg")},
        content_type="multipart/form-data",
    ), patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=_book()), \
            patch.object(actions.user_cover, "row_for_user", return_value=old_row), \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"):
        response, status = inspect.unwrap(actions.set_my_book_cover)(11)

    assert status == 500
    assert json.loads(response.get_data())["error"]["code"] == "save_failed"
    assert old_row.updated_at == old_updated_at
    assert os.path.isfile(old_path)
    assert open(old_path, "rb").read() == _jpeg("blue")
    assert sorted(os.listdir(user_cover.cover_directory(7))) == [os.path.basename(old_path)]
    session.commit.assert_called_once()
    session.rollback.assert_called_once()


@pytest.mark.unit
def test_get_and_clear_endpoints_never_address_another_users_cover():
    from cps.api import actions

    app = Flask(__name__)
    viewer = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        role_browse_global=lambda: True,
    )
    row = ub.UserBookCover(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    session = MagicMock()
    book = _book()

    with app.test_request_context("/api/v1/books/11/my-cover"), \
            patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "override_for_user", return_value=row) as get_lookup, \
            patch.object(actions.user_library, "mark_response_user_specific"):
        response = inspect.unwrap(actions.get_my_book_cover)(11)
    assert json.loads(response.get_data())["using_my_cover"] is True
    get_lookup.assert_called_once_with(7, 11)

    with app.test_request_context("/api/v1/books/11/my-cover", method="DELETE"), \
            patch.object(actions, "current_user", viewer), \
            patch.object(actions, "_personal_cover_book", return_value=book), \
            patch.object(actions.user_cover, "row_for_user", return_value=row) as clear_lookup, \
            patch.object(actions.user_cover, "remove_file") as remove_file, \
            patch.object(actions.ub, "session", session), \
            patch.object(actions.user_library, "mark_response_user_specific"), \
            patch.object(actions, "remove_synced_book") as remove_synced:
        response = inspect.unwrap(actions.clear_my_book_cover)(11)
    assert json.loads(response.get_data())["using_my_cover"] is False
    clear_lookup.assert_called_once_with(7, 11)
    session.delete.assert_called_once_with(row)
    remove_file.assert_called_once_with(7, 11, row=row)
    remove_synced.assert_not_called()


@pytest.mark.unit
def test_personal_cover_images_are_account_scoped_and_private(tmp_path, monkeypatch):
    """Even a colliding book id cannot cross a browser/account cache boundary."""
    from cps.api import actions

    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path))
    rows = {
        7: ub.UserBookCover(
            user_id=7,
            book_id=11,
            updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        8: ub.UserBookCover(
            user_id=8,
            book_id=11,
            updated_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
        ),
    }
    expected = {7: _jpeg("red"), 8: _jpeg("blue")}
    for user_id, row in rows.items():
        os.makedirs(user_cover.cover_directory(user_id), exist_ok=True)
        with open(user_cover.path_for_row(row), "wb") as cover_file:
            cover_file.write(expected[user_id])

    app = Flask(__name__)
    lookups = []
    received = {}
    headers = {}
    for user_id, row in rows.items():
        viewer = SimpleNamespace(
            id=user_id,
            is_authenticated=True,
            is_anonymous=False,
            role_browse_global=lambda: True,
        )
        with app.test_request_context(
            "/api/v1/books/11/my-cover/image?c={}".format(
                user_cover.version_token(row),
            )
        ), patch.object(actions, "current_user", viewer), \
                patch.object(actions, "_personal_cover_book", return_value=_book()), \
                patch.object(
                    actions.user_cover,
                    "override_for_user",
                    side_effect=lambda uid, bid: (
                        lookups.append((uid, bid)) or rows[uid]
                    ),
                ), patch.object(actions.user_library, "mark_response_user_specific"):
            response = inspect.unwrap(actions.get_my_book_cover_image)(11)
            response.direct_passthrough = False
            received[user_id] = response.get_data()
            headers[user_id] = dict(response.headers)

    assert received == expected
    assert lookups == [(7, 11), (8, 11)]
    for user_id in rows:
        assert "private" in headers[user_id]["Cache-Control"]
        assert "immutable" in headers[user_id]["Cache-Control"]
        assert "Cookie" in headers[user_id]["Vary"]


@pytest.mark.unit
def test_send_mail_propagates_the_requesting_user_to_direct_and_converted_deliveries(monkeypatch):
    from cps import helper

    viewer = SimpleNamespace(id=7, is_anonymous=False)
    book = SimpleNamespace(
        id=11,
        title="Public test book",
        path="Author/Book",
        data=[SimpleNamespace(format="EPUB", name="book")],
    )
    queued = []
    monkeypatch.setattr(helper.calibre_db, "get_filtered_book", lambda *_a, **_k: book)
    monkeypatch.setattr(helper.WorkerThread, "add", lambda user_id, task: queued.append((user_id, task)))
    monkeypatch.setattr(helper.config, "get_mail_settings", lambda: {})
    monkeypatch.setattr(helper, "get_email_body_text", lambda: "body")

    app = Flask(__name__)
    with app.test_request_context("/api/v1/books/11/send"):
        assert helper.send_mail(
            11, "EPUB", 0, "reader@example.invalid", "/library", 7,
            subject="Send", user=viewer,
        ) is None
    assert len(queued) == 1
    assert queued[0][1].cover_user_id == 7

    with app.test_request_context("/api/v1/books/11/send"), \
            patch.object(helper, "convert_book_format", return_value=None) as convert:
        assert helper.send_mail(
            11, "EPUB", 1, "reader@example.invalid", "/library", 7,
            subject="Send", user=viewer,
        ) is None
    assert convert.call_args.kwargs["cover_user_id"] == 7


@pytest.mark.unit
def test_personal_source_routes_do_not_grant_global_cover_write():
    from cps import cover_picker

    app = Flask(__name__)
    viewer = SimpleNamespace(role_edit=lambda: False, role_admin=lambda: False)
    called = MagicMock(return_value="ok")
    protected = cover_picker.cover_source_required(called)

    with app.test_request_context("/book/11/cover/candidates?scope=personal"), \
            patch.object(cover_picker, "current_user", viewer):
        assert protected() == "ok"

    with app.test_request_context("/book/11/cover/candidates"), \
            patch.object(cover_picker, "current_user", viewer), \
            pytest.raises(Exception) as error:
        protected()
    assert getattr(error.value, "code", None) == 403


@pytest.mark.unit
def test_personal_cover_image_does_not_bypass_membership_or_global_browse(monkeypatch):
    from cps.api import actions

    viewer = SimpleNamespace(
        id=7,
        is_authenticated=True,
        is_anonymous=False,
        role_browse_global=lambda: False,
    )
    lookups = []
    monkeypatch.setattr(
        actions.calibre_db,
        "get_filtered_book",
        lambda book_id, **options: lookups.append((book_id, options)),
    )
    override = MagicMock()
    monkeypatch.setattr(actions.user_cover, "override_for_user", override)
    monkeypatch.setattr(actions.user_library, "mark_response_user_specific", lambda: None)
    app = Flask(__name__)

    with app.test_request_context("/api/v1/books/11/my-cover/image"), \
            patch.object(actions, "current_user", viewer):
        response, status = inspect.unwrap(actions.get_my_book_cover_image)(11)

    assert status == 404
    assert json.loads(response.get_data())["error"]["code"] == "not_found"
    assert lookups == [(11, {
        "allow_show_archived": True,
        "allow_show_hidden": True,
        "allow_show_global": False,
    })]
    override.assert_not_called()


def _epub(path, cover_bytes):
    container = b'''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
    </container>'''
    package = b'''<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
      <metadata><meta name="cover" content="cover-image"/></metadata>
      <manifest>
        <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg"/>
        <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
      </manifest><spine><itemref idref="chapter"/></spine>
    </package>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/images/cover.jpg", cover_bytes)
        archive.writestr("OEBPS/chapter.xhtml", b"<p>unchanged</p>")


@pytest.mark.unit
def test_epub_delivery_copy_embeds_only_requesting_users_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(tmp_path / "config"))
    source = tmp_path / "library.epub"
    global_cover = _jpeg("blue")
    personal_cover = _jpeg("red")
    _epub(source, global_cover)
    row = SimpleNamespace(
        user_id=7, book_id=11,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    os.makedirs(user_cover.cover_directory(7), exist_ok=True)
    with open(user_cover.path_for_row(row), "wb") as cover_file:
        cover_file.write(personal_cover)

    monkeypatch.setattr(
        user_cover, "override_for_user",
        lambda user_id, book_id: row if (user_id, book_id) == (7, 11) else None,
    )
    from cps import helper
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path / "deliveries"))

    delivered = user_cover.materialize_delivery_copy(7, 11, str(source), "epub")
    assert delivered is not None
    delivered_path = os.path.join(delivered[0], delivered[1] + ".epub")
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(delivered_path) as private:
        assert original.read("OEBPS/images/cover.jpg") == global_cover
        assert private.read("OEBPS/images/cover.jpg") != global_cover
        assert private.read("OEBPS/chapter.xhtml") == original.read("OEBPS/chapter.xhtml")
        with Image.open(io.BytesIO(private.read("OEBPS/images/cover.jpg"))) as image:
            red, _green, blue = image.resize((1, 1)).getpixel((0, 0))
            assert red > blue

    assert user_cover.materialize_delivery_copy(8, 11, str(source), "epub") is None


@pytest.mark.unit
def test_kobo_cover_image_id_is_independent_of_personal_cover(monkeypatch):
    from cps import kobo

    book = SimpleNamespace(
        id=11, uuid="12345678-1234-1234-1234-123456789abc",
        path="Author/Book", last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(kobo.config, "config_use_google_drive", True, raising=False)
    monkeypatch.setattr(
        kobo, "_current_padding_settings",
        lambda: SimpleNamespace(enabled=False),
    )

    image_id = kobo._get_cover_image_id(book)
    assert image_id == str(book.uuid) + "-1767225600"
    assert kobo.normalize_cover_uuid(image_id) == str(book.uuid)
