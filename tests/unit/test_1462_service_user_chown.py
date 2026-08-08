# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A missing ``abc`` account is a source install, not a failure.

@Thovi98's YunoHost build printed this on every ``auto_library.py`` run
(#1462 follow-up)::

    chown: invalid user: 'abc:abc'
    [cwa-auto-library] An error occurred while attempting to recursively set
    ownership of /config to abc:abc. See the following error:
    Command '['chown', '-R', 'abc:abc', '/config']' returned non-zero exit status 1.

Nothing was wrong — there is no ``abc`` outside the LinuxServer.io image, and
the files already belong to the user running the app — but the operator has no
way to tell that from an error with a traceback in it.

``convert_library.py`` and ``kindle_epub_fixer.py`` already guarded for this;
``auto_library.py`` and ``ingest_processor.py`` did not. ``scripts/service_user.py``
is the extracted guard, so there is one answer instead of four.
"""

import importlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture
def service_user(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    for var in ("CWA_SERVICE_USER", "CWA_SERVICE_GROUP", "NETWORK_SHARE_MODE"):
        monkeypatch.delenv(var, raising=False)
    module = importlib.import_module("service_user")
    return importlib.reload(module)


class TestServiceIds:
    def test_returns_none_when_the_account_is_absent(self, service_user, monkeypatch):
        """The source-install case. None means 'skip', not 'error'."""
        monkeypatch.setenv("CWA_SERVICE_USER", "definitely-not-a-real-account-1462")

        assert service_user.service_ids() is None

    def test_resolves_a_real_account_to_numeric_ids(self, service_user, monkeypatch):
        """Stand in for 'abc' with an account that exists on every POSIX box."""
        import grp
        import pwd

        expected = (pwd.getpwnam("root").pw_uid, grp.getgrgid(pwd.getpwnam("root").pw_gid).gr_gid)
        monkeypatch.setenv("CWA_SERVICE_USER", "root")
        monkeypatch.setenv("CWA_SERVICE_GROUP", grp.getgrgid(expected[1]).gr_name)

        assert service_user.service_ids() == expected

    def test_blank_override_disables_the_chown(self, service_user, monkeypatch):
        monkeypatch.setenv("CWA_SERVICE_USER", "")

        assert service_user.service_ids() is None


class TestChownToServiceUser:
    def test_no_account_skips_without_running_chown(self, service_user, monkeypatch, tmp_path):
        """RED before the fix: auto_library shelled out and printed the error."""
        monkeypatch.setenv("CWA_SERVICE_USER", "definitely-not-a-real-account-1462")
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
        lines = []

        result = service_user.chown_to_service_user(tmp_path, "[test]", log=lines.append)

        assert result is False
        assert calls == [], "must not shell out to chown when the account is absent"
        assert len(lines) == 1
        assert "definitely-not-a-real-account-1462" in lines[0]

    def test_the_skip_message_does_not_read_as_an_error(self, service_user, monkeypatch, tmp_path):
        """The whole point: the operator must see a decision, not a fault."""
        monkeypatch.setenv("CWA_SERVICE_USER", "definitely-not-a-real-account-1462")
        lines = []

        service_user.chown_to_service_user(tmp_path, "[cwa-auto-library]", log=lines.append)

        message = lines[0]
        assert "error" not in message.lower()
        assert "traceback" not in message.lower()
        assert "expected outside the Docker image" in message

    def test_network_share_mode_still_skips(self, service_user, monkeypatch, tmp_path):
        monkeypatch.setenv("NETWORK_SHARE_MODE", "true")
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
        lines = []

        result = service_user.chown_to_service_user(tmp_path, "[test]", log=lines.append)

        assert result is False
        assert calls == []
        assert "NETWORK_SHARE_MODE=true" in lines[0]

    def test_network_share_mode_can_be_overridden_for_the_local_config_volume(
        self, service_user, monkeypatch, tmp_path
    ):
        """The calibre plugins dir is chowned even under NSM — it is never on NFS."""
        monkeypatch.setenv("NETWORK_SHARE_MODE", "true")
        monkeypatch.setenv("CWA_SERVICE_USER", "definitely-not-a-real-account-1462")
        lines = []

        service_user.chown_to_service_user(
            tmp_path, "[test]", respect_network_share_mode=False, log=lines.append
        )

        assert "NETWORK_SHARE_MODE" not in lines[0]

    def test_runs_recursive_chown_when_the_account_exists(self, service_user, monkeypatch, tmp_path):
        """The container path is unchanged: it still chowns, and recursively."""
        monkeypatch.setattr(service_user, "service_ids", lambda: (1000, 1000))
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["check"] = kwargs.get("check")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = service_user.chown_to_service_user(tmp_path, "[test]")

        assert result is True
        assert captured["command"] == ["chown", "-R", "1000:1000", str(tmp_path)]
        assert captured["check"] is True

    def test_non_recursive_for_a_single_log_file(self, service_user, monkeypatch, tmp_path):
        monkeypatch.setattr(service_user, "service_ids", lambda: (1000, 1000))
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **k: captured.setdefault("command", command)
            or subprocess.CompletedProcess(command, 0),
        )

        service_user.chown_to_service_user(tmp_path / "x.log", "[test]", recursive=False)

        assert "-R" not in captured["command"]

    def test_a_real_chown_failure_is_still_reported(self, service_user, monkeypatch, tmp_path):
        """Skipping an absent account must not swallow a genuine permission error."""
        monkeypatch.setattr(service_user, "service_ids", lambda: (1000, 1000))

        def fail(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        monkeypatch.setattr(subprocess, "run", fail)
        lines = []

        result = service_user.chown_to_service_user(tmp_path, "[test]", log=lines.append)

        assert result is False
        assert "error occurred" in lines[0].lower()


class TestCallSitesUseTheHelper:
    """No script may keep its own copy of the guard (single source of truth)."""

    @pytest.mark.parametrize(
        "script",
        ["auto_library.py", "ingest_processor.py", "convert_library.py", "kindle_epub_fixer.py"],
    )
    def test_no_hardcoded_abc_chown_remains(self, script):
        """RED before the fix for auto_library.py and ingest_processor.py."""
        source = (SCRIPTS_DIR / script).read_text()

        assert '"abc:abc"' not in source, f"{script} still shells out to a literal abc:abc"
        assert "'abc:abc'" not in source

    @pytest.mark.parametrize(
        "script",
        ["auto_library.py", "ingest_processor.py", "convert_library.py", "kindle_epub_fixer.py"],
    )
    def test_script_imports_the_helper(self, script):
        source = (SCRIPTS_DIR / script).read_text()

        assert "import service_user" in source
