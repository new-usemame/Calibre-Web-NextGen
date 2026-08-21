# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The re-delivery measurement must be unable to touch an annotated book.

scripts/measure_kobo_redelivery.py answers the one question blocking F-3e383a and
notes/3649: does a Kobo re-download when the server sends a ChangedEntitlement?

It runs against the operator's own device and mutates `Books.last_modified`, so
its safety properties are worth a test rather than a comment:

* it REFUSES a book that carries any annotation, because if the answer turns out
  to be "yes it re-delivers", a re-spined package could strand exactly those;
* it does nothing at all without `--go`;
* it never deletes a kobo_synced_books row — that empties the user's tracking
  table, resets the whole sync token, and would answer a different question.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "measure_kobo_redelivery.py"


def _module():
    spec = importlib.util.spec_from_file_location("measure_kobo_redelivery", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app_db(tmp_path, annotations, name="app.db"):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER)")
    conn.executemany("INSERT INTO annotation (book_id) VALUES (?)",
                     [(7,)] * annotations)
    conn.commit()
    conn.close()
    return path


def _metadata_db(tmp_path):
    path = tmp_path / "metadata.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, last_modified TEXT)")
    conn.execute("INSERT INTO books (id, last_modified) VALUES (7, '2020-01-01')")
    conn.commit()
    conn.close()
    return path


def test_the_script_exists_and_loads():
    assert SCRIPT.is_file(), SCRIPT
    assert callable(_module().main)


def test_it_refuses_a_book_that_has_annotations(tmp_path, capsys):
    module = _module()
    app_db = _app_db(tmp_path, annotations=3)
    metadata_db = _metadata_db(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        module.main([
            "--book-id", "7", "--device-path", "/mnt/onboard/x.kepub.epub",
            "--app-db", str(app_db), "--metadata-db", str(metadata_db), "--go",
        ])
    assert "REFUSING" in str(excinfo.value)

    # And it refused BEFORE changing anything.
    conn = sqlite3.connect(metadata_db)
    try:
        assert conn.execute(
            "SELECT last_modified FROM books WHERE id = 7").fetchone()[0] == "2020-01-01"
    finally:
        conn.close()


def test_a_dry_run_changes_nothing_even_for_a_clean_book(tmp_path):
    module = _module()
    app_db = _app_db(tmp_path, annotations=0)
    metadata_db = _metadata_db(tmp_path)

    assert module.main([
        "--book-id", "7", "--device-path", "/mnt/onboard/x.kepub.epub",
        "--app-db", str(app_db), "--metadata-db", str(metadata_db),
    ]) == 0

    conn = sqlite3.connect(metadata_db)
    try:
        assert conn.execute(
            "SELECT last_modified FROM books WHERE id = 7").fetchone()[0] == "2020-01-01"
    finally:
        conn.close()


def test_the_clean_book_case_really_is_clean(tmp_path):
    """Vacuity guard: the refusal test would also pass if EVERY book refused."""
    module = _module()
    assert module.annotations_for_book(
        _app_db(tmp_path, annotations=0, name="clean.db"), 7) == 0
    assert module.annotations_for_book(
        _app_db(tmp_path, annotations=3, name="dirty.db"), 7) == 3


def test_the_script_issues_no_delete_at_all():
    """Deleting the user's last kobo_synced_books row empties the tracking table,
    which resets the whole sync token and turns every book into a
    NewEntitlement — answering a different question entirely.

    This is a source check on purpose, and it is the legitimate kind: it proves
    an ABSENCE in a script whose dangerous path cannot be executed here without
    a device. It does not claim a runtime behaviour it never runs.
    """
    import re

    source = SCRIPT.read_text(encoding="utf-8")
    # Strip the module docstring, which explains at length what it does NOT do.
    body = source.split('"""', 2)[-1]
    assert not re.search(r"\bDELETE\s+FROM\b", body, re.I), (
        "the script contains a DELETE; it must only ever bump last_modified"
    )


def test_the_only_write_is_a_last_modified_bump():
    """One UPDATE, one column, one book."""
    import re

    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    writes = [" ".join(m.split()).upper()
              for m in re.findall(r"(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+\w+",
                                  body, re.I)]
    assert writes == ["UPDATE BOOKS"], writes


class TestTheDeviceReadPath:
    """The half that only runs when the Kobo is awake.

    The safety tests above cover the refusal and the dry run. They do NOT cover
    the code that actually talks to the device — and that is the code which will
    run in a narrow window, on hardware, probably once. A bug there wastes the
    window. These drive it against a fake `subprocess.run` so the command shape
    and the parsing are exercised without a device.
    """

    def _fake_run(self, captured, stdout="", stderr="", returncode=0):
        class _Result:
            pass

        def run(cmd, **kwargs):
            captured.append((cmd, kwargs))
            result = _Result()
            result.stdout, result.stderr, result.returncode = stdout, stderr, returncode
            return result

        return run

    def test_it_sends_the_command_on_stdin_not_argv(self, monkeypatch):
        """The recipe this fleet already paid for: plain `ssh host 'cmd'`
        connects, authenticates and returns NOTHING, and `-tt` needs the command
        on STDIN. Getting this wrong looks like a dead device."""
        module = _module()
        monkeypatch.setenv("SECRET", '{"username": "root", "password": "pw"}')
        captured = []
        monkeypatch.setattr(module.subprocess, "run",
                            self._fake_run(captured, stdout="ok"))

        module.read_device_file("10.0.0.9", "/mnt/onboard/b.kepub.epub", dry_run=False)

        (cmd, kwargs), = captured
        assert "-tt" in cmd, cmd
        assert "root@10.0.0.9" in cmd
        assert kwargs.get("input"), "the command must go on stdin"
        assert "/mnt/onboard/b.kepub.epub" in kwargs["input"]
        assert kwargs["input"].rstrip().endswith("exit"), (
            "an interactive shell that is never told to exit hangs until timeout"
        )
        # The path must not also be smuggled into argv, which is what makes the
        # command silently return nothing.
        assert not any(str(a).startswith("ls -l") for a in cmd), cmd

    def test_it_refuses_to_run_without_a_credential(self, monkeypatch):
        module = _module()
        monkeypatch.setenv("SECRET", "")
        with pytest.raises(SystemExit) as excinfo:
            module.read_device_file("10.0.0.9", "/x", dry_run=False)
        assert "secret exec" in str(excinfo.value)

    def test_a_dry_run_never_launches_ssh(self, monkeypatch):
        module = _module()
        captured = []
        monkeypatch.setattr(module.subprocess, "run", self._fake_run(captured))
        assert module.read_device_file("10.0.0.9", "/x", dry_run=True) is None
        assert captured == [], "a dry run reached for the network"

    def test_it_returns_both_streams_so_an_error_is_not_swallowed(self, monkeypatch):
        """dropbear writes some of what matters to stderr; dropping it would make
        a failed read look like an unchanged file, which is the WRONG answer in
        this experiment rather than merely a missing one."""
        module = _module()
        monkeypatch.setenv("SECRET", "pw")
        captured = []
        monkeypatch.setattr(module.subprocess, "run",
                            self._fake_run(captured, stdout="size line",
                                           stderr="Permission denied"))
        out = module.read_device_file("10.0.0.9", "/x", dry_run=False)
        assert "size line" in out and "Permission denied" in out

    def test_a_bare_string_credential_still_works(self, monkeypatch):
        """The vault entry is not guaranteed to be JSON; a raw password must not
        crash the run when the device is finally awake."""
        module = _module()
        monkeypatch.setenv("SECRET", "just-the-password")
        captured = []
        monkeypatch.setattr(module.subprocess, "run", self._fake_run(captured, stdout="ok"))
        module.read_device_file("10.0.0.9", "/x", dry_run=False)
        (cmd, kwargs), = captured
        assert "root@10.0.0.9" in cmd
        assert kwargs["env"]["SSHPASS"] == "just-the-password"

    def test_the_password_never_enters_argv(self, monkeypatch):
        """subprocess.TimeoutExpired embeds the WHOLE command list in its message.

        With `sshpass -p <password>` a timeout prints the device's root password in
        cleartext — into scrollback, into any log, and into the agent transcript
        this script is designed to be run from, which breaks the standing rule that
        a secret value never reaches a transcript. And a timeout is this script's
        EXPECTED failure: ConnectTimeout only covers TCP connect, so a Kobo that
        suspends mid-read hangs to the wall clock.
        """
        module = _module()
        monkeypatch.setenv("SECRET", '{"username": "root", "password": "s3cr3t-root-pw"}')
        captured = []
        monkeypatch.setattr(module.subprocess, "run", self._fake_run(captured, stdout="ok"))

        module.read_device_file("10.0.0.9", "/x", dry_run=False)

        (cmd, kwargs), = captured
        assert "s3cr3t-root-pw" not in " ".join(str(a) for a in cmd), cmd
        assert "-p" not in cmd, "sshpass -p puts the secret in argv; use -e"
        assert "-e" in cmd
        assert kwargs["env"]["SSHPASS"] == "s3cr3t-root-pw"

    def test_a_timeout_reports_without_the_command_line(self, monkeypatch):
        """Even with -e, an unhandled TimeoutExpired would print the argv. Catch it."""
        module = _module()
        monkeypatch.setenv("SECRET", "s3cr3t-root-pw")

        def run(cmd, **kwargs):
            raise module.subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(module.subprocess, "run", run)
        with pytest.raises(SystemExit) as excinfo:
            module.read_device_file("10.0.0.9", "/x", dry_run=False)
        message = str(excinfo.value)
        assert "s3cr3t-root-pw" not in message, message
        assert "timed out" in message
        # `raise X from None` sets __suppress_context__, NOT __cause__ — a bare
        # `raise X` inside an except block already leaves __cause__ as None while
        # chaining via __context__. Asserting on __cause__ therefore passed with
        # the `from None` removed; mutation caught it.
        assert excinfo.value.__suppress_context__, (
            "the original TimeoutExpired is still chained, so Python prints its "
            "message — which contains the whole command list, password included — "
            "as 'During handling of the above exception...'"
        )

    def test_the_host_key_is_pinned_to_a_persistent_file(self, monkeypatch):
        """`--host` moves with DHCP, so the address is not proof of identity.

        Discarding known_hosts to /dev/null means a lease that has moved hands the
        Kobo root password to whatever now answers on that address, with no
        "host key changed" warning. A persistent file with accept-new still learns
        a new host once but REFUSES a changed key.
        """
        module = _module()
        monkeypatch.setenv("SECRET", "pw")
        captured = []
        monkeypatch.setattr(module.subprocess, "run", self._fake_run(captured, stdout="ok"))

        module.read_device_file("10.0.0.9", "/x", dry_run=False)

        (cmd, _kwargs), = captured
        joined = " ".join(str(a) for a in cmd)
        assert "UserKnownHostsFile=/dev/null" not in joined, joined
        assert "StrictHostKeyChecking=no" not in joined, joined
        assert "StrictHostKeyChecking=accept-new" in joined, joined
        assert ".kobo_known_hosts" in joined, joined


def test_the_device_path_cannot_smuggle_a_command(monkeypatch):
    """`--device-path` is interpolated into a string a SHELL on the device runs.

    Unquoted, a path containing `;` would execute on the Kobo — which breaks the
    one promise this script makes, that every device command is a read. Found by
    asking the question the review gate asks about anything touching subprocess.
    """
    module = _module()
    monkeypatch.setenv("SECRET", "pw")
    captured = []

    class _Result:
        stdout, stderr, returncode = "", "", 0

    def run(cmd, **kwargs):
        captured.append(kwargs.get("input", ""))
        return _Result()

    monkeypatch.setattr(module.subprocess, "run", run)

    module.read_device_file(
        "10.0.0.9", "/mnt/onboard/x.epub; rm -rf /mnt/onboard", dry_run=False)

    sent, = captured
    quoted = "'/mnt/onboard/x.epub; rm -rf /mnt/onboard'"
    assert quoted in sent, sent
    # The payload appearing INSIDE the quotes is harmless — that is the point of
    # quoting. What must not exist is an unquoted occurrence, which would start a
    # second command. Strip the quoted form and require nothing dangerous is left.
    assert "rm -rf" not in sent.replace(quoted, ""), sent
