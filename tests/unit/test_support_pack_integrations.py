# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the support pack integration sections (fork issue #2728, PR 2).

Each section is checked two ways: it reports what a maintainer needs, and a
seeded canary (a device name, a public id, an email address, a document hash,
a reading position) never reaches the output. The end-to-end test then asserts
the same across every member of a built archive.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile

import pytest

from cps import support_pack_integrations as integ
from cps import support_pack_state as sps

DISPLAY_NAME = "Sams Secret Kobo"
PUBLIC_ID = "11111111-aaaa-bbbb-cccc-canarydevice1"
BOOK_SCOPE = '{"ids": [4242, 4343]}'
KINDLE_MAIL = "canary.reader@kindle.com"
KINDLE_SUBJECT = "canary subject line"
DOC_HASH = "deadbeefcanarydochash0000000000"
KOSYNC_DEVICE_NAME = "Canary KOReader Name"
KOSYNC_DEVICE_ID = "canary-kosync-device-id"
CFI = "epubcfi(/6/canary-position)"
SMTP_HOST = "smtp.canary-isp.example"
CANARIES = (DISPLAY_NAME, PUBLIC_ID, BOOK_SCOPE, KINDLE_MAIL, KINDLE_SUBJECT, DOC_HASH,
            KOSYNC_DEVICE_NAME, KOSYNC_DEVICE_ID, CFI, SMTP_HOST, "alice")

SCHEMA = [
    'CREATE TABLE "user" (id INTEGER PRIMARY KEY, name TEXT, kindle_mail TEXT, kindle_mail_subject TEXT)',
    "CREATE TABLE device (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT, user_id INTEGER, "
    "kind TEXT, display_name TEXT, model TEXT, platform TEXT, firmware_version TEXT, "
    "first_seen_at DATETIME, last_seen_at DATETIME, active BOOLEAN, created_by TEXT, "
    "share_place BOOLEAN, align_percent BOOLEAN, cwng_header_seen_at DATETIME, book_scope TEXT)",
    "CREATE TABLE device_reading_position (id INTEGER PRIMARY KEY, device_id INTEGER, book_id INTEGER, "
    "cfi TEXT, location_value TEXT, rehydrate_needed BOOLEAN)",
    "CREATE TABLE kobo_share_place_nudge (id INTEGER PRIMARY KEY, device_id INTEGER, book_id INTEGER)",
    "CREATE TABLE kosync_progress (id INTEGER PRIMARY KEY, user_id INTEGER, document TEXT, progress TEXT, "
    "percentage REAL, device TEXT, device_id TEXT, timestamp TIMESTAMP, client_modified_at TIMESTAMP, "
    "web_cfi TEXT)",
]


def _seed(path, extra=()):
    conn = sqlite3.connect(path)
    for stmt in list(SCHEMA) + list(extra):
        conn.execute(stmt)
    conn.executemany('INSERT INTO "user"(id, name, kindle_mail, kindle_mail_subject) VALUES (?,?,?,?)', [
        (7, "alice", KINDLE_MAIL, KINDLE_SUBJECT),
        (9, "bob", KINDLE_MAIL, ""),
        (11, "carol", "", None),
    ])
    conn.executemany(
        "INSERT INTO device(public_id, user_id, kind, display_name, model, platform, firmware_version, "
        "first_seen_at, last_seen_at, active, created_by, share_place, align_percent, "
        "cwng_header_seen_at, book_scope) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (PUBLIC_ID, 7, "kobo", DISPLAY_NAME, "Kobo Clara BW", "Kobo", "4.38.23171",
             "2026-10-01 08:00:00.000000", "2026-11-18 21:03:44.123456", 1, "auto", 1, 0,
             "2026-11-10 00:00:00", BOOK_SCOPE),
            ("22222222-aaaa-bbbb-cccc-000000000002", 9, "kobo", "Other", "Kobo Libra 2", "Kobo", "4.38.1",
             "2026-10-02 08:00:00", "2026-10-05 10:00:00", 0, "auto", 0, 0, None, None),
            ("33333333-aaaa-bbbb-cccc-000000000003", 7, "webreader", "Browser", "alice@example.org",
             "/home/alice/browser", "Firefox 130", "2026-10-03 08:00:00", "2026-11-19 09:00:00", 1,
             "account-browser", 0, 0, None, None),
        ])
    conn.executemany(
        "INSERT INTO device_reading_position(device_id, book_id, cfi, location_value, rehydrate_needed) "
        "VALUES (?,?,?,?,?)", [(1, 10, CFI, CFI, 1), (1, 11, CFI, CFI, 1), (1, 12, CFI, CFI, 0)])
    conn.execute("INSERT INTO kobo_share_place_nudge(device_id, book_id) VALUES (1, 10)")
    conn.executemany(
        "INSERT INTO kosync_progress(user_id, document, progress, percentage, device, device_id, timestamp, "
        "client_modified_at, web_cfi) VALUES (?,?,?,?,?,?,?,?,?)", [
            (7, DOC_HASH, CFI, 0.5, KOSYNC_DEVICE_NAME, KOSYNC_DEVICE_ID, "2026-11-17 12:00:00", "2026-11-17 12:00:00", None),
            (7, DOC_HASH + "2", CFI, 0.1, KOSYNC_DEVICE_NAME, KOSYNC_DEVICE_ID, "2026-11-18 12:00:00", None, CFI),
            (9, DOC_HASH + "3", CFI, 0.9, "Legacy", None, "2026-11-01 12:00:00", None, None),
        ])
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def app_db(tmp_path):
    return _seed(tmp_path / "app.db")


@pytest.mark.unit
class TestDevices:
    def test_reports_device_facts_with_short_refs(self, app_db):
        out = integ.collect_devices(app_db)
        kobo = out["devices"][0]
        assert re.fullmatch(r"dev_[0-9a-f]{8}", kobo["ref"])
        assert kobo["ref"] == "dev_" + hashlib.sha256(PUBLIC_ID.encode()).hexdigest()[:8]
        assert (kobo["kind"], kobo["model"], kobo["platform"], kobo["firmware_version"]) == (
            "kobo", "Kobo Clara BW", "Kobo", "4.38.23171")
        assert kobo["active"] is True and kobo["share_place"] is True and kobo["align_percent"] is False
        assert kobo["cwng_header_seen"] is True
        assert kobo["last_seen_at"] == "2026-11-18T21:03:44Z"
        assert kobo["reading_positions"] == 3 and kobo["rehydrate_pending"] == 2
        assert kobo["share_place_nudges_pending"] == 1

    def test_accounts_are_relabelled_in_order_of_appearance(self, app_db):
        devices = integ.collect_devices(app_db)["devices"]
        assert [d["user"] for d in devices] == ["u1", "u2", "u1"]

    def test_free_text_outside_the_safe_charset_is_redacted(self, app_db):
        web = integ.collect_devices(app_db)["devices"][2]
        assert web["model"] == "(redacted)" and web["platform"] == "(redacted)"

    def test_summary_aggregates_cover_every_device(self, app_db):
        summary = integ.collect_devices(app_db)["summary"]
        assert summary["total"] == 3 and summary["active"] == 2 and summary["share_place_enabled"] == 1
        assert summary["users_with_devices"] == 2
        assert summary["by_kind"] == {"kobo": 2, "webreader": 1}
        assert summary["listed"] == 3 and summary["truncated"] is False

    def test_listing_is_capped_but_aggregates_are_not(self, app_db, monkeypatch):
        monkeypatch.setattr(integ, "MAX_DEVICES_LISTED", 2)
        out = integ.collect_devices(app_db)
        assert len(out["devices"]) == 2
        assert out["summary"]["total"] == 3 and out["summary"]["truncated"] is True

    def test_no_canary_in_output(self, app_db):
        text = json.dumps(integ.collect_devices(app_db))
        for canary in CANARIES:
            assert canary not in text

    def test_older_schema_without_newer_columns_still_reports(self, tmp_path):
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE device (id INTEGER PRIMARY KEY, public_id TEXT, user_id INTEGER, "
                     "kind TEXT, model TEXT, last_seen_at DATETIME)")
        conn.execute("INSERT INTO device VALUES (1, 'p', 3, 'kobo', 'Kobo Nia', '2026-01-02 03:04:05')")
        conn.commit()
        conn.close()
        out = integ.collect_devices(str(path))
        device = out["devices"][0]
        assert device["share_place"] is None and device["reading_positions"] == 0
        assert out["summary"]["share_place_enabled"] is None and out["summary"]["total"] == 1

    def test_missing_database_and_missing_table(self, tmp_path):
        assert integ.collect_devices(None) == {"exists": False}
        assert integ.collect_devices(str(tmp_path / "nope.db")) == {"exists": False}
        empty = tmp_path / "empty.db"
        sqlite3.connect(empty).close()
        assert integ.collect_devices(str(empty)) == {"exists": True, "device_table": False}

    def test_database_is_not_modified(self, app_db):
        before = hashlib.sha256(open(app_db, "rb").read()).hexdigest()
        integ.collect_devices(app_db)
        integ.collect_koreader(app_db, True)
        integ.collect_email(None, None, app_db)
        assert hashlib.sha256(open(app_db, "rb").read()).hexdigest() == before


@pytest.mark.unit
class TestKoreader:
    def test_counts_and_per_device_last_sync(self, app_db):
        out = integ.collect_koreader(app_db, sync_enabled=True)
        assert out["sync_enabled"] is True
        assert out["rows"] == 3 and out["users"] == 2 and out["identified_devices"] == 1
        assert out["newest_sync_at"] == "2026-11-18T12:00:00Z"
        assert out["rows_without_client_clock"] == 2 and out["rows_with_browser_position"] == 1
        by_ref = {d["ref"]: d for d in out["devices"]}
        kos = by_ref["kos_" + hashlib.sha256(KOSYNC_DEVICE_ID.encode()).hexdigest()[:8]]
        assert kos["documents"] == 2 and kos["last_sync_at"] == "2026-11-18T12:00:00Z"
        assert by_ref["unidentified"]["documents"] == 1

    def test_no_canary_in_output(self, app_db):
        text = json.dumps(integ.collect_koreader(app_db, None))
        for canary in CANARIES:
            assert canary not in text

    def test_missing_table_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "bare.db"
        sqlite3.connect(path).close()
        assert integ.collect_koreader(str(path), False) == {
            "exists": True, "kosync_table": False, "sync_enabled": False}

    def test_device_list_is_capped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(integ, "MAX_KOSYNC_DEVICES", 1)
        out = integ.collect_koreader(_seed(tmp_path / "app.db"), True)
        assert len(out["devices"]) == 1


@pytest.mark.unit
class TestEmail:
    SETTINGS = {"configured": True, "server_type": 0, "port": 587, "encryption": 1,
                "max_attachment_bytes": 25 * 1024 * 1024, "host": SMTP_HOST}

    def test_smtp_shape_without_host(self, app_db):
        out = integ.collect_email(self.SETTINGS, [], app_db)
        assert out["smtp"] == {"available": True, "configured": True, "server_type": "smtp",
                               "port": 587, "encryption": "starttls", "max_attachment_mib": 25.0}
        assert SMTP_HOST not in json.dumps(out)

    def test_unknown_codes_degrade_to_unknown(self, app_db):
        settings = dict(self.SETTINGS, server_type=9, encryption=7, port="587")
        smtp = integ.collect_email(settings, [], app_db)["smtp"]
        assert smtp["server_type"] == "unknown" and smtp["encryption"] == "unknown" and smtp["port"] is None

    def test_account_counts_without_addresses(self, app_db):
        users = integ.collect_email(None, None, app_db)["users"]
        assert users == {"exists": True, "total": 3, "with_ereader_email": 2, "with_custom_subject": 1}
        text = json.dumps(integ.collect_email(self.SETTINGS, [], app_db))
        assert KINDLE_MAIL not in text and KINDLE_SUBJECT not in text

    def test_task_outcomes_counted_by_class_and_status(self, app_db):
        tasks = [("TaskEmail", 3), ("TaskEmail", 3), ("TaskEmail", 1), ("TaskConvert", 2), ("TaskEmail", 99)]
        out = integ.collect_email(self.SETTINGS, tasks, app_db)["tasks_since_start"]
        assert out["available"] is True
        assert out["by_task"]["TaskEmail"] == {"waiting": 0, "failed": 1, "started": 0, "success": 2,
                                               "ended": 1, "cancelled": 0}
        assert out["by_task"]["TaskConvert"]["started"] == 1

    def test_task_classes_are_capped(self, app_db, monkeypatch):
        monkeypatch.setattr(integ, "MAX_TASK_CLASSES", 2)
        tasks = [(f"Task{i}", 3) for i in range(5)]
        assert len(integ.collect_email(self.SETTINGS, tasks, app_db)["tasks_since_start"]["by_task"]) == 2

    def test_unavailable_inputs_are_reported(self, tmp_path):
        out = integ.collect_email(None, None, str(tmp_path / "nope.db"))
        assert out == {"smtp": {"available": False}, "users": {"exists": False},
                       "tasks_since_start": {"available": False}}


@pytest.mark.unit
class TestInPack:
    def _inputs(self):
        return {"mail_settings": TestEmail.SETTINGS, "worker_tasks": [("TaskEmail", 3)],
                "koreader_enabled": True}

    def test_collect_all_adds_the_three_sections(self, app_db, tmp_path):
        files = sps.collect_all({"app_db": app_db}, sanitized=True, integrations=self._inputs(),
                                scheduler=type("S", (), {"get_jobs": lambda self: []})())
        manifest = json.loads(files["manifest.json"])
        ok = {s["file"]: s["ok"] for s in manifest["sections"]}
        for name in ("state/devices.json", "state/koreader.json", "state/email.json"):
            assert ok[name] is True
            json.loads(files[name])
        blob = b"".join(files.values()).decode()
        for canary in CANARIES:
            assert canary not in blob

    def test_a_failing_integration_does_not_sink_the_pack(self, app_db, monkeypatch):
        def boom(_path):
            raise RuntimeError(f"/mnt/{DISPLAY_NAME} exploded")
        monkeypatch.setattr(integ, "collect_devices", boom)
        files = sps.collect_all({"app_db": app_db}, sanitized=True, integrations=self._inputs(),
                                scheduler=type("S", (), {"get_jobs": lambda self: []})())
        assert json.loads(files["state/devices.json"]) == {"error": "RuntimeError"}
        manifest = json.loads(files["manifest.json"])
        ok = {s["file"]: s["ok"] for s in manifest["sections"]}
        assert ok["state/devices.json"] is False and ok["state/koreader.json"] is True
        assert DISPLAY_NAME not in b"".join(files.values()).decode()

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_built_archive_carries_clean_sections(self, app_db, tmp_path, monkeypatch, sanitize):
        from cps import debug_info

        log = tmp_path / "calibre-web.log"
        log.write_text("[2026-11-19 09:00:00]  INFO {cps:1} started\n", encoding="utf-8")

        class _Cfg:
            config_logfile = str(log)
            config_access_logfile = str(tmp_path / "access.log")
            config_access_log = 0

            def to_dict(self):
                return {"config_port": 8083}

        monkeypatch.setattr(debug_info, "config", _Cfg())
        monkeypatch.setattr(debug_info, "collect_stats", lambda: {"python": "3"})
        monkeypatch.setattr(debug_info, "_resolve_state_paths", lambda: {
            "config": None, "library": None, "ingest": None, "app_db": app_db,
            "cwa_db": None, "metadata_db": None})
        monkeypatch.setattr(debug_info, "_resolve_integration_inputs", self._inputs)
        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=sanitize)) as zf:
            for name in ("state/devices.json", "state/koreader.json", "state/email.json"):
                text = zf.read(name).decode()
                json.loads(text)
                for canary in CANARIES:
                    assert canary not in text, f"{canary!r} leaked into {name}"

    def test_resolver_returns_only_what_it_could_read(self):
        from cps import debug_info
        inputs = debug_info._resolve_integration_inputs()
        assert set(inputs) <= {"mail_settings", "worker_tasks", "koreader_enabled"}
