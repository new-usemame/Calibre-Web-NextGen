# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #992.

The per-user duplicate-scan setup-notice dismissal marker was written to
root-owned ``/app`` and failed with EACCES (a 500) on stock containers, so the
notice could never be dismissed. The marker now lives in the application's
configured state directory (``CONFIG_DIR`` — ``/config`` in the image,
``CALIBRE_DBPATH`` elsewhere), the path is defined in one place
(``cps.duplicate_notice``) so the write side (``cps.duplicates``) and the read
side (``cps.render_template``) cannot drift, and a dismissal recorded under the
old ``/app`` path is still honoured.
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


duplicate_notice = _load_module("cps.duplicate_notice", "cps/duplicate_notice.py")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point the helper at a real, writable state directory."""
    state = tmp_path / "config"
    state.mkdir()
    monkeypatch.setattr(duplicate_notice, "_config_dir", lambda: str(state))
    return state


@pytest.fixture
def legacy_dir(tmp_path, monkeypatch):
    """Point the read-only /app fallback at a real directory."""
    legacy = tmp_path / "app"
    legacy.mkdir()
    monkeypatch.setattr(duplicate_notice, "LEGACY_NOTICE_DIR", str(legacy))
    return legacy


# --- behaviour -------------------------------------------------------------


def test_dismissal_round_trip(config_dir, legacy_dir):
    """The path the endpoint writes is the path the notice reads back."""
    assert duplicate_notice.duplicate_setup_notice_dismissed(7) is False

    # This is exactly what the dismiss endpoint does.
    with open(duplicate_notice.duplicate_setup_notice_file(7), "w") as handle:
        handle.write("dismissed\n")

    assert duplicate_notice.duplicate_setup_notice_dismissed(7) is True
    # Per-user: dismissing for one user must not silence it for another.
    assert duplicate_notice.duplicate_setup_notice_dismissed(8) is False


def test_marker_is_written_inside_the_configured_state_dir(config_dir):
    path = duplicate_notice.duplicate_setup_notice_file(7)
    assert os.path.dirname(path) == str(config_dir)
    assert not path.startswith("/app")


def test_legacy_app_dismissal_is_still_honoured(config_dir, legacy_dir):
    """Users who dismissed under the old /app path don't get the notice back."""
    (legacy_dir / "cwa_duplicate_index_setup_notice_7").write_text("dismissed\n")

    assert duplicate_notice.duplicate_setup_notice_dismissed(7) is True
    # ...and nothing new is written to the legacy location.
    assert not (config_dir / "cwa_duplicate_index_setup_notice_7").exists()


def test_anonymous_sentinel_and_odd_ids_stay_inside_the_state_dir(config_dir):
    for user_id in ("unknown", 7, "../../etc/passwd"):
        path = duplicate_notice.duplicate_setup_notice_file(user_id)
        assert os.path.dirname(path) == str(config_dir), path


def test_config_dir_follows_calibre_dbpath(tmp_path, monkeypatch):
    """The state dir is the app's configured one, not a hard-coded /config."""
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    constants = _load_module("cps.constants_reload_probe", "cps/constants.py")
    assert constants.CONFIG_DIR == str(tmp_path)
    # ...and the helper resolves through cps.constants rather than a literal.
    monkeypatch.setitem(
        sys.modules,
        "cps.constants",
        types.SimpleNamespace(CONFIG_DIR=str(tmp_path)),
    )
    assert duplicate_notice._config_dir() == str(tmp_path)


# --- source pins -----------------------------------------------------------


def _module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_no_callsite_writes_the_marker_under_app():
    """Neither callsite may hard-code the old root-owned /app path (the bug)."""
    for rel in ("cps/duplicates.py", "cps/render_template.py"):
        src = _module_source(rel)
        assert "/app/cwa_duplicate_index_setup_notice" not in src, rel


def test_both_callsites_use_the_single_source_of_truth():
    """Write side resolves the path here; read side uses the predicate here."""
    expected = {
        "cps/duplicates.py": "duplicate_setup_notice_file",
        "cps/render_template.py": "duplicate_setup_notice_dismissed",
    }
    for rel, name in expected.items():
        tree = ast.parse(_module_source(rel))
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module in ("cps.duplicate_notice", "duplicate_notice")
            and any(a.name == name for a in node.names)
            for node in ast.walk(tree)
        )
        called = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
            for node in ast.walk(tree)
        )
        assert imported, "{} must import {}".format(rel, name)
        assert called, "{} must call {}".format(rel, name)


def test_read_side_does_not_stat_the_marker_itself():
    """render_template must not re-implement the lookup and skip the fallback."""
    src = _module_source("cps/render_template.py")
    assert "cwa_duplicate_index_setup_notice" not in src
