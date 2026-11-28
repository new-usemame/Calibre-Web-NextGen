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
import zipfile
from io import BytesIO

from flask import send_file
from flask_babel.speaklater import LazyString

from . import logger, config, constants, support_pack_state
from .about import collect_stats

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


def _sanitize_log_bytes(raw: bytes, scrubber=None, *, app_log=False) -> bytes:
    """Read a log file, sanitize each line, return the sanitized bytes
    capped at ``_PER_FILE_MAX_BYTES`` (tail-biased: keep the most
    recent content, drop the head if oversize)."""
    if not raw:
        return raw
    # Tail-truncate first so we never burn CPU sanitizing bytes we'd
    # drop anyway.
    truncated = len(raw) > _PER_FILE_MAX_BYTES
    if truncated:
        # An application rollover can land at a configured access path.
        # Prefer a real application header in the file to its filename.
        app_log = app_log or bool(_APP_LOG_HEADER.match(raw[:512].decode("utf-8", errors="replace")))
        starts_midline = raw[-_PER_FILE_MAX_BYTES - 1] != ord("\n")
        raw = raw[-_PER_FILE_MAX_BYTES:]
        if starts_midline:
            raw = raw.partition(b"\n")[2]
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return raw
    if scrubber is None:
        install_paths = _install_path_patterns()

        def scrubber(line):
            return _sanitize_log_line(line, install_paths)
    sanitized_lines = []
    # An application log tail can start inside a legacy multiline record.
    # Wait for a complete record header when we have dropped its prefix.
    in_progress_payload = app_log and truncated
    for line in text.splitlines(keepends=True):
        if in_progress_payload and not _APP_LOG_HEADER.match(line):
            continue
        payload = _KOSYNC_PAYLOAD_LOG.match(line.rstrip("\r\n"))
        in_progress_payload = payload is not None
        if payload:
            line = payload.group(1) + REDACTED_PLACEHOLDER + "\n"
        sanitized_lines.append(scrubber(line))
    return "".join(sanitized_lines).encode("utf-8")


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


def _build_debug_zip(sanitize: bool):
    """Build the in-memory debug pack. When ``sanitize`` is True the
    settings file is redacted and every log line is scrubbed for IPs,
    paths, and auth headers."""
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
    state_files = support_pack_state.collect_all(_resolve_state_paths(), sanitized=sanitize)
    scrubber = None
    note = _SANITIZED_NOTE
    if sanitize:
        settings_dict = _redact_for_export(settings_dict)
        names = _known_usernames()
        scrubber = _LogScrubber(known_names=names or ())
        if names is None:
            note += _USERNAME_LOOKUP_FAILED_NOTE

    memory_zip = BytesIO()
    with zipfile.ZipFile(memory_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('settings.txt', json.dumps(settings_dict, sort_keys=True, indent=2))
        zf.writestr('libs.txt', json.dumps(libs_dict, sort_keys=True, indent=2, cls=lazyEncoder))
        for state_name, state_bytes in state_files.items():
            zf.writestr(state_name, state_bytes)
        if sanitize:
            zf.writestr('SANITIZED.txt', note)
        for fp, app_log in log_files.items():
            if sanitize:
                with open(fp, 'rb') as fd:
                    raw = fd.read()
                zf.writestr(os.path.basename(fp), _sanitize_log_bytes(raw, scrubber, app_log=app_log))
            else:
                zf.write(fp, os.path.basename(fp))
    memory_zip.seek(0)
    return memory_zip


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
    "  * Legacy KOReader progress request and response payloads.\n"
    "  * Logs: public IPs, /config/ and /calibre-library/ paths,\n"
    "          Authorization: Bearer/Basic header values, JWT-shaped tokens,\n"
    "          Kobo sync tokens in /kobo/<token>/ URLs, email addresses\n"
    "          (<email_1>, <email_2>, ...) and account names (<user_1>, ...).\n"
    "          The same email or account gets the same label throughout this\n"
    "          pack; labels are not stable between packs.\n"
    "\n"
    "What was kept:\n"
    "  * Loopback IP (127.0.0.1), timestamps, log levels, error messages,\n"
    "    book titles, request paths, application stack traces.\n"
    "\n"
    "Each log file is capped at the last 10 MiB so the zip stays small.\n"
    "\n"
    "manifest.json and state/*.json hold versions, sizes, counts and mount\n"
    "types only: no paths, IPs, usernames, titles or row contents.\n"
    "\n"
    "If you'd rather not share even the sanitized version, the Download Debug\n"
    "Package button (next to this one) generates an UNREDACTED pack for\n"
    "internal/private use only.\n"
)


def send_debug():
    """Unsanitized debug pack — admin/internal use only."""
    memory_zip = _build_debug_zip(sanitize=False)
    version = importlib.metadata.version("flask")
    if int(version.split('.')[0]) < 2:
        return send_file(memory_zip,
                         as_attachment=True,
                         attachment_filename="Calibre-Web-NextGen-debug-pack.zip")
    else:
        return send_file(memory_zip,
                         as_attachment=True,
                         download_name="Calibre-Web-NextGen-debug-pack.zip")


def send_debug_sanitized():
    """Sanitized debug pack — safe to share in public issues."""
    memory_zip = _build_debug_zip(sanitize=True)
    version = importlib.metadata.version("flask")
    if int(version.split('.')[0]) < 2:
        return send_file(memory_zip,
                         as_attachment=True,
                         attachment_filename="Calibre-Web-NextGen-support-pack.zip")
    else:
        return send_file(memory_zip,
                         as_attachment=True,
                         download_name="Calibre-Web-NextGen-support-pack.zip")
