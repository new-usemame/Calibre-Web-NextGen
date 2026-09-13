# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The reader route must answer an unreadable request with a status, never a page.

WHY THIS IS NOT COSMETIC. The web reader renders each EPUB section in an iframe,
and a link inside a book resolves against that section's path. Measured
2026-09-12 in WebKit: the frame performs the link's native navigation, so the
server receives a request the reader never intended to make. While this route
answered `redirect(url_for("web.index"))`, that request was answered with the
LIBRARY HOME PAGE — rendered inside the reader's own content frame. Following a
footnote therefore replaced the book with the catalogue, which is exactly what
was reported from iPhone Safari.

A frame that is showing a book must never be handed a page of the app. A 404 is
a status the caller can recognise, and it renders nothing that looks like the
product.

What breaks this test: restoring a redirect (or any 2xx page) on the
unsupported-format path of `read_book`.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import flask
import flask_babel
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def reader_client(monkeypatch):
    from cps import web

    book = SimpleNamespace(
        id=7,
        title="A Book",
        data=[SimpleNamespace(format="EPUB")],
        series=[],
        series_index=None,
        ordered_authors=[],
    )
    calibre_db = SimpleNamespace(
        get_filtered_book=lambda book_id, allow_show_hidden=False: book,
        order_authors=lambda books, *args, **kwargs: [],
    )
    monkeypatch.setattr(web, "calibre_db", calibre_db)
    # Anonymous: every per-user branch (annotations, bookmark, read history) is
    # gated on authentication, so this exercises the format dispatch itself.
    monkeypatch.setattr(
        web, "current_user",
        SimpleNamespace(is_authenticated=False, is_anonymous=True, id=0, name="Guest"),
    )

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    app.secret_key = "reader-route-test"
    # Babel and a `web.index` endpoint exist so the OLD behaviour — flash() a
    # message and redirect to the library — can actually be produced here. Without
    # them this fixture would answer 500 whatever the route did, and the test
    # could not tell a redirect from a crash.
    flask_babel.Babel(app)
    library = flask.Blueprint("web", __name__)
    library.add_url_rule("/", "index", lambda: "THE LIBRARY HOME PAGE")
    app.register_blueprint(library)
    app.add_url_rule(
        "/read/<int:book_id>/<book_format>",
        view_func=inspect.unwrap(web.read_book),
    )
    return app.test_client()


def test_a_format_the_reader_cannot_open_is_a_404_and_not_the_library(reader_client):
    response = reader_client.get("/read/7/xhtml")

    assert response.status_code == 404, (
        "an unreadable format answered %s; a reader frame must never receive a page"
        % response.status_code
    )
    # The specific regression: not a redirect to the library. The fixture can
    # produce that redirect, so this assertion discriminates.
    assert "Location" not in response.headers
    assert b"THE LIBRARY HOME PAGE" not in response.data


def test_a_stray_section_path_in_the_reader_route_is_a_404(reader_client):
    """The shape a book's own link actually produces: a path segment that is a
    document name rather than a format this reader knows."""
    response = reader_client.get("/read/7/ch1.xhtml")

    assert response.status_code == 404
    assert "Location" not in response.headers
