# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the support pack state collectors (fork issue #2728).

Each section is checked two ways: it reports what a maintainer needs, and a
seeded canary (a username, a share name, a title, a token) never reaches the
output. The end-to-end test then asserts the same property across every
member of a built archive.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest

from cps import support_pack_state as sps

SHARE_CANARY = "alice-nas-share"
TITLE_CANARY = "Canary Title Of A Secret Book"
USER_CANARY = "canaryuser"
TOKEN_CANARY = "hc-canary-token-12345"


def _make_db(path, statements, many_rows=0):
    conn = sqlite3.connect(path)
    for stmt in statements:
        conn.execute(stmt)
    if many_rows:
        conn.executemany("INSERT INTO bulk(v) VALUES (?)", [(i,) for i in range(many_rows)])
    conn.commit()
    conn.close()


@pytest.fixture
def metadata_db(tmp_path):
    path = tmp_path / "library" / "metadata.db"
    path.parent.mkdir()
    _make_db(str(path), [
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)",
        "CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT)",
        f"INSERT INTO books(title) VALUES ('{TITLE_CANARY}')",
        "INSERT INTO books(title) VALUES ('Other')",
        "INSERT INTO data(book, format) VALUES (1, 'EPUB'), (1, 'kepub'), (2, 'EPUB')",
    ])
    return str(path)


@pytest.mark.unit
class TestRuntime:
    def test_reports_version_and_platform(self):
        out = sps.collect_runtime(environ={})
        assert out["cwng_version"]
        assert out["python"] and out["machine"] and out["kernel"]
        assert isinstance(out["containerised"], bool)

    def test_env_values_allow_listed_and_secrets_name_only(self):
        env = {
            "TZ": "Australia/Brisbane",
            "PUID": "1000",
            "HARDCOVER_TOKEN": TOKEN_CANARY,
            "CWA_TMP_CONVERSION_DIR": f"/mnt/{SHARE_CANARY}/tmp",
            "NETWORK_SHARE_MODE": "true",
            "UNRELATED_SECRET": "should-not-appear",
        }
        out = sps.collect_runtime(environ=env)
        text = json.dumps(out)
        assert out["env_values"]["TZ"] == "Australia/Brisbane"
        assert out["env_values"]["NETWORK_SHARE_MODE"] == "true"
        assert "HARDCOVER_TOKEN" in out["env_names_set"]
        assert "CWA_TMP_CONVERSION_DIR" in out["env_names_set"]
        for canary in (TOKEN_CANARY, SHARE_CANARY, "should-not-appear", "UNRELATED_SECRET"):
            assert canary not in text

    def test_literal_env_reads_match_the_allow_list(self):
        # _safe_env_values spells each key out literally for the env SSOT
        # test; this keeps that list and _SAFE_ENV_VALUES from drifting.
        env = {name: "1" for name in sps._SAFE_ENV_VALUES}
        assert sps.collect_runtime(environ=env)["env_values"] == env
        assert set(sps._safe_env_values({"UNLISTED": "1"})) == set()

    def test_unexpected_safe_key_value_degrades_to_set(self):
        out = sps.collect_runtime(environ={"PUID": f"/home/{USER_CANARY}"})
        assert out["env_values"]["PUID"] == "set"
        assert USER_CANARY not in json.dumps(out)

    def test_deep_timezone_is_kept(self):
        out = sps.collect_runtime(environ={"TZ": "America/Argentina/Buenos_Aires"})
        assert out["env_values"]["TZ"] == "America/Argentina/Buenos_Aires"


MOUNTINFO = (
    "22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
    "40 22 0:35 / /mnt/calibre rw,relatime - nfs4 nas:/export rw\n"
    "41 22 0:36 / /mnt/with\\040space ro,relatime - cifs //nas/share ro\n"
    "garbage line\n"
)


@pytest.mark.unit
class TestStorage:
    def test_mountinfo_parsing_and_octal_unescape(self):
        mounts = sps.read_mounts(MOUNTINFO)
        assert ("/mnt/calibre", "nfs4", "rw,relatime") in mounts
        assert ("/mnt/with space", "cifs", "ro,relatime") in mounts
        assert len(mounts) == 3  # the garbage line is skipped

    def test_longest_mount_wins(self, tmp_path):
        mounts = [("/", "ext4", "rw"), (str(tmp_path), "nfs4", "rw")]
        assert sps.mount_for(str(tmp_path / "sub"), mounts)[1] == "nfs4"
        assert sps.mount_for("/etc", mounts)[1] == "ext4"

    def test_per_role_no_literal_paths(self, tmp_path):
        library = tmp_path / SHARE_CANARY / "library"
        library.mkdir(parents=True)
        mounts = [("/", "ext4", "rw"), (str(tmp_path), "nfs4", "rw")]
        out = sps.collect_storage(
            {"config": str(tmp_path), "library": str(library), "ingest": None}, mounts
        )
        text = json.dumps(out)
        assert SHARE_CANARY not in text and str(tmp_path) not in text
        assert out["library"]["fs_type"] == "nfs4" and out["library"]["network_fs"] is True
        assert out["library"]["free_bytes"] > 0 and out["library"]["writable"] is True
        assert out["ingest"] == {"configured": False}

    def test_missing_directory_is_reported_not_raised(self, tmp_path):
        out = sps.collect_storage({"ingest": str(tmp_path / "nope")}, [])
        assert out["ingest"] == {"configured": True, "exists": False}


@pytest.mark.unit
class TestDatabases:
    def test_health_counts_and_formats_without_contents(self, metadata_db):
        out = sps.collect_database(metadata_db, formats=True)
        assert out["exists"] and out["size_bytes"] > 0
        assert out["quick_check"] == "ok"
        assert out["journal_mode"] in {"delete", "wal"}
        assert out["row_counts"] == {"books": 2, "data": 3}
        assert out["formats"] == {"EPUB": 2, "KEPUB": 1}
        assert TITLE_CANARY not in json.dumps(out)

    def test_missing_file(self, tmp_path):
        assert sps.collect_database(str(tmp_path / "missing.db")) == {"exists": False}
        assert sps.collect_database(None) == {"exists": False}

    def test_corrupt_file_reports_type_only(self, tmp_path):
        bad = tmp_path / f"{SHARE_CANARY}.db"
        bad.write_bytes(b"this is not a sqlite database" * 100)
        out = sps.collect_database(str(bad))
        assert out["exists"] and out.get("error") == "DatabaseError"
        assert SHARE_CANARY not in json.dumps(out)

    def test_time_budget_stops_a_slow_scan(self, tmp_path):
        path = tmp_path / "big.db"
        _make_db(str(path), ["CREATE TABLE bulk (v INTEGER)"], many_rows=60_000)
        out = sps.collect_database(str(path), budget_s=0)
        assert out.get("timed_out") is True

    def test_database_is_not_modified(self, metadata_db):
        import hashlib
        before = hashlib.sha256(open(metadata_db, "rb").read()).hexdigest()
        sps.collect_database(metadata_db, formats=True)
        assert hashlib.sha256(open(metadata_db, "rb").read()).hexdigest() == before


class _FakeJob:
    def __init__(self, job_id, func_ref, trigger, next_run_time):
        self.id, self.func_ref, self.trigger, self.next_run_time = job_id, func_ref, trigger, next_run_time


class DateTrigger:  # name is what the collector reports
    pass


@pytest.mark.unit
class TestBackground:
    def test_jobs_listed_without_ids_and_send_state_counted(self, tmp_path):
        cwa = tmp_path / "cwa.db"
        _make_db(str(cwa), [
            "CREATE TABLE cwa_scheduled_jobs (id INTEGER PRIMARY KEY, username TEXT, title TEXT, "
            "state TEXT, last_error TEXT)",
            f"INSERT INTO cwa_scheduled_jobs(username, title, state, last_error) VALUES "
            f"('{USER_CANARY}', '{TITLE_CANARY}', 'dispatched', ''), "
            f"('{USER_CANARY}', 'x', 'scheduled', 'SMTP timeout'), "
            f"('{USER_CANARY}', 'y', 'scheduled', '')",
        ])
        ingest = tmp_path / "ingest"
        ingest.mkdir()
        for name in ("a.epub", "b.epub", ".hidden"):
            (ingest / name).write_text("x")
        scheduler = SimpleNamespace(get_jobs=lambda: [
            _FakeJob("auto_send_book123_user456", "cps.tasks.mail:send", DateTrigger(), None),
        ])
        out = sps.collect_background(str(cwa), str(ingest), scheduler=scheduler)
        text = json.dumps(out)
        assert out["scheduler"]["job_count"] == 1
        assert out["scheduler"]["jobs"] == [
            {"func": "cps.tasks.mail:send", "trigger": "DateTrigger", "paused": True}
        ]
        assert out["scheduled_send_jobs"]["by_state"] == {
            "dispatched": {"count": 1, "with_error": 0},
            "scheduled": {"count": 2, "with_error": 1},
        }
        assert out["ingest_queue"] == {"exists": True, "entries": 2, "capped": False}
        for canary in ("auto_send_book123_user456", USER_CANARY, TITLE_CANARY, "SMTP timeout"):
            assert canary not in text

    def test_ingest_count_is_capped(self, tmp_path):
        for i in range(8):
            (tmp_path / f"f{i}.epub").write_text("x")
        out = sps.collect_background(None, str(tmp_path), scheduler=SimpleNamespace(get_jobs=lambda: []),
                                     ingest_cap=5)
        assert out["ingest_queue"] == {"exists": True, "entries": 5, "capped": True}

    def test_scheduler_failure_is_isolated(self, tmp_path):
        def boom():
            raise RuntimeError(f"/mnt/{SHARE_CANARY} exploded")
        out = sps.collect_background(None, None, scheduler=SimpleNamespace(get_jobs=boom))
        assert out["scheduler"] == {"error": "RuntimeError"}
        assert SHARE_CANARY not in json.dumps(out)


@pytest.mark.unit
class TestCollectAll:
    def _paths(self, tmp_path, metadata_db):
        ingest = tmp_path / SHARE_CANARY
        ingest.mkdir()
        return {"config": str(tmp_path), "library": str(tmp_path / "library"), "ingest": str(ingest),
                "app_db": None, "cwa_db": None, "metadata_db": metadata_db}

    def test_manifest_lists_every_section(self, tmp_path, metadata_db):
        files = sps.collect_all(self._paths(tmp_path, metadata_db), sanitized=True,
                                scheduler=SimpleNamespace(get_jobs=lambda: []))
        manifest = json.loads(files["manifest.json"])
        assert manifest["schema_version"] == sps.SCHEMA_VERSION
        assert manifest["kind"] == "cwng-support-pack" and manifest["sanitized"] is True
        assert manifest["generated_at_utc"].endswith("+00:00")
        assert {s["file"] for s in manifest["sections"]} == {
            "state/runtime.json", "state/storage.json", "state/databases.json", "state/background.json"}
        assert all(s["ok"] for s in manifest["sections"])
        for section in manifest["sections"]:
            assert section["bytes"] == len(files[section["file"]])

    def test_split_library_reports_books_dir_as_its_own_role(self, tmp_path, metadata_db):
        books = tmp_path / "nfs-books"
        books.mkdir()
        paths = self._paths(tmp_path, metadata_db)
        paths["library_books"] = str(books)
        mounts = [("/", "ext4", "rw"), (str(books), "nfs4", "rw")]
        files = sps.collect_all(paths, sanitized=True, mounts=mounts,
                                scheduler=SimpleNamespace(get_jobs=lambda: []))
        storage = json.loads(files["state/storage.json"])
        assert storage["library_books"]["fs_type"] == "nfs4"
        assert storage["library_books"]["network_fs"] is True
        assert storage["library"]["fs_type"] == "ext4"
        assert str(books) not in files["state/storage.json"].decode()

    def test_library_books_role_absent_without_split(self, tmp_path, metadata_db):
        files = sps.collect_all(self._paths(tmp_path, metadata_db), sanitized=True,
                                scheduler=SimpleNamespace(get_jobs=lambda: []))
        assert "library_books" not in json.loads(files["state/storage.json"])

    def test_failing_collector_does_not_sink_the_pack(self, tmp_path, metadata_db, monkeypatch):
        def boom(_env=None):
            raise ValueError(f"/home/{USER_CANARY}/secret")
        monkeypatch.setattr(sps, "collect_runtime", boom)
        files = sps.collect_all(self._paths(tmp_path, metadata_db), sanitized=False,
                                scheduler=SimpleNamespace(get_jobs=lambda: []))
        assert json.loads(files["state/runtime.json"]) == {"error": "ValueError"}
        manifest = json.loads(files["manifest.json"])
        ok = {s["file"]: s["ok"] for s in manifest["sections"]}
        assert ok["state/runtime.json"] is False and ok["state/storage.json"] is True
        assert USER_CANARY not in b"".join(files.values()).decode()

    def test_oversize_section_is_truncated(self, tmp_path, metadata_db, monkeypatch):
        monkeypatch.setattr(sps, "MAX_SECTION_BYTES", 50)
        files = sps.collect_all(self._paths(tmp_path, metadata_db), sanitized=True,
                                scheduler=SimpleNamespace(get_jobs=lambda: []))
        body = json.loads(files["state/databases.json"])
        assert body["truncated"] is True and body["original_bytes"] > 50
        manifest = json.loads(files["manifest.json"])
        assert {s["file"]: s["ok"] for s in manifest["sections"]}["state/databases.json"] is False

    def test_no_canary_in_any_section(self, tmp_path, metadata_db):
        env = {"HARDCOVER_TOKEN": TOKEN_CANARY, "CWA_TMP_CONVERSION_DIR": f"/mnt/{SHARE_CANARY}"}
        files = sps.collect_all(self._paths(tmp_path, metadata_db), sanitized=True,
                                scheduler=SimpleNamespace(get_jobs=lambda: []), environ=env)
        blob = b"".join(files.values()).decode()
        for canary in (SHARE_CANARY, TITLE_CANARY, TOKEN_CANARY, str(tmp_path)):
            assert canary not in blob


@pytest.mark.unit
class TestInSupportPack:
    @pytest.fixture
    def built(self, tmp_path, metadata_db, monkeypatch):
        from cps import debug_info

        log = tmp_path / "calibre-web.log"
        log.write_text("[2026-10-26 09:00:00]  INFO {cps:1} started\n", encoding="utf-8")

        class _Cfg:
            config_logfile = str(log)
            config_access_logfile = str(tmp_path / "access.log")
            config_access_log = 0
            def to_dict(self):
                return {"config_port": 8083}

        ingest = tmp_path / SHARE_CANARY
        ingest.mkdir()
        monkeypatch.setattr(debug_info, "config", _Cfg())
        monkeypatch.setattr(debug_info, "collect_stats", lambda: {"python": "3"})
        monkeypatch.setattr(debug_info, "_resolve_state_paths", lambda: {
            "config": str(tmp_path), "library": str(tmp_path / "library"), "ingest": str(ingest),
            "app_db": None, "cwa_db": None, "metadata_db": metadata_db})
        return debug_info

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_state_files_present_and_clean(self, built, sanitize):
        with zipfile.ZipFile(built._build_debug_zip(sanitize=sanitize)) as zf:
            names = set(zf.namelist())
            assert {"manifest.json", "state/runtime.json", "state/storage.json",
                    "state/databases.json", "state/background.json"} <= names
            for name in names:
                if name == "manifest.json" or name.startswith("state/"):
                    text = zf.read(name).decode()
                    json.loads(text)
                    assert SHARE_CANARY not in text and TITLE_CANARY not in text
            assert json.loads(zf.read("manifest.json"))["sanitized"] is sanitize

    def test_resolver_failure_leaves_sections_unconfigured(self, built, monkeypatch):
        monkeypatch.undo()  # drop the fixture's patches: real resolver, no app booted
        paths = built._resolve_state_paths()
        assert set(paths) == {"config", "library", "ingest", "app_db", "cwa_db", "metadata_db"}
