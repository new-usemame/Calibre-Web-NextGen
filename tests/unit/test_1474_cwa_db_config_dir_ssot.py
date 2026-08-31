# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""``cwa.db`` has to land in the same config dir the rest of the install uses.

#1462/#1463 moved ``app.db``, ``dirs.json`` and the setup scripts onto the
``app_paths.config_dir()`` single source of truth, but ``cwa_db.py`` kept a
hardcoded ``/config/`` fallback. Off Docker that reproduces the exact bug the
#1462 work was for, one file over: ``CWA_DB()`` tries to *create* ``/config``
at the filesystem root, and either seeds ``cwa.db`` into a directory the app
never reads (running as root) or fails to open it and calls ``sys.exit(0)``
(everywhere else) — a silent, successful-looking death on the ingest path.

The container is unaffected: its Dockerfile sets ``CALIBRE_DBPATH=/config``,
so the resolver returns ``/config`` there exactly as the literal did. That is
also why CI never saw this — see ``notes/verify/FAILURE-MODES.md`` on
environment variables masking a divergent default.
"""

import os
import sqlite3

import pytest

pytestmark = pytest.mark.unit


def _fresh_cwa_db_module(monkeypatch=None, legacy=None):
    """Import ``cwa_db`` fresh, with the legacy config dir FORCED.

    Every test here has to state where the legacy directory is, because the
    answer changes the behaviour under test. Reading the real one is what broke
    this file in CI: the test container has a genuine ``/config/cwa.db``, so the
    compatibility branch fired and ``default_db_dir()`` correctly returned
    ``/config`` — the product was right and the test was asserting the runner.
    Pass ``legacy`` pointing at a directory with a ``cwa.db`` to exercise the
    compatibility branch, or at an empty/absent one to exercise the normal path.
    """
    import sys

    sys.modules.pop("cwa_db", None)
    import cwa_db

    if monkeypatch is not None:
        monkeypatch.setattr(cwa_db, "_LEGACY_NOTICE_SHOWN", False)
        if legacy is not None:
            monkeypatch.setattr(cwa_db.app_paths, "DEFAULT_CONFIG_DIR", str(legacy))
    return cwa_db


def test_default_db_dir_follows_calibre_dbpath_not_the_literal_config(tmp_path, monkeypatch):
    """A source install pointing CALIBRE_DBPATH somewhere real must put cwa.db there.

    Fails before the fix: ``db_path`` was ``/config/`` regardless of
    ``CALIBRE_DBPATH``, so ``cwa.db`` and ``app.db`` landed in different
    directories on every non-Docker install.
    """
    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    resolved = cwa_db.default_db_dir()

    assert os.path.realpath(resolved) == os.path.realpath(str(tmp_path))
    assert os.path.realpath(resolved) != "/config"


def test_container_default_is_byte_for_byte_unchanged(tmp_path, monkeypatch):
    """With CALIBRE_DBPATH=/config — what the image sets — nothing moves."""
    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", "/config")
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    assert cwa_db.default_db_dir().rstrip("/") == "/config"


def test_cwa_db_path_still_wins_for_test_isolation(tmp_path, monkeypatch):
    """The explicit override keeps beating the resolver; parallel workers rely on it."""
    monkeypatch.setenv("CALIBRE_DBPATH", "/config")
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    assert cwa_db.default_db_dir().rstrip("/") == str(tmp_path).rstrip("/")


def test_source_install_opens_cwa_db_beside_app_db(tmp_path, monkeypatch):
    """End of the actual user flow: instantiating CWA_DB writes into the resolved dir.

    Before the fix this raised ``SystemExit(0)`` on any machine without a
    writable ``/config`` — which is how the ingest processor died silently.
    """
    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    db = cwa_db.CWA_DB(verbose=False)
    try:
        assert (tmp_path / "cwa.db").is_file()
        assert os.path.realpath(db.db_path) == os.path.realpath(str(tmp_path))
    finally:
        if db.con:
            db.con.close()


def test_schema_column_add_does_not_depend_on_a_writable_slash_config(tmp_path, monkeypatch):
    """Adding a missing settings column must not be gated on ``/config`` existing.

    ``add_missing_setting`` appended its audit line to a hardcoded
    ``/config/.cwa_db_debug`` *before* running the ``ALTER TABLE``, inside one
    try/except. Off Docker the open raised, so the ALTER never ran and the
    method reported failure — a schema migration silently skipped on exactly
    the installs #1462 was fixing.
    """
    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    db = cwa_db.CWA_DB(verbose=False)
    try:
        db.cur.execute("PRAGMA table_info(cwa_settings)")
        before = {row[1] for row in db.cur.fetchall()}
        assert "auto_backup_imports" in before or before, "cwa_settings should exist"

        db.cur.execute("ALTER TABLE cwa_settings ADD COLUMN probe_marker INTEGER DEFAULT 0")
        db.con.commit()
        db.cur.execute("PRAGMA table_info(cwa_settings)")
        after = {row[1] for row in db.cur.fetchall()}
        assert "probe_marker" in after
    finally:
        if db.con:
            db.con.close()

    # The audit breadcrumb must never be written outside the resolved config dir.
    import inspect

    src = inspect.getsource(cwa_db.CWA_DB.add_missing_setting)
    assert "'/config/.cwa_db_debug'" not in src and '"/config/.cwa_db_debug"' not in src, (
        "add_missing_setting still writes its debug breadcrumb to a hardcoded /config"
    )


def test_no_hardcoded_slash_config_literal_remains_in_cwa_db():
    """Source pin: the literal path is what regressed, so pin its absence directly.

    Parsed with ``ast`` rather than grepped, so prose in a docstring explaining
    the old behaviour does not read as a reintroduction of it. Only a string
    constant that *is* a ``/config`` path counts.
    """
    import ast

    import cwa_db as _mod

    tree = ast.parse(open(_mod.__file__, "r", encoding="utf-8").read())
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/config")
    ]
    assert not offenders, f"hardcoded /config paths left in cwa_db.py: {offenders}"


def test_existing_legacy_database_is_kept_not_stranded(tmp_path, monkeypatch):
    """An upgrade must never silently open a fresh, empty settings database.

    Anyone overriding ``CALIBRE_DBPATH`` has ``app.db`` at their path and
    ``cwa.db`` at the legacy location today, because the old literal ignored
    the override. Resolving both to the new path would leave the real settings
    database behind and start an empty one — settings, import history and
    enforcement records apparently gone. Found by adversarial review of #1474.
    """
    legacy = tmp_path / "legacy"
    resolved = tmp_path / "resolved"
    legacy.mkdir()
    resolved.mkdir()
    (legacy / "cwa.db").write_bytes(b"")

    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(resolved))
    cwa_db = _fresh_cwa_db_module(monkeypatch, legacy)

    assert os.path.realpath(cwa_db.default_db_dir()) == os.path.realpath(str(legacy))


def test_legacy_notice_is_printed_once_per_process_not_per_call(tmp_path, monkeypatch, capsys):
    """CWA_DB is constructed inside web requests, so a per-call print is log spam.

    Found by the second adversarial pass: an install left on the legacy path
    would emit the same line on every request that reads settings.
    """
    legacy = tmp_path / "legacy"
    resolved = tmp_path / "resolved"
    legacy.mkdir()
    resolved.mkdir()
    (legacy / "cwa.db").write_bytes(b"")

    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(resolved))
    cwa_db = _fresh_cwa_db_module(monkeypatch, legacy)

    for _ in range(25):
        cwa_db.default_db_dir()

    assert capsys.readouterr().out.count("using the existing settings database") == 1


def test_repeated_trailing_separators_collapse(tmp_path, monkeypatch):
    """`_as_dir` promises exactly one trailing separator; make that true."""
    monkeypatch.setenv("CWA_DB_PATH", "/tmp/multi///")
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    assert cwa_db.default_db_dir() == "/tmp/multi/"


def test_legacy_is_ignored_once_the_resolved_database_exists(tmp_path, monkeypatch):
    """The legacy branch is a one-way rescue, not a permanent redirect."""
    legacy = tmp_path / "legacy"
    resolved = tmp_path / "resolved"
    legacy.mkdir()
    resolved.mkdir()
    (legacy / "cwa.db").write_bytes(b"")
    (resolved / "cwa.db").write_bytes(b"")

    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    monkeypatch.setenv("CALIBRE_DBPATH", str(resolved))
    cwa_db = _fresh_cwa_db_module(monkeypatch, legacy)

    assert os.path.realpath(cwa_db.default_db_dir()) == os.path.realpath(str(resolved))


def test_cwa_db_path_overrides_the_legacy_rescue(tmp_path, monkeypatch):
    """The explicit knob is the escape hatch from the legacy branch."""
    legacy = tmp_path / "legacy"
    chosen = tmp_path / "chosen"
    legacy.mkdir()
    chosen.mkdir()
    (legacy / "cwa.db").write_bytes(b"")

    monkeypatch.setenv("CALIBRE_DBPATH", str(tmp_path / "resolved"))
    monkeypatch.setenv("CWA_DB_PATH", str(chosen))
    cwa_db = _fresh_cwa_db_module(monkeypatch, legacy)

    assert os.path.realpath(cwa_db.default_db_dir()) == os.path.realpath(str(chosen))


def test_override_with_surrounding_whitespace_is_normalised(tmp_path, monkeypatch):
    """``CWA_DB_PATH=' /x '`` must not become the relative path ``' /x /'``.

    ``strip()`` was used to test emptiness but the untrimmed value was returned,
    so a stray space made the resolver create directories under the CWD.
    """
    monkeypatch.setenv("CWA_DB_PATH", f"  {tmp_path}  ")
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    resolved = cwa_db.default_db_dir()
    assert not resolved.startswith(" ")
    assert os.path.realpath(resolved) == os.path.realpath(str(tmp_path))


def test_failure_message_names_the_knob_actually_in_force(tmp_path, monkeypatch, capsys):
    """Telling someone to set CALIBRE_DBPATH while CWA_DB_PATH wins is a loop."""
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "missing"))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")
    monkeypatch.setattr(
        cwa_db.sqlite3,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("unable to open database file")),
    )

    with pytest.raises(SystemExit):
        cwa_db.CWA_DB(verbose=False)

    out = capsys.readouterr().out
    assert "CWA_DB_PATH" in out


def test_schema_migration_writes_no_breadcrumb_file_at_all():
    """The ``.cwa_db_debug`` append target is gone, not relocated.

    It had no delimiters, recorded intent before outcome, and was a predictable
    append path inside a user-controlled config volume — a FIFO left there
    blocks the writer before any OSError can be caught.
    """
    import cwa_db as _mod

    source = open(_mod.__file__, "r", encoding="utf-8").read()
    executable = [
        line for line in source.splitlines()
        if ".cwa_db_debug" in line and not line.strip().startswith("#")
    ]
    assert not executable, f".cwa_db_debug is still written: {executable}"


def test_unwritable_config_dir_is_reported_not_exited_silently(tmp_path, monkeypatch):
    """A DB that cannot be opened must not look like a clean exit to the caller.

    ``connect_to_db`` called ``sys.exit(0)``. Under the ingest processor that
    is indistinguishable from success. Whatever the failure handling is, it
    must not be exit status zero.
    """
    monkeypatch.delenv("CWA_DB_PATH", raising=False)
    unwritable = tmp_path / "nope"
    unwritable.mkdir()
    monkeypatch.setenv("CALIBRE_DBPATH", str(unwritable / "child"))
    cwa_db = _fresh_cwa_db_module(monkeypatch, tmp_path / "no-legacy")

    monkeypatch.setattr(
        cwa_db.sqlite3,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("unable to open database file")),
    )

    with pytest.raises(SystemExit) as exc:
        cwa_db.CWA_DB(verbose=False)
    assert exc.value.code != 0, (
        "connect_to_db exits 0 on failure, so callers cannot tell it died"
    )
