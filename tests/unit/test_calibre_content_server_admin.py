# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin failures must reject content-server settings before changing shared config."""
from types import SimpleNamespace

import pytest
from flask import Flask

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("form, expected", [
    ({"config_calibre_server_port": "99999"}, "port must be"),
    ({"config_calibre_server_listen": "not-an-address"}, "listen address"),
])
def test_invalid_server_form_rejects_before_other_configuration_writes(monkeypatch, form, expected):
    from cps import admin
    from cps import content_server

    monkeypatch.setattr(admin, "config", SimpleNamespace(
        config_port=8083, hardcover_sync_enabled=lambda: False,
        config_kobo_prefer_kepub=False, resolved_hardcover_token=lambda: ""))
    defaults = dict(content_server.SETTING_DEFAULTS)
    monkeypatch.setattr(admin.content_server, "setting", lambda key: defaults[key])
    monkeypatch.setattr(admin.content_server, "configuration_identity", lambda: ())
    monkeypatch.setattr(admin, "_configuration_result", lambda error, *args: str(error))
    monkeypatch.setattr(admin, "_", lambda message, **values: message % values)

    def reject_write(*args):
        pytest.fail("an invalid content-server form changed unrelated configuration")

    monkeypatch.setattr(admin, "_config_string", reject_write)
    with Flask(__name__).test_request_context(method="POST", data=form):
        result = admin._configuration_update_helper()
    assert expected in result
