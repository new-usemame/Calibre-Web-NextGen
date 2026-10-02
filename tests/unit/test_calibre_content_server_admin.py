# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin failures must reject content-server settings before changing shared config."""
from types import SimpleNamespace

import pytest
from flask import Flask

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("form, expected", [
    ({"config_calibre_server_port": "99999"}, "port must be"),
    ({"config_calibre_server_port": "8083", "config_calibre_server_enabled": "on",
      "config_calibre_server_anonymous_writes": "on"}, "port must differ"),
    ({"config_calibre_server_listen": "not-an-address"}, "listen address"),
])
def test_invalid_server_form_rejects_before_other_configuration_writes(monkeypatch, form, expected):
    from cps import admin
    from cps import content_server

    monkeypatch.setattr(admin, "config", SimpleNamespace(
        hardcover_sync_enabled=lambda: False,
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


def test_disabled_server_can_keep_the_web_port_without_blocking_basic_settings(monkeypatch):
    """A hidden default must not make the default-off app impossible to configure."""
    from cps import admin, content_server
    defaults = dict(content_server.SETTING_DEFAULTS)
    monkeypatch.setattr(content_server, "setting", lambda name: defaults[name])
    monkeypatch.setattr(admin.web_server, "listen_port", 8080)
    assert admin._content_server_settings_error({"config_calibre_server_port": "8080"}) is None


@pytest.mark.parametrize("database", [False, True])
def test_configuration_generation_is_exclusive_before_the_first_mutation(monkeypatch, database):
    from cps import admin, content_server
    defaults = dict(content_server.SETTING_DEFAULTS)
    monkeypatch.setattr(content_server, "setting", lambda name: defaults[name])
    monkeypatch.setattr(content_server, "configuration_identity", lambda: ())
    monkeypatch.setattr(admin, "config", SimpleNamespace(
        config_calibre_dir="/unchanged-library", hardcover_sync_enabled=lambda: False,
        config_kobo_prefer_kepub=False, resolved_hardcover_token=lambda: ""))

    class Checked(Exception):
        pass

    def first_mutation(*_args):
        with pytest.raises(TimeoutError):
            with content_server.ownership.operation(admin.constants.CONFIG_DIR, timeout=0.05):
                pytest.fail("a client can observe a partially applied server generation")
        raise Checked()

    monkeypatch.setattr(admin, "_db_simulate_change" if database else "_config_string", first_mutation)
    helper = admin._db_configuration_update_helper if database else admin._configuration_update_helper
    with Flask(__name__).test_request_context(method="POST", data={"config_calibre_dir": "/unchanged-library"}):
        with pytest.raises(Checked):
            helper()
    with content_server.ownership.operation(admin.constants.CONFIG_DIR, timeout=0.05):
        pass  # the exceptional draft path released its gate


def test_native_windows_rejects_enabled_server_but_keeps_default_off_usable(monkeypatch):
    from cps import admin, content_server
    defaults = dict(content_server.SETTING_DEFAULTS)
    monkeypatch.setattr(content_server, "setting", lambda name: defaults[name])
    monkeypatch.setattr(content_server, "platform_supported", lambda: False)
    monkeypatch.setattr(admin, "_", lambda message, **values: message % values)
    assert admin._content_server_settings_error({}) is None
    assert "POSIX" in admin._content_server_settings_error({
        "config_calibre_server_enabled": "on", "config_calibre_server_anonymous_writes": "on"})
