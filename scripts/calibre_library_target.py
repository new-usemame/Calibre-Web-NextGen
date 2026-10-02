# Calibre-Web Automated - fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""How the out-of-process scripts should address the Calibre library.

The app and standalone scripts share ``server_target``. The scripts load the
same settings from app.db without importing Flask.
"""

import ipaddress
import os
import socket
import sqlite3
import sys
from collections import namedtuple

try:
    from app_paths import app_db_path, config_dir
except ImportError:  # pragma: no cover - direct execution outside the app tree
    def app_db_path():
        return "/config/app.db"

    def config_dir():
        return "/config"

PROBE_TIMEOUT = 0.5

# ``args`` extend a calibredb command line; ``stdin`` is the payload that
# command must be fed, or None.
LibraryTarget = namedtuple("LibraryTarget", "args stdin")


def library_id(library_dir):
    """calibre-server's id for a library: folder name, spaces as ``_``.

    Mirrors Calibre's ``library_id_from_path``; both app and scripts use
    this implementation.
    """
    return os.path.basename(str(library_dir).rstrip("/")).replace(" ", "_")


def _path_target(library_dir):
    return LibraryTarget(["--library-path={}".format(library_dir)], None)


def _announce_fallback(reason):
    """Say why the library is being addressed by path despite the setting.

    Without this the fallback is silent, and an operator reading the ingest log
    cannot tell a run that went through the content server from one that did
    not."""
    print("[calibre-library-target] Content server is enabled but {}; "
          "addressing the library by path instead".format(reason), file=sys.stderr, flush=True)


def _read_settings():
    con = sqlite3.connect("file:{}?mode=ro".format(app_db_path()), uri=True, timeout=5)
    try:
        return con.execute(
            "select config_calibre_server_enabled, config_calibre_server_port, "
            "config_calibre_server_anonymous_writes, config_calibre_server_username, "
            "config_calibre_server_password_e, config_calibre_server_listen "
            "from settings").fetchone()
    finally:
        con.close()


def _apply_env(port, username, password):
    """Apply the same CALIBRE_SERVER_* overrides cps.config_sql applies.

    Those are read into the running app's config and never written back to
    app.db, so a deployment that configures the content server purely through
    the environment leaves no credentials here to find.
    """
    env_port = os.environ.get("CALIBRE_SERVER_PORT")
    if env_port and env_port.isdigit() and 1 <= int(env_port) <= 65535:
        port = int(env_port)
    return (port,
            os.environ.get("CALIBRE_SERVER_USERNAME") or username,
            os.environ.get("CALIBRE_SERVER_PASSWORD") or password)


def _decrypt(token):
    """Decrypt an app.db ``_e`` column with the key Calibre-Web keeps beside it."""
    if not token:
        return ""
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        return ""
    try:
        with open(os.path.join(os.path.dirname(app_db_path()), ".key"), "rb") as handle:
            key = handle.read()
        return Fernet(key).decrypt(token).decode()
    except (OSError, ValueError, InvalidToken):
        return ""


def connect_host(listen):
    """Where this host reaches the server; shared by app and scripts."""
    listen = (listen or "").strip()
    if listen in ("", "0.0.0.0"):
        return "127.0.0.1"
    if listen == "::":
        return "::1"
    try:
        return str(ipaddress.ip_address(listen))
    except ValueError:
        return "127.0.0.1"


def _is_answering(host, port):
    try:
        with socket.create_connection((host, int(port)), PROBE_TIMEOUT):
            return True
    except (OSError, ValueError):
        return False



def server_target(library_dir, enabled, port, listen, anonymous_writes, username, password,
                  is_answering, announce_fallback):
    """Shared app/script routing policy; an empty target means use the local path.

    Only the explicit anonymous choice can omit credentials. This policy is
    separate from loading Flask config or encrypted app.db values so both
    consumers use the same decision and argument boundaries.
    """
    if not enabled or not library_dir:
        return LibraryTarget([], None)
    if not anonymous_writes and not (username and password):
        announce_fallback("no content server credentials are configured")
        return LibraryTarget([], None)
    host = connect_host(listen)
    if not is_answering(host, port):
        announce_fallback("it is not answering on {} port {}".format(host, port))
        return LibraryTarget([], None)
    url_host = "[{}]".format(host) if ":" in host else host
    args = ["--with-library", "http://{}:{}/#{}".format(url_host, port, library_id(library_dir))]
    if anonymous_writes:
        return LibraryTarget(args, None)
    args += ["--username", username, "--password", "<stdin>"]
    return LibraryTarget(args, password + "\n")


def library_target(library_dir):
    """Address the library through the content server when it is actually up.

    While calibre-server is running it caches the library in memory, so writes
    made straight to metadata.db stay invisible to its clients until it reloads;
    going through the server keeps it in step. When the server is disabled, has
    been stopped (Convert Library does that for the length of its run) or has
    died, this falls back to the library path, which is safe exactly because
    nothing is holding the library then.
    """
    try:
        row = _read_settings()
    except sqlite3.Error:
        return _path_target(library_dir)
    if not row or not row[0]:
        return _path_target(library_dir)
    _enabled, port, anonymous_writes, username, password_e, listen = row
    port, username, password = _apply_env(port, username, _decrypt(password_e))
    target = server_target(library_dir, _enabled, port, listen, anonymous_writes, username, password,
                           _is_answering, _announce_fallback)
    return target if target.args else _path_target(library_dir)


def library_arguments(library_dir):
    """Back-compat shim for callers that cannot feed stdin."""
    return library_target(library_dir).args
