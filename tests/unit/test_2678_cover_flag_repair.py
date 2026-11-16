# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""A cover.jpg on disk is ignored when metadata.db says the book has no cover (#2678).

The admin action scans for those books, shows them, and sets ``has_cover`` only
for the ones the admin selects. A real library (``LibraryWorld``) is used so the
cover route is the one that serves the result.
"""

import contextlib
import re
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from cps import db
from cps.services import cover_flag_repair
from tests.unit.koreader_library_world import LibraryWorld


@pytest.fixture
def world(monkeypatch, tmp_path):
    library = LibraryWorld(monkeypatch, tmp_path)
    yield library
    library.close()


def _unflagged(world, book_id, title, *, flag=0):
    """A book whose cover.jpg exists but whose flag says it has none."""
    book = world.add_book(book_id, title, cover=True)
    book.has_cover = flag
    world.session.commit()
    return book


@pytest.fixture
def library(world):
    _unflagged(world, 1, "Restorable")
    _unflagged(world, 5, "Restorable With Null Flag", flag=None)
    world.add_book(2, "Has Flag", cover=True)
    world.add_book(3, "No Cover File", cover=False)
    empty = _unflagged(world, 4, "Empty Cover File")
    (world.root / empty.path / "cover.jpg").write_bytes(b"")
    return world


@pytest.fixture
def handlers(world, monkeypatch):
    from cps import admin

    cleared = []
    monkeypatch.setattr(admin, "calibre_db", world.cdb)
    monkeypatch.setattr(admin, "render_title_template", lambda _template, **context: context)
    monkeypatch.setattr(admin, "_", lambda message, **values: message % values if values else message)
    monkeypatch.setattr(admin, "metadata_db_write_lock", contextlib.nullcontext)
    monkeypatch.setattr(admin.helper, "clear_cover_thumbnail_cache", cleared.append)

    def scan():
        with world.app.test_request_context("/admin/cover-flags", method="POST"):
            return admin.cover_flag_repair_scan.__wrapped__.__wrapped__()

    def confirm(token, *book_ids):
        form = {"confirmation_token": token, "book_id": [str(i) for i in book_ids]}
        with world.app.test_request_context(
                "/admin/cover-flags/confirm", method="POST", data=form):
            return admin.cover_flag_repair_confirm.__wrapped__.__wrapped__()

    return scan, confirm, cleared


def _flag(world, book_id):
    from cps import db
    world.session.expire_all()
    return world.session.get(db.Books, book_id).has_cover


def _served_mimetype(world, book_id):
    from cps import db, helper
    world.session.expire_all()
    book = world.session.get(db.Books, book_id)
    with world.app.test_request_context("/cover/%d" % book_id):
        response = helper.get_book_cover_internal(book)
        response.direct_passthrough = False
        return response.mimetype


@pytest.mark.unit
def test_scan_lists_only_unflagged_books_with_a_usable_cover_file(library, handlers):
    scan, _confirm, _cleared = handlers

    context = scan()

    assert {candidate.book_id for candidate in context["preview"]} == {1, 5}
    assert context["confirmation_token"]
    assert _flag(library, 1) == 0, "scanning must not write anything"


@pytest.mark.unit
def test_selected_book_gets_its_cover_served_and_deselected_book_does_not(library, handlers):
    scan, confirm, cleared = handlers
    assert _served_mimetype(library, 1) == "image/svg+xml"

    token = scan()["confirmation_token"]
    context = confirm(token, 1)

    assert context["result"] == {"fixed": 1, "skipped": 0}
    assert _flag(library, 1) == 1
    assert _flag(library, 5) is None, "a candidate the admin did not select is left alone"
    assert cleared == [1]
    assert _served_mimetype(library, 1) == "image/jpeg"
    assert _served_mimetype(library, 5) == "image/svg+xml"


@pytest.mark.unit
def test_confirm_refuses_empty_tampered_and_expired_selections(library, handlers, monkeypatch):
    scan, confirm, cleared = handlers
    token = scan()["confirmation_token"]

    empty_context, empty_status = confirm(token)
    outside_context, outside_status = confirm(token, 2)  # flagged: never in the preview
    garbage_context, garbage_status = confirm("not-a-token", 1)
    monkeypatch.setattr(cover_flag_repair, "TOKEN_MAX_AGE", -1)
    expired_context, expired_status = confirm(token, 1)

    assert (empty_status, outside_status, garbage_status, expired_status) == (400, 400, 400, 400)
    assert all("error" in c and c["error"] for c in (
        empty_context, outside_context, garbage_context, expired_context))
    assert _flag(library, 1) == 0
    assert cleared == []


@pytest.mark.unit
def test_books_that_changed_since_the_scan_are_skipped_not_written(library, handlers):
    from cps import db

    scan, confirm, cleared = handlers
    token = scan()["confirmation_token"]
    # Between scan and confirm: one cover file disappears, one book gets its flag.
    (library.root / library.session.get(db.Books, 1).path / "cover.jpg").unlink()
    library.session.get(db.Books, 5).has_cover = 1
    library.session.commit()

    context = confirm(token, 1, 5)

    assert context["result"] == {"fixed": 0, "skipped": 2}
    assert _flag(library, 1) == 0
    assert cleared == []


@pytest.mark.unit
def test_google_drive_libraries_are_not_scanned(library, handlers, monkeypatch):
    from cps import admin

    scan, _confirm, _cleared = handlers
    monkeypatch.setattr(admin.config, "config_use_google_drive", True)

    context, status = scan()

    assert status == 400
    assert context["error"]
    assert _flag(library, 1) == 0


@pytest.mark.unit
def test_split_library_covers_are_looked_up_in_the_books_directory(tmp_path):
    db_dir = tmp_path / "db"
    books_dir = tmp_path / "books"
    db_dir.mkdir()
    (books_dir / "A" / "Split (7)").mkdir(parents=True)
    (books_dir / "A" / "Split (7)" / "cover.jpg").write_bytes(b"jpeg bytes")
    rows = [(7, "Split", "A/Split (7)", 0)]

    assert cover_flag_repair.find_candidates(rows, str(books_dir))[0].book_id == 7
    assert cover_flag_repair.find_candidates(rows, str(db_dir)) == []


@pytest.mark.unit
def test_template_gives_every_candidate_its_own_unchecked_checkbox_and_a_select_all():
    from cps.services.cover_flag_repair import CoverFlagCandidate

    environment = Environment(loader=ChoiceLoader([
        DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
        FileSystemLoader("cps/templates"),
    ]))
    environment.globals.update(
        _=lambda message, **values: message % values if values else message,
        csrf_token=lambda: "synthetic-csrf",
        url_for=lambda endpoint, **_values: "/" + endpoint,
    )
    html = environment.get_template("cover_flag_repair.html").render(
        preview=[CoverFlagCandidate(1, "One"), CoverFlagCandidate(5, "Five")],
        confirmation_token="token", result=None, error=None, gdrive=False)

    assert html.count('name="book_id"') == 2
    assert html.count('id="cover-flag-select-all"') == 1, "one control selects every row"
    assert not re.search(r'name="book_id"[^>]*\bchecked\b', html), "rows start unselected"
    assert 'name="confirmation_token" value="token"' in html


# -- the JSON API behind the new interface ----------------------------------

SCAN = "/api/v1/admin/cover-flags/scan"
RESTORE = "/api/v1/admin/cover-flags/restore"


@pytest.fixture
def api(library, monkeypatch):
    from cps import constants
    from cps.api import cover_flags
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    cleared = []
    monkeypatch.setattr(cover_flags, "calibre_db", library.cdb)
    monkeypatch.setattr(cover_flags, "metadata_db_write_lock", contextlib.nullcontext)
    monkeypatch.setattr(cover_flags.helper, "clear_cover_thumbnail_cache", cleared.append)
    CSRFProtect(library.app)

    @library.app.get("/test/cover-flags-csrf")
    def csrf_token():
        return generate_csrf()

    library.enable_web()
    admin = library.add_user("root")
    admin.role |= constants.ROLE_ADMIN
    library.session.commit()
    library.add_user("reader")
    return SimpleNamespace(
        world=library, cleared=cleared,
        admin=library.browser("root"), reader=library.browser("reader"),
        anonymous=library.browser())


@pytest.mark.unit
def test_api_is_for_signed_in_admins_only(api):
    assert api.anonymous.post(SCAN).status_code == 401
    assert api.reader.post(SCAN).status_code == 403
    assert api.reader.post(RESTORE, json={"token": "x", "book_ids": [1]}).status_code == 403
    assert _flag(api.world, 1) == 0


@pytest.mark.unit
def test_api_scan_lists_candidates_and_writes_nothing(api):
    response = api.admin.post(SCAN)

    assert response.status_code == 200
    body = response.get_json()
    assert {book["id"] for book in body["books"]} == {1, 5}
    assert body["count"] == 2 and body["token"]
    assert _flag(api.world, 1) == 0


@pytest.mark.unit
def test_api_restore_fixes_only_the_selected_book_and_it_is_served(api):
    token = api.admin.post(SCAN).get_json()["token"]

    response = api.admin.post(RESTORE, json={"token": token, "book_ids": [1]})

    assert response.status_code == 200
    assert response.get_json() == {"fixed": 1, "skipped": 0}
    assert _flag(api.world, 1) == 1
    assert _flag(api.world, 5) is None
    assert api.cleared == [1]
    assert _served_mimetype(api.world, 1) == "image/jpeg"


@pytest.mark.unit
def test_api_restore_refuses_bad_requests_with_distinct_codes(api, monkeypatch):
    token = api.admin.post(SCAN).get_json()["token"]

    def code(payload):
        response = api.admin.post(RESTORE, json=payload)
        assert response.status_code == 400
        return response.get_json()["error"]["code"]

    assert code({"token": token, "book_ids": []}) == "empty_selection"
    assert code({"token": token, "book_ids": [2]}) == "invalid_selection"  # never listed
    assert code({"token": token, "book_ids": ["one"]}) == "invalid_selection"
    assert code({"token": "forged", "book_ids": [1]}) == "invalid_token"
    assert code({"token": token}) == "invalid_request"
    monkeypatch.setattr(cover_flag_repair, "TOKEN_MAX_AGE", -1)
    assert code({"token": token, "book_ids": [1]}) == "invalid_token"
    assert _flag(api.world, 1) == 0
    assert api.cleared == []


@pytest.mark.unit
def test_api_skips_books_that_changed_since_the_scan(api):
    token = api.admin.post(SCAN).get_json()["token"]
    api.world.session.get(db.Books, 5).has_cover = 1
    api.world.session.commit()

    response = api.admin.post(RESTORE, json={"token": token, "book_ids": [1, 5]})

    assert response.get_json() == {"fixed": 1, "skipped": 1}


@pytest.mark.unit
def test_api_is_not_offered_for_google_drive_libraries(api, monkeypatch):
    from cps.api import cover_flags

    monkeypatch.setattr(cover_flags.config, "config_use_google_drive", True)

    for url, payload in ((SCAN, None), (RESTORE, {"token": "x", "book_ids": [1]})):
        response = api.admin.post(url, json=payload)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "not_available"


@pytest.mark.unit
@pytest.mark.parametrize("book_id", [True, 1.5, 1.0, "1.0", " 1", "+1"])
def test_api_rejects_ids_that_would_be_coerced_to_a_preview_book(api, book_id):
    """Malformed IDs must never restore a different, valid preview ID."""
    token = api.admin.post(SCAN).get_json()["token"]
    response = api.admin.post(RESTORE, json={"token": token, "book_ids": [book_id]})
    assert response.status_code == 400
    assert _flag(api.world, 1) == 0


@pytest.mark.unit
def test_restore_advances_the_book_revision_for_cached_covers_and_sync(api):
    """A repaired cover must be visible beyond a fresh uncached cover request."""
    before = api.world.session.get(db.Books, 1).last_modified
    token = api.admin.post(SCAN).get_json()["token"]
    response = api.admin.post(RESTORE, json={"token": token, "book_ids": [1]})
    assert response.status_code == 200
    api.world.session.expire_all()
    changed = api.world.session.query(db.Books).filter(
        db.Books.id == 1, db.Books.last_modified > before).one_or_none()
    assert changed is not None, "sync cursors and cover URLs use last_modified"


@pytest.mark.unit
def test_cache_queue_failure_does_not_report_a_committed_restore_as_rolled_back(api, monkeypatch):
    from cps.api import cover_flags

    def queue_failed(_book_id):
        raise RuntimeError("thumbnail worker unavailable")

    monkeypatch.setattr(cover_flags.helper, "clear_cover_thumbnail_cache", queue_failed)
    token = api.admin.post(SCAN).get_json()["token"]
    response = api.admin.post(RESTORE, json={"token": token, "book_ids": [1]})
    assert response.status_code == 200
    assert response.get_json() == {"fixed": 1, "skipped": 0}
    assert _flag(api.world, 1) == 1


@pytest.mark.unit
def test_restore_reads_live_rows_after_acquiring_the_writer_lock(library):
    """A writer finishing while we wait must not leave a stale path to restore."""
    from sqlalchemy import update

    # Model a different writer: its commit must not expire our identity map.
    library.session.expire_on_commit = False

    @contextlib.contextmanager
    def another_writer_finishes():
        library.session.execute(update(db.Books).where(db.Books.id == 1).values(
            path="Moved/No Cover").execution_options(synchronize_session=False))
        library.session.commit()
        yield

    outcome = cover_flag_repair.apply_selection(
        library.session, db.Books, [1], str(library.root),
        write_lock=another_writer_finishes, on_fixed=lambda _book_id: None)
    assert outcome.fixed == ()
    assert outcome.skipped == 1
    assert _flag(library, 1) == 0


@pytest.mark.unit
@pytest.mark.parametrize("escape", ["relative", "absolute", "symlink"])
def test_scan_and_restore_refuse_cover_paths_outside_the_books_directory(tmp_path, escape):
    """A library row cannot make an unrelated file qualify as a book cover."""
    root = tmp_path / "books"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "cover.jpg").write_bytes(b"cover")
    if escape == "symlink":
        (root / "linked").symlink_to(outside, target_is_directory=True)
        path = "linked"
    else:
        path = "../outside" if escape == "relative" else str(outside)
    book = SimpleNamespace(path=path, has_cover=0)
    assert cover_flag_repair.find_candidates([(1, "Outside", path, 0)], str(root)) == []
    assert cover_flag_repair.restore_flags({1: book}, [1], str(root)).fixed == ()
    assert book.has_cover == 0


@pytest.mark.unit
def test_commit_failure_rolls_back_all_selected_covers(library, monkeypatch):
    def commit_failed():
        raise RuntimeError("database write failed")

    cleared = []
    monkeypatch.setattr(library.session, "commit", commit_failed)
    with pytest.raises(RuntimeError, match="database write failed"):
        cover_flag_repair.apply_selection(
            library.session, db.Books, [1, 5], str(library.root),
            write_lock=contextlib.nullcontext, on_fixed=cleared.append)
    assert _flag(library, 1) == 0
    assert _flag(library, 5) is None
    assert cleared == []


@pytest.mark.unit
def test_scan_and_restore_require_csrf_in_a_browser_session(api):
    """Authenticated admin requests still need the browser session's CSRF token."""
    api.world.app.config["WTF_CSRF_ENABLED"] = True
    assert api.admin.post(SCAN).status_code == 400
    csrf = api.admin.get("/test/cover-flags-csrf").get_data(as_text=True)
    headers = {"X-CSRFToken": csrf}
    scan = api.admin.post(SCAN, headers=headers)
    assert scan.status_code == 200
    payload = {"token": scan.get_json()["token"], "book_ids": [1]}
    assert api.admin.post(RESTORE, json=payload).status_code == 400
    assert _flag(api.world, 1) == 0
    response = api.admin.post(RESTORE, json=payload, headers=headers)
    assert response.status_code == 200
    assert _flag(api.world, 1) == 1
