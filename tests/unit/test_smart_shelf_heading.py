# SPDX-License-Identifier: GPL-3.0-or-later
"""Stored smart-shelf names and icons remain text in the Classic heading."""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from flask import Flask, render_template
from flask_babel import Babel
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("field", ["name", "icon"])
def test_shelf_heading_does_not_interpret_stored_markup(monkeypatch, field):
    from cps import web

    payload = '<img src=x onerror="window.shelfProbe=1">'
    shelf = SimpleNamespace(
        id=17, user_id=7, name="A & B <Notes>", icon="🪄", is_public=0
    )
    setattr(shelf, field, payload)
    user = SimpleNamespace(
        id=7,
        is_authenticated=True,
        is_anonymous=False,
        get_view_property=lambda *_: "new",
        show_detail_random=lambda: False,
        role_admin=lambda: False,
        role_delete_books=lambda: False,
        role_edit_shelfs=lambda: False,
    )
    query = SimpleNamespace(get=lambda _: shelf)
    monkeypatch.setattr(web, "current_user", user)
    monkeypatch.setattr(web.ub, "session", SimpleNamespace(query=lambda _: query))
    monkeypatch.setattr(web, "load_configured_columns", lambda _: [])
    monkeypatch.setattr(
        web,
        "resolve_magic_shelf_sort",
        lambda *_: SimpleNamespace(
            persistable=False, order_by=[], key="new", join=None
        ),
    )
    monkeypatch.setattr(
        web.magic_shelf, "get_books_for_magic_shelf", lambda *_, **__: ([], 0)
    )
    monkeypatch.setattr(web, "config", SimpleNamespace(config_books_per_page=20))
    from cps import cwa_db_loader

    monkeypatch.setattr(
        cwa_db_loader,
        "load_cwa_db",
        lambda: SimpleNamespace(
            CWA_DB=lambda: SimpleNamespace(log_activity=lambda **_: None)
        ),
    )
    user.name = "Reader"
    app = Flask(__name__)
    Babel(app)
    # Exercise the real index template; omit the unrelated surrounding navigation.
    app.jinja_loader = ChoiceLoader(
        [
            DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
            FileSystemLoader(str(Path(web.__file__).parent / "templates")),
        ]
    )
    from cps.jinjia import jinjia

    app.register_blueprint(jinjia)
    app.jinja_env.globals.update(
        url_for=lambda *_, **__: "/", csrf_token=lambda: "test"
    )
    monkeypatch.setattr(
        web,
        "render_title_template",
        lambda template, **context: render_template(
            template, current_user=user, **context
        ),
    )
    with app.test_request_context("/magicshelf/17"):
        html = inspect.unwrap(web.render_magic_shelf)(17, "stored", 1)
    heading = BeautifulSoup(html, "html.parser").select_one(".discover.load-more h2")
    assert heading is not None
    assert heading.find("img") is None
    assert payload in heading.get_text()
    assert shelf.name in heading.get_text()
    assert shelf.icon in heading.get_text()
