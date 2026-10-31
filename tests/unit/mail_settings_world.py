# SPDX-License-Identifier: GPL-3.0-or-later
"""A shared app for the admin mail-settings API tests (#2771).

Built here rather than in a test file so these tests share one app
(tests/fitness/test_no_new_flask_apps.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from flask_babel import Babel

app = Flask(__name__)
Babel(app)


def mail_config(**overrides):
    """A stand-in for the settings object with the mail fields the API reads."""
    values = dict(
        mail_server="smtp.gmail.com", mail_port=587, mail_use_ssl=1, mail_login="me@example.invalid",
        mail_from="me@example.invalid", mail_size=25 * 1024 * 1024, mail_filename_template="",
        mail_server_type=1, mail_password_e="stored-secret", mail_gmail_token={},
        save=Mock(),
    )
    values.update(overrides)
    config = SimpleNamespace(**values)
    config.get_mail_server_configured = lambda: bool(
        (config.mail_server != "mail.example.org" and config.mail_server_type == 0)
        or (config.mail_gmail_token != {} and config.mail_server_type == 1))
    return config
