# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The metadata-change-detector service starts as root and the dispatcher used
to run ``cover_enforcer.py`` as root too. On a book library on a network share
(NFS with root squash, say) root cannot write, and calibredb fails opening the
library with ``PermissionError`` on its case-sensitivity probe file, so every
metadata edit logged ``Failed to enforce metadata ... exit status 1``.

The enforcer now runs as the app user, like the ingest service and the web
app. These tests cover the decision (when to drop), behaviourally, with the uid
and the helper lookup injected; the Calibre config directory it uses is set by
the service's run script (test_1764_calibre_config_service_env).
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import metadata_change_dispatch as dispatch  # noqa: E402

HELPER_PRESENT = lambda name: "/usr/local/bin/" + name  # noqa: E731
HELPER_ABSENT = lambda name: None  # noqa: E731
ENFORCER = "/app/calibre-web-automated/scripts/cover_enforcer.py"
BARE = ["python3", ENFORCER, "--log", "20261009095953-502.json"]


def test_root_runs_the_enforcer_through_the_privilege_drop_helper():
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=0, which=HELPER_PRESENT
    )
    assert cmd == ["cwa-as-abc"] + BARE


def test_non_root_runs_the_enforcer_as_it_is():
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=1000, which=HELPER_PRESENT
    )
    assert cmd == BARE


def test_without_the_helper_the_enforcer_still_runs():
    """Outside the image there is no helper; the command must not break."""
    cmd = dispatch.enforcer_command(
        ENFORCER, "20261009095953-502.json", euid=0, which=HELPER_ABSENT
    )
    assert cmd == BARE


def test_default_dispatch_runs_the_enforcer_through_the_helper(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)
    monkeypatch.setattr(dispatch.os, "geteuid", lambda: 0)
    monkeypatch.setattr(dispatch.shutil, "which", HELPER_PRESENT)

    dispatch._default_dispatch("/config/metadata_change_logs", ENFORCER)("x-502.json")

    assert seen["cmd"] == ["cwa-as-abc", "python3", ENFORCER, "--log", "x-502.json"]
    assert seen["kwargs"]["check"] is False


def test_a_failed_calibredb_run_logs_why_not_just_its_exit_status(capsys):
    """#2518: the failure line said only "returned non-zero exit status 1"; the
    PermissionError that explained it was in calibredb's stderr and never shown."""
    import subprocess
    from types import SimpleNamespace

    import cover_enforcer

    stderr = (
        "Traceback (most recent call last):\n"
        "  File \"/opt/calibre/lib/calibre/db/backend.py\", line 470, in __init__\n"
        "PermissionError: [Errno 13] Permission denied: "
        "'/mnt/calibre/calibre_test_case_sensitivity.txt'\n"
        "Warning: plugin 'x' could not be loaded\n\n"
    )
    error = subprocess.CalledProcessError(
        1, ["calibredb", "export", "502"], output="", stderr=stderr
    )
    stub = SimpleNamespace(db=SimpleNamespace(enforce_add_entry_from_log=lambda *a, **k: None))

    cover_enforcer.Enforcer.record_failed_enforcement(
        stub, {"title": "FRANKENSTEIN", "book_id": 502, "file_path": "x"}, error
    )

    out = capsys.readouterr().out
    assert "returned non-zero exit status 1" in out
    assert "PermissionError: [Errno 13] Permission denied" in out
    assert "Traceback" not in out


def test_metadata_export_never_reads_another_books_leftover_opf(monkeypatch, tmp_path):
    """Once the enforcer runs as the app user, a root-owned export left in
    metadata_temp by an earlier root run that was killed mid-pass can no longer
    be cleaned up (cwa-init skips the chown under NETWORK_SHARE_MODE). The
    export must not pick that stale metadata.opf up and embed it into the book
    being enforced now."""
    import contextlib
    from types import SimpleNamespace

    import cover_enforcer

    stale = tmp_path / "Shelley, Mary" / "Frankenstein (501)"
    stale.mkdir(parents=True)
    (stale / "metadata.opf").write_text("<package>book 501</package>")

    export_dirs = []

    def fake_run(cmd, **kwargs):
        to_dir = Path(cmd[cmd.index("--to-dir") + 1])
        # Which .opf the walk meets first depends on directory order, so pin
        # the cause instead: calibredb must export into a directory of its own.
        export_dirs.append(sorted(p.name for p in to_dir.rglob("*")))
        out = to_dir / "Austen, Jane" / "Emma (502)"
        out.mkdir(parents=True)
        (out / "metadata.opf").write_text("<package>book 502</package>")
        return SimpleNamespace(returncode=0, stdout="", stderr="", args=cmd)

    monkeypatch.setattr(cover_enforcer, "metadata_temp_dir", str(tmp_path))
    monkeypatch.setattr(cover_enforcer, "library_target",
                        lambda lib: SimpleNamespace(args=[], stdin=None))
    monkeypatch.setattr(cover_enforcer, "calibredb_command", lambda cmd, target: cmd)
    monkeypatch.setattr(cover_enforcer, "operation",
                        lambda timeout: contextlib.nullcontext())
    monkeypatch.setattr(cover_enforcer.time, "sleep", lambda s: None)
    monkeypatch.setattr(cover_enforcer.subprocess, "run", fake_run)

    book = SimpleNamespace(book_id="502", calibre_library="/calibre-library", calibre_env={})
    opf = cover_enforcer.Book.get_new_metadata_path(book)

    assert export_dirs == [[]]
    assert Path(opf).read_text() == "<package>book 502</package>"


def test_a_stale_lock_the_app_user_cannot_remove_ends_cleanly(monkeypatch, tmp_path):
    """A root-owned lock in sticky /tmp (left by a manual root run) cannot be
    removed by the app user; that must cancel the pass, not crash it."""
    import cover_enforcer

    monkeypatch.setattr(cover_enforcer.tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "cover_enforcer.lock").write_text("")

    def refuse(path):
        if path.endswith("cover_enforcer.lock"):
            raise PermissionError(13, "Operation not permitted", path)
        return real_remove(path)

    real_remove = cover_enforcer.os.remove
    monkeypatch.setattr(cover_enforcer.os, "remove", refuse)

    with pytest.raises(SystemExit) as stop:
        cover_enforcer._acquire_lock_or_exit()
    assert stop.value.code == 2


def test_the_app_user_does_not_try_to_chown_book_dirs(monkeypatch, tmp_path, capsys):
    import cover_enforcer

    calls = []
    monkeypatch.setattr(cover_enforcer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cover_enforcer.os, "chown", lambda *a: calls.append(a))
    (tmp_path / "book.epub").write_text("x")

    cover_enforcer.Enforcer._reset_book_dir_ownership(str(tmp_path))

    assert calls == []
    assert "failed to chown" not in capsys.readouterr().out
