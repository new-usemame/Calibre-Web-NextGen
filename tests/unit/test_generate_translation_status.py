# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit tests for scripts/generate_translation_status.py.

Regression guard for the chronic `Update Translations` CI failure: the
wiki-status generator crashed with FileNotFoundError when handed a path
that does not exist yet (the `Contributing-Translations` wiki page was
consolidated away). `update_between_markers` must return False on a
missing path instead of raising, so main()'s seed fallback can run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_translation_status.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "generate_translation_status", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
START = mod.START_MARKER
END = mod.END_MARKER


def test_missing_path_returns_false_without_raising(tmp_path):
    """The regression: a non-existent target must not raise
    FileNotFoundError — that was the chronic CI crash."""
    missing = tmp_path / "does-not-exist.md"
    assert missing.exists() is False
    assert mod.update_between_markers(missing, "body") is False
    # And it must not have created the file as a side effect.
    assert missing.exists() is False


def test_replaces_content_between_markers(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        f"intro\n{START}\nOLD TABLE\n{END}\noutro\n", encoding="utf-8"
    )
    changed = mod.update_between_markers(page, "NEW TABLE")
    assert changed is True
    text = page.read_text(encoding="utf-8")
    assert "NEW TABLE" in text
    assert "OLD TABLE" not in text
    # Surrounding content is preserved.
    assert text.startswith("intro\n")
    assert text.rstrip().endswith("outro")


def test_unchanged_content_returns_false(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(f"{START}\nSAME\n{END}\n", encoding="utf-8")
    assert mod.update_between_markers(page, "SAME") is False


def test_no_markers_returns_false_no_write(tmp_path):
    page = tmp_path / "page.md"
    original = "no markers here\n"
    page.write_text(original, encoding="utf-8")
    assert mod.update_between_markers(page, "body") is False
    assert page.read_text(encoding="utf-8") == original


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
