# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #992.

The per-user duplicate-scan setup-notice dismissal marker was written to
root-owned ``/app`` and failed with EACCES (a 500) on stock containers, so the
notice could never be dismissed. The marker now lives on the writable,
persistent ``/config`` volume, and the path is defined in one place
(``cps.duplicate_notice``) so the write side (``cps.duplicates``) and the read
side (``cps.render_template``) cannot drift.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_duplicate_notice_module():
    """Load cps/duplicate_notice.py without triggering the heavy package init."""
    module_path = REPO_ROOT / "cps" / "duplicate_notice.py"
    if "cps" not in sys.modules:
        cps_pkg = types.ModuleType("cps")
        cps_pkg.__path__ = [str(REPO_ROOT / "cps")]
        sys.modules["cps"] = cps_pkg
    spec = importlib.util.spec_from_file_location(
        "cps.duplicate_notice", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["cps.duplicate_notice"] = module
    spec.loader.exec_module(module)
    return module


duplicate_notice = _load_duplicate_notice_module()


def test_marker_lives_under_config_not_app():
    """The marker must live on the writable /config volume, never root-owned /app."""
    path = duplicate_notice.duplicate_setup_notice_file(7)
    assert path.startswith("/config/"), path
    assert not path.startswith("/app"), path


def test_marker_path_is_stable_and_per_user():
    assert (
        duplicate_notice.duplicate_setup_notice_file(7)
        == "/config/cwa_duplicate_index_setup_notice_7"
    )
    # Distinct users get distinct markers; the anonymous sentinel is accepted.
    assert duplicate_notice.duplicate_setup_notice_file(7) != (
        duplicate_notice.duplicate_setup_notice_file(8)
    )
    assert duplicate_notice.duplicate_setup_notice_file("unknown").endswith(
        "cwa_duplicate_index_setup_notice_unknown"
    )


def _module_source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_no_callsite_writes_the_marker_under_app():
    """Neither callsite may hard-code the old root-owned /app path (the bug)."""
    for rel in ("cps/duplicates.py", "cps/render_template.py"):
        src = _module_source(rel)
        assert "/app/cwa_duplicate_index_setup_notice" not in src, rel


def test_both_callsites_use_the_single_source_of_truth():
    """Write and read sides both resolve the path via the shared helper."""
    for rel in ("cps/duplicates.py", "cps/render_template.py"):
        tree = ast.parse(_module_source(rel))
        imported = any(
            isinstance(node, ast.ImportFrom)
            and node.module in ("cps.duplicate_notice", "duplicate_notice")
            and any(a.name == "duplicate_setup_notice_file" for a in node.names)
            for node in ast.walk(tree)
        )
        called = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "duplicate_setup_notice_file"
            for node in ast.walk(tree)
        )
        assert imported, "{} must import duplicate_setup_notice_file".format(rel)
        assert called, "{} must call duplicate_setup_notice_file".format(rel)
