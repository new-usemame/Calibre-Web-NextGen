# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import copy
import glob
import importlib
import io
import json
import os
import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from io import BytesIO

from flask import has_request_context, request, send_file
from flask_babel.speaklater import LazyString

from . import logger, config, constants, support_pack_state
from .about import collect_stats
from .spa import mount_cookie_path
from .services import parallel

log = logger.create()


# ---------------------------------------------------------------------------
# Sanitization for the support-debug zip (fork issue #312).
#
# The original implementation handed `settings.txt` + raw log files
# straight to the user with only a substring-based redaction in
# `config.to_dict()`. That filter missed plaintext fields like
# `mail_password`, the encrypted shadow `mail_password_e`, the JSON
# OAuth blob `mail_gmail_token`, and `config_ldap_serv_password` —
# every one a credential a non-technical user (the audience this
# feature exists for) might paste into a public GitHub issue.
#
# The functions below run between "read the live values" and "write
# into the zip." They never modify the running app's state.
# ---------------------------------------------------------------------------

REDACTED_PLACEHOLDER = "<redacted>"

# Explicit allow-list of field names that must always be redacted, even
# if `config.to_dict()`'s substring filter happens to miss them. Kept
# alphabetical for diff clarity.
_KNOWN_SECRET_FIELDS = frozenset({
    "config_github_oauth_clientsecret",
    "config_google_oauth_clientsecret",
    "config_hardcover_token",
    "config_ldap_serv_password",
    "config_ldap_serv_password_e",
    "mail_gmail_token",
    "mail_password",
    "mail_password_e",
})

# A sanitized archive can be shared outside the installation. These settings
# identify mail infrastructure or reveal where files are kept on that host.
_PRIVATE_INSTALL_FIELDS = frozenset({
    "config_access_logfile",
    "config_binariesdir",
    "config_calibre_dir",
    "config_calibre_split_dir",
    "config_certfile",
    "config_converterpath",
    "config_kepubifypath",
    "config_keyfile",
    "config_ldap_cacert_path",
    "config_ldap_cert_path",
    "config_ldap_key_path",
    "config_limiter_uri",
    "config_logfile",
    "config_rarfile_location",
    "mail_from",
    "mail_login",
    "mail_server",
})

# Substring matches for unknown/future fields. If the field name contains
# any of these as a whole token (case-insensitive), redact the value.
_SECRET_SUBSTRINGS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
)

# Defense-in-depth: even legitimate non-secret fields can carry a value
# that LOOKS like a JWT or a long bearer token. Redact those too.
# Three base64url segments separated by dots = JWT shape.
_JWT_PATTERN = re.compile(
    r"\b[A-Za-z0-9_\-]{8,}\."
    r"[A-Za-z0-9_\-]{8,}\."
    r"[A-Za-z0-9_\-]{16,}\b"
)
# A bare run of >= 40 base64-ish characters is very likely a token.
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9+/=_\-]{40,}\b")


def _looks_sensitive(field_name: str) -> bool:
    if field_name in _KNOWN_SECRET_FIELDS or field_name in _PRIVATE_INSTALL_FIELDS:
        return True
    lower = field_name.lower()
    return any(s in lower for s in _SECRET_SUBSTRINGS)


def _redact_value(value, *, aggressive=False):
    """Apply defense-in-depth regex redaction to a string value.

    Only operates on strings — lists, dicts, bools, ints, floats round-trip
    unchanged. The previous version called `str(value)` on everything,
    which turned `["a"]` into the string `"['a']"` in the exported
    JSON and broke any consumer that parsed the original type.

    `aggressive=True` applies the broader "long token shape" pattern,
    which catches arbitrary 40+ char base64-ish runs. It's only safe to
    enable on fields we already suspect (sensitive-key + opaque value)
    — applied to every field it scrubs legitimate long content like
    library paths, version strings, and UUID-like IDs.
    """
    if not isinstance(value, str) or value == "":
        return value
    text = _JWT_PATTERN.sub(REDACTED_PLACEHOLDER, value)
    if aggressive:
        text = _LONG_TOKEN_PATTERN.sub(REDACTED_PLACEHOLDER, text)
    return text


def _redact_for_export(settings_dict):
    """Return settings with secrets and private install fields replaced
    and JWT-shaped string values scrubbed.

    A field whose name LOOKS sensitive is only redacted if its value is
    a non-empty string — booleans and integers can't be credentials, and
    catching `config_password_min_length` (a policy integer) as a "leak"
    blanks legitimate diagnostic content. Container testing of fork
    issue #312 caught this false-positive.

    The broader "long token shape" heuristic only runs on fields whose
    KEY already matched the sensitive substring set — otherwise it
    blanks legitimate long values (URLs, library paths, hashes).

    Never mutates the caller's dict — the running app keeps the real
    values; only the export sees the redacted ones.
    """
    if not isinstance(settings_dict, dict):
        return settings_dict
    out = copy.deepcopy(settings_dict)
    for key in list(out.keys()):
        value = out[key]
        sensitive_name = _looks_sensitive(key)
        if sensitive_name and isinstance(value, str) and value:
            out[key] = REDACTED_PLACEHOLDER
            continue
        out[key] = _redact_value(value, aggressive=sensitive_name)
    return out


# ---------------------------------------------------------------------------
# Log line sanitization (fork issue #312).
#
# The live log on disk is unmodified — admins can still see full IPs,
# usernames, and paths there. The sanitizer only runs on the way INTO
# the zip the user sends to GitHub support.
# ---------------------------------------------------------------------------

# IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`) is the form Flask's
# `request.remote_addr` returns when the client connects over IPv6 to a
# v4 address. The kosync gate logs it; the IPv6 regex would otherwise
# match `::ffff:127` and leave a corrupted `.0.0.1` tail. We strip the
# whole IPv4-mapped form first, preserving loopback explicitly.
_IPV4_MAPPED_PATTERN = re.compile(
    r"::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    re.IGNORECASE,
)

# IPv4: four 1-3 digit groups. We intentionally PRESERVE 127.0.0.1 so
# loopback diagnostics stay legible.
_IPV4_PATTERN = re.compile(r"\b(?!127\.0\.0\.1\b)(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

# IPv6 — accept either:
#   * the full 8-group form (7 colons, no ::), e.g.
#     `2001:0db8:85a3:0000:0000:8a2e:0370:7334`
#   * a compressed form (with `::`) where at least ONE side has a hex
#     group — so bare `::` and unrelated `::` tokens in log content
#     (Python slice syntax `arr[1::2]`, C++ scope `Class::method`,
#     etc.) won't be redacted as IPs.
# Crucially does NOT match decimal timestamps like `12:00:00` — those
# have <4 colons and no `::`, so they fail all alternatives.
_IPV6_PATTERN = re.compile(
    r"(?:"
    # full 8-group form
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|"
    # compressed form with required prefix hex
    r"[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
    r"|"
    # compressed form with required suffix hex (catches `::1`, `::ffff:…`)
    r"(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?::[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*"
    r")"
)

# Per-install absolute paths likely to identify the user.
_CONFIG_PATH_PATTERN = re.compile(r"/config/")
_LIBRARY_PATH_PATTERN = re.compile(r"/calibre-library/")

# Auth header values.
_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_BASIC_PATTERN = re.compile(r"(Basic\s+)\S+", re.IGNORECASE)

# Kobo sync URLs carry the per-user auth token as a path segment
# (``/kobo/<auth_token>/v1/library/sync``, 32 hex chars from
# ``kobo_auth``). It is a bearer credential: anyone holding it can sync
# as that user, and it lands verbatim in the access log and in
# ``cps.kobo`` request lines. The 16-char floor keeps ``/kobo/v1`` style
# segments intact.
_KOBO_TOKEN_PATTERN = re.compile(r"(/kobo/)[0-9A-Za-z_\-]{16,}")

# KOReader progress URLs and diagnostics carry document and device references.
_KOSYNC_DOCUMENT_URL = re.compile(r"""(/kosync/syncs/progress/)[^/?\s"'<>]+""")
_KOSYNC_LOG_SOURCE = re.compile(
    r"^\[[^\]\r\n]+\] +[A-Z]+ \{cps\.(?:progress_syncing\.protocols\.)?kosync:\d+\} "
)
_KOSYNC_DOCUMENT_FIELD = re.compile(
    r"(\b(?:doc=|document=|document_checksum:\s*|checksum:\s*|book by checksum ))"
    r"[^\r\n]*?"
    # Preserve only the structured suffixes emitted after document values.
    r"(?=(?:>|, book_id: [\w-]+|, progress=[0-9.+-]+%|"
    r", incoming=[0-9.+-]+%, accepted=[0-9.+-]+%, incoming_device_id=<device>)?\r?$)"
)
_KOSYNC_DEVICE_FIELD = re.compile(r"(\bincoming_device_id=)[^\r\n]*")


# Email addresses (SMTP errors, login failures, send-to-eReader logs).
_EMAIL_PATTERN = re.compile(
    r"[\w.%+\-]+(?:@|%40)[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,}"
)

# Shortest account name we will replace by exact match. Anything shorter
# ("pi", "me") would mangle unrelated log text more often than it helps.
_MIN_KNOWN_NAME_LEN = 3

# Account names that are also the words logs are made of: HTTP methods, log
# levels, this scrubber's own placeholder words, and the default accounts and
# product names. Replacing these would rewrite every access-log line ("GET")
# or every admin hint ("Enable in Admin ->") and make the pack unreadable,
# and none of them identifies a person. Compared case-insensitively.
_NAME_SCRUB_SKIP = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options",
    "debug", "info", "warn", "warning", "error", "critical",
    "token", "user", "email", "redacted", "config", "library", "ip",
    "admin", "guest", "kobo", "opds", "kosync", "calibre",
})


# Sentinel for IPv4-mapped loopback during sanitization. The plain IPv6
# regex would otherwise match `::ffff:127` after the loopback was
# preserved, corrupting the line. We swap the preserved form for this
# marker before running IPv6, then restore it. Uses ASCII control chars
# that real log content cannot contain.
_LOOPBACK_V4M_MARKER = "\x01CWNG_LOOPBACK_V4M\x01"


def _maybe_redact_ipv4_mapped(match):
    """Preserve `::ffff:127.0.0.1` as loopback; redact other mapped v4."""
    ip = match.group(1)
    if ip == "127.0.0.1":
        return _LOOPBACK_V4M_MARKER
    return "<ip>"


def _install_path_patterns():
    """Patterns for this install's own config and library dirs, longest first."""
    candidates = (
        (getattr(constants, "CONFIG_DIR", None), "<config>"),
        (getattr(config, "config_calibre_dir", None), "<library>"),
    )
    patterns = []
    for path, token in candidates:
        if not isinstance(path, str) or not path.strip():
            continue
        path = os.path.normpath(path.strip())
        if not os.path.isabs(path) or path == "/":
            continue
        patterns.append(
            (re.compile(re.escape(path) + r"(?![\w.-])"), token, len(path))
        )
    patterns.sort(key=lambda entry: entry[2], reverse=True)
    return tuple((pattern, token) for pattern, token, _length in patterns)


def _sanitize_log_line(line: str, install_paths=()) -> str:
    """Apply PII/credential scrubbing to a single log line.

    ``install_paths`` comes from ``_install_path_patterns()``. Idempotent:
    running on already-sanitized text is a no-op.
    """
    if not line:
        return line
    # IPv4-mapped IPv6 BEFORE plain IPv6 so the regex sees the full
    # `::ffff:n.n.n.n` token instead of half-matching `::ffff:n` and
    # corrupting the tail. Loopback is shielded behind a marker so the
    # downstream IPv6 regex won't re-match the preserved literal.
    line = _IPV4_MAPPED_PATTERN.sub(_maybe_redact_ipv4_mapped, line)
    line = _IPV4_PATTERN.sub("<ip>", line)
    line = _IPV6_PATTERN.sub("<ip>", line)
    line = line.replace(_LOOPBACK_V4M_MARKER, "::ffff:127.0.0.1")
    # Paths, this install's own before the container defaults.
    for pattern, token in install_paths:
        line = pattern.sub(token, line)
    line = _CONFIG_PATH_PATTERN.sub("<config>/", line)
    line = _LIBRARY_PATH_PATTERN.sub("<library>/", line)
    # Auth headers.
    line = _BEARER_PATTERN.sub(r"\1<redacted>", line)
    line = _BASIC_PATTERN.sub(r"\1<redacted>", line)
    # Kobo auth token in the URL path.
    line = _KOBO_TOKEN_PATTERN.sub(r"\1<token>", line)
    line = _KOSYNC_DOCUMENT_URL.sub(r"\1<document>", line)
    if _KOSYNC_LOG_SOURCE.match(line):
        line = _KOSYNC_DEVICE_FIELD.sub(r"\1<device>", line)
        line = _KOSYNC_DOCUMENT_FIELD.sub(r"\1<document>", line)
    return line


class _LogScrubber:
    """Per-pack log scrubber: the stateless line sanitiser plus consistent
    pseudonyms for emails and known account names.

    The same email or username maps to the same label (``<email_1>``,
    ``<user_1>``) across every file in one pack, so a maintainer can still
    follow one account through the app log and the access log. The mapping
    is never written to the pack and a new scrubber is created per build,
    so labels cannot be correlated between packs.

    Account names cannot be found by pattern in free-text logs, so they are
    replaced by exact, whole-token match against ``known_names`` (the user
    table). Names shorter than ``_MIN_KNOWN_NAME_LEN`` are skipped, and a
    name is not matched inside a path segment, a dotted identifier or an
    email, which keeps ``/admin/...`` and ``admin.admin`` intact for an
    account called "admin".
    """

    def __init__(self, known_names=()):
        self._install_paths = _install_path_patterns()
        self._emails = {}
        self._users = {}
        names = {n.strip() for n in known_names
                 if isinstance(n, str) and len(n.strip()) >= _MIN_KNOWN_NAME_LEN
                 and n.strip().lower() not in _NAME_SCRUB_SKIP}
        # Longest first so "alice2" wins over "alice".
        ordered = sorted(names, key=len, reverse=True)
        self._names_pattern = None
        if ordered:
            self._names_pattern = re.compile(
                r"(?<![\w/.\-@])(?:" + "|".join(re.escape(n) for n in ordered)
                + r")(?![\w/\-@]|\.\w)",
                re.IGNORECASE,
            )

    def _email_label(self, match):
        key = match.group(0).lower()
        return self._emails.setdefault(key, "<email_%d>" % (len(self._emails) + 1))

    def _user_label(self, match):
        key = match.group(0).lower()
        return self._users.setdefault(key, "<user_%d>" % (len(self._users) + 1))

    def __call__(self, line: str) -> str:
        line = _sanitize_log_line(line, self._install_paths)
        if not line:
            return line
        line = _EMAIL_PATTERN.sub(self._email_label, line)
        if self._names_pattern is not None:
            line = self._names_pattern.sub(self._user_label, line)
        return line


# Truncation safety net. A multi-MiB log over a slow link is enough to
# discourage non-technical users from sharing it at all; cap each file
# at the last N MiB so the zip stays under ~12 MiB even when several
# rotated files are bundled.
_PER_FILE_MAX_BYTES = 10 * 1024 * 1024


# Older versions printed whole KOReader progress requests and responses.
# Keep the method and route in shared archives, omitting the payload and any
# continuation lines before the next formatted application log record.
_APP_LOG_HEADER = re.compile(r"^\[\d{4}-\d{2}-\d{2} [^\]\r\n]+\] +[A-Z]+ \{[^}]+:\d+\} ")
_KOSYNC_PAYLOAD_LOG = re.compile(
    r"^(\[[^\]\r\n]+\] +DEBUG \{cps\.(?:progress_syncing\.protocols\.)?kosync:\d+\} "
    r"(?:GET|PUT) /kosync/syncs/progress/)(?!(?:<document>(?: response sent)?|<redacted>)$)"
)


# APScheduler logs a "Running job" / "executed successfully" pair at INFO on
# every run, and some jobs run every 5 seconds (#2673). Current releases keep
# the executor logger at WARNING, but older rotated logs still hold the
# pairs, and they come back at DEBUG, which is the level reporters are asked
# to reproduce at. They are the bulk of such a log and say nothing a
# maintainer needs, so the sanitised pack counts them instead of carrying
# them. Failures, warnings and missed runs do not match and are kept.
_ROUTINE_SCHEDULER_MARKER = "INFO {apscheduler.executors.default:"
_LOG_TIMESTAMP_PATTERN = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
# Bound the work on a pathological file: past this many times the cap, only the
# tail is even looked at.
_RAW_READ_FACTOR = 4
_LOG_HEADER_PEEK_BYTES = 512


# --- tracebacks digest -------------------------------------------------------
#
# Tracebacks are already in the log files. In a long log they are a handful of
# lines among thousands, so ``tracebacks.txt`` lifts them out: one entry per
# place an exception was raised, with how often and when, and the most recent
# full traceback. It is built from the text that goes into the pack (already
# scrubbed in the sanitised one), so it cannot carry anything the logs do not.
_TRACEBACK_HEADER = "Traceback (most recent call last):"
_TRACEBACK_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)', re.MULTILINE)
MAX_TRACEBACK_GROUPS = 20
_MAX_TRACEBACK_CHARS = 6000
_MAX_TRACEBACKS_FILE_BYTES = 64 * 1024


def _extract_tracebacks(text: str):
    """Yield ``(timestamp, traceback text, exception line)`` for each traceback.

    A traceback is the header, the indented frame lines under it, and the first
    unindented line after them (the exception). The timestamp is the most recent
    log record's, since the traceback follows its record's message line."""
    if _TRACEBACK_HEADER not in text:
        return
    lines = text.splitlines()
    timestamp = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stamp = _LOG_TIMESTAMP_PATTERN.match(line)
        if stamp:
            timestamp = stamp.group(1)
        if line.startswith(_TRACEBACK_HEADER):
            end = index + 1
            while end < len(lines) and lines[end].startswith((" ", "\t")):
                end += 1
            last = min(end, len(lines) - 1)
            yield timestamp, "\n".join(lines[index:last + 1]), lines[last]
            index = last
        index += 1


def _traceback_site(block: str, exception_line: str) -> str:
    """Where it was raised: ``ExceptionType @ file.py:LINE in function``."""
    exc_type = exception_line.split(":", 1)[0].strip() or "unknown"
    frame = None
    for match in _TRACEBACK_FRAME.finditer(block):
        frame = match
    if frame is None:
        return exc_type
    name = os.path.basename(frame.group("file"))
    return f"{exc_type} @ {name}:{frame.group('line')} in {frame.group('func')}"


def _clip_traceback(block: str) -> str:
    """Keep the top and the tail of an over-long traceback: the entry point and
    the failing frames matter, the middle of a deep recursion does not."""
    if len(block) <= _MAX_TRACEBACK_CHARS:
        return block
    head, tail = block[:1200], block[-(_MAX_TRACEBACK_CHARS - 1200):]
    return f"{head}\n  ... [{len(block) - _MAX_TRACEBACK_CHARS:,} characters omitted] ...\n{tail}"


class _TracebackDigest:
    def __init__(self):
        self._groups = {}
        self._total = 0

    def feed(self, text: str) -> None:
        for timestamp, block, exception_line in _extract_tracebacks(text):
            self._total += 1
            site = _traceback_site(block, exception_line)
            group = self._groups.setdefault(site, {
                "count": 0, "first": None, "last": None, "exception": "", "block": "",
            })
            group["count"] += 1
            if timestamp:
                group["first"] = timestamp if group["first"] is None or timestamp < group["first"] else group["first"]
            # The sample is the most recent occurrence; without timestamps, the latest one fed.
            if not timestamp or group["last"] is None or timestamp >= group["last"]:
                group["last"] = timestamp or group["last"]
                group["exception"] = exception_line.strip()[:300]
                group["block"] = _clip_traceback(block)

    def render(self, source_label: str) -> str:
        if not self._total:
            return (f"No tracebacks were found in the included application logs ({source_label}).\n"
                    "That is evidence too: if you are chasing an error, it was not logged as one.\n")
        ordered = sorted(self._groups.items(), key=lambda item: item[1]["last"] or "", reverse=True)
        shown = ordered[:MAX_TRACEBACK_GROUPS]
        out = [
            f"{self._total:,} traceback(s) in {len(self._groups):,} distinct place(s) in the included "
            f"application logs ({source_label}).\n",
            "One entry per place the exception was raised, most recently seen first, with the most "
            "recent full traceback.\n",
        ]
        if len(ordered) > len(shown):
            out.append(f"Showing the {len(shown)} most recent; {len(ordered) - len(shown)} older place(s) omitted.\n")
        for number, (site, group) in enumerate(shown, 1):
            span = f", first {group['first']}, last {group['last']}" if group["first"] and group["last"] else ""
            out.append(f"\n== {number}. {site}\nseen {group['count']:,} time(s){span}\n\n{group['block']}\n")
        text = "".join(out)
        data = text.encode("utf-8")
        if len(data) > _MAX_TRACEBACKS_FILE_BYTES:
            text = data[:_MAX_TRACEBACKS_FILE_BYTES].decode("utf-8", errors="ignore") + "\n[digest truncated]\n"
        return text


def _is_routine_scheduler_line(line: str) -> bool:
    """A successful-run line from the APScheduler executor, at INFO."""
    return (_ROUTINE_SCHEDULER_MARKER in line
            and (" executed successfully" in line or ' Running job "' in line))


def _omitted_lines_header(count: int, first_ts, last_ts) -> str:
    span = f" logged between {first_ts} and {last_ts}" if first_ts and last_ts else ""
    return (f"[support pack] Omitted {count:,} routine APScheduler per-run lines "
            f'("Running job" / "executed successfully"){span}. '
            "Errors, warnings and missed runs are kept.\n")


def _starts_with_app_log_header(data: bytes) -> bool:
    return bool(_APP_LOG_HEADER.match(data[:_LOG_HEADER_PEEK_BYTES].decode("utf-8", errors="replace")))


def _sanitize_log_bytes(raw: bytes, scrubber=None, *, app_log=False) -> bytes:
    """``_sanitize_log`` without the report of whether the head was dropped."""
    return _sanitize_log(raw, scrubber, app_log=app_log)[0]


def _sanitize_log(raw: bytes, scrubber=None, *, app_log=False, was_cut=False):
    """Read a log file, drop routine scheduler lines, sanitize the rest, and
    return ``(data, cut)``: the result capped at ``_PER_FILE_MAX_BYTES``
    (tail-biased: keep the most recent content, drop the head if oversize), and
    whether the oldest content was dropped to fit that cap.

    ``cut`` is about the size cap only. Routine scheduler lines that are counted
    instead of carried are announced by the header note, not by ``cut``.

    Order matters. Routine lines are dropped *before* the cap, so the cap
    spends its budget on lines worth reading rather than on noise, and the cap
    is applied *before* scrubbing, so no CPU goes on bytes that would be
    dropped anyway."""
    if not raw:
        return raw, was_cut
    app_log = app_log or _starts_with_app_log_header(raw)
    cut = was_cut
    if len(raw) > _PER_FILE_MAX_BYTES * _RAW_READ_FACTOR:
        cut = True
        raw = raw[-_PER_FILE_MAX_BYTES * _RAW_READ_FACTOR:]
        newline = raw.find(b"\n")
        if newline != -1:
            raw = raw[newline + 1:]  # the first line is cut mid-way
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return raw[-_PER_FILE_MAX_BYTES:], True
    if scrubber is None:
        install_paths = _install_path_patterns()

        def scrubber(line):
            return _sanitize_log_line(line, install_paths)

    kept = []
    omitted = 0
    first_ts = last_ts = None
    # A bounded read can start in an old multiline progress record.
    # Keep nothing from its orphaned continuation before a complete header.
    in_progress_payload = app_log and cut
    for line in text.splitlines(keepends=True):
        if in_progress_payload and not _APP_LOG_HEADER.match(line):
            continue
        payload = _KOSYNC_PAYLOAD_LOG.match(line.rstrip("\r\n"))
        in_progress_payload = payload is not None
        if payload:
            line = payload.group(1) + REDACTED_PLACEHOLDER + "\n"
        if _is_routine_scheduler_line(line):
            omitted += 1
            stamp = _LOG_TIMESTAMP_PATTERN.match(line)
            if stamp:
                ts = stamp.group(1)
                first_ts = ts if first_ts is None or ts < first_ts else first_ts
                last_ts = ts if last_ts is None or ts > last_ts else last_ts
            continue
        kept.append(line)

    # Cap what is left before scrubbing, so no CPU goes on lines that would be
    # dropped anyway, counted in bytes.
    within_cap = _tail_within(kept, _PER_FILE_MAX_BYTES)
    cut = cut or len(within_cap) < len(kept)
    kept = within_cap

    # Scrubbing can make a line longer (a short address becomes "<email_1>") and
    # the omission note adds bytes, so cap again on what is actually written: the
    # file in the archive is never over the limit, whatever redaction did.
    scrubbed = [scrubber(line) for line in kept]
    header = _omitted_lines_header(omitted, first_ts, last_ts) if omitted else ""
    budget = _PER_FILE_MAX_BYTES - len(header.encode("utf-8"))
    if budget < 0:  # the note alone does not fit: drop it rather than break the cap
        header, budget = "", _PER_FILE_MAX_BYTES
    final = _tail_within(scrubbed, budget)
    cut = cut or len(final) < len(scrubbed)
    return (header + "".join(final)).encode("utf-8"), cut


def _tail_within(lines, limit: int):
    """The longest run of whole lines, from the end, that fits in ``limit`` bytes."""
    total = 0
    start = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        total += len(lines[index].encode("utf-8", errors="replace"))
        if total > limit:
            break
        start = index
    return lines[start:]


def _read_log_tail(path: str, limit: int):
    """Return the bounded tail, whether it was cut, and the file's log format.

    Read the header through the same handle as the tail. A rotated application
    log can share an access log's path, and its tail can start inside a record.
    A log that disappears before opening is reported by the caller.
    """
    with open(path, "rb") as fd:
        size = os.fstat(fd.fileno()).st_size
        if size <= limit:
            data = fd.read(size)
            return data, False, _starts_with_app_log_header(data)
        app_header = _starts_with_app_log_header(fd.read(_LOG_HEADER_PEEK_BYTES))
        fd.seek(size - limit)
        data = fd.read(limit)
    newline = data.find(b"\n")
    return (data[newline + 1:] if newline != -1 else data), True, app_header

# ---------------------------------------------------------------------------
# Existing entry points (kept verbatim where unchanged).
# ---------------------------------------------------------------------------

class lazyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, LazyString):
            return str(obj)
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def assemble_logfiles(file_name):
    log_list = sorted(glob.glob(file_name + '*'), reverse=True)
    wfd = BytesIO()
    for f in log_list:
        with open(f, 'rb') as fd:
            shutil.copyfileobj(fd, wfd)
    wfd.seek(0)
    version = importlib.metadata.version("flask")
    if int(version.split('.')[0]) < 2:
        return send_file(wfd,
                         as_attachment=True,
                         attachment_filename=os.path.basename(file_name))
    else:
        return send_file(wfd,
                         as_attachment=True,
                         download_name=os.path.basename(file_name))


def _resolve_integration_inputs() -> dict:
    """Live inputs for the per-integration sections (``support_pack_integrations``).

    Each lookup is independent and best-effort: an input that cannot be read
    is omitted, and its section reports it as unavailable.
    """
    inputs = {}
    try:
        inputs["mail_settings"] = {
            "configured": config.get_mail_server_configured(),
            "server_type": config.mail_server_type,
            "port": config.mail_port,
            "encryption": config.mail_use_ssl,
            "max_attachment_bytes": config.mail_size,
        }
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        from .services.worker import WorkerThread
        inputs["worker_tasks"] = [
            (type(queued.task).__name__, queued.task.stat)
            for queued in WorkerThread.get_instance().tasks
        ]
    except Exception:  # pylint: disable=broad-except
        pass
    return inputs


def _resolve_state_paths() -> dict:
    """Real paths for the state collectors (see ``support_pack_state``).

    Each lookup is independent and best-effort: a role that cannot be
    resolved is ``None`` and its section reports ``configured: false``
    rather than failing the whole pack.
    """
    from . import ub

    paths = {"config": None, "library": None, "ingest": None,
             "app_db": None, "cwa_db": None, "metadata_db": None}
    try:
        app_db = getattr(ub, "app_DB_path", None)
        if app_db:
            paths["app_db"] = app_db
            paths["config"] = os.path.dirname(app_db)
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        from .cwa_db_loader import load_cwa_db
        db_dir = load_cwa_db().default_db_dir()
        paths["cwa_db"] = os.path.join(db_dir, "cwa.db")
        paths["config"] = paths["config"] or db_dir
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        # metadata.db lives in config_calibre_dir. With a split library the
        # book files live elsewhere (get_book_path() returns that directory),
        # which is often the network mount, so report it as its own role.
        calibre_dir = getattr(config, "config_calibre_dir", None)
        if calibre_dir:
            paths["library"] = calibre_dir
            paths["metadata_db"] = os.path.join(calibre_dir, "metadata.db")
        if getattr(config, "config_calibre_split", False):
            split_dir = getattr(config, "config_calibre_split_dir", None)
            if split_dir:
                paths["library_books"] = split_dir
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        paths["ingest"] = constants.ingest_folder()
    except Exception:  # pylint: disable=broad-except
        pass
    return paths


def _known_usernames():
    """Account names from the user table, for exact-match scrubbing of logs.

    Returns ``None`` if the lookup fails, so the caller can say so in the
    pack rather than silently shipping logs with usernames intact.
    """
    try:
        from . import ub
        return [name for (name,) in ub.session.query(ub.User.name).all() if name]
    except Exception as ex:  # pylint: disable=broad-except
        log.warning("Support pack: could not read account names for log scrubbing: %s", ex)
        return None


def _prepare_pack(sanitize: bool) -> dict:
    """Everything the pack needs from live application state.

    Runs on the request greenlet, on purpose. The database session, the
    config object and the worker's task list are not safe to touch from the
    offload thread while other requests use them, so they are read here, into
    plain data. ``_assemble_pack`` then does the heavy file work on that data
    and nothing else.
    """
    log_sources = (
        (logger.get_accesslogfile(config.config_access_logfile), False),
        (logger.get_logfile(config.config_logfile), True),
    )
    log_files = {}
    # RotatingFileHandler appends a positive integer to the full log path.
    # Check that suffix to distinguish a rotation from a neighboring file.
    for log_path, app_log in sorted(log_sources, key=lambda source: len(source[0])):
        if log_path in (logger.LOG_TO_STDOUT, logger.LOG_TO_STDERR):
            continue
        for file_path in glob.iglob(glob.escape(log_path) + '*'):
            suffix = file_path[len(log_path):]
            if not suffix or re.fullmatch(r"\.[1-9][0-9]*", suffix):
                log_files[file_path] = app_log

    settings_dict = config.to_dict()
    libs_dict = collect_stats()
    names = None
    if sanitize:
        settings_dict = _redact_for_export(settings_dict)
        names = _known_usernames()
    return {
        "sanitize": sanitize,
        "log_files": log_files,
        "settings": settings_dict,
        "libs": libs_dict,
        "paths": _resolve_state_paths(),
        "integrations": _resolve_integration_inputs(),
        "names": names,
    }


def _assemble_pack(prep: dict):
    """Build the in-memory zip from ``_prepare_pack``'s data.

    Pure file and SQLite work, so it is safe to run on the offload pool, which
    is where the endpoints run it: reading, scrubbing and compressing logs is
    seconds of CPU on a Raspberry Pi, and gevent here is not monkey-patched, so
    doing it on the request greenlet froze every other request until it
    finished.
    """
    sanitize = prep["sanitize"]
    state_files = support_pack_state.collect_all(
        prep["paths"], sanitized=sanitize, integrations=prep["integrations"]
    )
    scrubber = None
    note = _SANITIZED_NOTE
    if sanitize:
        scrubber = _LogScrubber(known_names=prep["names"] or ())
        if prep["names"] is None:
            note += _USERNAME_LOOKUP_FAILED_NOTE

    memory_zip = BytesIO()
    log_status = []
    with zipfile.ZipFile(memory_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('settings.txt', json.dumps(prep["settings"], sort_keys=True, indent=2))
        zf.writestr('libs.txt', json.dumps(prep["libs"], sort_keys=True, indent=2, cls=lazyEncoder))
        # The manifest is written last, once it can say how each log fared.
        manifest = json.loads(state_files.pop("manifest.json"))
        for state_name, state_bytes in state_files.items():
            zf.writestr(state_name, state_bytes)
        if sanitize:
            zf.writestr('SANITIZED.txt', note)
        digest = _TracebackDigest()
        for fp, app_log in prep["log_files"].items():
            name = os.path.basename(fp)
            try:
                raw, was_cut, app_header = _read_log_tail(fp, _PER_FILE_MAX_BYTES * _RAW_READ_FACTOR)
            except FileNotFoundError:
                # Rotated away between the directory listing and the read.
                log_status.append({"file": name, "status": "missing"})
                continue
            except OSError:
                log_status.append({"file": name, "status": "unreadable"})
                continue
            app_log = app_log or app_header
            if sanitize:
                data, was_cut = _sanitize_log(raw, scrubber, app_log=app_log, was_cut=was_cut)
            else:
                data = raw
            zf.writestr(name, data)
            if app_log:
                # From the same bytes that went into the archive, so the digest can
                # never describe a different version of the file.
                digest.feed(data.decode("utf-8", errors="replace"))
            log_status.append({"file": name, "status": "included", "bytes": len(data), "cut_to_tail": was_cut})
        app_log_path = next((path for path, app_log in prep["log_files"].items() if app_log), None)
        source_label = (os.path.basename(app_log_path).split(".log")[0] + ".log*"
                        if app_log_path else "no application log")
        zf.writestr('tracebacks.txt', digest.render(source_label))
        manifest["logs"] = log_status
        zf.writestr('manifest.json', json.dumps(manifest, sort_keys=True, indent=2))
    memory_zip.seek(0)
    return memory_zip


def _build_debug_zip(sanitize: bool):
    """Build the pack synchronously: prepare, then assemble. The endpoints use
    ``_get_pack`` instead, which assembles off the request greenlet."""
    return _assemble_pack(_prepare_pack(sanitize))


_USERNAME_LOOKUP_FAILED_NOTE = (
    "\n"
    "WARNING: account names could not be read from the user table, so they\n"
    "were NOT replaced in the logs. Check the logs before sharing.\n"
)

_SANITIZED_NOTE = (
    "This debug pack has been sanitized for sharing in public issues.\n"
    "\n"
    "What was removed or replaced:\n"
    "  * Settings: credentials, SMTP identifiers, and configured file paths.\n"
    "  * KOReader document and device references and legacy progress payloads.\n"
    "  * Logs: public IPs, /config/ and /calibre-library/ paths,\n"
    "          Authorization: Bearer/Basic header values, JWT-shaped tokens,\n"
    "          Kobo sync tokens in /kobo/<token>/ URLs, email addresses\n"
    "          (<email_1>, <email_2>, ...) and account names (<user_1>, ...).\n"
    "          The same email or account gets the same label throughout this\n"
    "          pack; labels are not stable between packs.\n"
    "  * Logs: routine APScheduler per-run lines (\"Running job\" and\n"
    "          \"executed successfully\") are omitted and counted in a note at\n"
    "          the top of each log file. Failures, warnings and missed runs\n"
    "          are kept.\n"
    "\n"
    "What was kept:\n"
    "  * Loopback IP (127.0.0.1), timestamps, log levels, error messages,\n"
    "    book titles, request paths, application stack traces.\n"
    "  * tracebacks.txt lifts the tracebacks out of the application logs, one\n"
    "    entry per place an exception was raised. It is built from the already\n"
    "    scrubbed log text, so it holds nothing the logs do not.\n"
    "\n"
    "Each log file is capped at the last 10 MiB so the zip stays small.\n"
    "\n"
    "manifest.json and state/*.json hold versions, sizes, counts, mount types\n"
    "and device kind, model and firmware only. KOReader device references are\n"
    "scoped to this pack. The summaries omit paths, IPs, usernames, email\n"
    "addresses, titles and row contents.\n"
    "\n"
    "If you'd rather not share even the sanitized version, the Download Debug\n"
    "Package button (next to this one) generates an UNREDACTED pack for\n"
    "internal/private use only.\n"
)


class PackBusy(Exception):
    """A pack is already being built."""


# One pack at a time, across both kinds. The flag is guarded by a real lock
# that is only held for the two assignments, never across the build, so
# taking it cannot block the hub.
_pack_state_lock = threading.Lock()
_pack_in_progress = False

PACK_BUSY_RETRY_SECONDS = 10


def _get_pack(sanitize: bool) -> bytes:
    """Build a pack without freezing the app, one at a time.

    * Off the hub: inputs are read here on the request greenlet, and the file
      work runs on the shared offload pool (``parallel.run_blocking``), so
      every other request keeps being served while a pack builds.
    * Single-flight: a second request while one is building raises
      ``PackBusy`` straight away instead of queueing another build behind it.
      Before this, fifteen clicks meant fifteen serial builds on the one
      thread everything shares, a reverse-proxy 504, and an app that stayed
      frozen until it was restarted.
    """
    global _pack_in_progress
    with _pack_state_lock:
        if _pack_in_progress:
            raise PackBusy()
        _pack_in_progress = True
    # Once the native worker starts, it owns the gate. Cancelling the
    # request stops its wait, but the worker still has to finish the archive.
    worker_started = False
    abandoned = False

    def assemble():
        global _pack_in_progress
        nonlocal worker_started
        with _pack_state_lock:
            if abandoned:
                return None
            worker_started = True
        try:
            return _assemble_pack(prep).getvalue()
        finally:
            with _pack_state_lock:
                _pack_in_progress = False

    try:
        prep = _prepare_pack(sanitize)
        return parallel.run_blocking(assemble)
    except BaseException:
        with _pack_state_lock:
            if not worker_started:
                abandoned = True
                _pack_in_progress = False
        raise


def _busy_response():
    """429 with ``Retry-After``: the same pack is already being prepared."""
    body = json.dumps({
        "success": False,
        "busy": True,
        "message": "A support pack is already being prepared. Please wait a few seconds, "
                   "then try again.",
    })
    return body, 429, {"Content-Type": "application/json",
                       "Retry-After": str(PACK_BUSY_RETRY_SECONDS)}


def _pack_filename(stem: str, now=None) -> str:
    """``YYYYMMDD-HHMMSSZ-<stem>.zip``, stamped in UTC, timestamp first.

    Several packs from the same instance land in one Downloads folder (and in
    one issue), so the name says when each was made, and leading with the
    stamp makes a folder listing sort chronologically. UTC, because the pack
    is read by people in other timezones and ``manifest.json`` records
    ``generated_at_utc``; the ``Z`` says so.
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    return f"{moment:%Y%m%d-%H%M%S}Z-{stem}.zip"


# Download-start signal for the pages that offer the pack. A page generates a
# random token, requests the normal download URL with ``?dl_token=<token>``, and
# watches for this cookie. It appears only on a successful download, so the page
# can tell "started" from "busy" or "failed" without holding the file in memory:
# saving from a blob: URL is blocked by this app's CSP in Firefox (#2710).
PACK_TOKEN_COOKIE = "cwng_pack_token"
PACK_TOKEN_COOKIE_SECONDS = 60
_PACK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _requested_token():
    """The page's download token, if the request carries a well-formed one."""
    if not has_request_context():
        return None
    token = request.args.get("dl_token", "")
    return token if _PACK_TOKEN_PATTERN.fullmatch(token) else None


def _is_attachment_get(response) -> bool:
    """True only for a GET that is answering with the attachment itself.

    The cookie says "your download started", so it must not go out on a HEAD
    (which sends the headers and no file), nor on a partial or error answer.
    """
    return request.method == "GET" and response.status_code == 200


def _send_zip(memory_zip, filename: str, token=None):
    version = importlib.metadata.version("flask")
    if int(version.split('.')[0]) < 2:
        response = send_file(memory_zip, as_attachment=True, attachment_filename=filename)
    else:
        response = send_file(memory_zip, as_attachment=True, download_name=filename)
    if token and _is_attachment_get(response):
        # Readable by the page's script on purpose (not HttpOnly): it carries only
        # a random token the page itself made up. Scoped to the app's mount path,
        # not "/", so that two instances on one host do not see each other's
        # token. It has to be the mount path rather than the download route: a
        # cookie is only visible to pages under its Path, and the page that
        # watches for it is not under /admin/debug.
        response.set_cookie(PACK_TOKEN_COOKIE, token, max_age=PACK_TOKEN_COOKIE_SECONDS,
                            path=mount_cookie_path(), samesite="Lax", secure=request.is_secure)
    return response


def _send_pack(sanitize: bool, stem: str):
    try:
        data = _get_pack(sanitize)
    except PackBusy:
        return _busy_response()
    return _send_zip(BytesIO(data), _pack_filename(stem), _requested_token())


def send_debug():
    """Unsanitized debug pack — admin/internal use only."""
    return _send_pack(False, "Calibre-Web-NextGen-debug-pack")


def send_debug_sanitized():
    """Sanitized debug pack — safe to share in public issues."""
    return _send_pack(True, "Calibre-Web-NextGen-support-pack")
