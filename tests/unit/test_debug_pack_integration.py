# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end integration tests for fork issue #312 — the debug-pack
build pipeline must produce a real zip with sanitized settings and
sanitized log content when called via `send_debug_sanitized()`.

The unit tests cover the redactor + sanitizer functions in isolation.
This file exercises `_build_debug_zip(sanitize=True)` against an
in-memory zip and confirms:

* `settings.txt` has no plaintext secrets after sanitization
* log files inside the zip have IPs/paths/auth headers scrubbed
* `SANITIZED.txt` marker is included to remind users
* unsanitized pack still includes the raw content (preserves
  admin/private-use behavior)
* per-file log truncation enforces the 10 MiB cap
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_config_and_logs(tmp_path, monkeypatch):
    """Set up a fake on-disk log file and a fake config.to_dict() so we
    can drive `_build_debug_zip` without booting the whole app."""
    from cps import debug_info, logger

    log_path = tmp_path / "calibre-web.log"
    log_path.write_text(
        "[2026-05-26 12:00:00] WARN {cps.web:2521} Login failed for "
        "user \"alice\" IP-address: 192.168.1.42\n"
        "[2026-05-26 12:00:01] INFO {cps.kobo:850} Reading "
        "/calibre-library/Author/Book/x.epub\n"
        "[2026-05-26 12:00:02] INFO {cps.kobo:851} Authorization: "
        "Bearer eyJhbGciOi.body.sig\n",
        encoding="utf-8",
    )
    access_path = tmp_path / "access.log"
    access_path.write_text("dummy access line\n", encoding="utf-8")

    fake_settings = {
        "mail_password": "smtp-secret-789",
        "mail_gmail_token": json.dumps({"refresh_token": "1//abc.def"}),
        "config_ldap_serv_password": "ldap-bind-pw",
        "config_port": 8083,
        "config_title_regex": r"^(A|The)\s+",
    }

    class _FakeConfig:
        config_logfile = str(log_path)
        config_access_logfile = str(access_path)
        config_access_log = 1
        def to_dict(self):
            return dict(fake_settings)

    fake_cfg = _FakeConfig()
    monkeypatch.setattr(debug_info, "config", fake_cfg)
    monkeypatch.setattr(
        "cps.debug_info.collect_stats",
        lambda: {"python": "3.12.7", "platform": "Darwin"},
    )

    return tmp_path, log_path, access_path, fake_settings


@pytest.mark.unit
class TestBuildDebugZipSanitized:
    def test_settings_txt_has_no_plaintext_secrets(self, fake_config_and_logs):
        from cps.debug_info import _build_debug_zip

        zip_buf = _build_debug_zip(sanitize=True)
        with zipfile.ZipFile(zip_buf) as zf:
            settings_blob = zf.read("settings.txt").decode("utf-8")
        assert "smtp-secret-789" not in settings_blob, (
            "sanitized pack must not contain mail_password plaintext: "
            f"{settings_blob!r}"
        )
        assert "1//abc.def" not in settings_blob
        assert "ldap-bind-pw" not in settings_blob

    def test_log_file_inside_zip_is_scrubbed(self, fake_config_and_logs):
        from cps.debug_info import _build_debug_zip

        zip_buf = _build_debug_zip(sanitize=True)
        with zipfile.ZipFile(zip_buf) as zf:
            log_blob = zf.read("calibre-web.log").decode("utf-8")
        assert "192.168.1.42" not in log_blob
        assert "/calibre-library/Author/Book/" not in log_blob
        assert "eyJhbGciOi.body.sig" not in log_blob
        assert "<ip>" in log_blob
        assert "<library>/" in log_blob
        assert "Bearer <redacted>" in log_blob

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_smtp_fields_and_configured_paths_are_private(self, fake_config_and_logs, sanitize):
        from cps.debug_info import _build_debug_zip

        _, _, _, settings = fake_config_and_logs
        private = {
            "mail_server": "smtp.private.test",
            "mail_login": "login@private.test",
            "mail_from": "Private Sender <sender@private.test>",
            "config_calibre_dir": "/srv/Private Reader",
            "config_logfile": "/srv/private-logs/cwng.log",
            "config_ldap_cacert_path": "/etc/private-certs/ldap.pem",
            "config_limiter_uri": "redis+unix:///var/run/private-limiter.sock",
        }
        settings.update(private)
        with zipfile.ZipFile(_build_debug_zip(sanitize=sanitize)) as zf:
            exported = json.loads(zf.read("settings.txt"))
        assert exported["config_port"] == 8083
        for key, value in private.items():
            assert exported[key] == ("<redacted>" if sanitize else value), key

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_old_progress_payloads_are_private(self, fake_config_and_logs, sanitize):
        from cps.debug_info import _build_debug_zip

        _, log_path, _, _ = fake_config_and_logs
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                "[2026-05-26 12:01:00] DEBUG {cps.progress_syncing.protocols.kosync:2546} "
                "PUT /kosync/syncs/progress/<document> with: {'device': 'private-device'}\n"
                "private-continuation\n"
                "[2026-05-26 12:01:01] DEBUG {cps.progress_syncing.protocols.kosync:1439} "
                "GET /kosync/syncs/progress/old-request-document\n"
                "[2026-05-26 12:01:01] DEBUG {cps.progress_syncing.protocols.kosync:1550} "
                "GET /kosync/syncs/progress/old-document-id Response: {'progress': 'private-position'}\n"
                "[2026-05-26 12:01:02] INFO {cps.progress_syncing.protocols.kosync:50} "
                "Progress request completed\n"
            )
        with zipfile.ZipFile(_build_debug_zip(sanitize=sanitize)) as zf:
            log = zf.read("calibre-web.log").decode("utf-8")
        for value in ("private-device", "private-continuation", "old-request-document",
                      "old-document-id", "private-position"):
            assert (value in log) is not sanitize
        assert "Progress request completed" in log

    def test_truncated_app_log_starts_at_a_complete_record(self, fake_config_and_logs, monkeypatch):
        from cps import debug_info

        _, log_path, _, _ = fake_config_and_logs
        continuation = "private-continued-request\n"
        next_record = "[2026-05-26 12:01:01] INFO {cps.web:12} Reader ready\n"
        # The cut starts in the old request's continuation, followed by a
        # complete line in that same record. Neither belongs in the archive.
        partial_record = "private-second-line\n"
        log_path.write_text(
            "[2026-05-26 12:01:00] DEBUG {cps.kosync:2546} "
            "PUT /kosync/syncs/progress/<document> with: private-start\n"
            + continuation + partial_record + next_record,
            encoding="utf-8",
        )
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", len(partial_record + next_record) + 8)
        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=True)) as zf:
            assert zf.read("calibre-web.log").decode("utf-8") == next_record

    @pytest.mark.parametrize(("app_name", "access_name"), [
        ("server.log", "server.log.access"), ("server.log.app", "server.log"),
    ])
    def test_log_type_follows_the_more_specific_path(self, fake_config_and_logs, monkeypatch,
                                                     app_name, access_name):
        from cps import debug_info

        directory, _, _, _ = fake_config_and_logs
        app_log = directory / app_name
        access_log = directory / access_name
        next_record = "[2026-05-26 12:01:01] INFO {cps.web:12} Reader ready\n"
        app_log.write_text(
            "[2026-05-26 12:01:00] DEBUG {cps.kosync:2546} "
            "PUT /kosync/syncs/progress/<document> with: private-start\n"
            "private-continued-request\nprivate-second-line\n" + next_record,
            encoding="utf-8",
        )
        access_record = '"GET /api/v1/books HTTP/1.1" 200 10\n'
        access_log.write_text(access_record * 4, encoding="utf-8")
        monkeypatch.setattr(debug_info.config, "config_logfile", str(app_log))
        monkeypatch.setattr(debug_info.config, "config_access_logfile", str(access_log))
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", len(next_record) + len("private-second-line\n") + 8)
        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=True)) as zf:
            assert zf.read(app_name).decode("utf-8") == next_record
            assert zf.read(access_name).decode("utf-8").endswith(access_record)

    def test_app_rotation_keeps_its_format_when_it_matches_an_access_path(
            self, fake_config_and_logs, monkeypatch):
        from cps import debug_info

        directory, _, _, _ = fake_config_and_logs
        app_log = directory / "server.log"
        rotated = directory / "server.log.1"
        next_record = "[2026-05-26 12:01:01] INFO {cps.web:12} Reader ready\n"
        rotated.write_text(
            "[2026-05-26 12:01:00] DEBUG {cps.kosync:2546} "
            "PUT /kosync/syncs/progress/<document> with: private-start\n"
            "private-continued-request\nprivate-second-line\n" + next_record,
            encoding="utf-8",
        )
        monkeypatch.setattr(debug_info.config, "config_logfile", str(app_log))
        monkeypatch.setattr(debug_info.config, "config_access_logfile", str(rotated))
        monkeypatch.setattr(debug_info, "_PER_FILE_MAX_BYTES", len(next_record) + len("private-second-line\n") + 8)
        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=True)) as zf:
            assert zf.read(rotated.name).decode("utf-8") == next_record

    @pytest.mark.parametrize("sanitize", [True, False])
    def test_pack_only_selects_configured_logs_and_their_rotations(
            self, fake_config_and_logs, monkeypatch, sanitize):
        from cps import debug_info

        directory, _, _, _ = fake_config_and_logs
        configured = directory / "application"
        rotated = directory / "application.2"
        neighboring = directory / "application-notes.txt"
        for path, line in ((configured, "active record"), (rotated, "rotated record"),
                           (neighboring, "unrelated note")):
            path.write_text(line + "\n", encoding="utf-8")
        monkeypatch.setattr(debug_info.config, "config_logfile", str(configured))

        with zipfile.ZipFile(debug_info._build_debug_zip(sanitize=sanitize)) as archive:
            assert archive.read(configured.name) == b"active record\n"
            assert archive.read(rotated.name) == b"rotated record\n"
            assert neighboring.name not in archive.namelist()

    def test_sanitized_pack_includes_marker(self, fake_config_and_logs):
        from cps.debug_info import _build_debug_zip

        zip_buf = _build_debug_zip(sanitize=True)
        with zipfile.ZipFile(zip_buf) as zf:
            names = set(zf.namelist())
            assert "SANITIZED.txt" in names, names

    def test_unsanitized_pack_preserves_originals(self, fake_config_and_logs):
        from cps.debug_info import _build_debug_zip

        zip_buf = _build_debug_zip(sanitize=False)
        with zipfile.ZipFile(zip_buf) as zf:
            settings_blob = zf.read("settings.txt").decode("utf-8")
            log_blob = zf.read("calibre-web.log").decode("utf-8")
            names = set(zf.namelist())
        assert "smtp-secret-789" in settings_blob, (
            "non-sanitized pack should preserve full content for admin/private use"
        )
        assert "192.168.1.42" in log_blob
        assert "SANITIZED.txt" not in names


@pytest.mark.unit
class TestPerFileSizeCap:
    def test_oversize_log_truncated_to_last_10mib(self, tmp_path, monkeypatch):
        from cps import debug_info

        big_log = tmp_path / "calibre-web.log"
        # 12 MiB of content; the cap is 10 MiB so 2 MiB should be dropped.
        chunk = ("[2026-05-26 12:00:00] INFO {x:1} filler line\n").encode("utf-8")
        with big_log.open("wb") as f:
            written = 0
            while written < 12 * 1024 * 1024:
                f.write(chunk)
                written += len(chunk)

        class _FakeConfig:
            config_logfile = str(big_log)
            config_access_logfile = str(tmp_path / "access.log")
            def to_dict(self):
                return {"config_port": 8083}

        monkeypatch.setattr(debug_info, "config", _FakeConfig())
        monkeypatch.setattr(
            "cps.debug_info.collect_stats", lambda: {"python": "3.12.7"}
        )

        zip_buf = debug_info._build_debug_zip(sanitize=True)
        with zipfile.ZipFile(zip_buf) as zf:
            log_blob = zf.read("calibre-web.log")
        # 10 MiB cap; allow a small overhead for the final line completion.
        assert len(log_blob) <= 11 * 1024 * 1024, (
            f"log inside sanitized zip must be capped at ~10 MiB, got "
            f"{len(log_blob)} bytes"
        )


@pytest.mark.unit
class TestNonMutation:
    def test_redactor_does_not_mutate_caller_dict(self):
        from cps.debug_info import _redact_for_export

        original = {
            "mail_password": "live-secret",
            "config_port": 8083,
        }
        out = _redact_for_export(original)
        # Critically — the live config must still have its real values.
        assert original["mail_password"] == "live-secret"
        assert out is not original
