# Calibre-Web Automated - fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""How the out-of-process scripts should address the Calibre library.

The app and standalone scripts share ``server_target``. The scripts load the
same settings from app.db without importing Flask.
"""

import os
import sqlite3
import sys
import importlib.util
from contextvars import ContextVar
from functools import wraps
from contextlib import contextmanager
from pathlib import Path

try:
    from app_paths import app_db_path, config_dir
except ImportError:  # pragma: no cover - direct execution outside the app tree
    def app_db_path():
        return "/config/app.db"

    def config_dir():
        return "/config"

PROBE_TIMEOUT = 0.5

# Load the packaged, dependency-free policy without importing cps/__init__.
# Its path belongs to this checkout; an unrelated top-level scripts package
# cannot shadow the application's runtime policy.
_policy_path = Path(__file__).resolve().parents[1] / "cps" / "calibre_library_target.py"
_policy_spec = importlib.util.spec_from_file_location("_cwng_calibre_target_policy", _policy_path)
_policy = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(_policy)
LibraryTarget = _policy.LibraryTarget
library_id = _policy.library_id
connect_host = _policy.connect_host
server_target = _policy.server_target
calibredb_command = _policy.calibredb_command
path_is_available = _policy.path_is_available
_guard = importlib.util.spec_from_file_location("_cwng_server_guard", _policy_path.with_name("calibre_server_guard.py"))
ownership = importlib.util.module_from_spec(_guard)
_guard.loader.exec_module(ownership)
LibraryBusyError = ownership.LibraryBusyError


def operation(timeout=120):
    return ownership.operation(config_dir(), timeout=timeout)


def check_maintenance():
    """Avoid expensive conversion while an offline run already owns the library."""
    if ownership.busy(config_dir(), "maintenance"):
        raise LibraryBusyError("Calibre library maintenance is already running")


_offline_owner_fd = ContextVar("calibre_offline_owner_fd", default=None)
_offline_writer_fd = ContextVar("calibre_offline_writer_fd", default=None)


def offline_child_ownership():
    fds = tuple(fd for fd in (_offline_owner_fd.get(), _offline_writer_fd.get()) if fd is not None)
    return {"pass_fds": fds} if fds and os.name != "nt" else {}


@contextmanager
def offline_writer_ownership(fd):
    """Let a raw transaction child retain its parent's metadata exclusion."""
    token = _offline_writer_fd.set(fd)
    try:
        yield
    finally:
        _offline_writer_fd.reset(token)


@contextmanager
def offline_library_access():
    """Drain the managed server only around a raw Calibre transaction.

    Enter before the metadata gate. Both locks are inherited by the raw child,
    so a killed ingest parent cannot expose an unfinished import to a restart.
    Post-processing and network requests run after this scope releases.
    """
    if _offline_owner_fd.get() is not None:
        yield
        return
    with ownership.maintenance(config_dir()) as fd:
        token = _offline_owner_fd.set(fd)
        try:
            yield
        finally:
            _offline_owner_fd.reset(token)


def offline_library_operation(callback):
    @wraps(callback)
    def run(*args, **kwargs):
        with offline_library_access():
            return callback(*args, **kwargs)
    return run


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



def _is_answering(host, port, library_dir=None):
    return _policy.server_ready(host, port, library_dir or "")



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
        path_is_available(lambda: ownership.busy(config_dir(), "owner"))
        return _path_target(library_dir)
    if not row or not row[0]:
        path_is_available(lambda: ownership.busy(config_dir(), "owner"))
        return _path_target(library_dir)
    _enabled, port, anonymous_writes, username, password_e, listen = row
    port, username, password = _apply_env(port, username, _decrypt(password_e))
    target = server_target(library_dir, _enabled, port, listen, anonymous_writes, username, password,
                           lambda host, port: _is_answering(host, port, library_dir), _announce_fallback,
                           lambda: ownership.busy(config_dir(), "owner"))
    return target if target.args else _path_target(library_dir)
