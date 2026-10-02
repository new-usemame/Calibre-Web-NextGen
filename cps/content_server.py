# -*- coding: utf-8 -*-

#   This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#     Copyright (C) 2026 OzzieIsaacs
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program. If not, see <http://www.gnu.org/licenses/>.

import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque

from . import config, constants, logger
from scripts.calibre_library_target import LibraryTarget, connect_host, library_id, server_target

log = logger.create()

_process = None
_lock = threading.RLock()
_stopped_on_purpose = False
_library_holds = 0
_restart_on_release = False

PROBE_TIMEOUT = 0.5
WATCH_INTERVAL = 5
QUIET_BEFORE_RELOAD = 30
# A server that dies this soon after starting is failing on its configuration
# (port taken, bad userdb, unreadable library), not crashing at random; after
# this many such exits in a row it is left down with the reason in the log
# instead of being relaunched every WATCH_INTERVAL forever (#2210 review).
QUICK_EXIT_SECONDS = 60
MAX_QUICK_EXITS = 3
_quick_exits = 0
_started_at = None
# The last lines calibre-server printed, for the give-up message.
_recent_output = deque(maxlen=20)

# ``args`` extend a calibredb command line; ``stdin`` is the payload that
# command must be fed, or None. They are produced together because the password
# is passed as ``--password <stdin>`` and means nothing without the payload.

NO_TARGET = LibraryTarget([], None)

# These settings arrive with a migration, and the export path reaches this
# module with whatever configuration the process happens to have loaded, so
# every read carries a default rather than assuming the attribute is present.
SETTING_DEFAULTS = {
    "config_calibre_server_enabled": False,
    "config_calibre_server_port": 8080,
    "config_calibre_server_listen": "127.0.0.1",
    "config_calibre_server_anonymous_writes": False,
    "config_calibre_server_trusted_ips": "",
    "config_calibre_server_username": "",
    "config_calibre_server_password_e": "",
    "config_calibre_dir": "",
    "config_binariesdir": "",
    "config_calibre_split": False,
    "config_calibre_split_dir": "",
}


def setting(name):
    return getattr(config, name, SETTING_DEFAULTS[name])


# calibre.srv.users.validate_username/validate_password, as measured with
# calibre 9.0: a name outside this set or a non-ASCII password makes the user
# database helper fail, and the server then never starts while the admin page
# says "saved" (#2210 review).
_USERNAME_CHARS = re.compile(r"^[A-Za-z0-9 _-]+$")


def settings_problem(port, username, new_password, app_port):
    """Why these content server settings cannot work, or ``None``.

    Returns a key the admin page turns into a message: ``port`` (not an integer
    in 1-65535), ``port-in-use`` (the web UI's own port -- calibre-server would
    crash-loop and the connection probe would then find the web UI answering),
    ``username`` or ``password`` (rejected by calibre). ``new_password`` is only
    what was just submitted; an empty value means "unchanged".
    """
    try:
        port = int(str(port).strip())
    except (TypeError, ValueError):
        return "port"
    if not 1 <= port <= 65535:
        return "port"
    if app_port and port == int(app_port):
        return "port-in-use"
    if username and not _USERNAME_CHARS.match(username):
        return "username"
    if new_password and not all(32 <= ord(ch) < 127 for ch in new_password):
        return "password"
    return None



def configuration_identity():
    """Settings that require a managed process reconciliation after a save."""
    return tuple(setting(name) for name in SETTING_DEFAULTS)


def _url_host(host):
    return "[{}]".format(host) if ":" in host else host


def library_url():
    return "http://{}:{}/#{}".format(
        _url_host(connect_host(setting("config_calibre_server_listen"))),
        setting("config_calibre_server_port"),
        library_id(setting("config_calibre_dir")))


def _auth_enabled():
    return bool(not setting("config_calibre_server_anonymous_writes")
                and setting("config_calibre_server_username")
                and setting("config_calibre_server_password_e"))


def is_answering(timeout=PROBE_TIMEOUT):
    """True when something accepts connections on the content server port."""
    try:
        with socket.create_connection((connect_host(setting("config_calibre_server_listen")),
                                       int(setting("config_calibre_server_port"))), timeout):
            return True
    except (OSError, ValueError):
        return False


def library_target():
    """How calibredb should address the library right now.

    Returns an empty target whenever the content server is disabled or is not
    answering, so callers fall back to the library path. That fallback is safe
    precisely because the server is down: nothing else is holding the library.
    It is what keeps ingest and metadata embedding working while Convert Library
    has the server stopped, and after the server has died.
    """
    return server_target(
        setting("config_calibre_dir"), setting("config_calibre_server_enabled"),
        setting("config_calibre_server_port"), setting("config_calibre_server_listen"),
        setting("config_calibre_server_anonymous_writes"),
        setting("config_calibre_server_username"), setting("config_calibre_server_password_e"),
        lambda _host, _port: is_answering(),
        lambda reason: log.warning("Calibre content server is enabled but %s, "
                                   "addressing the library by path instead", reason),
    )


def library_arguments():
    """Back-compat shim for callers that cannot feed stdin."""
    return library_target().args


def _db_mtime(db_path):
    mtime = None
    for path in (db_path, db_path + "-wal"):
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            continue
        if mtime is None or stamp > mtime:
            mtime = stamp
    return mtime


def _watch(process, db_path, last=None):
    """Keep the running content server honest about the library.

    Two things happen behind its back. calibre-server keeps the library in
    memory and never notices writes made directly to metadata.db (web UI edits,
    ingest, calibredb), so external changes stay invisible to its clients until
    it reloads; it is restarted once the database has changed and then been
    quiet, so it is never bounced in the middle of a burst of writes. The
    process can also die, in which case it is started again rather than left
    down with the setting still switched on.
    """
    changed = None
    while True:
        time.sleep(WATCH_INTERVAL)
        with _lock:
            if _process is not process:
                return
            if process.poll() is not None:
                if _stopped_on_purpose:
                    return
                _restart_after_exit(process)
                return
        mtime = _db_mtime(db_path)
        if mtime is None:
            continue
        if last is None:
            last = mtime
        elif mtime != last:
            last = mtime
            changed = time.time()
        elif changed and time.time() - changed >= QUIET_BEFORE_RELOAD:
            log.info("Library database changed, reloading calibre content server")
            with _lock:
                if _process is process and process.poll() is None:
                    _locked_start()
            return


def _restart_after_exit(process):
    """Relaunch a server that died, unless it keeps dying on startup."""
    global _quick_exits, _process
    ran_for = time.monotonic() - (_started_at or 0)
    _quick_exits = _quick_exits + 1 if ran_for < QUICK_EXIT_SECONDS else 1
    if _quick_exits >= MAX_QUICK_EXITS:
        log.error("Calibre content server exited %s times within %ss of starting (last code %s); "
                  "leaving it stopped. Fix the setting it reports and save to try again. "
                  "Last output: %s", _quick_exits, QUICK_EXIT_SECONDS, process.returncode,
                  " | ".join(_recent_output) or "(none)")
        _process = None
        return
    log.error("Calibre content server exited unexpectedly (code %s), restarting it",
              process.returncode)
    _locked_start()


def _drain_output(stream):
    """Copy calibre-server's own output into this app's log.

    Otherwise the reason it refuses to start (a port in use, a user it cannot
    load) reaches only the container's stdout, not the log the admin reads.
    """
    try:
        for line in stream:
            line = line.rstrip()
            if line:
                _recent_output.append(line)
                log.info("calibre-server: %s", line)
    except (OSError, ValueError):
        pass


def server_binary():
    return os.path.join(setting("config_binariesdir") or "",
                        "calibre-server.exe" if sys.platform == "win32" else "calibre-server")


def debug_binary():
    return os.path.join(setting("config_binariesdir") or "",
                        "calibre-debug.exe" if sys.platform == "win32" else "calibre-debug")


def userdb_path():
    return os.path.join(constants.CONFIG_DIR, "content_server_users.sqlite")


def write_userdb(username, password, userdb=None, binary=None):
    """Create the single-user database calibre-server authenticates against.

    The password is written to the helper's stdin, never passed as an argument:
    /proc/<pid>/cmdline is world readable, so an argument is visible to every
    other process on the host for as long as the call runs. calibre stores the
    password in cleartext in the resulting sqlite file, so that file is made
    owner-only.
    """
    userdb = userdb or userdb_path()
    binary = binary or debug_binary()
    try:
        os.remove(userdb)
    except OSError:
        pass
    helper = os.path.join(constants.SCRIPTS_DIR, "calibre_server_user.py")
    result = subprocess.run([binary, "-e", helper, "--", userdb, username],
                            input=password + "\n", capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Failed to create calibre content server user: %s", result.stderr)
        return False
    try:
        os.chmod(userdb, 0o600)
    except OSError as ex:
        log.warning("Could not restrict permissions on %s: %s", userdb, ex)
    return True


def _remove_userdb():
    """calibre keeps the password in cleartext there; without auth it has no use."""
    try:
        os.remove(userdb_path())
    except FileNotFoundError:
        pass
    except OSError as ex:
        log.warning("Could not remove %s: %s", userdb_path(), ex)


def server_arguments():
    """The calibre-server command line for the current configuration."""
    args = [server_binary(), "--port", str(setting("config_calibre_server_port")),
            "--listen-on", setting("config_calibre_server_listen") or "127.0.0.1",
            "--disable-fallback-to-detected-interface"]
    if setting("config_calibre_server_anonymous_writes"):
        args.append("--enable-local-write")
        if setting("config_calibre_server_trusted_ips"):
            args += ["--trusted-ips", setting("config_calibre_server_trusted_ips")]
    elif _auth_enabled():
        # calibre's default auth mode: Digest over plain HTTP, so the password
        # never crosses the wire in the clear; calibredb authenticates with it
        # (measured with calibre 9.0). Forcing "basic" sent it base64-encoded
        # on every request whenever the listen address was not loopback.
        args += ["--enable-auth", "--userdb", userdb_path()]
    args.append(setting("config_calibre_dir"))
    return args



def _run_lifecycle(callback):
    """Wait for process work without blocking the production request hub.

    Watchers and task threads already run outside the request hub. HTTP/startup
    calls use gevent's existing native worker pool, whose wait yields to other
    requests while the same lifecycle lock serializes process transitions.
    """
    if threading.current_thread() is not threading.main_thread():
        return callback()
    try:
        from gevent import get_hub
    except ImportError:  # minimal/CLI installations without a gevent server
        return callback()
    return get_hub().threadpool.apply(callback)


def start():
    """Start (or restart) on request: a save, startup, the end of a pause.

    A deliberate start clears the give-up count, so saving corrected settings
    is always another attempt."""
    return _run_lifecycle(_start_blocking)


def _start_blocking():
    global _quick_exits
    with _lock:
        _quick_exits = 0
        _locked_start()


def _locked_start():
    global _process, _stopped_on_purpose, _started_at, _restart_on_release
    if _library_holds:
        _restart_on_release = bool(setting("config_calibre_server_enabled"))
        return
    _locked_stop()
    if not setting("config_calibre_server_enabled") or not setting("config_calibre_dir"):
        return
    if setting("config_calibre_split"):
        log.error("Calibre content server not started: split library mode is unsupported. "
                  "Disable split library mode before enabling the content server.")
        return
    if not os.path.isfile(server_binary()):
        log.error("calibre-server binary not found: %s", server_binary())
        return
    anonymous = setting("config_calibre_server_anonymous_writes")
    if not anonymous and not _auth_enabled():
        # Without --enable-auth calibre-server serves the whole library to
        # anyone who reaches the port. The admin form refuses this state, but
        # a cleared password or credentials removed from the environment reach
        # it at the next start, so the refusal lives here (#2210 review).
        log.error("Calibre content server not started: authentication is on but no "
                  "username/password is configured. Set both, or allow anonymous writes.")
        _remove_userdb()
        return
    if anonymous:
        _remove_userdb()
    elif not write_userdb(setting("config_calibre_server_username"),
                          setting("config_calibre_server_password_e")):
        return
    _stopped_on_purpose = False
    db_path = os.path.join(setting("config_calibre_dir"), "metadata.db")
    initial_mtime = _db_mtime(db_path)
    try:
        _process = subprocess.Popen(server_arguments(), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
    except OSError as ex:
        log.error("Failed to start calibre content server: %s", ex)
        _process = None
        return
    _started_at = time.monotonic()
    threading.Thread(target=_drain_output, args=(_process.stdout,), daemon=True).start()
    log.info("Calibre content server started on port %s", setting("config_calibre_server_port"))
    threading.Thread(target=_watch,
                     args=(_process, db_path, initial_mtime),
                     daemon=True).start()


def stop():
    return _run_lifecycle(_stop_blocking)


def _stop_blocking():
    with _lock:
        _locked_stop()



class _LibraryHold:
    """One owned hold on the library; releasing twice cannot resume it early."""
    def __init__(self):
        self.released = False

    def release(self):
        return _run_lifecycle(self._release_blocking)

    def _release_blocking(self):
        global _library_holds, _restart_on_release
        with _lock:
            if self.released:
                return
            self.released = True
            _library_holds -= 1
            if not _library_holds:
                restart = _restart_on_release
                _restart_on_release = False
                if restart and setting("config_calibre_server_enabled"):
                    _locked_start()

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.release()


def hold_library():
    """Stop the server until every conversion/restore owner releases its hold.

    A hold also protects a currently disabled or stopped server. Enabling or
    saving settings during that operation defers startup until the last hold
    is released, rather than taking the database lock back mid-conversion.
    """
    return _run_lifecycle(_hold_library_blocking)


def _hold_library_blocking():
    global _library_holds, _restart_on_release
    with _lock:
        if not _library_holds:
            _restart_on_release = _process is not None and _process.poll() is None
            _locked_stop()
        _library_holds += 1
        return _LibraryHold()


def _locked_stop():
    global _process, _stopped_on_purpose
    _stopped_on_purpose = True
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(10)
        except subprocess.TimeoutExpired:
            _process.kill()
            # A kill request does not establish that the database owner has
            # exited. If reap fails, keep the process reference and propagate
            # the failure instead of handing the library to another writer.
            _process.wait(10)
        log.info("Calibre content server stopped")
    _process = None
