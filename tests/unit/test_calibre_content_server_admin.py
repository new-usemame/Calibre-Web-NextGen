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


@pytest.mark.parametrize("helper_name", ["_configuration_update_helper", "_db_configuration_update_helper", "clear_calibre_server_password"])
def test_busy_gate_rejects_save_with_recoverable_result_before_mutation(monkeypatch, helper_name):
    from contextlib import contextmanager
    from cps import admin, content_server
    @contextmanager
    def blocked(*_a, **_kw):
        raise TimeoutError("Restore owns gate")
        yield
    monkeypatch.setattr(content_server.ownership, "operation", blocked)
    monkeypatch.setattr(admin, "_configuration_result", lambda error, *args: {"error": error, "kind": "json"})
    monkeypatch.setattr(admin, "_db_configuration_result", lambda error, *args: {"error": error, "kind": "html"})
    monkeypatch.setattr(admin, "_", lambda message: message)
    helper = getattr(admin, helper_name)
    while helper_name == "clear_calibre_server_password" and hasattr(helper, "__wrapped__") and helper.__wrapped__.__name__ == helper.__name__:
        # Strip only auth wrappers: retain the generation wrapper itself.
        if helper.__module__ == "cps.content_server":
            break
        if helper.__wrapped__.__code__.co_filename.endswith("content_server.py"):
            helper = helper.__wrapped__
            break
        helper = helper.__wrapped__
    with Flask(__name__).test_request_context(method="POST", data={}):
        result = helper()
    assert "maintenance" in result["error"].lower()
    assert result["kind"] == ("html" if helper_name == "_db_configuration_update_helper" else "json")
