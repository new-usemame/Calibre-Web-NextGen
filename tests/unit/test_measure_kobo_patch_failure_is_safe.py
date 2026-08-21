# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Safety contract for the disposable-book Kobo PATCH experiment."""

from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import http.server
import importlib.util
import inspect
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "measure_kobo_patch_failure.py"
TARGET = "11111111-2222-4333-8444-555555555555"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _module():
    spec = importlib.util.spec_from_file_location("measure_kobo_patch_failure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _app_db(tmp_path, count=0):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER)")
    conn.executemany("INSERT INTO annotation (book_id) VALUES (?)", [(7,)] * count)
    conn.commit()
    conn.close()
    return path


def _metadata_db(tmp_path, content_id=TARGET):
    path = tmp_path / "metadata.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, uuid TEXT)")
    conn.execute("INSERT INTO books (id, uuid) VALUES (?, ?)", (7, content_id))
    conn.commit()
    conn.close()
    return path


def _device_db(path, rows=()):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE Bookmark (BookmarkID TEXT, VolumeID TEXT, Type TEXT, Hidden TEXT)"
    )
    conn.executemany(
        "INSERT INTO Bookmark (BookmarkID, VolumeID, Type, Hidden) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _main_args(tmp_path, *, go=False, content_id=TARGET, annotations=0):
    args = [
        "--book-id",
        "7",
        "--content-id",
        content_id,
        "--app-db",
        str(_app_db(tmp_path, annotations)),
        "--metadata-db",
        str(_metadata_db(tmp_path, TARGET)),
        "--backup-dir",
        str(tmp_path / "backups"),
        "--upstream",
        "http://127.0.0.1:9",
    ]
    if go:
        args.append("--go")
    return args


def test_the_script_exists_and_loads():
    assert SCRIPT.is_file(), SCRIPT
    assert callable(_module().main)


def test_no_go_means_no_device_listener_prompt_or_local_write(tmp_path, monkeypatch):
    module = _module()
    reached = []

    def forbidden(*_args, **_kwargs):
        reached.append(True)
        raise AssertionError("dry run crossed into an active surface")

    monkeypatch.setattr(module, "capture_device_snapshot", forbidden)
    monkeypatch.setattr(module, "PatchFailureServer", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)

    assert module.main(_main_args(tmp_path)) == 0
    assert reached == []
    assert not (tmp_path / "backups").exists()


def test_it_refuses_any_server_annotation_before_device_access(tmp_path, monkeypatch):
    module = _module()
    reached = []
    monkeypatch.setattr(
        module,
        "capture_device_snapshot",
        lambda *_args, **_kwargs: reached.append(True),
    )

    with pytest.raises(module.SafetyRefusal, match="server annotation"):
        module.main(_main_args(tmp_path, go=True, annotations=1))
    assert reached == []


def test_it_refuses_a_metadata_uuid_mismatch_before_device_access(
    tmp_path, monkeypatch
):
    module = _module()
    reached = []
    monkeypatch.setattr(
        module,
        "capture_device_snapshot",
        lambda *_args, **_kwargs: reached.append(True),
    )

    with pytest.raises(module.SafetyRefusal, match="not requested"):
        module.main(_main_args(tmp_path, go=True, content_id=OTHER))
    assert reached == []


def test_it_refuses_a_duplicate_metadata_uuid(tmp_path):
    module = _module()
    app_db = _app_db(tmp_path)
    metadata_db = _metadata_db(tmp_path)
    conn = sqlite3.connect(metadata_db)
    conn.execute("INSERT INTO books (id, uuid) VALUES (?, ?)", (8, TARGET.upper()))
    conn.commit()
    conn.close()
    with pytest.raises(module.SafetyRefusal, match="not uniquely"):
        module.require_safe_server_book(app_db, metadata_db, 7, TARGET)


def test_it_refuses_any_preexisting_device_row():
    module = _module()
    dirty = [module.DeviceAnnotation("old", "highlight", "0")]
    with pytest.raises(module.SafetyRefusal, match="baseline must be empty"):
        module.require_clean_device_baseline(dirty)
    assert module.require_clean_device_baseline([]) is None


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            ("new-1", "highlight", "0"),
            ("new-2", "highlight", "0"),
        ],
        [("", "highlight", "0")],
        [("new-1", "note", "0")],
        [("new-1", "highlight", "1")],
    ],
)
def test_only_one_new_active_highlight_can_be_sacrificed(rows):
    module = _module()
    materialized = [module.DeviceAnnotation(*row) for row in rows]
    with pytest.raises(module.SafetyRefusal):
        module.require_one_sacrificial_highlight(materialized)


def test_the_single_sacrificial_highlight_case_is_not_vacuous():
    module = _module()
    row = module.DeviceAnnotation("new-1", "highlight", "0")
    assert module.require_one_sacrificial_highlight([row]) == row


def test_server_queries_are_bound_and_both_databases_are_read_only(tmp_path):
    module = _module()
    app_db = _app_db(tmp_path, 0)
    metadata_db = _metadata_db(tmp_path)
    before = {
        app_db: hashlib.sha256(app_db.read_bytes()).digest(),
        metadata_db: hashlib.sha256(metadata_db.read_bytes()).digest(),
    }
    assert module.require_safe_server_book(app_db, metadata_db, 7, TARGET) == 0
    assert before == {
        app_db: hashlib.sha256(app_db.read_bytes()).digest(),
        metadata_db: hashlib.sha256(metadata_db.read_bytes()).digest(),
    }
    source = (
        inspect.getsource(module.server_annotation_count)
        + inspect.getsource(module.metadata_content_id)
        + inspect.getsource(module.metadata_book_ids_for_content)
    )
    assert "WHERE book_id = ?" in source and "WHERE id = ?" in source
    assert "(book_id,)" in source


def test_every_sqlite_connection_is_forced_read_only_twice():
    module = _module()
    source = inspect.getsource(module._sqlite_ro)
    assert "?mode=ro" in source, "the OS-level SQLite open must be read-only"
    assert "PRAGMA query_only = ON" in source, (
        "SQLite must also reject writes at the connection"
    )


def test_device_sqlite_is_queried_only_after_backup_and_never_changes(tmp_path):
    module = _module()
    backup = _device_db(
        tmp_path / "KoboReader.backup.sqlite",
        [
            (
                "ann-1",
                "file:///mnt/onboard/urn:uuid:{" + TARGET.upper() + "}",
                "highlight",
                "0",
            )
        ],
    )
    before = hashlib.sha256(backup.read_bytes()).digest()
    rows = module.device_annotations_from_backup(backup, TARGET)
    assert rows == [module.DeviceAnnotation("ann-1", "highlight", "0")]
    assert hashlib.sha256(backup.read_bytes()).digest() == before
    source = inspect.getsource(module.device_annotations_from_backup).upper()
    assert (
        "UPDATE " not in source and "DELETE " not in source and "INSERT " not in source
    )
    assert "?" in source, "the ContentId must remain a bound SQL parameter"


def test_snapshot_creates_the_backup_before_opening_sqlite(tmp_path, monkeypatch):
    module = _module()
    backup = tmp_path / "persisted.sqlite"
    order = []

    def make_backup(*_args):
        order.append("backup")
        _device_db(backup)
        return backup

    def read_backup(path, _content_id):
        assert path == backup and path.exists()
        order.append("read")
        return []

    monkeypatch.setattr(module, "backup_device_db", make_backup)
    monkeypatch.setattr(module, "device_annotations_from_backup", read_backup)
    assert module.capture_device_snapshot("host", "/db", tmp_path, TARGET) == (
        backup,
        [],
    )
    assert order == ["backup", "read"]


class _RunResult:
    returncode = 0
    stderr = ""

    def __init__(self, stdout):
        self.stdout = stdout


def test_device_backup_uses_read_only_commands_and_keeps_password_out_of_argv(
    tmp_path,
    monkeypatch,
):
    module = _module()
    source_db = _device_db(tmp_path / "source.sqlite")
    encoded = base64.b64encode(gzip.compress(source_db.read_bytes())).decode("ascii")
    echo = module._device_backup_command("/mnt/onboard/.kobo/KoboReader.sqlite")
    output = (
        f"{echo}\r\n{module.DEVICE_DB_BEGIN}\r\n{encoded}\r\n{module.DEVICE_DB_END}\r\n"
    )
    captured = []

    def run(cmd, **kwargs):
        captured.append((cmd, kwargs))
        return _RunResult(output)

    monkeypatch.setenv("SECRET", '{"username":"root","password":"device-secret"}')
    monkeypatch.setattr(module.subprocess, "run", run)
    backup = module.backup_device_db(
        "10.0.0.9",
        "/mnt/onboard/.kobo/KoboReader.sqlite",
        tmp_path / "backups",
    )

    assert backup.is_file() and backup.read_bytes() == source_db.read_bytes()
    ((cmd, kwargs),) = captured
    joined = " ".join(str(part) for part in cmd)
    assert "device-secret" not in joined
    assert "-p" not in cmd and "-e" in cmd
    assert kwargs["env"]["SSHPASS"] == "device-secret"
    assert "StrictHostKeyChecking=accept-new" in joined
    assert ".kobo_known_hosts" in joined
    sent = kwargs["input"]
    assert "gzip -c <" in sent and "| base64" in sent
    for mutator in ("sqlite3", "cp ", "mv ", "rm ", "> ", ">>"):
        assert mutator not in sent, sent


def test_device_path_is_shell_quoted():
    module = _module()
    command = module._device_backup_command("/mnt/onboard/x; rm -rf /mnt/onboard")
    quoted = "'/mnt/onboard/x; rm -rf /mnt/onboard'"
    assert quoted in command
    assert "rm -rf" not in command.replace(quoted, "")


def test_timeout_hides_the_subprocess_command(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setenv("SECRET", "device-secret")

    def run(cmd, **_kwargs):
        raise module.subprocess.TimeoutExpired(cmd, 90)

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.SafetyRefusal) as excinfo:
        module.backup_device_db("host", "/db", tmp_path)
    assert "device-secret" not in str(excinfo.value)
    assert excinfo.value.__suppress_context__


class _Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _fmt, *_args):
        pass

    def _reply(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        self.server.seen.append((self.command, self.path, body))
        reply = b'{"upstream":true}'
        self.send_response_only(207, "Multi-Status")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.send_header("ETag", 'W/"test"')
        self.end_headers()
        self.wfile.write(reply)

    do_GET = _reply
    do_PATCH = _reply
    do_DELETE = _reply


class _FramingUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    reply = b'{"chunked":true}'

    def log_message(self, _fmt, *_args):
        pass

    def do_GET(self):
        self.send_response_only(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(f"{len(self.reply):x}\r\n".encode())
        self.wfile.write(self.reply + b"\r\n0\r\n\r\n")

    def do_HEAD(self):
        self.send_response_only(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.reply)))
        self.end_headers()


class _RunningProxy:
    def __init__(self, module, expected="ann-1", upstream_handler=_Upstream):
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), upstream_handler
        )
        self.upstream.seen = []
        self.up_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.up_thread.start()
        self.proxy = module.PatchFailureServer(
            ("127.0.0.1", 0),
            upstream=f"http://127.0.0.1:{self.upstream.server_port}",
            content_id=TARGET,
            expected_annotation_id=expected,
        )
        self.proxy_thread = threading.Thread(
            target=self.proxy.serve_forever, daemon=True
        )
        self.proxy_thread.start()

    def request(self, method, path, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.proxy.server_port, timeout=3
        )
        conn.request(
            method, path, body=body, headers={"Content-Type": "application/json"}
        )
        response = conn.getresponse()
        result = (response.status, response.read(), response.headers)
        conn.close()
        return result

    def close(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.proxy_thread.join(timeout=3)
        self.up_thread.join(timeout=3)


def _single(annotation_id="ann-1"):
    return {
        "updatedAnnotations": [{"id": annotation_id, "highlightedText": "sacrificial"}],
        "deletedAnnotationIds": [],
    }


def test_only_the_exact_disposable_single_update_can_receive_503():
    module = _module()
    running = _RunningProxy(module)
    try:
        cases = [
            ("GET", f"/api/v3/content/{TARGET}/annotations", None),
            ("PATCH", f"/api/v3/content/{OTHER}/annotations", _single()),
            ("PATCH", f"/api/v3/content/{TARGET}/annotations-extra", _single()),
            (
                "PATCH",
                f"/api/v3/content/{TARGET}/annotations",
                {
                    "updatedAnnotations": [{"id": "a"}, {"id": "b"}],
                    "deletedAnnotationIds": [],
                },
            ),
            (
                "PATCH",
                f"/api/v3/content/{TARGET}/annotations",
                {
                    "updatedAnnotations": [{"id": "ann-1"}],
                    "deletedAnnotationIds": ["old"],
                },
            ),
            ("PATCH", f"/api/v3/content/{TARGET}/annotations", _single("different-id")),
        ]
        for method, path, payload in cases:
            status, _body, _headers = running.request(method, path, payload)
            assert status == 207, (method, path, payload)
        assert running.proxy.patch_state.failure_count == 0
        assert len(running.upstream.seen) == len(cases)

        status, body, headers = running.request(
            "PATCH",
            f"/api/v3/content/{TARGET}/annotations",
            _single(),
        )
        assert status == 503 and headers["Retry-After"] == "30"
        assert b"controlled disposable-book" in body
        assert running.proxy.patch_state.failure_count == 1
        assert len(running.upstream.seen) == len(cases), (
            "the failed PATCH leaked upstream"
        )

        status, body, headers = running.request(
            "PATCH",
            f"/api/v3/content/{TARGET}/annotations",
            _single(),
        )
        assert status == 207 and body == b'{"upstream":true}'
        assert headers["ETag"] == 'W/"test"'
        assert running.proxy.patch_state.failure_count == 1
        assert running.proxy.patch_state.retry_count == 1
        assert len(running.upstream.seen) == len(cases) + 1
    finally:
        running.close()


def test_relayed_responses_are_reframed_to_the_exact_emitted_bytes():
    module = _module()
    running = _RunningProxy(module, upstream_handler=_FramingUpstream)
    try:
        status, body, headers = running.request("GET", "/chunked")
        assert status == 200
        assert headers.get_all("Content-Length") == [str(len(_FramingUpstream.reply))]
        assert "Transfer-Encoding" not in headers
        assert body == _FramingUpstream.reply

        status, body, headers = running.request("HEAD", "/head")
        assert status == 200
        assert headers.get_all("Content-Length") == ["0"]
        assert "Transfer-Encoding" not in headers
        assert body == b""
    finally:
        running.close()


def test_routing_correction_is_printed_at_startup_and_immediately_before_sync(
    tmp_path, monkeypatch, capsys
):
    module = _module()
    row = module.DeviceAnnotation("ann-1", "highlight", "0")
    snapshots = iter(
        [
            (tmp_path / "baseline.gz", []),
            (tmp_path / "staged.gz", [row]),
            (tmp_path / "after.gz", [row]),
        ]
    )
    monkeypatch.setattr(
        module,
        "capture_device_snapshot",
        lambda *_args, **_kwargs: next(snapshots),
    )
    output_before_prompts = []

    def answer_prompt(_prompt):
        output_before_prompts.append(capsys.readouterr().out)
        return ""

    monkeypatch.setattr("builtins.input", answer_prompt)
    args = _main_args(tmp_path, go=True) + ["--port", "0"]
    assert module.main(args) == 2
    final_output = capsys.readouterr().out
    all_output = "".join(output_before_prompts) + final_output
    warning = "ROUTING SAFETY — NETWORK LEVEL ONLY"

    assert all_output.count(warning) == 2
    startup_output = output_before_prompts[0]
    assert startup_output.index(warning) < startup_output.index("DECISION TABLE")
    before_sync_output = output_before_prompts[1]
    assert before_sync_output.rstrip().endswith(
        "DO NOT edit the device or production CWNG configuration."
    )
    for phrase in (
        "reading_services_host, not api_endpoint",
        "address the Kobo already believes is CWNG",
        "--upstream at the real CWNG origin",
        "Kobo Clara BW firmware 4.42.23291",
        "measured to stop syncing",
    ):
        assert phrase in startup_output


@pytest.mark.parametrize(
    "state, rows, expected",
    [
        ("none", [], "INCONCLUSIVE"),
        ("failed", [], "ROW_MISSING_AFTER_503"),
        ("failed", ["row"], "PRESENT_NOT_RETRIED"),
        ("retried", ["row"], "RETRIED_AND_PRESENT"),
        ("unsafe", ["row"], "INCONCLUSIVE"),
    ],
)
def test_every_device_outcome_has_a_decision(state, rows, expected):
    module = _module()
    patch = module.PatchState()
    if state in {"failed", "retried", "unsafe"}:
        patch.failure_count = 1
    if state == "retried":
        patch.retry_count = 1
    if state == "unsafe":
        patch.unsafe_target_patch_count = 1
    assert module.classify_result(patch, rows) == expected


def test_hidden_or_changed_sacrificial_row_cannot_be_misclassified_as_preserved():
    module = _module()
    patch = module.PatchState(failure_count=1)
    hidden = module.DeviceAnnotation("ann-1", "highlight", "1")
    changed = module.DeviceAnnotation("ann-2", "highlight", "0")
    assert (
        module.classify_result(
            patch,
            [hidden],
            expected_annotation_id="ann-1",
        )
        == "ROW_MISSING_AFTER_503"
    )
    assert (
        module.classify_result(
            patch,
            [changed],
            expected_annotation_id="ann-1",
        )
        == "INCONCLUSIVE"
    )


def test_the_script_prints_what_every_outcome_means(capsys):
    module = _module()
    module.print_decision_table()
    output = capsys.readouterr().out
    for phrase in (
        "RETRIED + ROW PRESENT",
        "ROW PRESENT, NO RETRY",
        "ROW MISSING AFTER 503",
        "INCONCLUSIVE",
        "fix may return 503",
        "durable staging",
        "Do NOT ship a non-success",
        "Do not change the live handler",
    ):
        assert phrase in output


def test_live_patch_handler_has_no_experiment_bypass():
    source = (REPO / "cps" / "readingservices.py").read_text(encoding="utf-8")
    for needle in ("F-5c1146", "measure_kobo_patch", "PATCH_EXPERIMENT", "failed_once"):
        assert needle not in source
