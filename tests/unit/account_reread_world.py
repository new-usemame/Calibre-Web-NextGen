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

from cps import constants, usermanagement

app = flask.Flask(__name__)


def basic_auth(username, password):
    """An OPDS-style basic-auth sign-in through usermanagement.verify_password."""
    with app.test_request_context("/opds"), \
            patch.object(usermanagement, "limiter", MagicMock()), \
            patch.object(usermanagement, "_verify_app_password_digest", return_value=False), \
            patch.object(usermanagement, "_verify_app_password_older", return_value=False), \
            patch.object(usermanagement.config, "config_login_type", constants.LOGIN_STANDARD, create=True):
        return usermanagement.verify_password(username, password)
