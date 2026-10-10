# Calibre-Web Automated - fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Library addressing for the scripts that run outside the Flask app.

ingest_processor and cover_enforcer take the same decision as
cps.content_server, but from app.db rather than the loaded config. The cases
that matter are the ones where the two could disagree: credentials supplied
through the environment, and a content server that is switched on but not
running.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "calibre_library_target.py"
LIBRARY = "/calibre-library"
PASSWORD = "Sup3rSecret-PW-2026"

SETTINGS_COLUMNS = ("config_calibre_server_enabled", "config_calibre_server_port",
                    "config_calibre_server_anonymous_writes", "config_calibre_server_username",
                    "config_calibre_server_password_e", "config_calibre_server_listen")


def _make_app_db(path, enabled=1, port=7777, anonymous=0, username="ccsuser", password_e=None,
                 listen="127.0.0.1"):
    con = sqlite3.connect(str(path))
    con.execute("create table settings ({})".format(", ".join(SETTINGS_COLUMNS)))
    con.execute("insert into settings values (?, ?, ?, ?, ?, ?)",
                (enabled, port, anonymous, username, password_e, listen))
    con.commit()
    con.close()


@pytest.fixture
def target_module(monkeypatch, tmp_path):
    for name in ("CALIBRE_SERVER_PORT", "CALIBRE_SERVER_USERNAME", "CALIBRE_SERVER_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    app_db = tmp_path / "app.db"

    spec = importlib.util.spec_from_file_location("calibre_library_target_undertest", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "calibre_library_target_undertest", module)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "app_db_path", lambda: str(app_db))
    monkeypatch.setattr(module, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(module, "_is_answering", lambda host, port, library_dir=None: True)
    def managed_owner(*_args):
        try:
            row = module._read_settings()
        except sqlite3.Error:
            return False
        if not row or not row[0]:
            return False
        _, port, anonymous, username, password_e, listen = row
        port, username, password = module._apply_env(port, username, module._decrypt(password_e))
        return bool((anonymous or (username and password)) and module._is_answering(listen, port, LIBRARY))
    monkeypatch.setattr(module.ownership, "busy", managed_owner)
    module.real_decrypt = module._decrypt
    monkeypatch.setattr(module, "_decrypt", lambda token: PASSWORD if token else "")
    module.app_db = app_db
    return module


def test_disabled_server_addresses_the_library_path(target_module):
    _make_app_db(target_module.app_db, enabled=0)
    target = target_module.library_target(LIBRARY)
    assert target.args == ["--library-path=/calibre-library"]
    assert target.stdin is None


def test_enabled_server_is_addressed_with_credentials(target_module):
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    target = target_module.library_target(LIBRARY)
    assert target.args == ["--with-library", "http://127.0.0.1:7777/#calibre-library",
                           "--username", "ccsuser", "--password", "<stdin>"]
    assert target.stdin == PASSWORD + "\n"


def test_password_is_never_an_argument(target_module):
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    target = target_module.library_target(LIBRARY)
    assert not any(PASSWORD in argument for argument in target.args)


def test_anonymous_writes_need_no_credentials(target_module):
    _make_app_db(target_module.app_db, anonymous=1, username="", password_e=None)
    target = target_module.library_target(LIBRARY)
    assert target.args == ["--with-library", "http://127.0.0.1:7777/#calibre-library"]
    assert target.stdin is None


def test_environment_credentials_are_honoured(target_module, monkeypatch):
    """A deployment can configure the server purely through the environment.

    cps.config_sql applies CALIBRE_SERVER_* to the running config and never
    writes it back, so app.db holds no credentials and the scripts would
    otherwise fall back to the library path while the app runs an
    authenticating server.
    """
    _make_app_db(target_module.app_db, username="", password_e=None)
    monkeypatch.setenv("CALIBRE_SERVER_USERNAME", "envuser")
    monkeypatch.setenv("CALIBRE_SERVER_PASSWORD", "env-password")
    target = target_module.library_target(LIBRARY)
    assert target.args[2:] == ["--username", "envuser", "--password", "<stdin>"]
    assert target.stdin == "env-password\n"


def test_environment_port_overrides_the_database(target_module, monkeypatch):
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    monkeypatch.setenv("CALIBRE_SERVER_PORT", "9999")
    assert target_module.library_target(LIBRARY).args[1] == "http://127.0.0.1:9999/#calibre-library"


def test_a_server_that_is_not_running_falls_back_to_the_library_path(target_module, monkeypatch):
    """Convert Library stops the server for its run, and a server can die."""
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    monkeypatch.setattr(target_module, "_is_answering", lambda host, port, library_dir=None: False)
    assert target_module.library_target(LIBRARY).args == ["--library-path=/calibre-library"]


def test_missing_credentials_fall_back_rather_than_fail_authentication(target_module):
    _make_app_db(target_module.app_db, username="", password_e=None)
    assert target_module.library_target(LIBRARY).args == ["--library-path=/calibre-library"]


def test_an_unreadable_app_db_falls_back_to_the_library_path(target_module, monkeypatch):
    monkeypatch.setattr(target_module, "app_db_path", lambda: "/nonexistent/app.db")
    assert target_module.library_target(LIBRARY).args == ["--library-path=/calibre-library"]


def test_library_id_is_the_directory_name(target_module):
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    assert target_module.library_target("/books/My Library/").args[1] == \
        "http://127.0.0.1:7777/#My_Library"


def test_ingest_transaction_helper_is_addressed_by_path_only():
    """The helper is not calibredb.

    scripts/calibre_ingest_transaction.py parses --library-path and opens the
    library with LibraryDatabase(path); it has no notion of a server URL.
    Handing it --with-library made every import exit 2 on an argparse error for
    as long as the content server was switched on.
    """
    import importlib
    scripts_dir = str(REPO_ROOT / "scripts")
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(scripts_dir)
        processor_module = importlib.import_module("ingest_processor")
        processor = object.__new__(processor_module.NewBookProcessor)
        processor.library_dir = "/books/Library With Spaces"
        processor.cwa_settings = {"auto_ingest_automerge": "overwrite"}
        processor.calibre_env = {}
        command = processor._calibre_transaction_command(
            Path("/ingest/staged.epub"), Path("/ingest/original.acsm"),
            "import-digest", "ticket-digest", {}, "add")
    assert command[:2] == ["calibre-debug", "-e"]
    assert Path(command[2]).name == "calibre_ingest_transaction.py"
    assert command[command.index("--library-path") + 1] == processor.library_dir
    assert "--with-library" not in command
    assert command[command.index("--identity-path") + 1] == "/ingest/original.acsm"


def test_the_fallback_says_why_it_fell_back(target_module, monkeypatch, capsys):
    """An operator reading the ingest log can tell the two paths apart."""
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    monkeypatch.setattr(target_module, "_is_answering", lambda host, port, library_dir=None: False)
    target_module.library_target(LIBRARY)
    message = capsys.readouterr().err
    assert "not answering on 127.0.0.1 port 7777" in message
    assert "addressing the library by path instead" in message


def test_the_fallback_message_never_carries_the_password(target_module, monkeypatch, capsys):
    _make_app_db(target_module.app_db, password_e=b"encrypted")
    monkeypatch.setattr(target_module, "_is_answering", lambda host, port, library_dir=None: False)
    target_module.library_target(LIBRARY)
    assert PASSWORD not in capsys.readouterr().err



@pytest.mark.parametrize("env_port", ["0", "65536", "99999"])
def test_an_out_of_range_env_port_is_ignored(target_module, monkeypatch, env_port):
    """A port calibre-server could never listen on must not replace the
    configured one; the app's own config applies the same bound."""
    _make_app_db(target_module.app_db, password_e=b"token")
    monkeypatch.setenv("CALIBRE_SERVER_PORT", env_port)

    assert target_module.library_target(LIBRARY).args[1] == "http://127.0.0.1:7777/#calibre-library"


def test_custom_app_database_uses_the_key_beside_that_database(target_module, monkeypatch, tmp_path):
    """Out-of-process ingest must decrypt the same custom app.db as the Flask config."""
    from cryptography.fernet import Fernet
    app_directory = tmp_path / 'custom-app-database'
    app_directory.mkdir()
    target_module.app_db = app_directory / 'app.db'
    monkeypatch.setattr(target_module, 'app_db_path', lambda: str(target_module.app_db))
    key = Fernet.generate_key()
    (app_directory / '.key').write_bytes(key)
    _make_app_db(target_module.app_db, password_e=Fernet(key).encrypt(PASSWORD.encode()).decode())
    monkeypatch.setattr(target_module, '_decrypt', target_module.real_decrypt)
    target = target_module.library_target(LIBRARY)
    assert target.args[1] == 'http://127.0.0.1:7777/#calibre-library'
    assert target.stdin == PASSWORD + '\n'


@pytest.mark.parametrize("case", ["disabled", "missing-credentials", "unreadable-settings"])
def test_scripts_never_fall_back_to_path_while_managed_owner_is_alive(target_module, monkeypatch, case):
    if case == "disabled":
        _make_app_db(target_module.app_db, enabled=0)
    elif case == "missing-credentials":
        _make_app_db(target_module.app_db, username="", password_e=None)
    monkeypatch.setattr(target_module.ownership, "busy", lambda *_args: True)
    with pytest.raises(TimeoutError, match="owns the library"):
        target_module.library_target(LIBRARY)
