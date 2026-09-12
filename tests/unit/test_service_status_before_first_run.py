# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Service status endpoints on an install where the service never ran.

Convert Library and the EPUB Fixer each write their log only on their first
run, so on a fresh install the file is simply absent. Both status endpoints
read it on every poll of their admin page, and the read was unguarded:

    FileNotFoundError: [Errno 2] No such file or directory:
        '/config/convert-library.log'

which Flask turned into a 500 plus a traceback in the container log, repeating
for as long as the page stayed open. Observed on a real install immediately
after migrating from upstream CWA, where Convert Library had never been run.

These pin the read helper rather than the routes, since the routes need an
app context: a missing log reads as empty, an unreadable one degrades the same
way, a present one is returned intact, and the "finished" predicates answer
False instead of raising.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (REPO_ROOT / "cps" / "cwa_functions.py").read_text()


def _read_service_log(tmp_path, filename, contents=None, mode=None):
    """Exercise the real helper body against a temp config dir."""
    if contents is not None:
        target = tmp_path / filename
        target.write_text(contents)
        if mode is not None:
            target.chmod(mode)

    def _service_log_path(name):
        return str(tmp_path / name)

    # The helper is small and has no imports of its own; run its real source
    # so this test tracks the shipped implementation rather than a copy.
    body = re.search(r"def _read_service_log\(filename: str\) -> str:.*?\n(?=\n\S|\ndef )",
                     SRC, re.S)
    assert body, "_read_service_log not found in cwa_functions.py"
    namespace = {"_service_log_path": _service_log_path}
    exec(compile(body.group(0), "cwa_functions.py", "exec"), namespace)
    return namespace["_read_service_log"](filename)


def test_missing_log_reads_as_empty(tmp_path):
    assert _read_service_log(tmp_path, "convert-library.log") == ""


def test_missing_epub_fixer_log_reads_as_empty(tmp_path):
    assert _read_service_log(tmp_path, "epub-fixer.log") == ""


def test_existing_log_is_returned_intact(tmp_path):
    assert _read_service_log(tmp_path, "convert-library.log", "3/10 converted\n") == "3/10 converted\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions, so the log stays readable")
def test_unreadable_log_degrades_to_empty(tmp_path):
    # A log left unreadable (e.g. after a PUID/PGID change) must not 500 either.
    assert _read_service_log(tmp_path, "convert-library.log", "x", mode=0o000) == ""


def test_empty_status_yields_zero_progress():
    # What the status endpoint does with the empty string the helper returns.
    namespace = {"re": re}
    body = re.search(r"def extract_progress\(log_content\):.*?\n(?=\n\S|\ndef )", SRC, re.S)
    assert body, "extract_progress not found"
    exec(compile(body.group(0), "cwa_functions.py", "exec"), namespace)
    assert namespace["extract_progress"]("") == {"current": 0, "total": 0}


def test_status_routes_no_longer_open_the_log_directly():
    # Guard against the unguarded open() coming back on either service.
    for route in ("convert-library-status", "epub-fixer-status"):
        m = re.search(rf"@\w+\.route\('/{route}'.*?\ndef get_status\(\):(.*?)return json\.dumps",
                      SRC, re.S)
        assert m, f"{route} handler not found"
        assert "open(" not in m.group(1), (
            f"/{route} must read its log through _read_service_log so a service "
            "that has never run returns empty instead of raising"
        )


def test_finished_predicates_use_the_guarded_read():
    for fn in ("is_convert_library_finished", "is_epub_fixer_finished"):
        m = re.search(rf"def {fn}\(\).*?\n(?=\n\S|\ndef )", SRC, re.S)
        assert m, f"{fn} not found"
        assert "_read_service_log(" in m.group(0), f"{fn} must use the guarded read"
        assert "open(" not in m.group(0), f"{fn} must not open the log directly"
