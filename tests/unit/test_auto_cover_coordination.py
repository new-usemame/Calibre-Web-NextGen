# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic cover file/flag writes share ownership and recover after flag failure."""
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(params=["enforcer", "ingest"])
def automatic_cover(request, monkeypatch, tmp_path):
    from cps.services import cover_generator
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    target = importlib.import_module("calibre_library_target")
    module = importlib.import_module("cover_enforcer" if request.param == "enforcer" else "ingest_processor")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("CALIBRE_DBPATH", str(config_dir))
    monkeypatch.setenv("CWA_METADATA_LOCK_DIR", str(config_dir))
    monkeypatch.setattr(target, "config_dir", lambda: config_dir)
    library = tmp_path / "library"
    book_dir = library / "Author/Book (1)"
    book_dir.mkdir(parents=True)
    metadata = library / "metadata.db"
    with sqlite3.connect(metadata) as connection:
        connection.executescript(
            "CREATE TABLE books(id INTEGER PRIMARY KEY, path TEXT, title TEXT, has_cover INTEGER, series_index REAL);"
            "INSERT INTO books VALUES(1, 'Author/Book (1)', 'Book', 0, 1);"
            "CREATE TABLE authors(id INTEGER, name TEXT);"
            "CREATE TABLE books_authors_link(id INTEGER, book INTEGER, author INTEGER);"
            "CREATE TABLE series(id INTEGER, name TEXT);"
            "CREATE TABLE books_series_link(book INTEGER, series INTEGER);"
        )
    settings = SimpleNamespace(auto_enabled=True, default_preset="classic")
    monkeypatch.setattr(cover_generator, "settings_from_app_db", lambda _path: settings)
    monkeypatch.setattr(cover_generator, "resolve_design", lambda *_args, **_kwargs: object())
    renders = []
    def render(*_args, **_kwargs):
        renders.append(True)
        return SimpleNamespace(data=b"generated fixture cover", renderer="fixture")
    monkeypatch.setattr(cover_generator, "render", render)
    if request.param == "enforcer":
        instance = module.Enforcer.__new__(module.Enforcer)
        instance.calibre_library = str(library)
        instance.split_library = None
        instance.args = None
        instance.supported_formats = []
        instance.supported_formats_label = lambda: "fixture"
        argument = str(book_dir)
    else:
        instance = module.NewBookProcessor.__new__(module.NewBookProcessor)
        instance.library_dir = str(library)
        instance.metadata_db = str(metadata)
        monkeypatch.setattr(module, "metadata_db_write_lock", target.operation)
        argument = 1
    yield SimpleNamespace(kind=request.param, module=module, instance=instance,
        call=lambda: instance.generate_missing_cover_if_enabled(argument),
        target=target, metadata=metadata, cover=book_dir / "cover.jpg", settings=settings,
        generator=cover_generator, renders=renders, config_dir=config_dir, scripts=scripts)


def flag(state):
    with sqlite3.connect(state.metadata) as connection:
        return connection.execute("SELECT has_cover FROM books WHERE id=1").fetchone()[0]


def test_actual_cover_generation_owns_maintenance_and_cross_process_writer_gate(automatic_cover, monkeypatch):
    state = automatic_cover
    real_connect = sqlite3.connect
    reads = []
    def connect(database, *args, **kwargs):
        if os.fspath(database) == str(state.metadata):
            maintenance = state.target.ownership.busy(state.config_dir, "maintenance")
            try:
                with state.target.operation(timeout=0):
                    writer_blocked = False
            except TimeoutError:
                writer_blocked = True
            reads.append((maintenance, writer_blocked))
        return real_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    child_results = []
    def render(*_args, **_kwargs):
        code = """import json,sys
sys.path.insert(0, sys.argv[1])
from calibre_library_target import operation, ownership
try:
    with operation(timeout=0.02):
        blocked=False
except TimeoutError:
    blocked=True
print(json.dumps([ownership.busy(sys.argv[2], 'maintenance'), blocked]))
"""
        result = subprocess.run([sys.executable, "-c", code, str(state.scripts), str(state.config_dir)],
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        child_results.append(json.loads(result.stdout))
        return SimpleNamespace(data=b"generated fixture cover", renderer="fixture")
    monkeypatch.setattr(state.generator, "render", render)
    if state.kind == "enforcer":
        # The production --log path enters enforce_cover with no outer gate.
        # No ebook format is present, so optional embedding launches nothing.
        state.instance.enforce_cover(str(state.cover.parent))
    else:
        assert state.call()
    assert reads and all(held == (True, True) for held in reads)
    assert child_results == [[True, True]]
    assert state.cover.read_bytes() == b"generated fixture cover" and flag(state) == 1
    assert not state.target.ownership.busy(state.config_dir, "maintenance")
    with state.target.operation(timeout=0):
        pass


def test_flag_failure_then_retry_recovers_without_overwriting_cover(automatic_cover):
    state = automatic_cover
    with sqlite3.connect(state.metadata) as connection:
        connection.executescript("CREATE TRIGGER fail_cover BEFORE UPDATE OF has_cover ON books "
            "BEGIN SELECT RAISE(ABORT, 'fixture flag failure'); END;")
    assert not state.call()
    assert state.cover.read_bytes() == b"generated fixture cover" and flag(state) == 0
    with sqlite3.connect(state.metadata) as connection:
        connection.execute("DROP TRIGGER fail_cover")
    assert state.call()
    assert flag(state) == 1
    assert state.cover.read_bytes() == b"generated fixture cover" and len(state.renders) == 1
    assert not state.target.ownership.busy(state.config_dir, "maintenance")


def test_default_disabled_does_not_probe_library_or_take_holds(automatic_cover, monkeypatch):
    state = automatic_cover
    state.settings.auto_enabled = False
    real_connect, real_exists = sqlite3.connect, os.path.exists
    touched = []
    def connect(database, *args, **kwargs):
        if os.fspath(database) == str(state.metadata):
            touched.append("database")
        return real_connect(database, *args, **kwargs)
    def exists(path):
        if os.fspath(path) == str(state.cover):
            touched.append("cover")
        return real_exists(path)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(os.path, "exists", exists)
    assert not state.call()
    assert touched == [] and state.renders == []
    assert not (state.config_dir / ".cwa-content-server-maintenance.lock").exists()
    assert not (state.config_dir / ".cwa-metadata-write.lock").exists()


def test_existing_cover_and_flag_are_never_overwritten(automatic_cover):
    state = automatic_cover
    state.cover.write_bytes(b"reader cover")
    with sqlite3.connect(state.metadata) as connection:
        connection.execute("UPDATE books SET has_cover=1 WHERE id=1")
    assert not state.call()
    assert state.cover.read_bytes() == b"reader cover" and state.renders == []
