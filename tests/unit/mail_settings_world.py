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

from cps import csrf
from cps.api import api_v1
from cps.config_sql import ConfigSQL

app = Flask(__name__)
app.config["SECRET_KEY"] = "mail-settings-test"
Babel(app)
csrf.init_app(app)
app.register_blueprint(api_v1)


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
    config.get_mail_server_configured = lambda: ConfigSQL.get_mail_server_configured(config)
    return config
