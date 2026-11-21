# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared HTTP test app for the Classic Hardcover administration views."""
from pathlib import Path

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from flask import Flask
from flask_babel import Babel
from flask_wtf.csrf import CSRFProtect, generate_csrf

from cps import admin, cw_login


class Editor(cw_login.UserMixin):
    locale = "fr"

    def __init__(self, name):
        self.id = self.name = name

    def role_admin(self):
        return self.name == "admin"


@pytest.fixture(scope="module")
def hardcover_admin_app(tmp_path_factory):
    translations = tmp_path_factory.mktemp("hardcover-translations")
    output = translations / "fr" / "LC_MESSAGES" / "messages.mo"
    output.parent.mkdir(parents=True)
    source = Path(admin.__file__).parent / "translations" / "fr" / "LC_MESSAGES" / "messages.po"
    with source.open("rb") as catalog, output.open("wb") as compiled:
        write_mo(compiled, read_po(catalog))

    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", BABEL_TRANSLATION_DIRECTORIES=str(translations))
    Babel(app, locale_selector=lambda: cw_login.current_user.locale
          if cw_login.current_user.is_authenticated else "en")
    manager = cw_login.LoginManager(app)
    manager.user_loader(lambda user_id, user_random, session_key: Editor(user_id))
    CSRFProtect(app)
    app.add_url_rule("/csrf", view_func=generate_csrf)
    app.add_url_rule("/admin/hardcover/missing", "admin.hardcover_missing_ids", admin.hardcover_missing_ids)
    app.add_url_rule("/admin/hardcover/rematch", "admin.hardcover_rematch",
                     admin.hardcover_rematch, methods=["POST"])
    app.add_url_rule("/admin/hardcover/allow-retry/<int:book_id>", "admin.hardcover_allow_retry",
                     admin.hardcover_allow_retry, methods=["POST"])
    app.add_url_rule("/admin/hardcover/review-matches", "admin.hardcover_review_matches",
                     admin.hardcover_review_matches)
    app.add_url_rule("/admin", "admin.admin", lambda: "")
    app.add_url_rule("/book/<int:book_id>", "web.show_book", lambda book_id: "")
    app.add_url_rule("/admin/book/<int:book_id>", "edit-book.show_edit_book", lambda book_id: "")
    return app
