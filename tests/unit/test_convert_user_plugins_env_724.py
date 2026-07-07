"""Regression test for fork #724.

Converting from a plugin-provided input format (KFX → EPUB) failed with
``ValueError: No plugin to handle input format: kfx`` even though the KFX Input
plugin was present, because the ``ebook-convert`` subprocess inherited
``HOME=/root/.config/calibre`` (not writable by the abc service user). Calibre
then fell back to a throwaway temp config dir with no plugins registered.

do_calibre_export in embed_helper already routes calibre at the user's config
dir via ``calibre_user_plugins.apply_to_env`` when the opt-in
``CWA_CALIBRE_USER_PLUGINS`` feature is on; the converter did not. These tests
pin that the convert subprocess now gets the same plugin-bearing env when the
feature is enabled, and is left untouched when it is disabled.
"""
import os
import types

import pytest

# Import helper first so the cps.helper <-> cps.tasks.convert import cycle
# resolves in the same order the app uses (helper pulls in TaskConvert). Importing
# cps.tasks.convert first at collection time trips a partial-init ImportError.
from cps import helper as _helper  # noqa: F401  (import-order side effect)

pytestmark = pytest.mark.unit


class _FakeProc:
    returncode = 0

    def poll(self):
        return 0

    class _Stream:
        def readline(self):
            return b""

        def readlines(self):
            return []

    stdout = _Stream()
    stderr = _Stream()


def _run_convert_capture_env(monkeypatch, plugins_enabled):
    from cps.tasks import convert as convert_mod

    if plugins_enabled:
        monkeypatch.setenv("CWA_CALIBRE_USER_PLUGINS", "1")
    else:
        monkeypatch.delenv("CWA_CALIBRE_USER_PLUGINS", raising=False)

    # Replace convert's module-level `config` with a plain namespace holding
    # exactly what _convert_calibre reads on the no-embed path. The real global
    # ConfigSQL is a bare, unloaded instance in a fresh test process (CI), so
    # monkeypatching individual attributes on it is fragile — this is robust.
    # config_embed_metadata=False skips the show_metadata branch so we reach the
    # ebook-convert command we care about.
    fake_config = types.SimpleNamespace(
        config_embed_metadata=False,
        config_calibre="",
        config_converterpath="/usr/bin/ebook-convert",
    )
    monkeypatch.setattr(convert_mod, "config", fake_config)

    captured = {}

    def fake_process_open(command, quotes=(), env=None, *a, **k):
        captured["command"] = command
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(convert_mod, "process_open", fake_process_open)

    task = convert_mod.TaskConvert("/tmp/somebook", 281, "msg", {}, None)
    check, msg = task._convert_calibre("/tmp/somebook", ".kfx", ".epub", False)
    return check, captured


def test_convert_env_carries_user_plugin_config_dir_when_enabled(monkeypatch):
    check, captured = _run_convert_capture_env(monkeypatch, plugins_enabled=True)
    assert check == 0
    env = captured["env"]
    assert env is not None, "ebook-convert must run with an explicit env (#724)"
    assert env.get("CALIBRE_CONFIG_DIRECTORY") == "/config/.config/calibre", \
        "converter must see the user's Calibre plugins dir or KFX input fails (#724)"
    assert env.get("HOME") == "/config"


def test_convert_env_untouched_when_feature_disabled(monkeypatch):
    _, captured = _run_convert_capture_env(monkeypatch, plugins_enabled=False)
    env = captured["env"]
    # apply_to_env is a no-op when disabled: it must NOT force the /config dir.
    assert env is not None
    assert env.get("CALIBRE_CONFIG_DIRECTORY") != "/config/.config/calibre"
