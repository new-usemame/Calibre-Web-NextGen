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
import os
import re
import shutil
import subprocess
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


# --- fuzzy entries must not count as translated -------------------------
#
# A fuzzy entry carries a non-empty msgstr but msgfmt drops it from the .mo,
# so the user sees English. Counting it as translated made the published
# README table overstate 27 of 28 locales by 4.7-16.8 points.


def _write_po(tmp_path, body):
    po_dir = tmp_path / "cps" / "translations" / "xx" / "LC_MESSAGES"
    po_dir.mkdir(parents=True)
    po = po_dir / "messages.po"
    header = (
        'msgid ""\nmsgstr ""\n'
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: 8bit\\n"\n\n'
    )
    po.write_text(header + body, encoding="utf-8")
    return po


def test_fuzzy_entry_is_not_counted_as_translated(tmp_path, monkeypatch):
    """One real, one fuzzy, one empty -> 1/3 translated, not 2/3.

    Fails before the fix with translated=2 / percent=66.7.
    """
    _write_po(
        tmp_path,
        'msgid "real"\nmsgstr "shipped"\n\n'
        '#, fuzzy\nmsgid "guessed"\nmsgstr "not shipped"\n\n'
        'msgid "empty"\nmsgstr ""\n',
    )
    monkeypatch.setattr(mod, "ROOT_DIR", tmp_path)
    (lang, _name, total, translated, fuzzy, percent) = mod.collect_stats()[0]
    assert (lang, total, translated, fuzzy) == ("xx", 3, 1, 1)
    assert percent == pytest.approx(33.3)


def test_published_counts_match_msgfmt_for_every_shipped_locale():
    """The table must agree with the compiler for every locale we ship.

    msgfmt is the ground truth for what reaches a user: its "N translated"
    excludes fuzzy. Pinning to it means the published percentage can never
    drift from what the build actually compiles.
    """
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        pytest.skip("gettext msgfmt not available")

    stats = {row[0]: row for row in mod.collect_stats()}
    assert stats, "no locales collected"

    mismatches = []
    for lang, row in sorted(stats.items()):
        po_path = (
            REPO_ROOT / "cps" / "translations" / lang / "LC_MESSAGES" / "messages.po"
        )
        proc = subprocess.run(
            [msgfmt, "--statistics", "-o", os.devnull, str(po_path)],
            capture_output=True,
            text=True,
        )
        match = re.search(r"(\d+) translated message", proc.stderr)
        assert match, f"could not read msgfmt statistics for {lang}: {proc.stderr!r}"
        if int(match.group(1)) != row[3]:
            mismatches.append(f"{lang}: msgfmt={match.group(1)} table={row[3]}")

    assert not mismatches, "published table disagrees with msgfmt: " + ", ".join(
        mismatches
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
