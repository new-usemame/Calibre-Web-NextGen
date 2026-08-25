#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure what Nickel does after one annotations PATCH receives HTTP 503.

This is a disposable-book hardware experiment for finding F-5c1146. It does
not change CWNG's live PATCH handler. Instead, while this process is running,
it is a narrowly-scoped reverse proxy which refuses exactly one well-formed
PATCH for exactly one explicitly named ContentId. Every other request is
forwarded to ``--upstream`` unchanged.

Safety model
------------
* dry-run by default; no SSH, listener, prompt, or local write without --go;
* the Calibre book id must resolve to the exact --content-id;
* the book must have zero server annotations across all users;
* KoboReader.sqlite is copied off-device before each read and opened locally
  with SQLite mode=ro + query_only; no command ever writes the device;
* the first device snapshot must contain zero Bookmark rows for the book;
* after the operator creates the sacrificial highlight, exactly one active
  highlight row must exist before the failure proxy is armed;
* only one updated annotation, with no deletion delta, is eligible for the
  one-shot 503. GET, DELETE, other books, malformed bodies, and batches pass
  through unchanged.

The operator can leave scripts/measure_kobo_redelivery.py waiting at its
"SYNC THE DEVICE NOW" prompt. The same physical wake and sync then measures
F-3e383a and this experiment together.

Routing is network-level only: the proxy must listen at the address the Kobo
already believes is CWNG, while ``--upstream`` names the real CWNG origin.
Never edit the device's ``reading_services_host`` or production CWNG config.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import gzip
import hashlib
import http.client
import http.server
import json
import os
import shlex
import sqlite3
import ssl
import subprocess
import sys
import threading
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path


SSHPASS = "/opt/homebrew/bin/sshpass"
DEVICE_DB_BEGIN = "__CWNG_KOBO_DB_BEGIN__"
DEVICE_DB_END = "__CWNG_KOBO_DB_END__"
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class SafetyRefusal(SystemExit):
    """The experiment did not arm because a fail-closed safety gate fired."""


@dataclasses.dataclass(frozen=True)
class DeviceAnnotation:
    bookmark_id: str
    annotation_type: str
    hidden: str


@dataclasses.dataclass
class PatchState:
    failed_annotation_id: str | None = None
    failure_count: int = 0
    retry_count: int = 0
    unsafe_target_patch_count: int = 0


def canonical_content_id(value):
    try:
        return str(uuid.UUID(str(value).strip().strip("{}")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SafetyRefusal(f"REFUSING: invalid ContentId UUID: {value!r}") from exc


def _sqlite_ro(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def server_annotation_count(app_db, book_id):
    """Count this book's annotations across every user, read-only."""
    conn = _sqlite_ro(app_db)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM annotation WHERE book_id = ?", (book_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def metadata_content_id(metadata_db, book_id):
    """Resolve the selected Calibre row to its UUID, read-only."""
    conn = _sqlite_ro(metadata_db)
    try:
        row = conn.execute("SELECT uuid FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    if row is None or not row[0]:
        raise SafetyRefusal(f"REFUSING: metadata book {book_id} has no UUID")
    return canonical_content_id(row[0])


def metadata_book_ids_for_content(metadata_db, content_id):
    """Require the proxy UUID to name one Calibre row, not an ambiguous set."""
    conn = _sqlite_ro(metadata_db)
    try:
        rows = conn.execute(
            "SELECT id FROM books WHERE lower(trim(uuid, '{} ')) = ? ORDER BY id",
            (content_id,),
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]


def require_safe_server_book(app_db, metadata_db, book_id, content_id):
    actual = metadata_content_id(metadata_db, book_id)
    if actual != content_id:
        raise SafetyRefusal(
            f"REFUSING: metadata book {book_id} is {actual}, not requested {content_id}"
        )
    matching_ids = metadata_book_ids_for_content(metadata_db, content_id)
    if matching_ids != [book_id]:
        raise SafetyRefusal(
            f"REFUSING: ContentId {content_id} maps to metadata book ids {matching_ids}, "
            f"not uniquely to {book_id}"
        )
    count = server_annotation_count(app_db, book_id)
    if count:
        raise SafetyRefusal(
            f"REFUSING: book {book_id} has {count} server annotation(s) across all users; "
            "only a disposable annotation-free book is authorized"
        )
    return count


def _credential():
    raw = os.environ.get("SECRET", "")
    try:
        parsed = json.loads(raw)
        return (
            parsed.get("username") or parsed.get("user") or "root",
            parsed.get("password") or parsed.get("secret") or parsed.get("value") or "",
        )
    except Exception:
        return "root", raw.strip()


def _known_hosts_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kobo_known_hosts")


def _device_backup_command(device_db):
    quoted = shlex.quote(device_db)
    return (
        f"printf '{DEVICE_DB_BEGIN}\\n'; "
        f"gzip -c < {quoted} | base64; "
        f"printf '\\n{DEVICE_DB_END}\\n'; exit\n"
    )


def _decode_device_backup(output):
    normalized = output.replace("\r", "")
    # dropbear's forced TTY may echo the command, including both marker
    # literals, before printing the command's actual output. The final begin
    # marker is therefore the only safe delimiter.
    start = normalized.rfind(DEVICE_DB_BEGIN)
    end = normalized.find(DEVICE_DB_END, start + len(DEVICE_DB_BEGIN))
    if start < 0 or end < 0:
        raise SafetyRefusal(
            "REFUSING: device database backup markers were not returned"
        )
    encoded = normalized[start + len(DEVICE_DB_BEGIN) : end]
    compact = "".join(encoded.split())
    try:
        compressed = base64.b64decode(compact, validate=True)
        data = gzip.decompress(compressed)
    except Exception as exc:
        raise SafetyRefusal(
            "REFUSING: device database backup was not valid gzip/base64"
        ) from exc
    if not data.startswith(b"SQLite format 3\x00"):
        raise SafetyRefusal("REFUSING: backed-up device file is not a SQLite database")
    return data


def backup_device_db(host, device_db, backup_dir):
    """Copy KoboReader.sqlite off-device using read-only device commands."""
    user, password = _credential()
    if not password:
        raise SafetyRefusal("no credential in $SECRET — run this under `secret exec`")

    child_env = dict(os.environ)
    child_env["SSHPASS"] = password
    command = _device_backup_command(device_db)
    try:
        result = subprocess.run(
            [
                SSHPASS,
                "-e",
                "ssh",
                "-tt",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                f"UserKnownHostsFile={_known_hosts_path()}",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "LogLevel=ERROR",
                f"{user}@{host}",
            ],
            input=command,
            capture_output=True,
            text=True,
            timeout=90,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise SafetyRefusal(
            f"device database backup timed out against {host}"
        ) from None
    if result.returncode != 0:
        raise SafetyRefusal(
            f"device database backup failed against {host} (ssh exit {result.returncode})"
        )
    data = _decode_device_backup((result.stdout or "") + (result.stderr or ""))

    directory = Path(backup_dir).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = (
        directory / f"KoboReader.{stamp}.{hashlib.sha256(data).hexdigest()[:12]}.sqlite"
    )
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    return destination


def device_annotations_from_backup(backup, content_id):
    """Read only the already-created local backup; never the live device DB."""
    conn = _sqlite_ro(backup)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(Bookmark)").fetchall()
        }
        required = {"BookmarkID", "VolumeID", "Type", "Hidden"}
        if not required.issubset(columns):
            raise SafetyRefusal(
                f"REFUSING: device Bookmark schema lacks {sorted(required - columns)}"
            )
        rows = conn.execute(
            """
            SELECT BookmarkID, Type, Hidden
              FROM Bookmark
             WHERE instr(lower(coalesce(VolumeID, '')), ?) > 0
             ORDER BY BookmarkID
            """,
            (content_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        DeviceAnnotation(str(row[0] or ""), str(row[1] or ""), str(row[2] or ""))
        for row in rows
    ]


def capture_device_snapshot(host, device_db, backup_dir, content_id):
    """Back up first, then query that immutable local copy in read-only mode."""
    backup = backup_device_db(host, device_db, backup_dir)
    rows = device_annotations_from_backup(backup, content_id)
    return backup, rows


def require_clean_device_baseline(rows):
    if rows:
        raise SafetyRefusal(
            f"REFUSING: disposable book already has {len(rows)} device Bookmark row(s); "
            "the baseline must be empty before the sacrificial highlight is created"
        )


def _is_hidden(value):
    return str(value).strip().casefold() not in {"", "0", "false", "none", "null"}


def require_one_sacrificial_highlight(rows):
    if len(rows) != 1:
        raise SafetyRefusal(
            f"REFUSING: expected exactly one newly-created sacrificial highlight, found {len(rows)} rows"
        )
    row = rows[0]
    if (
        not row.bookmark_id
        or row.annotation_type.casefold() != "highlight"
        or _is_hidden(row.hidden)
    ):
        raise SafetyRefusal(
            "REFUSING: the one sacrificial row must be an active highlight with a BookmarkID"
        )
    return row


def _connection_tokens(headers):
    tokens = set()
    for name, value in headers:
        if name.lower() == "connection":
            tokens.update(
                part.strip().lower() for part in value.split(",") if part.strip()
            )
    return tokens


def _end_to_end_headers(headers):
    blocked = HOP_BY_HOP | _connection_tokens(headers)
    return [(name, value) for name, value in headers if name.lower() not in blocked]


def _target_annotation_path(raw_path, content_id):
    path = urllib.parse.urlsplit(raw_path).path
    parts = path.split("/")
    if (
        len(parts) != 7
        or parts[1:4] != ["api", "v3", "content"]
        or parts[5:] != ["annotations", ""]
    ):
        # Accept the ordinary no-trailing-slash route below. Keeping this branch
        # explicit prevents substring matches against a different endpoint.
        if (
            len(parts) != 6
            or parts[1:4] != ["api", "v3", "content"]
            or parts[5] != "annotations"
        ):
            return False
    try:
        return canonical_content_id(urllib.parse.unquote(parts[4])) == content_id
    except SafetyRefusal:
        return False


def _eligible_single_update(body):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    updated = payload.get("updatedAnnotations")
    deleted = payload.get("deletedAnnotationIds")
    if not isinstance(updated, list) or len(updated) != 1 or deleted not in (None, []):
        return None
    annotation = updated[0]
    if not isinstance(annotation, dict) or not isinstance(annotation.get("id"), str):
        return None
    annotation_id = annotation["id"].strip()
    return annotation_id or None


class PatchFailureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = ""
    sys_version = ""

    def log_message(self, _fmt, *_args):
        pass

    def _request_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks = []
            while True:
                line = self.rfile.readline(65537)
                if not line or len(line) > 65536:
                    raise ValueError("invalid chunked request")
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(chunks)
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("truncated chunked request")
                chunks.append(chunk)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("truncated request body")
        return body

    def _send_experiment_503(self):
        body = json.dumps(
            {"error": "controlled disposable-book PATCH failure"}, separators=(",", ":")
        ).encode()
        self.send_response_only(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "30")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _forward(self, body):
        upstream = self.server.upstream
        connection_cls = (
            http.client.HTTPSConnection
            if upstream.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_cls(
            upstream.hostname, upstream.port, timeout=self.server.timeout
        )
        try:
            path = self.path if self.path.startswith("/") else "/" + self.path
            if upstream.path and upstream.path != "/":
                path = upstream.path.rstrip("/") + path
            connection.putrequest(
                self.command, path, skip_host=True, skip_accept_encoding=True
            )
            incoming = list(self.headers.raw_items())
            saw_length = False
            for name, value in _end_to_end_headers(incoming):
                lower = name.lower()
                if lower == "host":
                    continue
                if lower == "content-length":
                    value = str(len(body))
                    saw_length = True
                connection.putheader(name, value)
            host = upstream.hostname
            default_port = 443 if upstream.scheme == "https" else 80
            if upstream.port and upstream.port != default_port:
                host = f"{host}:{upstream.port}"
            connection.putheader("Host", host)
            if body and not saw_length:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body if body else None)
            response = connection.getresponse()
            response_body = response.read()
            response_headers = _end_to_end_headers(list(response.getheaders()))
            self.send_response_only(response.status, response.reason)
            for name, value in response_headers:
                if name.lower() == "content-length":
                    continue
                self.send_header(name, value)
            # The upstream body is fully buffered, so frame the exact bytes this
            # handler will emit. Transfer-Encoding was removed with the other
            # hop-by-hop headers above; never forward its now-invalid framing.
            emitted_body = b"" if self.command == "HEAD" else response_body
            self.send_header("Content-Length", str(len(emitted_body)))
            self.end_headers()
            self.wfile.write(emitted_body)
            self.wfile.flush()
        except Exception:
            body = b'{"error":"upstream unavailable"}'
            self.send_response_only(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "30")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        finally:
            connection.close()

    def _proxy(self):
        try:
            body = self._request_body()
        except Exception:
            self.send_error(400)
            return

        is_target_patch = self.command == "PATCH" and _target_annotation_path(
            self.path, self.server.content_id
        )
        if is_target_patch:
            annotation_id = _eligible_single_update(body)
            with self.server.state_lock:
                state = self.server.patch_state
                if annotation_id is None or (
                    self.server.expected_annotation_id is not None
                    and annotation_id != self.server.expected_annotation_id
                ):
                    state.unsafe_target_patch_count += 1
                elif state.failure_count == 0:
                    state.failed_annotation_id = annotation_id
                    state.failure_count = 1
                    self.server.events.append(
                        {
                            "event": "failed_once",
                            "annotation_id": annotation_id,
                        }
                    )
                    self._send_experiment_503()
                    return
                elif annotation_id == state.failed_annotation_id:
                    state.retry_count += 1
                    self.server.events.append(
                        {
                            "event": "retry_seen",
                            "annotation_id": annotation_id,
                        }
                    )
                else:
                    state.unsafe_target_patch_count += 1
        self._forward(body)

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_PATCH = _proxy
    do_DELETE = _proxy
    do_HEAD = _proxy
    do_OPTIONS = _proxy


class PatchFailureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        *,
        upstream,
        content_id,
        expected_annotation_id=None,
        timeout=15.0,
    ):
        super().__init__(address, PatchFailureHandler)
        self.upstream = urllib.parse.urlsplit(upstream)
        if self.upstream.scheme not in {"http", "https"} or not self.upstream.hostname:
            raise SafetyRefusal("REFUSING: --upstream must be an absolute HTTP(S) URL")
        self.content_id = canonical_content_id(content_id)
        self.expected_annotation_id = expected_annotation_id
        self.timeout = timeout
        self.patch_state = PatchState()
        self.state_lock = threading.Lock()
        self.events = []


def print_decision_table():
    print("\nDECISION TABLE — what the device outcome means for F-5c1146")
    print("  RETRIED + ROW PRESENT:")
    print(
        "    A transient non-success preserves and re-offers the delta. The fix may return 503"
    )
    print("    when local capture is lost; the device supplies the retry path.")
    print("  ROW PRESENT, NO RETRY AFTER AN EXPLICIT SECOND SYNC:")
    print(
        "    503 preserves the device copy but does not heal CWNG. The fix needs durable staging/"
    )
    print("    replay; changing the status alone is insufficient.")
    print("  ROW MISSING AFTER 503:")
    print(
        "    Do NOT ship a non-success response for capture loss. The fix must durably capture"
    )
    print(
        "    before answering success (or use another proven mechanism), because 503 loses data."
    )
    print("  NO ELIGIBLE PATCH / MULTIPLE OR CHANGED ROWS / DB UNREADABLE:")
    print(
        "    INCONCLUSIVE. Do not change the live handler; repeat only with a clean disposable book."
    )


def print_routing_constraint():
    print("\nROUTING SAFETY — NETWORK LEVEL ONLY")
    print("  Annotations use reading_services_host, not api_endpoint.")
    print(
        "  Run this proxy on the address the Kobo already believes is CWNG, and point"
    )
    print("  --upstream at the real CWNG origin.")
    print("  Kobo Clara BW firmware 4.42.23291 was measured to stop syncing after a")
    print("  device-side reading_services_host edit.")
    print("  DO NOT edit the device or production CWNG configuration.")


def classify_result(state, rows, expected_annotation_id=None):
    if state.failure_count != 1 or state.unsafe_target_patch_count:
        return "INCONCLUSIVE"
    if not rows:
        return "ROW_MISSING_AFTER_503"
    if len(rows) != 1:
        return "INCONCLUSIVE"
    row = rows[0]
    if isinstance(row, DeviceAnnotation):
        if _is_hidden(row.hidden):
            return "ROW_MISSING_AFTER_503"
        if (
            expected_annotation_id is not None
            and row.bookmark_id != expected_annotation_id
        ):
            return "INCONCLUSIVE"
        if row.annotation_type.casefold() != "highlight":
            return "INCONCLUSIVE"
    if state.retry_count:
        return "RETRIED_AND_PRESENT"
    return "PRESENT_NOT_RETRIED"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument(
        "--content-id", required=True, help="exact disposable Calibre/Kobo UUID"
    )
    parser.add_argument("--app-db", required=True, help="CWNG app.db")
    parser.add_argument("--metadata-db", required=True, help="Calibre metadata.db")
    parser.add_argument("--device-host", default="10.0.20.250")
    parser.add_argument("--device-db", default="/mnt/onboard/.kobo/KoboReader.sqlite")
    parser.add_argument(
        "--backup-dir", required=True, help="persistent local directory for DB backups"
    )
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--upstream", required=True, help="the ordinary CWNG reading-services origin"
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument(
        "--go", action="store_true", help="arm the device/proxy experiment"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    content_id = canonical_content_id(args.content_id)
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SafetyRefusal(
            "REFUSING: --tls-cert and --tls-key must be supplied together"
        )

    print(
        "Kobo annotations PATCH failure measurement"
        + ("" if args.go else "  [DRY RUN]")
    )
    print(f"  disposable book {args.book_id}, ContentId {content_id}")
    print_routing_constraint()
    print_decision_table()

    require_safe_server_book(
        args.app_db,
        args.metadata_db,
        args.book_id,
        content_id,
    )
    print("\n1. server gate: exact metadata UUID, zero annotations across all users")

    if not args.go:
        print(
            "2. [dry run] would back up KoboReader.sqlite off-device, then read the backup mode=ro"
        )
        print("3. [dry run] would refuse any pre-existing Bookmark row for this book")
        print(
            "4. [dry run] would require exactly one newly-created sacrificial highlight"
        )
        print(
            "5. [dry run] would listen only while running and fail one exact one-update PATCH"
        )
        print(
            "\n[DRY RUN] no SSH, listener, prompt, backup, or mutation occurred. Re-run with --go."
        )
        return 0

    baseline_backup, baseline_rows = capture_device_snapshot(
        args.device_host,
        args.device_db,
        args.backup_dir,
        content_id,
    )
    require_clean_device_baseline(baseline_rows)
    print(f"2. device gate: backup {baseline_backup} contains zero rows for this book")

    input(
        "\nCreate ONE sacrificial highlight in the disposable book WITHOUT syncing, then press Enter. "
    )
    staged_backup, staged_rows = capture_device_snapshot(
        args.device_host,
        args.device_db,
        args.backup_dir,
        content_id,
    )
    sacrificial = require_one_sacrificial_highlight(staged_rows)
    # Recheck after the human pause. An accidental sync must not turn a stale
    # preflight into permission to experiment on a now-populated server book.
    require_safe_server_book(args.app_db, args.metadata_db, args.book_id, content_id)
    print(
        f"3. staged gate: backup {staged_backup} contains only {sacrificial.bookmark_id}"
    )

    server = PatchFailureServer(
        (args.listen, args.port),
        upstream=args.upstream,
        content_id=content_id,
        expected_annotation_id=sacrificial.bookmark_id,
        timeout=args.timeout,
    )
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scheme = "https" if args.tls_cert else "http"
    print(f"4. ARMED: {scheme}://{args.listen}:{server.server_port}")
    print(
        "   Only the first exact one-update PATCH for this ContentId can receive 503."
    )
    print(
        "   Leave measure_kobo_redelivery.py at its SYNC prompt; this same sync settles both."
    )
    print_routing_constraint()

    try:
        input(
            "\nWith the network-level route already in place, SYNC ONCE, then press Enter. "
        )
        after_one_backup, after_one_rows = capture_device_snapshot(
            args.device_host,
            args.device_db,
            args.backup_dir,
            content_id,
        )
        print(f"5. first post-sync backup: {after_one_backup}")

        state = server.patch_state
        first_result = classify_result(
            state,
            after_one_rows,
            expected_annotation_id=sacrificial.bookmark_id,
        )
        final_rows = after_one_rows
        final_backup = after_one_backup
        if first_result == "PRESENT_NOT_RETRIED":
            input(
                "No retry observed yet. SYNC ONCE MORE during this same wake, then press Enter. "
            )
            final_backup, final_rows = capture_device_snapshot(
                args.device_host,
                args.device_db,
                args.backup_dir,
                content_id,
            )
        result = classify_result(
            server.patch_state,
            final_rows,
            expected_annotation_id=sacrificial.bookmark_id,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print("\nRESULT: " + result)
    print(f"  final device backup: {final_backup}")
    print(f"  failed PATCH count: {server.patch_state.failure_count}")
    print(f"  matching retry count: {server.patch_state.retry_count}")
    print(
        f"  unsafe/multi/different target PATCH count: {server.patch_state.unsafe_target_patch_count}"
    )
    print(f"  final target Bookmark rows: {len(final_rows)}")
    print_decision_table()
    return 0 if result != "INCONCLUSIVE" else 2


if __name__ == "__main__":
    sys.exit(main())
