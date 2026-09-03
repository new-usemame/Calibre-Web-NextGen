# SPDX-License-Identifier: GPL-3.0-or-later
"""Global Library cover visibility regressions."""

from types import SimpleNamespace

from flask import Flask, Response
import pytest
from werkzeug.exceptions import NotFound

from cps.api.serializers import serialize_book_list_item


pytestmark = pytest.mark.unit


def _covered_book():
    return SimpleNamespace(
        id=42,
        title="Archive title",
        series_index=1.0,
        has_cover=1,
        last_modified=None,
        authors=[SimpleNamespace(name="Archive author")],
        series=[],
        data=[],
        tags=[],
    )


def test_non_member_global_cover_url_resolves_to_real_cover(monkeypatch):
    """A browse-global viewer gets bytes for the URL emitted by the list API.

    The regression emitted a perfectly good URL, then resolved its book through
    the membership-scoped default filter.  ``get_book_cover_internal(None)``
    answered the branded generic SVG with HTTP 200, so status alone could not
    distinguish the broken response from the actual global cover.
    """
    from cps import helper

    book = _covered_book()
    item = serialize_book_list_item(book)
    assert item["cover_url"] == "/cover/42/sm"

    calls = []

    def filtered_book(book_id, **options):
        calls.append((book_id, options))
        # Model a book outside My Library: the ordinary scoped lookup cannot
        # see it; the explicit browse-global lookup can.
        return book if options.get("allow_show_global") else None

    monkeypatch.setattr(helper.calibre_db, "get_filtered_book", filtered_book)
    monkeypatch.setattr(
        helper,
        "current_user",
        SimpleNamespace(role_browse_global=lambda: True),
    )
    monkeypatch.setattr(
        helper,
        "get_book_cover_internal",
        lambda resolved, resolution=None: (
            Response(b"actual-cover", mimetype="image/jpeg")
            if resolved is book
            else Response(b"generic-cover", status=404, mimetype="image/svg+xml")
        ),
    )

    app = Flask(__name__)

    @app.get("/cover/<int:book_id>/<string:resolution>")
    def cover(book_id, resolution):
        resolutions = {"sm": 1, "md": 2, "lg": 4, "og": 0}
        return helper.get_book_cover(book_id, resolutions[resolution])

    response = app.test_client().get(item["cover_url"])

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.data == b"actual-cover"
    assert calls == [(42, {
        "allow_show_archived": True,
        "allow_show_hidden": True,
        "allow_show_global": True,
    })]


def test_cover_lookup_does_not_bypass_membership_without_global_role(monkeypatch):
    """Knowing a cover URL is not permission to browse an unowned book."""
    from cps import helper

    calls = []
    monkeypatch.setattr(
        helper.calibre_db,
        "get_filtered_book",
        lambda book_id, **options: calls.append((book_id, options)),
    )
    monkeypatch.setattr(
        helper,
        "current_user",
        SimpleNamespace(role_browse_global=lambda: False),
    )
    monkeypatch.setattr(helper, "get_book_cover_internal", lambda book, resolution=None: book)

    assert helper.get_book_cover(42, 1) is None
    assert calls == [(42, {
        "allow_show_archived": True,
        "allow_show_hidden": True,
        "allow_show_global": False,
    })]


def test_non_member_detail_does_not_turn_its_format_url_into_download_access(monkeypatch):
    """Global metadata discovery does not widen the download policy funnel."""
    from cps import helper

    filtered = []
    raw_lookup = []
    monkeypatch.setattr(
        helper.calibre_db,
        "get_filtered_book",
        lambda book_id, **options: filtered.append((book_id, options)),
    )
    monkeypatch.setattr(
        helper.calibre_db,
        "get_book",
        lambda book_id: raw_lookup.append(book_id),
    )
    monkeypatch.setattr(
        helper,
        "current_user",
        SimpleNamespace(
            id=17,
            is_authenticated=True,
            is_anonymous=False,
            role_admin=lambda: False,
        ),
    )
    app = Flask(__name__)

    with app.test_request_context("/download/42/epub/book"), pytest.raises(NotFound):
        helper.get_download_link(42, "epub", "web")

    assert filtered == [(42, {
        "allow_show_archived": True,
        "allow_show_hidden": True,
    })]
    assert raw_lookup == []
