# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Security regressions for numeric settings read by the ingest s6 unit.

SQLite's declared ``INTEGER`` type is not a shell trust boundary: an existing
database can contain text, and Bash recursively evaluates variable contents in
``$(( ... ))``.  Every value returned from ``cwa.db`` must therefore be
validated before arithmetic, ``[ ... -lt ... ]``, ``find -mmin``, or ``sleep``
sees it.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = (
    REPO_ROOT
    / "root"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "cwa-ingest-service"
    / "run"
)


def _run_db_getters(tmp_path: Path, *, timeout: str, minutes: str, interval: str):
    """Source the real unit with a sqlite3 stub, then call all three getters."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sqlite = bin_dir / "sqlite3"
    sqlite.write_text(
        """#!/usr/bin/env bash
case "$*" in
  *ingest_timeout_minutes*) printf '%s\\n' "$CWA_TEST_TIMEOUT_VALUE" ;;
  *ingest_stale_temp_minutes*) printf '%s\\n' "$CWA_TEST_STALE_MINUTES_VALUE" ;;
  *ingest_stale_temp_interval*) printf '%s\\n' "$CWA_TEST_STALE_INTERVAL_VALUE" ;;
  *) exit 1 ;;
esac
""",
        encoding="utf-8",
    )
    sqlite.chmod(0o755)

    watch = tmp_path / "watch"
    watch.mkdir()
    sentinel = tmp_path / "arithmetic-evaluated"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "WATCH_FOLDER": str(watch),
        "CWA_INGEST_SERVICE_TEST_MODE": "1",
        "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry-queue"),
        "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
        "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
        "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
        "CWA_TEST_TIMEOUT_VALUE": timeout,
        "CWA_TEST_STALE_MINUTES_VALUE": minutes,
        "CWA_TEST_STALE_INTERVAL_VALUE": interval,
        "CWA_TEST_ARITHMETIC_SENTINEL": str(sentinel),
    }
    script = textwrap.dedent(
        f"""
        set -uo pipefail
        source "{RUN_SCRIPT}" >/dev/null
        printf 'timeout=%s\\n' "$(get_timeout_from_db)"
        printf 'minutes=%s\\n' "$(get_stale_temp_minutes_from_db)"
        printf 'interval=%s\\n' "$(get_stale_temp_interval_from_db)"
        """
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, sentinel


def test_non_numeric_db_values_use_documented_defaults_without_evaluation(tmp_path):
    """RED before F-fc734f: timeout payload executes inside ``$(( ... ))``."""
    arithmetic_payload = 'slot[$(touch "$CWA_TEST_ARITHMETIC_SENTINEL")]'

    result, sentinel = _run_db_getters(
        tmp_path,
        timeout=arithmetic_payload,
        minutes="not-a-number",
        interval="600 seconds",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "timeout=900",
        "minutes=120",
        "interval=600",
    ]
    assert not sentinel.exists(), (
        "Bash evaluated the DB-sourced timeout as shell arithmetic; validation "
        "must happen before the first arithmetic expansion"
    )


def test_valid_non_negative_db_values_are_decimal_normalized(tmp_path):
    """Leading zeroes are valid input but must not acquire Bash octal meaning."""
    result, sentinel = _run_db_getters(
        tmp_path,
        timeout="00015",
        minutes="0",
        interval="00600",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "timeout=900",
        "minutes=0",
        "interval=600",
    ]
    assert not sentinel.exists()
