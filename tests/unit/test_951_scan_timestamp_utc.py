# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""cwa_duplicate_cache.scan_timestamp must be written in UTC (#951).

Found while fixing #944. `cwa_duplicate_cache.scan_timestamp` had the identical
shape to the bug #944 hit: the column's schema default is UTC
(`DATETIME DEFAULT CURRENT_TIMESTAMP`, which SQLite evaluates in UTC) while all
four writers stamped a naive *local* `datetime.now().isoformat()`. The column
therefore held a mix of time bases — UTC on the seed row, local on every update.

It was latent, not user-visible, only because no Python consumer parses
scan_timestamp and compares it against a datetime (unlike #944, where the
cooldown comparison existed and auto-resolution silently never ran). The moment
someone adds a "last scanned N minutes ago" staleness check, it reproduces — and
it would be invisible in CI, which runs in UTC where local == UTC.

The fix unifies on UTC by stamping the SQL-native `CURRENT_TIMESTAMP` at every
write path (the singleton row's seed INSERT already used it), so the column is
UTC by construction and there is no second, drift-prone Python copy of the time
base. cwa_duplicate_cache is a single overwritten row (id = 1), so no migration
is needed — the next scan replaces the row.

These tests force a non-UTC zone: in UTC (the CI default) local == UTC and the
bug is invisible.
"""

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_CACHE_WRITER_FILES = (
    REPO_ROOT / "scripts" / "cwa_db.py",
    REPO_ROOT / "cps" / "duplicate_index.py",
    REPO_ROOT / "cps" / "tasks" / "duplicate_scan.py",
)


@pytest.fixture
def tz_new_york():
    """Force a west-of-UTC zone so the local/UTC skew is observable.

    In UTC (the CI default) local == UTC and the #951 timezone mix is invisible.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    # test_duplicates_timezone installs a stub `cwa_db` module into sys.modules
    # and never removes it. Drop any stub (a real module has __file__) so this
    # test imports the real CWA_DB regardless of collection order.
    stub = sys.modules.get("cwa_db")
    if stub is not None and not getattr(stub, "__file__", None):
        del sys.modules["cwa_db"]
    from cwa_db import CWA_DB

    return CWA_DB(verbose=False)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _stored_scan_timestamp(db):
    return db.cur.execute(
        "SELECT scan_timestamp FROM cwa_duplicate_cache WHERE id = 1"
    ).fetchone()[0]


@pytest.mark.unit
class TestScanTimestampWrittenUTC:
    """A row written 'now' must read back as UTC-now, not offset by the TZ."""

    def test_full_scan_write_stamps_utc(self, tmp_path, monkeypatch, tz_new_york):
        """update_duplicate_cache (no max_book_id branch) must stamp UTC.

        On main this stamps local time; read back as UTC (the storage
        convention) it reads ~4h off in EDT, which is exactly the latent skew.
        """
        db = _fresh_db(tmp_path, monkeypatch)
        db.update_duplicate_cache([], 0)

        stored = _stored_scan_timestamp(db)
        last = datetime.fromisoformat(stored).replace(tzinfo=timezone.utc)
        skew = abs((datetime.now(timezone.utc) - last).total_seconds())

        assert skew < 60, (
            f"scan_timestamp written now reads {skew / 60:.0f} min off UTC "
            f"(stored={stored!r}) — the writer stamped naive local time instead "
            f"of UTC, mixing time bases in the column (#951)"
        )

    def test_incremental_scan_write_stamps_utc(
        self, tmp_path, monkeypatch, tz_new_york
    ):
        """update_duplicate_cache (max_book_id branch) must stamp UTC too."""
        db = _fresh_db(tmp_path, monkeypatch)
        db.update_duplicate_cache([], 0, max_book_id=7)

        stored = _stored_scan_timestamp(db)
        last = datetime.fromisoformat(stored).replace(tzinfo=timezone.utc)
        skew = abs((datetime.now(timezone.utc) - last).total_seconds())

        assert skew < 60, (
            f"incremental scan_timestamp reads {skew / 60:.0f} min off UTC "
            f"(stored={stored!r})"
        )

    def test_stamp_is_parseable(self, tmp_path, monkeypatch):
        db = _fresh_db(tmp_path, monkeypatch)
        db.update_duplicate_cache([], 0)
        stored = _stored_scan_timestamp(db)

        # Whatever the shape, a future reader parses it with fromisoformat.
        assert datetime.fromisoformat(stored) is not None
        # CURRENT_TIMESTAMP yields 'YYYY-MM-DD HH:MM:SS'.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stored), (
            f"unexpected scan_timestamp shape {stored!r}"
        )


@pytest.mark.unit
class TestCacheWritersHaveOneUTCTimeBase:
    """Root cause: four writers each carried their own naive-local time base.

    Aligning them by hand would fix the symptom and leave the drift class intact
    — the next writer added would reintroduce it. Stamping the SQL-native
    CURRENT_TIMESTAMP at every write removes the second copy of the time base
    entirely, so there is nothing left to drift.
    """

    def test_no_writer_stamps_naive_local_time(self):
        for path in _CACHE_WRITER_FILES:
            src = _read(path)
            # The exact pre-fix pattern: a bound scan_timestamp fed local time.
            assert not re.search(
                r"scan_timestamp\s*=\s*\?[^)]*?datetime\.now\(\)\.isoformat\(\)",
                src,
                flags=re.DOTALL,
            ), (
                f"{path.name} stamps scan_timestamp with naive local "
                f"datetime.now() — mixes time bases in the column (#951); use "
                f"SQL CURRENT_TIMESTAMP so the value is UTC by construction"
            )

    def test_every_writer_uses_current_timestamp(self):
        total = sum(
            _read(path).count("scan_timestamp = CURRENT_TIMESTAMP")
            for path in _CACHE_WRITER_FILES
        )
        assert total == 4, (
            f"expected all 4 cwa_duplicate_cache writers to stamp "
            f"scan_timestamp = CURRENT_TIMESTAMP, found {total} — a writer "
            f"drifted back to a local time base (#951)"
        )

    def test_schema_default_is_still_utc(self):
        schema = _read(REPO_ROOT / "scripts" / "cwa_schema.sql")
        assert re.search(
            r"scan_timestamp\s+DATETIME\s+DEFAULT\s+CURRENT_TIMESTAMP",
            schema,
        ), "scan_timestamp schema default must remain CURRENT_TIMESTAMP (UTC)"
