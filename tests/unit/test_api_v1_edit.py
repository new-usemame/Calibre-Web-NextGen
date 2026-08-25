# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 edit-metadata — role gating, result parsing, and the
per-field dispatch to the shared edit_book_param core (mocked)."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _editor(role_edit=True, anon=False, role_delete=True):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon, name="ed",
                           role_edit=lambda: role_edit, role_delete_books=lambda: role_delete, id=1)


# ── result parsing ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_edit_result_success_response():
    from cps.api import edit as mod
    resp = flask.Response(json.dumps({"success": True, "newValue": "X"}), mimetype="application/json")
    assert mod._parse_edit_result(resp) == (True, "")


@pytest.mark.unit
def test_parse_edit_result_failure_response():
    from cps.api import edit as mod
    resp = flask.Response(json.dumps({"success": False, "msg": "bad lang"}), mimetype="application/json")
    ok, msg = mod._parse_edit_result(resp)
    assert ok is False and msg == "bad lang"


@pytest.mark.unit
def test_parse_edit_result_tuple_is_error():
    from cps.api import edit as mod
    ok, msg = mod._parse_edit_result(("Parameter not found", 400))
    assert ok is False and "Parameter" in msg


@pytest.mark.unit
def test_parse_edit_result_empty_is_success():
    from cps.api import edit as mod
    assert mod._parse_edit_result("") == (True, "")


# ── role gating ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_get_metadata_requires_edit_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/metadata", method="GET"):
        with patch.object(mod, "current_user", _editor(role_edit=False)):
            resp = inspect.unwrap(mod.get_metadata)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_update_metadata_anonymous_401():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/metadata", body={"title": "X"}):
        with patch.object(mod, "current_user", _editor(anon=True)):
            resp = inspect.unwrap(mod.update_metadata)(5)
    assert resp[1] == 401


# ── dispatch ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_update_metadata_calls_core_per_field():
    from cps.api import edit as mod
    fake_book = SimpleNamespace(
        id=5, title="T", authors=[], series=[], series_index=1.0,
        tags=[], publishers=[], languages=[], comments=[], ratings=[],
    )
    success = flask.Response(json.dumps({"success": True}), mimetype="application/json")
    with _ctx("/api/v1/books/5/metadata", body={"title": "New Title", "tags": "a, b"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_book=lambda _id: fake_book,
                                                    get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "edit_book_param", return_value=success) as core, \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)
    # core called once per supplied field (title, tags) — not for absent fields
    called_params = [c.args[0] for c in core.call_args_list]
    assert called_params == ["title", "tags"]
    # each call carries the book pk + the value
    for c in core.call_args_list:
        assert c.args[1]["pk"] == "5"
    assert resp.status_code == 200


@pytest.mark.unit
def test_delete_book_requires_delete_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/delete"):
        with patch.object(mod, "current_user", _editor(role_delete=False)):
            resp = inspect.unwrap(mod.delete_book)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_delete_book_not_found_404():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/999/delete"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_filtered_book=lambda *a, **k: None)):
            resp = inspect.unwrap(mod.delete_book)(999)
    assert resp[1] == 404


@pytest.mark.unit
def test_delete_book_visibility_scoped_404_does_not_delete():
    """IDOR guard: a user with the (global) delete role but a visibility
    restriction that hides the book must not be able to delete it. The endpoint
    authorizes against get_filtered_book (visible library), not the raw table,
    so an inaccessible id returns 404 and the delete core is never invoked."""
    from cps.api import edit as mod
    with _ctx("/api/v1/books/7/delete"):
        with patch.object(mod, "current_user", _editor(role_delete=True)), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: SimpleNamespace(id=7),          # raw row EXISTS
                 get_filtered_book=lambda *a, **k: None)), \
             patch.object(mod, "delete_book_from_table") as core:
            resp = inspect.unwrap(mod.delete_book)(7)
    assert resp[1] == 404
    core.assert_not_called()


@pytest.mark.unit
def test_delete_book_authorizes_with_visibility_filter():
    """The authorization lookup passes allow_show_archived/hidden so a user's own
    archived/hidden books stay deletable (a listing exclusion, not access loss)."""
    from cps.api import edit as mod
    seen = {}

    def _gfb(book_id, allow_show_archived=False, allow_show_hidden=False):
        seen["archived"], seen["hidden"] = allow_show_archived, allow_show_hidden
        return SimpleNamespace(id=book_id)

    with _ctx("/api/v1/books/5/delete"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_filtered_book=_gfb)), \
             patch.object(mod, "delete_book_from_table", return_value='{"location":"/"}') as core:
            resp = inspect.unwrap(mod.delete_book)(5)
    assert resp[1] == 204
    # whole-book delete: book_format="" , json_response=True
    assert core.call_args.args[0] == 5 and core.call_args.args[1] == ""
    assert seen == {"archived": True, "hidden": True}


@pytest.mark.unit
def test_update_metadata_collects_field_errors():
    from cps.api import edit as mod
    fake_book = SimpleNamespace(
        id=5, title="T", authors=[], series=[], series_index=1.0,
        tags=[], publishers=[], languages=[], comments=[], ratings=[],
    )
    fail = flask.Response(json.dumps({"success": False, "msg": "Invalid languages"}),
                          mimetype="application/json")
    with _ctx("/api/v1/books/5/metadata", body={"languages": "zz"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_book=lambda _id: fake_book,
                                                    get_cc_columns=lambda *a, **k: [])), \
             patch.object(mod, "edit_book_param", return_value=fail), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)
    body = json.loads(resp.get_data())
    assert body["errors"]["languages"] == "Invalid languages"


# ── format delete + convert (#18) ────────────────────────────────────────────

@pytest.mark.unit
def test_convert_requires_edit_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/convert", body={"from": "epub", "to": "mobi"}):
        with patch.object(mod, "current_user", _editor(role_edit=False)):
            resp = inspect.unwrap(mod.convert_format)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_convert_same_format_400():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/convert", body={"from": "epub", "to": "epub"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_book", return_value=SimpleNamespace(id=5)):
            resp = inspect.unwrap(mod.convert_format)(5)
    assert resp[1] == 400


@pytest.mark.unit
def test_convert_success_calls_core():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/convert", body={"from": "epub", "to": "mobi"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_book", return_value=SimpleNamespace(id=5)), \
             patch.object(mod, "config", SimpleNamespace(get_book_path=lambda: "/books")), \
             patch.object(mod, "get_convert_options", return_value=(["epub"], ["mobi"])), \
             patch.object(mod, "convert_book_format", return_value=None) as core:
            resp = inspect.unwrap(mod.convert_format)(5)
    # success returns a Response (jsonify), not an (resp, status) tuple
    body = json.loads(resp.get_data())
    assert body["ok"] is True
    core.assert_called_once()
    assert core.call_args.args[2] == "EPUB" and core.call_args.args[3] == "MOBI"


@pytest.mark.unit
def test_delete_format_requires_delete_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/formats/epub/delete"):
        with patch.object(mod, "current_user", _editor(role_delete=False)):
            resp = inspect.unwrap(mod.delete_format)(5, "epub")
    assert resp[1] == 403


@pytest.mark.unit
def test_delete_format_uses_core_with_uppercased_format():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/formats/epub/delete"):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=SimpleNamespace(id=5)), \
             patch.object(mod, "delete_book_from_table") as core:
            resp = inspect.unwrap(mod.delete_format)(5, "epub")
    assert resp[1] == 204
    core.assert_called_once_with(5, "EPUB", True)


@pytest.mark.unit
def test_delete_format_visibility_scoped_404_does_not_delete():
    """Same IDOR guard as whole-book delete, on the per-format endpoint."""
    from cps.api import edit as mod
    with _ctx("/api/v1/books/7/formats/epub/delete"):
        with patch.object(mod, "current_user", _editor(role_delete=True)), \
             patch.object(mod, "calibre_db", SimpleNamespace(
                 get_book=lambda _id: SimpleNamespace(id=7),          # raw row EXISTS
                 get_filtered_book=lambda *a, **k: None)), \
             patch.object(mod, "delete_book_from_table") as core:
            resp = inspect.unwrap(mod.delete_format)(7, "epub")
    assert resp[1] == 404
    core.assert_not_called()


# ── cover (#27) ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_set_cover_requires_edit_role():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/cover", body={"url": "http://x/y.jpg"}):
        with patch.object(mod, "current_user", _editor(role_edit=False)):
            resp = inspect.unwrap(mod.set_cover)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_set_cover_no_input_400():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/cover", body={}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=SimpleNamespace(path="p")):
            resp = inspect.unwrap(mod.set_cover)(5)
    assert resp[1] == 400


def _cover_book():
    return SimpleNamespace(id=5, path="p", has_cover=0, last_modified=None)


@pytest.mark.unit
def test_set_cover_from_url_calls_core_and_returns_cover_url():
    from cps.api import edit as mod
    with _ctx("/api/v1/books/5/cover", body={"url": "http://x/y.jpg"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=_cover_book()), \
             patch.object(mod.calibre_db, "session", MagicMock()), \
             patch.object(mod, "mark_book_modified"), \
             patch.object(mod, "log_metadata_change"), \
             patch.object(mod, "replace_cover_thumbnail_cache"), \
             patch.object(mod, "save_cover_from_url", return_value=(True, "ok")) as core:
            resp = inspect.unwrap(mod.set_cover)(5)
    body = json.loads(resp.get_data())
    assert body["ok"] is True
    # `md`, not `og` — `og` is COVER_THUMBNAIL_ORIGINAL == 0 (falsy), so it
    # serves the unresized library file. Same URL shape the detail serializer
    # emits, so the preview and the next fetch agree.
    assert body["cover_url"].startswith("/cover/5/md")
    core.assert_called_once_with("http://x/y.jpg", "p")


@pytest.mark.unit
def test_set_cover_records_the_change_so_every_cover_url_rebusts():
    """Writing cover.jpg is not the whole job.

    Books.last_modified is what every cover URL is versioned by (jinjia's
    `last_modified` filter, the /api/v1 serializers) and what Kobo native sync
    re-selects on. This endpoint used to skip it, so a cover replaced from the
    new UI stayed invisible everywhere except the one response it returned —
    and with cover responses now cacheable, a stale image would survive in the
    browser. cover_picker's apply path has always recorded it; so must this.
    """
    from cps.api import edit as mod
    book = _cover_book()
    with _ctx("/api/v1/books/5/cover", body={"url": "http://x/y.jpg"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=book), \
             patch.object(mod.calibre_db, "session", MagicMock()) as session, \
             patch.object(mod, "mark_book_modified") as marked, \
             patch.object(mod, "log_metadata_change") as logged, \
             patch.object(mod, "replace_cover_thumbnail_cache") as thumbs, \
             patch("cps.kobo_sync_status.remove_synced_book") as unsync, \
             patch.object(mod, "save_cover_from_url", return_value=(True, "ok")):
            resp = inspect.unwrap(mod.set_cover)(5)
    assert json.loads(resp.get_data())["ok"] is True
    assert book.has_cover == 1
    marked.assert_called_once_with(book)
    logged.assert_called_once_with(book, {'cover': True})
    session.commit.assert_called_once()
    thumbs.assert_called_once()
    # Kobo devices have to be told to re-pull the image, exactly as
    # cover_picker's apply path does — and after the commit, since this writes
    # to app.db and the commit above writes to metadata.db.
    unsync.assert_called_once_with(5, all=True)


@pytest.mark.unit
def test_set_cover_reports_failure_when_the_change_cannot_be_recorded():
    """The bytes are on disk but nothing else can see them — never report
    success, or the UI shows "saved" for a change no other surface will have."""
    from cps.api import edit as mod
    session = MagicMock()
    session.commit.side_effect = RuntimeError("db gone")
    with _ctx("/api/v1/books/5/cover", body={"url": "http://x/y.jpg"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=_cover_book()), \
             patch.object(mod.calibre_db, "session", session), \
             patch.object(mod, "mark_book_modified"), \
             patch.object(mod, "log_metadata_change"), \
             patch.object(mod, "replace_cover_thumbnail_cache"), \
             patch.object(mod, "save_cover_from_url", return_value=(True, "ok")):
            resp = inspect.unwrap(mod.set_cover)(5)
    assert resp[1] == 500
    session.rollback.assert_called_once()
