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


def test_flag_failure_removes_owned_file_then_later_pass_generates_again(automatic_cover):
    state = automatic_cover
    with sqlite3.connect(state.metadata) as connection:
        connection.executescript("CREATE TRIGGER fail_cover BEFORE UPDATE OF has_cover ON books "
            "BEGIN SELECT RAISE(ABORT, 'fixture flag failure'); END;")
    assert not state.call()
    assert not state.cover.exists() and flag(state) == 0
    with sqlite3.connect(state.metadata) as connection:
        connection.execute("DROP TRIGGER fail_cover")
    assert state.call()
    assert flag(state) == 1
    assert state.cover.read_bytes() == b"generated fixture cover" and len(state.renders) == 2
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
    assert not (state.config_dir / ".cwa-content-server-maintenance.lock").exists()


@pytest.mark.parametrize("cover_bytes", [b"", b"not an image"])
def test_unproven_existing_file_is_not_blessed_or_drained(automatic_cover, cover_bytes):
    state = automatic_cover
    state.cover.write_bytes(cover_bytes)
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == cover_bytes and state.renders == []
    assert not (state.config_dir / ".cwa-content-server-maintenance.lock").exists()


def test_already_flagged_book_without_file_does_not_take_maintenance(automatic_cover):
    state = automatic_cover
    with sqlite3.connect(state.metadata) as connection:
        connection.execute("UPDATE books SET has_cover=1 WHERE id=1")
    assert not state.call()
    assert state.renders == []
    assert not (state.config_dir / ".cwa-content-server-maintenance.lock").exists()


def test_preflight_is_rechecked_after_maintenance_handoff(automatic_cover, monkeypatch):
    from contextlib import contextmanager
    state = automatic_cover
    real_offline = state.module.offline_library_access
    @contextmanager
    def offline():
        # A writer finishes after the readonly preflight but before admission.
        state.cover.write_bytes(b"concurrent cover")
        with real_offline():
            yield
    monkeypatch.setattr(state.module, "offline_library_access", offline)
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == b"concurrent cover"
    assert state.renders == []


def test_uncertain_flag_state_preserves_generated_file(automatic_cover, monkeypatch):
    state = automatic_cover
    real_connect = sqlite3.connect
    with real_connect(state.metadata) as connection:
        connection.executescript("CREATE TRIGGER fail_cover BEFORE UPDATE OF has_cover ON books "
            "BEGIN SELECT RAISE(ABORT, 'fixture flag failure'); END;")
    def connect(database, *args, **kwargs):
        if kwargs.get("uri") and state.cover.exists():
            raise sqlite3.OperationalError("fixture rollback read unavailable")
        return real_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == b"generated fixture cover"


@pytest.mark.parametrize("mutation", ["bytes", "inode"])
def test_failed_flag_commit_preserves_cover_replaced_by_another_writer(automatic_cover, monkeypatch, mutation):
    state = automatic_cover
    real_connect = sqlite3.connect
    def change_cover():
        if mutation == "bytes":
            state.cover.write_bytes(b"external replacement")
        else:
            replacement = state.cover.with_name("replacement.jpg")
            replacement.write_bytes(b"external replacement")
            os.replace(replacement, state.cover)
        return 1
    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.create_function("change_cover", 0, change_cover)
        return connection
    monkeypatch.setattr(sqlite3, "connect", connect)
    with sqlite3.connect(state.metadata) as connection:
        connection.executescript("CREATE TRIGGER fail_cover BEFORE UPDATE OF has_cover ON books "
            "BEGIN SELECT change_cover(); SELECT RAISE(ABORT, 'fixture flag failure'); END;")
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == b"external replacement"
    with sqlite3.connect(state.metadata) as connection:
        connection.execute("DROP TRIGGER fail_cover")
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == b"external replacement"


def test_actual_generic_cover_edit_is_preserved_by_later_enforcement(automatic_cover, monkeypatch, tmp_path):
    import flask
    from flask_babel import Babel
    from cps import editbooks
    from tests.unit.test_f50a5cb_cover_write_staging import _book, _editor
    state = automatic_cover
    assert state.call()
    book = _book()
    book.id, book.path, book.has_cover = 1, "Author/Book (1)", 1
    def commit():
        with sqlite3.connect(state.metadata) as connection:
            connection.execute("UPDATE books SET has_cover=? WHERE id=1", (book.has_cover,))
    session = SimpleNamespace(merge=lambda _book: None, commit=commit, rollback=lambda: None)
    monkeypatch.setattr(editbooks, "current_user", _editor())
    monkeypatch.setattr(editbooks.calibre_db, "get_filtered_book", lambda *_args, **_kwargs: book)
    monkeypatch.setattr(editbooks.calibre_db, "session", session)
    monkeypatch.setattr(editbooks, "metadata_db_write_lock", state.target.operation)
    monkeypatch.setattr(editbooks, "upload_cover", lambda *_args: None)
    monkeypatch.setattr(editbooks, "_book_cover_is_locked", lambda _id: False)
    monkeypatch.setattr(editbooks, "handle_author_on_edit", lambda *_args: (["Author"], False))
    for name in ("edit_book_ratings", "edit_book_series_index", "edit_book_comments", "edit_book_tags",
                 "edit_book_series", "edit_book_publisher", "edit_book_languages", "edit_all_cc_data"):
        monkeypatch.setattr(editbooks, name, lambda *_args, **_kwargs: False)
    monkeypatch.setattr(editbooks, "identifier_list", lambda *_args: [])
    monkeypatch.setattr(editbooks, "modify_identifiers", lambda *_args: (False, False))
    monkeypatch.setattr(editbooks.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(editbooks.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(editbooks.constants, "CWA_METADATA_CHANGE_LOGS_DIR", str(tmp_path / "change-logs"))
    app = flask.Flask(__name__)
    app.secret_key = "fixture"
    Babel(app)
    app.add_url_rule("/book/<int:book_id>", endpoint="web.show_book", view_func=lambda book_id: str(book_id))
    with app.test_request_context(method="POST", data={"authors": "Author", "detail_view": "1",
            "cover_url": "/static/generic_cover.svg"}):
        response = editbooks.do_edit_book(1)
    assert response.status_code == 302
    assert list((tmp_path / "change-logs").glob("*.json"))
    assert flag(state) == 0 and state.cover.read_bytes() == b"generated fixture cover"
    assert not state.call()
    assert flag(state) == 0 and state.cover.read_bytes() == b"generated fixture cover"
