# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #1447.

The per-locale incomplete-translation notice throttle marker was written to
``/app``, which is part of the application tree rather than state: replaced on
image upgrade, absent on a source install, unwritable when the container runs
rootless. Off Docker the write raised ``FileNotFoundError`` on every page render
for any non-English user, and because the caller catches and logs, the throttle
silently never persisted.

The marker now lives in the configured state directory (``CONFIG_DIR`` —
``/config`` in the image, ``CALIBRE_DBPATH`` elsewhere), the path is defined in
one place (``cps.translation_notice``), a marker recorded under the old ``/app``
path is still honoured, and an unwritable state dir degrades to an extra notice
rather than an exception.

Mirrors tests/unit/test_translation_notice_path.py's sibling for #992.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(dotted: str, rel_path: str):
    module_path = REPO_ROOT / rel_path
    if "cps" not in sys.modules:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg
    spec = importlib.util.spec_from_file_location(dotted, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


translation_notice = _load_module("cps.translation_notice", "cps/translation_notice.py")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point the helper at a real, writable state directory."""
    state = tmp_path / "config"
    state.mkdir()
    monkeypatch.setattr(translation_notice, "_config_dir", lambda: str(state))
    return state


@pytest.fixture
def legacy_dir(tmp_path, monkeypatch):
    """Point the read-only /app fallback at a real directory."""
    legacy = tmp_path / "app"
    legacy.mkdir()
    monkeypatch.setattr(translation_notice, "LEGACY_NOTICE_DIR", str(legacy))
    return legacy


# --- behaviour -------------------------------------------------------------


def test_throttle_round_trip(config_dir, legacy_dir):
    """The path the notice writes is the path it reads back."""
    assert translation_notice.last_notified("de") is None

    assert translation_notice.record_notified("de", "2026-08-07") is True

    assert translation_notice.last_notified("de") == "2026-08-07"
    # Per-locale: throttling German must not silence French.
    assert translation_notice.last_notified("fr") is None


def test_marker_is_written_inside_the_configured_state_dir(config_dir):
    path = translation_notice.translation_notice_file("de")
    assert os.path.dirname(path) == str(config_dir)
    assert not path.startswith("/app")


def test_legacy_app_marker_is_still_honoured(config_dir, legacy_dir):
    """A locale already throttled under /app doesn't re-fire after upgrade."""
    (legacy_dir / "cwa_translation_notice_de").write_text("2026-08-07\n")

    assert translation_notice.last_notified("de") == "2026-08-07"
    # ...and nothing new is written to the legacy location.
    assert not (config_dir / "cwa_translation_notice_de").exists()


def test_new_marker_takes_precedence_over_legacy(config_dir, legacy_dir):
    (legacy_dir / "cwa_translation_notice_de").write_text("2020-01-01\n")
    translation_notice.record_notified("de", "2026-08-07")

    assert translation_notice.last_notified("de") == "2026-08-07"


def test_unwritable_state_dir_does_not_raise(tmp_path, monkeypatch):
    """This is the bug: off Docker the write blew up on every page render.

    Degrading to "not recorded" costs at most one extra notice; raising cost a
    logged traceback on every render and a throttle that never persisted.
    """
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(translation_notice, "_config_dir", lambda: str(missing))
    monkeypatch.setattr(translation_notice, "LEGACY_NOTICE_DIR", str(missing))

    assert translation_notice.record_notified("de", "2026-08-07") is False
    assert translation_notice.last_notified("de") is None


def test_hostile_locales_never_raise_and_never_escape(config_dir, legacy_dir):
    """``lang`` is untrusted, so neither helper may raise or write outside.

    ``current_user.locale`` is returned verbatim by ``get_locale()`` and stored
    without validation by the self-service profile route. The null-byte case is
    the one that bites: ``open()`` raises ``ValueError``, not ``OSError``, so an
    ``except OSError`` alone left the module's "best-effort" contract untrue.
    """
    hostile = [
        "..", "../../..", "../" * 8, "/etc/passwd", "de/../../../../etc",
        "", ".", "de\x00", "\x00", "a" * 5000, "de/../..", "~/../..",
        "..\\..\\..", "\n/etc", "  ..  ", "....//....//",
    ]
    for lang in hostile:
        # Neither helper may raise for any of these.
        translation_notice.record_notified(lang, "2026-08-07")
        translation_notice.last_notified(lang)

    # Nothing was written outside the state dir, under any name.
    for name in os.listdir(str(config_dir)):
        assert name.startswith("cwa_translation_notice_"), name
    assert os.listdir(str(legacy_dir)) == []


def test_odd_locales_stay_inside_the_state_dir(config_dir):
    for lang in ("de", "pt_BR", "../../etc/passwd"):
        path = translation_notice.translation_notice_file(lang)
        assert os.path.dirname(path) == str(config_dir), path


def test_config_dir_follows_calibre_dbpath(tmp_path, monkeypatch):
    """The state dir is the app's configured one, not a hard-coded /config."""
    monkeypatch.setitem(
        sys.modules,
        "cps.constants",
        types.SimpleNamespace(CONFIG_DIR=str(tmp_path)),
    )
    assert translation_notice._config_dir() == str(tmp_path)


# --- source pins -----------------------------------------------------------


def _module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_callsite_does_not_write_the_marker_under_app():
    """The callsite may not hard-code the old /app path (the bug)."""
    src = _module_source("cps/render_template.py")
    assert "/app/cwa_translation_notice" not in src
    assert 'f"/app/cwa_translation_notice_{lang}"' not in src


def test_callsite_uses_the_single_source_of_truth():
    """render_template resolves the marker here rather than rebuilding it."""
    tree = ast.parse(_module_source("cps/render_template.py"))
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module in ("cps.translation_notice", "translation_notice")
        and any(a.name in ("last_notified", "record_notified") for a in node.names)
        for node in ast.walk(tree)
    )
    assert imported, "render_template must import the notice helpers"

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "last_notified" in called
    assert "record_notified" in called


def test_po_lookup_is_contained_under_translations_dir():
    """The locale is a path segment, and it is not a trusted value.

    ``get_locale()`` returns ``current_user.locale`` verbatim (cw_babel.py) and
    the self-service profile route stores it without checking it against the
    shipped locales, so a crafted value reaches this join. ``basename()`` alone
    is not enough: ``basename("../../..")`` is ``".."``, which still escapes one
    level. The callsite must require a direct child of TRANSLATIONS_DIR.
    """
    translations_dir = "/app/calibre-web-automated/cps/translations"

    def resolve(lang):
        return os.path.normpath(os.path.join(translations_dir, lang))

    # Values that must be rejected — each resolves outside TRANSLATIONS_DIR.
    for lang in ("../../../../etc", "../../..", "..", "de/../..", "/etc"):
        assert os.path.dirname(resolve(lang)) != translations_dir, (
            "{!r} would need rejecting".format(lang)
        )

    # ...and a real locale must still be accepted.
    assert os.path.dirname(resolve("de")) == translations_dir
    assert os.path.dirname(resolve("pt_BR")) == translations_dir

    # The callsite performs exactly this check rather than trusting the value.
    src = _module_source("cps/render_template.py")
    assert "os.path.dirname(locale_dir) != translations_dir" in src


def test_po_lookup_is_not_cwd_relative():
    """The .po path must not depend on the process working directory.

    It resolved only because the s6 service happens to `cd
    /app/calibre-web-automated` first; anywhere else the file was never found,
    so the notice silently never fired.
    """
    src = _module_source("cps/render_template.py")
    assert 'f"cps/translations/{lang}/LC_MESSAGES/messages.po"' not in src
    assert "TRANSLATIONS_DIR" in src
