from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

from flask import Flask, Response
from PIL import Image
from tests.unit.test_1925_kobo_sync_dedownload import _entitlements, sync_harness
from tests.unit.test_user_cover_overrides import _epub, _jpeg
from werkzeug.datastructures import Headers


def test_personal_cover_set_and_clear_emit_zero_entitlements_for_held_book(
    sync_harness, monkeypatch,
):
    from cps import kobo
    from cps.services import user_cover

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    state = {"row": None}
    monkeypatch.setattr(
        user_cover,
        "override_for_user",
        lambda user_id, book_id: state["row"],
    )

    initial = sync_harness.sync()
    assert len(_entitlements(initial)) == 1

    state["row"] = SimpleNamespace(
        user_id=sync_harness.user.id,
        book_id=sync_harness.book.id,
        updated_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    # Personal image preference is not entitlement state: no ledger or sync
    # cursor is invalidated when the row changes.
    after_set = sync_harness.sync(initial.headers[sync_harness.token_header])
    set_entitlements = _entitlements(after_set)

    state["row"] = None
    after_clear = sync_harness.sync(after_set.headers[sync_harness.token_header])
    clear_entitlements = _entitlements(after_clear)
    assert (set_entitlements, clear_entitlements) == ([], [])


def test_kobo_cover_handler_returns_personal_bytes_when_override_exists(monkeypatch):
    from cps import kobo
    from cps.services import user_cover

    app = Flask(__name__)
    viewer = SimpleNamespace(id=17)
    override = SimpleNamespace(
        user_id=17, book_id=1,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    personal_bytes = b"personal-cover"
    global_bytes = b"global-cover"
    padded_calls = []
    marked_private = []
    monkeypatch.setattr(kobo, "current_user", viewer)
    monkeypatch.setattr(
        user_cover, "override_for_user", lambda user_id, book_id: override,
    )
    monkeypatch.setattr(
        kobo, "_serve_padded_cover_if_enabled",
        lambda *_args, **kwargs: padded_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        kobo.calibre_db,
        "get_book_by_uuid_for_kobo",
        lambda *_args, **_kwargs: SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        user_cover,
        "send_override",
        lambda _row: Response(personal_bytes, mimetype="image/jpeg"),
    )
    monkeypatch.setattr(
        kobo.helper,
        "get_book_cover_with_uuid",
        lambda *_args, **_kwargs: Response(global_bytes, mimetype="image/jpeg"),
    )
    monkeypatch.setattr(
        kobo.user_library,
        "mark_response_user_specific",
        lambda: marked_private.append(True),
    )

    with app.test_request_context(
        "/kobo/token/book-id/300/400/false/image.jpg"
    ):
        response = inspect.unwrap(kobo.HandleCoverImageRequest)(
            "book-id", "300", "400", "", "false",
        )

    assert response.get_data() == personal_bytes
    assert marked_private == [True]
    assert padded_calls[0]["private"] is True
    assert padded_calls[0]["cache_identity"].endswith(
        "-user-17-1769904000000000"
    )


def test_kobo_init_refreshes_only_the_image_url_for_personal_cover_changes(monkeypatch):
    from cps import kobo

    app = Flask(__name__)
    app.register_blueprint(kobo.kobo)
    original_wsgi = app.wsgi_app

    class UnproxiedWsgi:
        is_proxied = False

        def __call__(self, environ, start_response):
            return original_wsgi(environ, start_response)

    app.wsgi_app = UnproxiedWsgi()
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_external_port", 80, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(
        kobo.config,
        "config_hardcover_annotations_sync",
        False,
        raising=False,
    )
    monkeypatch.setattr(kobo, "current_user", SimpleNamespace(id=17))
    monkeypatch.setattr(kobo.kobo_auth, "get_auth_token", lambda: "token")

    templates = []
    for version in ("first-version", "second-version", None):
        monkeypatch.setattr(
            kobo.user_cover,
            "kobo_resource_version_for_user",
            lambda _user_id, value=version: value,
        )
        with app.test_request_context("/kobo/token/v1/initialization"):
            response = inspect.unwrap(kobo.HandleInitRequest)()
            templates.append(
                response.get_json()["Resources"]["image_url_template"]
            )

    assert "pc=first-version" in templates[0]
    assert "pc=second-version" in templates[1]
    assert "pc=" not in templates[2]
    assert len(set(templates)) == 3
    assert all("{ImageId}" in template for template in templates)


def test_direct_epub_download_embeds_current_users_personal_cover(tmp_path, monkeypatch):
    import io
    import os
    import zipfile

    from cps import helper
    from cps.services import user_cover

    library = tmp_path / "library"
    book_dir = library / "Author" / "Book"
    book_dir.mkdir(parents=True)
    source = book_dir / "stable.epub"
    global_cover = _jpeg("blue")
    personal_cover = _jpeg("red")
    _epub(source, global_cover)

    config_dir = tmp_path / "config"
    monkeypatch.setattr(user_cover.constants, "CONFIG_DIR", str(config_dir))
    override = SimpleNamespace(
        user_id=17, book_id=1,
        updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    os.makedirs(user_cover.cover_directory(17), exist_ok=True)
    with open(user_cover.path_for_row(override), "wb") as cover_file:
        cover_file.write(personal_cover)
    monkeypatch.setattr(
        user_cover,
        "override_for_user",
        lambda user_id, book_id: override,
    )
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(library))
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(helper.config, "config_binariesdir", "", raising=False)

    app = Flask(__name__)
    book = SimpleNamespace(id=1, path="Author/Book")
    data = SimpleNamespace(name="stable")
    headers = Headers({"Content-Disposition": "attachment; filename=stable.epub"})
    with app.test_request_context("/download/1/epub"):
        response = helper.do_download_file(
            book, "epub", "web", data, headers, cover_user_id=17,
        )
        response.direct_passthrough = False
        delivered = response.get_data()

    with zipfile.ZipFile(io.BytesIO(delivered)) as archive:
        embedded = archive.read("OEBPS/images/cover.jpg")
    with Image.open(io.BytesIO(embedded)) as delivered_cover:
        red, _green, blue = delivered_cover.resize((1, 1)).getpixel((0, 0))
    assert embedded != global_cover and red > blue
