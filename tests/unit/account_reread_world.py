# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared request setup for test_account_changes_from_another_process.py.

One Flask app for every basic-auth sign-in those tests make, built here rather
than in the test file (fork #2546).
"""

from unittest.mock import MagicMock, patch

import flask

from cps import constants, opds, usermanagement

app = flask.Flask(__name__)
app.config["TESTING"] = True
app.add_url_rule("/opds/download/<book_id>/<book_format>/",
                 view_func=opds.opds_download_link)


def basic_auth(username, password):
    """An OPDS-style basic-auth sign-in through usermanagement.verify_password."""
    with app.test_request_context("/opds"), \
            patch.object(usermanagement, "limiter", MagicMock()), \
            patch.object(usermanagement, "_verify_app_password_digest", return_value=False), \
            patch.object(usermanagement, "_verify_app_password_older", return_value=False), \
            patch.object(usermanagement.config, "config_login_type", constants.LOGIN_STANDARD, create=True):
        return usermanagement.verify_password(username, password)


def opds_download(username, password):
    """Real download auth gates; book visibility and file serving are separate."""
    with patch.object(usermanagement, "limiter", MagicMock()), \
            patch.object(usermanagement.config, "config_login_type", constants.LOGIN_STANDARD, create=True), \
            patch.object(usermanagement.config, "config_anonbrowse", 0, create=True), \
            patch.object(usermanagement.config, "config_allow_reverse_proxy_header_login", False, create=True), \
            patch.object(opds, "abort_unless_opds_book_exposed"), \
            patch.object(opds, "get_download_link", return_value="ebook bytes"):
        return app.test_client().get("/opds/download/1/epub/", auth=(username, password))
