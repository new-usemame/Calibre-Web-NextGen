# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""A cover.jpg on disk is ignored when metadata.db says the book has no cover (#2678).

The admin action scans for those books, shows them, and sets ``has_cover`` only
for the ones the admin selects. A real library (``LibraryWorld``) is used so the
cover route is the one that serves the result.
"""

import contextlib

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

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
    from cps import admin

    scan, confirm, cleared = handlers
    token = scan()["confirmation_token"]

    empty_context, empty_status = confirm(token)
    outside_context, outside_status = confirm(token, 2)  # flagged: never in the preview
    garbage_context, garbage_status = confirm("not-a-token", 1)
    monkeypatch.setattr(admin, "_COVER_FLAG_TOKEN_MAX_AGE", -1)
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
def test_template_gives_every_candidate_its_own_unchecked_checkbox():
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
    assert "checked" not in html
    assert 'name="confirmation_token" value="token"' in html
