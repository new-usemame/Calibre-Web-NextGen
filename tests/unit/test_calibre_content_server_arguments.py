# Calibre-Web Automated - fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Argument construction and user-database creation for the Calibre content server.

The command lines are the whole interface to calibre here, and the configured
password travels along them, so both are pinned: what is built for each
configuration, and that the password never reaches a process argument or a log
record. /proc/<pid>/cmdline is world readable, so a password passed as an
argument is visible to every other process on the host for as long as the call
runs.
"""

import importlib.util
import logging
import os
import socket
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PASSWORD = "Sup3rSecret-PW-2026"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "cps" / "content_server.py"


class _Config:
    """Stand-in for cps.config carrying only the keys content_server reads."""

    def __init__(self, **overrides):
        self.config_calibre_server_enabled = True
        self.config_calibre_server_port = 7777
        self.config_calibre_server_listen = "127.0.0.1"
        self.config_calibre_server_anonymous_writes = False
        self.config_calibre_server_trusted_ips = ""
        self.config_calibre_server_username = "ccsuser"
        self.config_calibre_server_password_e = PASSWORD
        self.config_calibre_dir = "/calibre-library"
        self.config_binariesdir = "/usr/bin"
        self.__dict__.update(overrides)


@pytest.fixture
def content_server(monkeypatch, tmp_path):
    """Import cps.content_server with its module-level dependencies stubbed out."""
    for name in ("cps", "cps.content_server"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    records = []

    class _Log:
        def _capture(self, level):
            def emit(msg, *args):
                records.append(logging.LogRecord("cps.content_server", level, __file__, 0,
                                                 msg, args, None).getMessage())
            return emit

        def __getattr__(self, item):
            return self._capture(getattr(logging, item.upper(), logging.INFO))

    package = types.ModuleType("cps")
    package.__path__ = [str(REPO_ROOT / "cps")]
    package.config = _Config()
    package.constants = types.SimpleNamespace(
        CONFIG_DIR=str(tmp_path), SCRIPTS_DIR="/app/calibre-web-automated/scripts", DEFAULT_PORT=8083)
    package.logger = types.SimpleNamespace(create=lambda: _Log())
    monkeypatch.setitem(sys.modules, "cps", package)

    # Loaded from this checkout by path: the image under test carries its own
    # copy of the app, and a plain import resolves to that one instead.
    spec = importlib.util.spec_from_file_location("cps.content_server", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cps.content_server", module)
    spec.loader.exec_module(module)
    module.log_records = records
    return module


def _answering(module, monkeypatch, answering=True):
    monkeypatch.setattr(module, "is_ready", lambda: answering)
    monkeypatch.setattr(module.ownership, "busy", lambda *_args: bool(
        answering and module.config.config_calibre_server_enabled
        and module.config.config_calibre_dir
        and (module.config.config_calibre_server_anonymous_writes
             or (module.config.config_calibre_server_username and module.config.config_calibre_server_password_e))))


# --------------------------------------------------------------------------
# calibredb target construction
# --------------------------------------------------------------------------

def test_disabled_server_yields_no_arguments(content_server, monkeypatch):
    content_server.config.config_calibre_server_enabled = False
    _answering(content_server, monkeypatch)
    assert content_server.library_target() == content_server.NO_TARGET


def test_unconfigured_library_yields_no_arguments(content_server, monkeypatch):
    content_server.config.config_calibre_dir = ""
    _answering(content_server, monkeypatch)
    assert content_server.library_target() == content_server.NO_TARGET


def test_authenticated_target_addresses_the_server(content_server, monkeypatch):
    _answering(content_server, monkeypatch)
    target = content_server.library_target()
    assert target.args == ["--with-library", "http://127.0.0.1:7777/#calibre-library",
                           "--username", "ccsuser", "--password", "<stdin>"]


def test_anonymous_target_carries_no_credentials(content_server, monkeypatch):
    content_server.config.config_calibre_server_anonymous_writes = True
    _answering(content_server, monkeypatch)
    target = content_server.library_target()
    assert target.args == ["--with-library", "http://127.0.0.1:7777/#calibre-library"]
    assert target.stdin is None


def test_library_id_is_the_directory_name_without_trailing_separator(content_server, monkeypatch):
    content_server.config.config_calibre_dir = "/books/My Library/"
    _answering(content_server, monkeypatch)
    assert content_server.library_target().args[1] == "http://127.0.0.1:7777/#My_Library"


def test_a_server_that_is_not_answering_falls_back_to_the_library_path(content_server, monkeypatch):
    """Convert Library stops the server for its run, and a server can die.

    Callers substitute the library path for an empty target, so ingest and
    metadata embedding keep working instead of aiming at a closed port.
    """
    _answering(content_server, monkeypatch, answering=False)
    assert content_server.library_target() == content_server.NO_TARGET
    assert any("not answering" in record for record in content_server.log_records)


def test_probe_reports_a_closed_port_as_not_answering(content_server):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        content_server.config.config_calibre_server_port = probe.getsockname()[1]
    assert content_server.is_answering(timeout=0.2) is False


def test_probe_reports_an_open_port_as_answering(content_server):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        content_server.config.config_calibre_server_port = listener.getsockname()[1]
        assert content_server.is_answering(timeout=0.5) is True


# --------------------------------------------------------------------------
# calibre-server command line
# --------------------------------------------------------------------------

def test_authenticated_server_arguments(content_server):
    args = content_server.server_arguments()
    assert args[1:] == ["--port", "7777", "--listen-on", "127.0.0.1",
                        "--disable-fallback-to-detected-interface",
                        "--enable-auth",
                        "--userdb", content_server.userdb_path(),
                        "/calibre-library"]
    # No forced "basic": calibre's default is Digest over plain HTTP, which
    # calibredb speaks (measured, calibre 9.0) and which keeps the password
    # off the wire; basic sent it base64-encoded on every request (#2210 review).
    assert "--auth-mode" not in args


def test_anonymous_writes_pass_trusted_ips(content_server):
    content_server.config.config_calibre_server_anonymous_writes = True
    content_server.config.config_calibre_server_trusted_ips = "10.0.0.0/8,192.168.1.5"
    args = content_server.server_arguments()
    assert "--enable-local-write" in args
    assert args[args.index("--trusted-ips") + 1] == "10.0.0.0/8,192.168.1.5"
    assert "--enable-auth" not in args


def test_trusted_ips_are_omitted_when_empty(content_server):
    content_server.config.config_calibre_server_anonymous_writes = True
    assert "--trusted-ips" not in content_server.server_arguments()


def test_listen_address_defaults_to_loopback(content_server):
    content_server.config.config_calibre_server_listen = ""
    args = content_server.server_arguments()
    assert args[args.index("--listen-on") + 1] == "127.0.0.1"


# --------------------------------------------------------------------------
# user database creation
# --------------------------------------------------------------------------

@pytest.fixture
def userdb_call(content_server, monkeypatch, tmp_path):
    """Capture the subprocess the user-database creation would run."""
    calls = []
    userdb = tmp_path / "content_server_users.sqlite"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        userdb.write_bytes(b"sqlite")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(content_server.subprocess, "run", fake_run)
    content_server.write_userdb("ccsuser", PASSWORD, userdb=str(userdb), binary="/usr/bin/calibre-debug")
    return types.SimpleNamespace(command=calls[0][0], kwargs=calls[0][1], path=userdb,
                                 module=content_server)


def test_userdb_is_created_through_the_calibre_helper(userdb_call):
    assert userdb_call.command[:3] == ["/usr/bin/calibre-debug", "-e",
                                       "/app/calibre-web-automated/scripts/calibre_server_user.py"]
    assert userdb_call.command[-2:] == [str(userdb_call.path), "ccsuser"]


def test_userdb_password_is_supplied_on_stdin(userdb_call):
    assert userdb_call.kwargs["input"] == PASSWORD + "\n"


def test_userdb_password_never_reaches_a_command_argument(userdb_call):
    """The regression this file exists for: an argv password is world readable."""
    assert not any(PASSWORD in str(argument) for argument in userdb_call.command)


def test_userdb_is_not_readable_by_other_users(userdb_call):
    """calibre stores the password in cleartext in this file."""
    if os.name == "nt":
        pytest.skip("POSIX permission bits")
    assert userdb_call.path.stat().st_mode & 0o077 == 0


def test_userdb_failure_is_reported_without_the_password(content_server, monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="cannot open database")

    monkeypatch.setattr(content_server.subprocess, "run", fake_run)
    assert content_server.write_userdb("ccsuser", PASSWORD, userdb=str(tmp_path / "u.sqlite"),
                                       binary="/usr/bin/calibre-debug") is False
    assert any("Failed to create calibre content server user" in r for r in content_server.log_records)
    assert not any(PASSWORD in r for r in content_server.log_records)


# --------------------------------------------------------------------------
# the password stays out of argv and the log, whatever is built
# --------------------------------------------------------------------------

@pytest.mark.parametrize("anonymous", [False, True])
def test_no_built_command_line_carries_the_password(content_server, monkeypatch, anonymous):
    content_server.config.config_calibre_server_anonymous_writes = anonymous
    _answering(content_server, monkeypatch)
    for argument in content_server.server_arguments() + content_server.library_target().args:
        assert PASSWORD not in str(argument)


def test_the_password_is_only_ever_the_stdin_payload(content_server, monkeypatch):
    _answering(content_server, monkeypatch)
    target = content_server.library_target()
    assert target.stdin == PASSWORD + "\n"
    assert "--password" in target.args
    assert target.args[target.args.index("--password") + 1] == "<stdin>"


def test_no_log_record_carries_the_password(content_server, monkeypatch):
    _answering(content_server, monkeypatch, answering=False)
    content_server.library_target()
    content_server.config.config_calibre_server_enabled = False
    content_server.start()
    assert content_server.log_records
    assert not any(PASSWORD in record for record in content_server.log_records)


def test_a_config_without_the_settings_is_treated_as_disabled(content_server, monkeypatch):
    """The export path reaches this module with whatever config is loaded.

    cps.embed_helper calls library_target() during a metadata export, and the
    settings only exist once their migration has run. A config without them made
    the export raise AttributeError instead of falling back to the library path.
    """
    monkeypatch.setattr(content_server, "config", types.SimpleNamespace())
    assert content_server.library_target() == content_server.NO_TARGET


def test_every_setting_read_has_a_default(content_server, monkeypatch):
    monkeypatch.setattr(content_server, "config", types.SimpleNamespace())
    for name in content_server.SETTING_DEFAULTS:
        assert content_server.setting(name) == content_server.SETTING_DEFAULTS[name]


def _spawns(module, monkeypatch, tmp_path):
    """Record every calibre-server launch instead of performing it."""
    binary = tmp_path / "calibre-server"
    binary.write_text("")
    monkeypatch.setattr(module, "server_binary", lambda: str(binary))
    monkeypatch.setattr(module, "write_userdb", lambda *a, **k: True)
    monkeypatch.setattr(module, "threading", types.SimpleNamespace(
        Thread=lambda *a, **k: types.SimpleNamespace(start=lambda: None),
        current_thread=module.threading.current_thread,
        main_thread=module.threading.main_thread,
    ))
    launched = []

    class _Popen:
        returncode = None
        stdout = None

        def __init__(self, args, **_kwargs):
            launched.append(args)

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, *_a):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", _Popen)
    monkeypatch.setattr(module, "is_ready", lambda: True)
    return launched


@pytest.mark.parametrize("missing", ["config_calibre_server_password_e",
                                     "config_calibre_server_username"])
def test_an_enabled_server_without_credentials_is_not_started_open(
        content_server, monkeypatch, tmp_path, missing):
    """Review of #2210: "Reset Password", a startup with the credentials gone, or
    an env var removed before restart all reached a calibre-server with no auth
    flag at all -- the whole library readable by anyone who can reach the port.
    Without credentials, and without the explicit anonymous choice, it must not
    run."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    setattr(content_server.config, missing, "")

    content_server.start()

    assert launched == []
    assert any("authentication" in r for r in content_server.log_records)


def test_the_explicit_anonymous_choice_still_starts(content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.config.config_calibre_server_anonymous_writes = True
    content_server.config.config_calibre_server_password_e = ""

    content_server.start()

    assert len(launched) == 1 and "--enable-auth" not in launched[0]


def test_a_start_without_auth_leaves_no_old_password_on_disk(content_server, monkeypatch, tmp_path):
    """calibre keeps the password in cleartext in the userdb; once auth is off,
    the file has no purpose and must not outlive it."""
    _spawns(content_server, monkeypatch, tmp_path)
    userdb = tmp_path / "content_server_users.sqlite"
    userdb.write_text("old cleartext password")
    content_server.config.config_calibre_server_anonymous_writes = True

    content_server.start()

    assert not userdb.exists()


# calibre's own ``srv.library_broker.library_id_from_path``, measured with
# calibre 9.0's calibre-debug.
CALIBRE_LIBRARY_IDS = {
    "/calibre-library": "calibre-library",
    "/books/Calibre Library": "Calibre_Library",
    "/x/My Library/": "My_Library",
    "/x/a.b-c": "a.b-c",
}


@pytest.mark.parametrize("path,expected", sorted(CALIBRE_LIBRARY_IDS.items()))
def test_both_copies_derive_the_same_library_id(content_server, monkeypatch, path, expected):
    """The app and the standalone scripts each build the server URL. Both must
    name the library the way calibre-server does -- "#Calibre Library" matches
    no library there -- and they must not drift apart."""
    spec = importlib.util.spec_from_file_location(
        "calibre_library_target_idcheck", REPO_ROOT / "scripts" / "calibre_library_target.py")
    scripts_copy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "calibre_library_target_idcheck", scripts_copy)
    spec.loader.exec_module(scripts_copy)

    assert content_server.library_id(path) == expected
    assert scripts_copy.library_id(path) == expected


@pytest.mark.parametrize("port,username,password,problem", [
    ("8081", "ccsuser", "", None),
    (8081, "calibre user_1-a", "p@ss w0rd!", None),
    ("", "ccsuser", "", "port"),                 # was a 500 from int("")
    ("abc", "ccsuser", "", "port"),
    ("0", "ccsuser", "", "port"),
    ("65536", "ccsuser", "", "port"),
    ("8083", "ccsuser", "", "port-in-use"),       # the web UI's own port
    ("8081", "me@example", "", "username"),       # calibre rejects . and @
    ("8081", "a.b", "", "username"),
    ("8081", "ccsuser", "pässword", "password"),  # calibre: ASCII only
])
def test_settings_calibre_would_refuse_are_rejected_at_save(content_server, port, username,
                                                            password, problem):
    """Review of #2210: each of these was stored as "saved", after which the
    server crash-looped or never started, with the reason only in a log line."""
    assert content_server.settings_problem(port, username, password, 8083) == problem


def test_pause_reports_a_running_server_and_stops_it(content_server, monkeypatch, tmp_path):
    """Restore Calibre DB runs calibredb against the library path, which a
    running server holds open; it pauses the server and restarts it after,
    but only if one was running."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.start()
    assert len(launched) == 1

    first = content_server.hold_library()
    assert content_server._process is None
    second = content_server.hold_library()
    first.release()
    assert len(launched) == 1
    second.release()
    assert len(launched) == 2


def test_a_server_that_keeps_dying_on_startup_is_left_down_with_its_reason(
        content_server, monkeypatch, tmp_path):
    """Review of #2210: a taken port or a bad user database made calibre-server
    exit at once, and the watcher relaunched it every 5 s forever, with the
    reason only on the container's stdout. It now gives up after a few quick
    exits, and says why, until the admin saves again."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    monkeypatch.setattr(content_server.time, "sleep", lambda _s: None)
    content_server._drain_output(iter(["OSError: [Errno 98] Address already in use\n"]))

    content_server.start()
    for _ in range(10):
        process = content_server._process
        if process is None:
            break
        process.returncode = 1                     # died straight after starting
        content_server._watch(process, str(tmp_path / "metadata.db"))

    assert len(launched) == content_server.MAX_QUICK_EXITS
    assert content_server._process is None
    assert any("leaving it stopped" in r and "Address already in use" in r
               for r in content_server.log_records)

    content_server.start()                         # the admin saves again
    assert len(launched) == content_server.MAX_QUICK_EXITS + 1


def test_calibre_server_output_reaches_the_app_log(content_server):
    content_server._drain_output(iter(["listening on 127.0.0.1:8081\n", "\n"]))

    assert "calibre-server: listening on 127.0.0.1:8081" in content_server.log_records


@pytest.mark.parametrize("listen,host,url_host", [
    ("127.0.0.1", "127.0.0.1", "127.0.0.1"),
    ("", "127.0.0.1", "127.0.0.1"),
    ("0.0.0.0", "127.0.0.1", "127.0.0.1"),
    ("::", "::1", "[::1]"),
    ("192.168.1.20", "192.168.1.20", "192.168.1.20"),
    ("example.com", "127.0.0.1", "127.0.0.1"),   # never a name: only this host's own IPs
])
def test_calibredb_reaches_the_server_where_it_listens(content_server, monkeypatch, tmp_path,
                                                        listen, host, url_host):
    """Review of #2210: with a LAN listen address the loopback probe failed, so
    every calibredb call went to the library path while the server held it.
    Both copies (app and standalone scripts) must probe and address the same
    host."""
    spec = importlib.util.spec_from_file_location(
        "calibre_library_target_hostcheck", REPO_ROOT / "scripts" / "calibre_library_target.py")
    scripts_copy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "calibre_library_target_hostcheck", scripts_copy)
    spec.loader.exec_module(scripts_copy)
    content_server.config.config_calibre_server_listen = listen
    probed = []
    monkeypatch.setattr(content_server.socket, "create_connection",
                        lambda addr, timeout: probed.append(addr) or _Closing())

    assert content_server.is_answering() is True
    assert probed == [(host, 7777)]
    http_probes = []
    monkeypatch.setattr(content_server, "server_ready",
                        lambda target_host, port, library: http_probes.append(
                            (target_host, port, library)) or True)
    monkeypatch.setattr(content_server.ownership, "busy", lambda *_args: True)
    assert content_server.library_target().args[1].startswith("http://{}:7777/#".format(url_host))
    assert http_probes == [(host, 7777, "/calibre-library")]
    assert scripts_copy.connect_host(listen) == host


class _Closing:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.mark.parametrize('operation', ['start', 'stop', 'hold_library'])
def test_lifecycle_start_yields_to_other_web_requests(content_server, monkeypatch, operation):
    """A slow native process start must not park the production gevent request hub."""
    import time
    import gevent
    ticks = []
    if operation == 'stop':
        # Exercise an actual pending stop; stopping an absent server is now
        # deliberately immediate and does not enter the writer gate.
        content_server._process = types.SimpleNamespace()
    monkeypatch.setattr(content_server, '_locked_start' if operation == 'start' else '_locked_stop',
                        lambda: time.sleep(0.12))
    def other_request():
        for number in range(3):
            gevent.sleep(0.01)
            ticks.append(number)
    peer = gevent.spawn(other_request)
    getattr(content_server, operation)()
    observed = list(ticks)
    peer.join()
    assert observed == [0, 1, 2], 'unrelated requests stalled behind content-server startup'


def test_settings_start_cannot_reopen_library_while_conversion_has_it(content_server, monkeypatch, tmp_path):
    """A settings save during Convert Library must not reacquire Calibre's exclusive lock."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.start()
    hold = content_server.hold_library()
    content_server.start()
    assert len(launched) == 1, 'a settings save reopened the library during conversion'

    hold.release()
    assert len(launched) == 2
    hold.release()
    assert len(launched) == 2, 'a repeated release restarted the server twice'


def test_enable_during_disabled_library_hold_waits_for_release(content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.config.config_calibre_server_enabled = False
    with content_server.hold_library():
        content_server.config.config_calibre_server_enabled = True
        content_server.start()
        assert launched == []
    assert len(launched) == 1


def test_disable_during_library_hold_stays_disabled_on_release(content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.start()
    with content_server.hold_library():
        content_server.config.config_calibre_server_enabled = False
        content_server.stop()
    assert len(launched) == 1
    assert content_server._process is None


def test_split_library_cannot_start_a_server_on_the_metadata_only_directory(content_server, monkeypatch, tmp_path):
    """Calibre's broker requires library-local metadata.db despite the split override."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.config.config_calibre_split = True
    content_server.config.config_calibre_split_dir = '/separate-book-files'
    content_server.start()
    assert launched == []
    assert any('split' in message.lower() for message in content_server.log_records)


def test_authenticated_target_without_credentials_never_sends_anonymous_server_call(content_server, monkeypatch):
    """A changed/cleared credential must not silently turn routing into an anonymous call."""
    content_server.config.config_calibre_server_password_e = ''
    _answering(content_server, monkeypatch)
    assert content_server.library_target().args == []


def test_external_write_before_first_watch_poll_still_reloads(content_server, monkeypatch, tmp_path):
    """The initial five-second sleep must not absorb an edit made after cache loading."""
    launched = _spawns(content_server, monkeypatch, tmp_path)
    callbacks = []
    content_server.threading.Thread = lambda target, args=(), **kwargs: types.SimpleNamespace(
        start=lambda: callbacks.append((target, args)))
    stamp = {'value': 10}
    clock = {'value': 0}
    def sleep(seconds):
        clock['value'] += seconds
        if clock['value'] > 40:
            content_server._process = None
    monkeypatch.setattr(content_server, 'time', types.SimpleNamespace(
        monotonic=lambda: clock['value'], time=lambda: clock['value'], sleep=sleep))
    monkeypatch.setattr(content_server, '_db_mtime', lambda _path: stamp['value'])
    content_server.start()
    watcher, args = callbacks[-1]
    stamp['value'] = 20  # an external write after start, before the watcher wakes
    watcher(*args)
    assert len(launched) == 2, 'the first polling delay accepted stale cache as its baseline'
    assert any('database changed' in message for message in content_server.log_records)


@pytest.mark.parametrize('reap_fails', [False, True])
def test_forced_stop_must_reap_before_handing_library_to_another_owner(content_server, reap_fails):
    """SIGKILL is a request: the old child still owns the library until wait confirms exit."""
    calls = []
    class Process:
        def poll(self):
            return None
        def terminate(self):
            calls.append('terminate')
        def kill(self):
            calls.append('kill')
        def wait(self, timeout):
            calls.append(('wait', timeout))
            if len([x for x in calls if isinstance(x, tuple)]) == 1 or reap_fails:
                raise content_server.subprocess.TimeoutExpired('calibre-server', timeout)
            return -9
    process = Process()
    content_server._process = process
    if reap_fails:
        with pytest.raises(content_server.subprocess.TimeoutExpired):
            content_server.hold_library()
        assert content_server._process is process
        assert content_server._library_holds == 0
    else:
        hold = content_server.hold_library()
        assert calls == ['terminate', ('wait', 10), 'kill', ('wait', 10)]
        assert content_server._process is None
        hold.release()


def test_startup_refuses_the_actual_web_port_even_for_previously_saved_settings(content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    content_server.config.config_calibre_server_port = 8083
    content_server.start()
    assert launched == []
    assert any("port-in-use" in message for message in content_server.log_records)


def test_server_child_gets_writable_font_cache_without_changing_plugin_selection(content_server, monkeypatch, tmp_path):
    _spawns(content_server, monkeypatch, tmp_path)
    observed = {}
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.setenv('CALIBRE_CONFIG_DIRECTORY', '/plugin-free-config')
    existing_home = os.environ.get('HOME')
    class Process:
        stdout = None
        def poll(self):
            return None
    def launch(_args, **kwargs):
        observed.update(kwargs.get('env') or {})
        return Process()
    monkeypatch.setattr(content_server.subprocess, 'Popen', launch)
    content_server.start()
    assert observed.get('XDG_CACHE_HOME') == str(tmp_path / '.cache')
    assert observed.get('CALIBRE_CONFIG_DIRECTORY') == '/plugin-free-config'
    assert observed.get('HOME') == existing_home


def test_packaged_app_routing_does_not_depend_on_an_unrelated_scripts_package(content_server, monkeypatch):
    """An installed cps package must start even if another package owns scripts."""
    unrelated = types.ModuleType("scripts")
    unrelated.__path__ = []
    monkeypatch.setitem(sys.modules, "scripts", unrelated)
    monkeypatch.delitem(sys.modules, "scripts.calibre_library_target", raising=False)
    spec = importlib.util.spec_from_file_location("cps.content_server_installed", MODULE_PATH)
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)
    _answering(installed, monkeypatch)
    target = installed.library_target()
    assert target.args[:2] == ["--with-library", "http://127.0.0.1:7777/#calibre-library"]
    assert target.stdin == PASSWORD + "\n"


def test_anonymous_specific_lan_bind_trusts_only_its_own_local_source_address(content_server):
    """Calibre local-write recognizes loopback; a specific LAN bind needs its local IP."""
    content_server.config.config_calibre_server_anonymous_writes = True
    content_server.config.config_calibre_server_listen = "192.168.1.20"
    content_server.config.config_calibre_server_trusted_ips = "10.0.0.5/32"
    arguments = content_server.server_arguments()
    assert arguments[arguments.index("--trusted-ips") + 1] == "10.0.0.5/32,192.168.1.20"
    assert "0.0.0.0/0" not in arguments


def test_deferred_reconciler_survives_a_second_maintenance_start(content_server, monkeypatch):
    """A conversion starting at the handoff cannot strand the enabled server down."""
    callbacks = []
    monkeypatch.setattr(content_server.threading, "Thread",
                        lambda target, **kwargs: types.SimpleNamespace(start=lambda: callbacks.append(target)))
    active = [False]
    starts = []
    monkeypatch.setattr(content_server.ownership, "busy", lambda *_args: active[0])
    monkeypatch.setattr(content_server.time, "sleep", lambda *_args: active.__setitem__(0, False))
    def start():
        starts.append(True)
        active[0] = len(starts) == 1
        if active[0]:
            content_server._defer_for_maintenance()
    monkeypatch.setattr(content_server, "_locked_start", start)
    content_server._defer_for_maintenance()
    assert len(callbacks) == 1
    callbacks[0]()
    assert len(starts) == 2
    assert content_server._maintenance_waiter is False


def test_reconciler_thread_failure_leaves_later_saves_able_to_retry(content_server, monkeypatch):
    class FailingThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("thread capacity")
    monkeypatch.setattr(content_server.threading, "Thread", FailingThread)
    with pytest.raises(RuntimeError, match="thread capacity"):
        content_server._defer_for_maintenance()
    assert content_server._maintenance_waiter is False


def test_restore_hold_releases_its_write_gate_even_if_server_restart_fails(
        content_server, monkeypatch, tmp_path):
    _spawns(content_server, monkeypatch, tmp_path)
    content_server.start()
    hold = content_server.hold_library(exclusive=True)
    monkeypatch.setattr(content_server, "_locked_start",
                        lambda: (_ for _ in ()).throw(RuntimeError("restart failed")))
    try:
        with pytest.raises(RuntimeError, match="restart failed"):
            hold.release()
        assert content_server._library_holds == 0
        with content_server.ownership.operation(str(tmp_path), timeout=0.05):
            pass
        assert hold.child_ownership() == {}
        hold.release()  # a failed restart must not decrement the hold twice
    finally:
        if hold.exclusive_context is not None:
            hold.exclusive_context.__exit__(None, None, None)


def test_configuration_reconcile_retains_one_gate_across_native_lifecycle(
        content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    observed = []

    @content_server.configuration_update
    def save():
        with pytest.raises(TimeoutError):
            with content_server.ownership.operation(str(tmp_path), timeout=0.05):
                pytest.fail("settings generation became visible before reconciliation")
        content_server.start()
        observed.append(content_server._process is not None)
        content_server.stop()
        observed.append(content_server._process is None)
        raise RuntimeError("later configuration failure")

    with pytest.raises(RuntimeError, match="later configuration failure"):
        save()
    assert observed == [True, True]
    assert len(launched) == 1
    with content_server.ownership.operation(str(tmp_path), timeout=0.05):
        pass
    assert content_server._configuration_gate_owned.get() is False


def test_native_windows_saved_enabled_settings_cannot_launch_an_unowned_child(
        content_server, monkeypatch, tmp_path):
    launched = _spawns(content_server, monkeypatch, tmp_path)
    monkeypatch.setattr(content_server, "platform_supported", lambda: False)
    content_server.start()
    assert launched == []
    assert any("POSIX" in message for message in content_server.log_records)


def test_failures_before_readiness_exhaust_the_genuine_crash_budget(
        content_server, monkeypatch, tmp_path):
    """Three failed launches must stop even before Calibre answers HTTP."""
    _spawns(content_server, monkeypatch, tmp_path)
    launches = []

    def exited(*_args, **_kwargs):
        process = types.SimpleNamespace(returncode=1, stdout=None, poll=lambda: 1)
        launches.append(process)
        return process

    monkeypatch.setattr(content_server.subprocess, "Popen", exited)
    monkeypatch.setattr(content_server, "is_ready", lambda: False)
    monkeypatch.setattr(content_server.time, "monotonic", lambda: 1000.0)
    content_server.start()
    for _ in range(3):
        content_server._restart_after_exit(content_server._process)
    assert len(launches) == 3, "startup failures kept launching after the crash budget"
    assert content_server._quick_exits == 3
    assert content_server._process is None
    assert not any("started on port" in record for record in content_server.log_records)


def test_finished_maintenance_exit_does_not_consume_genuine_crash_budget(content_server, monkeypatch):
    """The maintenance lease may be free before the watcher sees the drain exit."""
    callbacks = []
    monkeypatch.setattr(content_server, '_defer_for_maintenance', lambda: callbacks.append(True))
    monkeypatch.setattr(content_server.ownership, 'busy', lambda *_args: False)
    monkeypatch.setattr(content_server, '_locked_start', lambda: None)
    content_server._quick_exits = 1
    for _ in range(4):
        process = types.SimpleNamespace(returncode=75)
        content_server._process = process
        content_server._restart_after_exit(process)
    assert content_server._quick_exits == 1
    assert len(callbacks) == 4
    assert not any('leaving it stopped' in r for r in content_server.log_records)


@pytest.mark.parametrize('boundary', ['watch', 'reconcile', 'initial-start'])
def test_lifecycle_gate_timeout_preserves_monitoring_and_start_retry(content_server, monkeypatch, boundary):
    """Inject one acquisition deadline, then release the gate; actors must survive."""
    from contextlib import contextmanager
    attempts = []
    starts = []
    callbacks = []
    monkeypatch.setattr(content_server.threading, 'Thread', lambda target, **kwargs:
                        types.SimpleNamespace(start=lambda: callbacks.append(target)))
    monkeypatch.setattr(content_server.time, 'sleep', lambda *_args: None)
    monkeypatch.setattr(content_server.ownership, 'busy', lambda *_args: False)

    @contextmanager
    def contended(*args, **kwargs):
        attempts.append(kwargs.get('timeout'))
        if len(attempts) == 1:
            raise TimeoutError('existing writer still owns the gate')
        yield

    monkeypatch.setattr(content_server.ownership, 'operation', contended)
    monkeypatch.setattr(content_server, '_locked_start', lambda: starts.append(True))
    if boundary == 'watch':
        process = types.SimpleNamespace(poll=lambda: 1)
        content_server._process = process
        monkeypatch.setattr(content_server, '_restart_after_exit', lambda _p: starts.append(True))
        content_server._watch(process, '/unused/metadata.db')
    elif boundary == 'reconcile':
        content_server._defer_for_maintenance()
        callbacks[0]()
    else:
        content_server._start_blocking()
        assert callbacks, 'startup contention must schedule later reconciliation'
        assert attempts[0] < 1, 'an offline child must not delay web startup for 120s'
        callbacks[0]()
    assert starts == [True]
    assert len(attempts) >= 2


def test_completed_conversion_hold_does_not_leak_when_another_writer_has_gate(
        content_server, monkeypatch, tmp_path):
    _spawns(content_server, monkeypatch, tmp_path)
    content_server.start()
    hold = content_server.hold_library()
    deferred = []
    monkeypatch.setattr(content_server, "_defer_for_maintenance", lambda: deferred.append(True))
    from contextlib import contextmanager
    @contextmanager
    def blocked(*_a, **_kw):
        raise TimeoutError("Restore owns gate")
        yield
    monkeypatch.setattr(content_server.ownership, "operation", blocked)
    hold.release()
    assert hold.released and content_server._library_holds == 0
    assert deferred == [True]
    hold.release()
    assert content_server._library_holds == 0 and deferred == [True]


def test_app_exit_uses_lifeline_when_stop_cannot_enter_busy_writer_gate(
        content_server, monkeypatch):
    def blocked():
        raise TimeoutError("Restore owns gate")
    monkeypatch.setattr(content_server, "stop", blocked)
    content_server.stop_before_app_exit()
    assert any("lifeline" in message for message in content_server.log_records)
