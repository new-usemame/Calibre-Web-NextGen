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
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps

from . import config, constants, logger
from .calibre_library_target import LibraryTarget, connect_host, library_id, server_target, server_ready
from . import calibre_server_guard as ownership

log = logger.create()

_process = None
_lock = threading.RLock()
_configuration_gate_owned = ContextVar("calibre_configuration_gate_owned", default=False)
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


def _auth_enabled():
    return bool(not setting("config_calibre_server_anonymous_writes")
                and setting("config_calibre_server_username")
                and setting("config_calibre_server_password_e"))


def platform_supported():
    """Child-held process ownership requires POSIX descriptor inheritance."""
    return os.name != "nt"


def is_answering(timeout=PROBE_TIMEOUT):
    """True when something accepts connections on the content server port."""
    try:
        with socket.create_connection((connect_host(setting("config_calibre_server_listen")),
                                       int(setting("config_calibre_server_port"))), timeout):
            return True
    except (OSError, ValueError):
        return False


def is_ready():
    return server_ready(connect_host(setting("config_calibre_server_listen")),
                        setting("config_calibre_server_port"), setting("config_calibre_dir"))


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
        lambda _host, _port: is_ready(),
        lambda reason: log.warning("Calibre content server is enabled but %s, "
                                   "addressing the library by path instead", reason),
        lambda: ownership.busy(constants.CONFIG_DIR, "owner"),
    )


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
        try:
            with ownership.operation(constants.CONFIG_DIR, timeout=0.2), _lock:
                if _process is not process:
                    return
                if process.poll() is not None:
                    if _stopped_on_purpose:
                        return
                    _restart_after_exit(process)
                    return
        except TimeoutError:
            continue
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
            try:
                with ownership.operation(constants.CONFIG_DIR, timeout=0.2), _lock:
                    if _process is process and process.poll() is None:
                        _locked_start()
            except TimeoutError:
                continue
            return


def _restart_after_exit(process):
    """Relaunch a server that died, unless it keeps dying on startup."""
    global _quick_exits, _process
    if (process.returncode == ownership.EXIT_MAINTENANCE
            or ownership.busy(constants.CONFIG_DIR, "maintenance")):
        _process = None
        _defer_for_maintenance()
        return
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


def _calibre_environment():
    # The abc service user cannot write /root's default fontconfig cache.
    # Give the child a writable cache without changing Calibre's plugin/config
    # selection or the caller's environment.
    environment = os.environ.copy()
    environment.setdefault("XDG_CACHE_HOME", os.path.join(constants.CONFIG_DIR, ".cache"))
    return environment


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
                            input=password + "\n", capture_output=True, text=True, env=_calibre_environment())
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
        trusted = [entry.strip() for entry in setting("config_calibre_server_trusted_ips").split(",")
                   if entry.strip()]
        local_source = connect_host(setting("config_calibre_server_listen"))
        if not ipaddress.ip_address(local_source).is_loopback and local_source not in trusted:
            # A connection to our specific LAN bind originates from that same
            # host address. Calibre's local-write rule recognizes loopback only.
            trusted.append(local_source)
        if trusted:
            args += ["--trusted-ips", ",".join(trusted)]
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
    gate_owned = _configuration_gate_owned.get()
    return _run_lifecycle(lambda: _start_blocking(gate_owned))


def _start_blocking(gate_owned=False):
    global _quick_exits
    if not gate_owned and not setting("config_calibre_server_enabled") and _process is None:
        return
    operation = nullcontext() if gate_owned else ownership.operation(constants.CONFIG_DIR, timeout=0.2)
    try:
        with operation, _lock:
            _quick_exits = 0
            _locked_start()
    except TimeoutError:
        with _lock:
            _quick_exits = 0
            _defer_for_maintenance()


def _locked_start():
    global _process, _stopped_on_purpose, _started_at, _restart_on_release
    if _library_holds:
        _restart_on_release = bool(setting("config_calibre_server_enabled"))
        return False
    _locked_stop()
    if ownership.busy(constants.CONFIG_DIR, "maintenance"):
        _defer_for_maintenance()
        return False
    if not setting("config_calibre_server_enabled") or not setting("config_calibre_dir"):
        return
    if not platform_supported():
        log.error("Calibre content server not started: managed child ownership requires a POSIX platform")
        return
    if setting("config_calibre_split"):
        log.error("Calibre content server not started: split library mode is unsupported. "
                  "Disable split library mode before enabling the content server.")
        return
    problem = settings_problem(setting("config_calibre_server_port"),
                               setting("config_calibre_server_username"),
                               setting("config_calibre_server_password_e"), constants.DEFAULT_PORT)
    if problem:
        log.error("Calibre content server not started: invalid settings (%s)", problem)
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
    # Failed launches are quick exits too, even when no HTTP readiness was
    # reached. Do not measure them from an earlier successful server's clock.
    _started_at = time.monotonic()
    db_path = os.path.join(setting("config_calibre_dir"), "metadata.db")
    initial_mtime = _db_mtime(db_path)
    try:
        command = [sys.executable, ownership.__file__, constants.CONFIG_DIR, *server_arguments()]
        _process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=_calibre_environment())
    except OSError as ex:
        log.error("Failed to start calibre content server: %s", ex)
        _process = None
        return
    threading.Thread(target=_drain_output, args=(_process.stdout,), daemon=True).start()
    deadline = time.monotonic() + 30
    while _process.poll() is None and not is_ready():
        if time.monotonic() >= deadline:
            log.error("Calibre content server did not become ready within 30 seconds")
            _locked_stop()
            return
        time.sleep(0.1)
    if _process.poll() is not None:
        if _process.returncode == ownership.EXIT_MAINTENANCE:
            _process = None
            _defer_for_maintenance()
            return False
        log.error("Calibre content server exited before readiness (code %s)", _process.returncode)
        threading.Thread(target=_watch, args=(_process, db_path, initial_mtime), daemon=True).start()
        return
    _started_at = time.monotonic()
    log.info("Calibre content server started on port %s", setting("config_calibre_server_port"))
    threading.Thread(target=_watch,
                     args=(_process, db_path, initial_mtime),
                     daemon=True).start()


def stop():
    gate_owned = _configuration_gate_owned.get()
    return _run_lifecycle(lambda: _stop_blocking(gate_owned))


def _stop_blocking(gate_owned=False):
    if not gate_owned and _process is None:
        return
    operation = nullcontext() if gate_owned else ownership.operation(constants.CONFIG_DIR)
    with operation, _lock:
        _locked_stop()



def stop_before_app_exit():
    """App exit/exec closes the guardian lifeline even when a writer is busy."""
    try:
        stop()
    except TimeoutError:
        log.warning("Calibre stop deferred to app-exit lifeline while the library is busy")


def configuration_update(callback=None, *, on_busy=None):
    """Keep settings persistence and process reconciliation one generation.

    Flask/session work stays on its caller. Capture gate ownership before
    offloading lifecycle work: native gevent workers do not inherit ContextVars.
    """
    if callback is None:
        return lambda function: configuration_update(function, on_busy=on_busy)

    @wraps(callback)
    def update(*args, **kwargs):
        if _configuration_gate_owned.get():
            return callback(*args, **kwargs)
        operation = ownership.operation(constants.CONFIG_DIR)
        try:
            _run_lifecycle(operation.__enter__)
        except TimeoutError:
            if on_busy is not None:
                return on_busy()
            raise
        token = _configuration_gate_owned.set(True)
        try:
            return callback(*args, **kwargs)
        finally:
            _configuration_gate_owned.reset(token)
            _run_lifecycle(lambda: operation.__exit__(None, None, None))
    return update



class _LibraryHold:
    """One owned hold on the library; releasing twice cannot resume it early."""
    def __init__(self, exclusive_context=None, fd=None):
        self.released = False
        self.exclusive_context = exclusive_context
        self.fd = fd

    def child_ownership(self):
        return {"pass_fds": (self.fd,)} if self.fd is not None and os.name != "nt" else {}

    def release(self):
        return _run_lifecycle(self._release_blocking)

    def _release_blocking(self):
        global _library_holds, _restart_on_release
        # Releasing a completed owner is state bookkeeping, not a database
        # write. Drop its count even when a separate Restore owns the gate.
        # Never acquire the gate while holding _lock: lifecycle order is gate
        # then _lock. Actual restart below still requires the writer gate.
        try:
            with _lock:
                if self.released:
                    return
                self.released = True
                _library_holds -= 1
                restart = not _library_holds and _restart_on_release
                if not _library_holds:
                    _restart_on_release = False
            if restart and setting("config_calibre_server_enabled"):
                operation = (nullcontext() if self.exclusive_context is not None
                             else ownership.operation(constants.CONFIG_DIR, timeout=0.2))
                try:
                    with operation, _lock:
                        _locked_start()
                except TimeoutError:
                    with _lock:
                        _defer_for_maintenance()
        finally:
            if self.exclusive_context is not None:
                self.exclusive_context.__exit__(None, None, None)
                self.exclusive_context = None
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.release()


def hold_library(exclusive=False):
    """Stop the server until every conversion/restore owner releases its hold.

    A hold also protects a currently disabled or stopped server. Enabling or
    saving settings during that operation defers startup until the last hold
    is released, rather than taking the database lock back mid-conversion.
    """
    return _run_lifecycle(lambda: _hold_library_blocking(exclusive))


def _hold_library_blocking(exclusive=False):
    global _library_holds, _restart_on_release
    operation = ownership.operation(constants.CONFIG_DIR)
    fd = operation.__enter__()
    try:
        with _lock:
            if not _library_holds:
                _restart_on_release = _process is not None and _process.poll() is None
                _locked_stop()
            _library_holds += 1
            hold = _LibraryHold(operation if exclusive else None, fd if exclusive else None)
        if not exclusive:
            operation.__exit__(None, None, None)
        return hold
    except BaseException:
        operation.__exit__(*sys.exc_info())
        raise


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


_maintenance_waiter = False


def _defer_for_maintenance():
    global _maintenance_waiter
    if _maintenance_waiter or not setting("config_calibre_server_enabled"):
        return
    _maintenance_waiter = True

    def reconcile():
        global _maintenance_waiter
        try:
            while setting("config_calibre_server_enabled"):
                if ownership.busy(constants.CONFIG_DIR, "maintenance") or _library_holds:
                    time.sleep(WATCH_INTERVAL)
                    continue
                try:
                    with ownership.operation(constants.CONFIG_DIR, timeout=0.2), _lock:
                        if not setting("config_calibre_server_enabled"):
                            return
                        outcome = _locked_start()
                        retry = (outcome is False or ownership.busy(constants.CONFIG_DIR, "maintenance")
                                 or _library_holds)
                    if retry:
                        time.sleep(WATCH_INTERVAL)
                        continue
                    return
                except TimeoutError:
                    time.sleep(WATCH_INTERVAL)
        finally:
            _maintenance_waiter = False

    try:
        threading.Thread(target=reconcile, daemon=True).start()
    except RuntimeError:
        _maintenance_waiter = False
        raise
