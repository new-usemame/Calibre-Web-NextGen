# SPDX-License-Identifier: GPL-3.0-or-later
"""A support pack must never report "0" for data it could not read (fork issue #2728).

A locked or unreadable table used to come out as "0 devices" or "0 pending", so a
pack could say everything was fine when it had read nothing. That is the worst
outcome for a debugging tool. These tests pin that unreadable data is reported as
unavailable, with a reason, and that the device list trims itself to its size cap
without losing the summary that always covers every device.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from cps import support_pack_integrations as integ
from cps import support_pack_state as sps

pytestmark = pytest.mark.unit


def _db(path, statements):
    conn = sqlite3.connect(path)
    for stmt in statements:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return str(path)


DEVICE_TABLE = ("CREATE TABLE device (id INTEGER PRIMARY KEY, public_id TEXT, user_id INTEGER, kind TEXT, "
                "model TEXT, platform TEXT, firmware_version TEXT, active BOOLEAN, created_by TEXT, "
                "first_seen_at DATETIME, last_seen_at DATETIME, share_place BOOLEAN, align_percent BOOLEAN, "
                "cwng_header_seen_at DATETIME)")
POSITIONS_TABLE = ("CREATE TABLE device_reading_position (id INTEGER PRIMARY KEY, device_id INTEGER, "
                   "rehydrate_needed BOOLEAN)")
NUDGE_TABLE = "CREATE TABLE kobo_share_place_nudge (id INTEGER PRIMARY KEY, device_id INTEGER)"


class TestReasons:
    @pytest.mark.parametrize("message, expected", [
        ("database is locked", "database locked"),
        ("database table is locked: device", "database locked"),
        ("no such table: device_reading_position", "table missing"),
        ("no such column: share_place", "column missing"),
        ("disk I/O error at /mnt/secret-share/app.db", "OperationalError"),
    ])
    def test_a_short_safe_reason_never_the_message(self, message, expected):
        reason = integ._reason(sqlite3.OperationalError(message))
        assert reason == expected
        assert "secret-share" not in reason  # messages can quote paths; the reason never does

    def test_other_errors_report_their_type(self):
        assert integ._reason(sqlite3.DatabaseError("x")) == "DatabaseError"


class TestHelpers:
    def test_columns_distinguishes_missing_from_unreadable(self, tmp_path):
        conn = sqlite3.connect(_db(tmp_path / "a.db", ["CREATE TABLE t (a, b)"]))
        assert integ._columns(conn, "t") == {"a", "b"}
        assert integ._columns(conn, "absent") == set()  # genuinely not there
        conn.close()
        broken = integ._columns(conn, "t")  # unreadable: the connection is closed
        assert isinstance(broken, integ.Unavailable) and broken.reason == "ProgrammingError"

    def test_grouped_distinguishes_no_rows_from_unreadable(self, tmp_path):
        conn = sqlite3.connect(_db(tmp_path / "a.db", ["CREATE TABLE t (k, n)"]))
        assert integ._grouped(conn, "SELECT k, COUNT(*) FROM t GROUP BY k") == {}  # no rows: empty, fine
        missing = integ._grouped(conn, "SELECT k, COUNT(*) FROM absent GROUP BY k")
        assert isinstance(missing, integ.Unavailable) and missing.reason == "table missing"


class TestDevicesReportUnavailableNotZero:
    def _seeded(self, tmp_path, with_tables=True):
        statements = [DEVICE_TABLE,
                      "INSERT INTO device(id, public_id, user_id, kind, model, platform, active) "
                      "VALUES (1, 'p1', 7, 'kobo', 'Kobo Clara BW', 'nickel', 1)"]
        if with_tables:
            statements += [POSITIONS_TABLE, NUDGE_TABLE,
                           "INSERT INTO device_reading_position(device_id, rehydrate_needed) VALUES (1, 1), (1, 0)"]
        return _db(tmp_path / "app.db", statements)

    def test_readable_data_is_counted_and_nothing_is_flagged(self, tmp_path):
        out = integ.collect_devices(self._seeded(tmp_path))
        device = out["devices"][0]
        assert device["reading_positions"] == 2 and device["rehydrate_pending"] == 1
        assert device["share_place_nudges_pending"] == 0  # a real zero: the table is there and empty
        assert "unavailable" not in out

    def test_a_missing_table_is_unavailable_with_null_values_not_zero(self, tmp_path):
        out = integ.collect_devices(self._seeded(tmp_path, with_tables=False))
        device = out["devices"][0]
        assert device["reading_positions"] is None and device["rehydrate_pending"] is None
        assert device["share_place_nudges_pending"] is None
        assert out["unavailable"] == {"reading_positions": "table missing", "rehydrate_pending": "table missing",
                                      "share_place_nudges_pending": "table missing"}

    def test_a_failing_kind_breakdown_is_unavailable_but_the_total_survives(self, tmp_path, monkeypatch):
        real = integ._grouped

        def flaky(conn, sql):
            return integ.Unavailable("database locked") if "GROUP BY kind" in sql else real(conn, sql)

        monkeypatch.setattr(integ, "_grouped", flaky)
        out = integ.collect_devices(self._seeded(tmp_path))
        assert out["summary"]["by_kind"] is None and out["summary"]["total"] == 1
        assert out["unavailable"]["by_kind"] == "database locked"

    def test_a_locked_database_reports_unavailable_never_zero_devices(self, tmp_path):
        path = self._seeded(tmp_path)
        writer = sqlite3.connect(path, isolation_level=None)
        writer.execute("BEGIN EXCLUSIVE")  # a rollback-journal DB: readers are blocked
        try:
            began = time.monotonic()
            out = integ.collect_devices(path)
            waited = time.monotonic() - began
        finally:
            writer.execute("ROLLBACK")
            writer.close()
        assert out == {"exists": True, "unavailable": "database locked"}
        assert waited < 10  # it gave up (the connection timeout is 2s) rather than hanging the pack

    def test_an_unreadable_section_is_not_marked_ok_in_the_manifest(self, tmp_path):
        path = self._seeded(tmp_path, with_tables=False)
        files = sps.collect_all({"app_db": path}, sanitized=True,
                                scheduler=type("S", (), {"get_jobs": lambda self: []})())
        ok = {s["file"]: s["ok"] for s in json.loads(files["manifest.json"])["sections"]}
        assert ok["state/devices.json"] is False


class TestOtherSections:
    def test_koreader_unreadable_table_is_unavailable_not_missing(self, tmp_path, monkeypatch):
        path = _db(tmp_path / "app.db", ["CREATE TABLE kosync_progress (id INTEGER)"])
        monkeypatch.setattr(integ, "_columns", lambda conn, table: integ.Unavailable("database locked"))
        out = integ.collect_koreader(path, True)
        assert out == {"exists": True, "unavailable": "database locked", "sync_enabled": True}

    def test_koreader_absent_table_is_still_reported_as_absent(self, tmp_path):
        path = _db(tmp_path / "app.db", ["CREATE TABLE other (id INTEGER)"])
        assert integ.collect_koreader(path, False) == {"exists": True, "kosync_table": False, "sync_enabled": False}

    def test_account_counts_unavailable_instead_of_zero(self, tmp_path):
        conn = sqlite3.connect(_db(tmp_path / "app.db", ['CREATE TABLE "user" (id INTEGER, kindle_mail TEXT)']))
        conn.close()
        assert integ._account_counts(conn) == {"exists": True, "unavailable": "ProgrammingError"}

    def test_email_section_carries_the_unavailable_users_marker(self, tmp_path, monkeypatch):
        path = _db(tmp_path / "app.db", ['CREATE TABLE "user" (id INTEGER)'])
        monkeypatch.setattr(integ, "_columns", lambda conn, table: integ.Unavailable("database locked"))
        assert integ.collect_email(None, None, path)["users"] == {"exists": True, "unavailable": "database locked"}


class TestDeviceListFitsItsSizeCapKeepingTheSummary:
    def _many(self, tmp_path, count):
        statements = [DEVICE_TABLE]
        statements += [
            f"INSERT INTO device(id, public_id, user_id, kind, model, platform, firmware_version, active, "
            f"created_by, first_seen_at, last_seen_at, share_place) VALUES ({i}, 'pub-{i}', {i % 7}, 'kobo', "
            f"'Kobo Clara BW', 'nickel', '4.38.23171', 1, 'auto', '2026-10-01 08:00:00', '2026-11-18 21:03:44', 1)"
            for i in range(1, count + 1)
        ]
        return _db(tmp_path / "app.db", statements)

    def test_two_hundred_devices_really_do_exceed_the_cap_and_are_trimmed_not_replaced(self, tmp_path):
        path = self._many(tmp_path, 200)
        out = integ.collect_devices(path)
        size = len(json.dumps(out, sort_keys=True, indent=2).encode())
        assert size <= sps.MAX_SECTION_BYTES
        summary = out["summary"]
        assert summary["total"] == 200 and summary["share_place_enabled"] == 200 and summary["by_kind"] == {"kobo": 200}
        assert 0 < summary["listed"] < 200 and summary["listed"] == len(out["devices"])
        assert summary["truncated"] is True and summary["trimmed_for_size"] is True

    def test_through_the_pack_the_summary_survives_instead_of_a_truncation_marker(self, tmp_path):
        files = sps.collect_all({"app_db": self._many(tmp_path, 200)}, sanitized=True,
                                scheduler=type("S", (), {"get_jobs": lambda self: []})())
        body = json.loads(files["state/devices.json"])
        assert "truncated" not in body and body["summary"]["total"] == 200  # not the generic marker

    def test_a_small_list_is_untouched(self, tmp_path):
        out = integ.collect_devices(self._many(tmp_path, 5))
        assert out["summary"]["listed"] == 5 and out["summary"]["truncated"] is False
        assert "trimmed_for_size" not in out["summary"]

    def test_the_cap_is_applied_to_bytes_not_just_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sps, "MAX_SECTION_BYTES", 4000)
        out = integ.collect_devices(self._many(tmp_path, 30))
        assert len(json.dumps(out, sort_keys=True, indent=2).encode()) <= 4000
        assert 0 < out["summary"]["listed"] < 30 and out["summary"]["total"] == 30
