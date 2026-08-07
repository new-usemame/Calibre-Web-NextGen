# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural tests for the library/DB locator in ``scripts/auto_library.py`` (#1022).

``check_for_app_db`` and ``check_for_existing_library`` used to unconditionally
``os.walk`` the entire ``/config`` and ``/calibre-library`` trees on every boot.
On a large library the library walk recursed into every per-book folder and was
measured spending ~5 minutes before the service reported "Existing library
found" (#1022, from #868). The fix:

* **app.db** — try the canonical ``/config/app.db`` first; only fall back to a
  full ``os.walk`` of ``/config`` when it's missing.
* **metadata.db** — walk top-down but stop descending into a directory once it
  yields a ``metadata.db`` (a Calibre library keeps its DB at the root and never
  nests another library inside its book folders), so the deep book-folder
  recursion is skipped while every candidate library *root* is still compared for
  the "largest wins" selection.

What's pinned here:

* **The perf win** — the metadata walk prunes book folders (never descends past a
  found ``metadata.db``); the app.db fast path skips the ``/config`` walk entirely.
* **The crash the naive first cut (community PR #1075) introduced** — it set
  ``self.app_db = None`` and only reassigned it on the found-at-default branch, so
  a *fresh* container left ``app_db`` ``None`` and the later
  ``sqlite3.connect(self.app_db)`` failed into ``sys.exit(1)`` — a boot crash-loop
  on every new deployment. The fresh-install tests drive the real one-shot flow.
* **The selection contract** — a ``metadata.db`` at the library root is
  authoritative (a nested library below it is not scanned); "largest wins" still
  holds across sibling library roots when there's no root DB. Directories named
  ``metadata.db`` are ignored (files only).
* **Only a real library counts (#1428)** — a candidate is validated as an
  actual Calibre database before it can claim the mount point. Matching on the
  name alone let a stale 0-byte ``metadata.db`` at the library root prune away
  the real library nested below it; the container then crash-looped forever on
  ``no such table: custom_columns``. Invalid candidates are named, skipped, and
  walked past; when *nothing* usable is found the boot stops with an actionable
  error instead of seeding an empty library over the user's files.

The library ships ``empty_library/app.db`` (a real SQLite file with the
``settings`` table) and ``empty_library/metadata.db``; the tests use those as the
seed copies, so the flow runs real SQL rather than a mock.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_LIB = REPO_ROOT / "scripts" / "auto_library.py"
EMPTY_APPDB = REPO_ROOT / "empty_library" / "app.db"
EMPTY_METADB = REPO_ROOT / "empty_library" / "metadata.db"


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_library_under_test", AUTO_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """An ``AutoLibrary`` pointed at a throwaway config/library tree.

    Returns ``(module, auto_library_instance, config_dir, library_dir)``. chown
    is neutralised (the real runtime user ``abc`` doesn't exist on a test box).
    """
    mod = _load_module()
    cfg = tmp_path / "config"
    library = tmp_path / "calibre-library"
    cfg.mkdir()
    library.mkdir()

    al = mod.AutoLibrary()
    al.config_dir = str(cfg)
    al.library_dir = str(library)
    al.DEFAULT_APPDB_PATH = str(cfg / "app.db")
    # NB: deliberately do NOT pre-set al.app_db here — the whole point of the
    # crash tests is that check_for_app_db() itself must establish a usable
    # (non-None) handle in every branch. Pre-seeding it would mask the #1075
    # regression.
    al.empty_appdb = str(EMPTY_APPDB)
    al.empty_metadb = str(EMPTY_METADB)
    al.dirs_path = str(tmp_path / "dirs.json")
    Path(al.dirs_path).write_text('{"calibre_library_dir": "/calibre-library"}')

    # chown -> no-op; the test user cannot chown to abc:abc.
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    return mod, al, cfg, library


def _walk_must_not_run(*_a, **_k):
    raise AssertionError("os.walk was called — the app.db default-location fast path did not short-circuit")


def _make_calibre_db(path, pad_rows=0):
    """Write a small but *genuine* Calibre database at ``path``.

    Carries the tables the locator validates against, so it is a real library as
    far as selection is concerned. ``pad_rows`` inflates the file when a test
    needs to control which of two valid candidates is larger — size has to be
    steered with real content now that junk files no longer count as libraries.
    """
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, datatype TEXT)")
        for i in range(pad_rows):
            con.execute("INSERT INTO books (title) VALUES (?)", ("x" * 512,))
        con.commit()
    finally:
        con.close()
    return path


# --------------------------------------------------------------------------- #
# The crash the naive fast path introduced (fresh install).
# --------------------------------------------------------------------------- #

def test_fresh_install_keeps_app_db_non_none(lib):
    """On a fresh container, check_for_app_db must leave a usable app.db handle.

    Regression guard: the None-initialised first cut left self.app_db == None
    after copying the empty db, which crashed the later sqlite3.connect.
    """
    _mod, al, _cfg, _library = lib
    assert not os.path.exists(al.DEFAULT_APPDB_PATH)

    al.check_for_app_db()

    assert al.app_db is not None
    assert al.app_db == al.DEFAULT_APPDB_PATH
    assert os.path.isfile(al.app_db), "empty app.db should have been copied to the default location"


def test_fresh_install_full_flow_via_set_library_location(lib):
    """The real one-shot sequence (copy app.db -> new library -> persist location
    to BOTH dirs.json and app.db) completes without SystemExit — this is the boot
    crash #1075 shipped. Driven through the public set_library_location()."""
    _mod, al, _cfg, library = lib

    al.check_for_app_db()
    assert al.check_for_existing_library() is False  # nothing anywhere, empty tree
    al.make_new_library()
    al.set_library_location()  # update_dirs_json() + update_calibre_web_db()

    dirs = json.loads(Path(al.dirs_path).read_text())
    assert dirs["calibre_library_dir"] == str(library)

    con = sqlite3.connect(al.app_db)
    try:
        value = con.execute("SELECT config_calibre_dir FROM settings").fetchone()[0]
    finally:
        con.close()
    assert value == al.lib_path == str(library)


# --------------------------------------------------------------------------- #
# app.db perf win: default present => no /config walk.
# --------------------------------------------------------------------------- #

def test_app_db_at_default_skips_walk(lib, monkeypatch):
    _mod, al, _cfg, _library = lib
    shutil.copyfile(EMPTY_APPDB, al.DEFAULT_APPDB_PATH)
    monkeypatch.setattr(os, "walk", _walk_must_not_run)

    al.check_for_app_db()  # must not walk /config

    assert al.app_db == al.DEFAULT_APPDB_PATH


# --------------------------------------------------------------------------- #
# metadata.db: root-authoritative + largest-wins across siblings + prune.
# --------------------------------------------------------------------------- #

def test_root_metadb_is_authoritative_over_larger_subfolder(lib):
    """Deliberate contract: a metadata.db at the library ROOT wins, even when a
    larger one exists in a sub-folder below it (the root is the mount point)."""
    _mod, al, _cfg, library = lib
    _make_calibre_db(library / "metadata.db")  # small root DB, but a real one
    sub = library / "NestedLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")  # larger, but nested below root
    assert os.path.getsize(sub / "metadata.db") > os.path.getsize(library / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(library / "metadata.db")
    assert al.lib_path == str(library)


def test_metadb_walk_prunes_book_folders(lib, monkeypatch):
    """The walk must not descend into a library's per-book folders once its
    metadata.db is found — that deep recursion is the #1022 cost."""
    _mod, al, _cfg, library = lib
    shutil.copyfile(EMPTY_METADB, library / "metadata.db")
    book = library / "Some Author" / "Some Book (1)"
    book.mkdir(parents=True)
    (book / "book.epub").write_text("x")

    visited = []
    real_walk = os.walk

    def spy_walk(top, *a, **k):
        for dp, dn, fn in real_walk(top, *a, **k):
            visited.append(dp)
            yield dp, dn, fn

    monkeypatch.setattr(os, "walk", spy_walk)

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(library / "metadata.db")
    assert str(library / "Some Author") not in visited, "book folders must be pruned, not walked"
    assert str(book) not in visited


def test_metadb_directory_at_default_ignored(lib):
    """A directory named metadata.db is not a database — it must be ignored."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").mkdir()

    assert al.check_for_existing_library() is False


def test_metadb_single_subfolder_found(lib):
    """No metadata.db at the root, one in a sub-folder: found and mounted."""
    _mod, al, _cfg, library = lib
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")
    assert not os.path.exists(library / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")
    assert al.lib_path == str(sub)


def test_metadb_multiple_subfolders_largest_wins(lib):
    """No root DB, two sibling library roots: the larger metadata.db wins
    (the historical selection, preserved by the pruned walk)."""
    _mod, al, _cfg, library = lib
    small = library / "LibA"
    big = library / "LibB"
    small.mkdir()
    big.mkdir()
    _make_calibre_db(small / "metadata.db")
    shutil.copyfile(EMPTY_METADB, big / "metadata.db")
    assert os.path.getsize(big / "metadata.db") > os.path.getsize(small / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(big / "metadata.db")


def test_no_library_anywhere_returns_false(lib):
    _mod, al, _cfg, _library = lib
    assert al.check_for_existing_library() is False


# --------------------------------------------------------------------------- #
# #1428 — a metadata.db-shaped file that isn't a Calibre database must never
# claim the mount point and prune away the real library below it.
#
# Reported symptom: every release from v4.1.20 refused to start, looping on
#     ERROR {cps.db} (sqlite3.OperationalError) no such table: custom_columns
#     [SQL: SELECT id, datatype FROM custom_columns]
# while v4.1.19 was fine. The name-only match accepted a stale/0-byte
# metadata.db at the library root, pruned the walk there, and wrote that
# directory into config_calibre_dir — so Calibre-Web opened a database with no
# Calibre tables in it, on every boot, forever.
# --------------------------------------------------------------------------- #

def test_is_calibre_database_accepts_a_real_library(tmp_path):
    mod = _load_module()
    assert mod.is_calibre_database(str(EMPTY_METADB)) is True
    assert mod.is_calibre_database(str(_make_calibre_db(tmp_path / "metadata.db"))) is True


@pytest.mark.parametrize(
    "name, payload",
    [
        ("empty", b""),                                  # 0-byte placeholder
        ("garbage", b"not a database at all, just text"),  # unrelated file
        ("truncated", b"SQLite format 3\x00truncated"),   # looks like sqlite, isn't
    ],
)
def test_is_calibre_database_rejects_non_libraries(tmp_path, name, payload):
    mod = _load_module()
    path = tmp_path / f"{name}-metadata.db"
    path.write_bytes(payload)
    assert mod.is_calibre_database(str(path)) is False


def test_is_calibre_database_rejects_sqlite_without_calibre_tables(tmp_path):
    """A perfectly valid SQLite file is still not a Calibre library."""
    mod = _load_module()
    path = tmp_path / "metadata.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    assert mod.is_calibre_database(str(path)) is False


def test_is_calibre_database_does_not_modify_the_candidate(tmp_path):
    """Validation opens read-only — checking a file must never write to it."""
    mod = _load_module()
    path = tmp_path / "metadata.db"
    path.write_bytes(b"")
    before = (path.stat().st_size, path.stat().st_mtime_ns)

    assert mod.is_calibre_database(str(path)) is False

    assert (path.stat().st_size, path.stat().st_mtime_ns) == before
    assert not (tmp_path / "metadata.db-wal").exists()
    assert not (tmp_path / "metadata.db-journal").exists()


def test_empty_root_metadb_does_not_shadow_real_nested_library(lib):
    """#1428, exactly as reported: a 0-byte metadata.db sits at the mount root
    and the real library is one folder down. v4.1.19 picked the real one."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")  # stale placeholder at the root
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")  # the actual library

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")
    assert al.lib_path == str(sub)


def test_non_database_root_metadb_does_not_shadow_real_nested_library(lib):
    """Same shape, but the root file is unrelated content rather than 0 bytes."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"leftover junk from a failed restore")
    sub = library / "Books"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")


def test_selected_library_can_answer_the_startup_query(lib):
    """Symptom-level pin: whatever gets mounted must satisfy the query whose
    failure crash-looped the container — SELECT id, datatype FROM custom_columns."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")

    assert al.check_for_existing_library() is True

    con = sqlite3.connect(al.metadb_path)
    try:
        con.execute("SELECT id, datatype FROM custom_columns").fetchall()
    finally:
        con.close()


def test_invalid_candidate_is_recorded_and_walk_continues_below_it(lib):
    """The rejected file is named for the user rather than silently ignored."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")

    al.check_for_existing_library()

    assert al.rejected_dbs == [str(library / "metadata.db")]


def test_only_invalid_candidates_exits_without_touching_them(lib):
    """No usable library, but metadata.db-shaped files are present: stop with a
    clear error. Seeding an empty library here would copy over the top of files
    the user may still be able to recover."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")
    before = (library / "metadata.db").stat().st_size

    with pytest.raises(SystemExit) as exc:
        al.check_for_existing_library()

    assert exc.value.code == 1
    assert (library / "metadata.db").stat().st_size == before, "the user's file was modified"
    assert al.metadb_path is None


def test_only_invalid_candidates_names_them_in_the_error(lib, capsys):
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")

    with pytest.raises(SystemExit):
        al.check_for_existing_library()

    out = capsys.readouterr().out
    assert str(library / "metadata.db") in out
    assert "not a Calibre database" in out or "readable Calibre database" in out


def test_backup_sibling_does_not_outrank_the_real_metadata_db(lib):
    """A larger metadata.db.bak next to the real metadata.db is a copy, not the
    library — only the exactly-named file is ever a candidate."""
    _mod, al, _cfg, library = lib
    _make_calibre_db(library / "metadata.db")
    _make_calibre_db(library / "metadata.db.bak", pad_rows=200)
    assert os.path.getsize(library / "metadata.db.bak") > os.path.getsize(library / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(library / "metadata.db")


def test_a_backup_alone_is_not_a_library(lib):
    """Only ``metadata.db`` counts, because Calibre-Web opens exactly
    ``<config_calibre_dir>/metadata.db`` (cps/db.py). Mounting a directory on the
    strength of a metadata.db.bak configures a library whose real database does
    not exist — the same "validated one file, opened another" mistake as #1428."""
    _mod, al, _cfg, library = lib
    sub = library / "MyLibrary"
    sub.mkdir()
    _make_calibre_db(sub / "metadata.db.bak")

    assert al.check_for_existing_library() is False
    assert al.rejected_dbs == []


def test_a_backup_does_not_shadow_a_real_library_below_it(lib):
    _mod, al, _cfg, library = lib
    _make_calibre_db(library / "metadata.db.old", pad_rows=200)
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")


def test_sqlite_sidecars_are_never_candidates(lib):
    """metadata.db-wal / -shm / -journal sit next to a real library."""
    _mod, al, _cfg, library = lib
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")
    for suffix in ("-wal", "-shm", "-journal"):
        (library / f"metadata.db{suffix}").write_bytes(b"x" * 32)

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")
    assert al.rejected_dbs == []


def test_invalid_root_candidate_still_prunes_the_real_librarys_book_folders(lib, monkeypatch):
    """Skipping past a bad root file must not cost the #1022 perf win below it:
    once the real library is found, its per-book folders are still pruned."""
    _mod, al, _cfg, library = lib
    (library / "metadata.db").write_bytes(b"")
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")
    book = sub / "Some Author" / "Some Book (1)"
    book.mkdir(parents=True)
    (book / "book.epub").write_text("x")

    visited = []
    real_walk = os.walk

    def spy_walk(top, *a, **k):
        for dp, dn, fn in real_walk(top, *a, **k):
            visited.append(dp)
            yield dp, dn, fn

    monkeypatch.setattr(os, "walk", spy_walk)

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")
    assert str(sub / "Some Author") not in visited, "book folders must still be pruned"
    assert str(book) not in visited


# --------------------------------------------------------------------------- #
# Validator edge cases found by the cross-family review of #1429. Each of these
# is a way the check could REJECT a real library — a worse outcome than the
# mis-selection it exists to prevent, so each gets a pin.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["plain.db", "has space.db", "weird?query.db", "hash#frag.db", "pct%20.db"])
def test_is_calibre_database_handles_awkward_path_characters(tmp_path, name):
    """'?' and '#' are legal in a filename and start a URI's query and fragment.
    Interpolating the path into the URI truncated it, so a real library whose
    folder contained either character was rejected on its name alone."""
    mod = _load_module()
    assert mod.is_calibre_database(str(_make_calibre_db(tmp_path / name))) is True


def test_awkward_path_library_is_still_selected(lib):
    """End to end through the locator, not just the validator."""
    _mod, al, _cfg, library = lib
    sub = library / "Books #2 (why?)"
    sub.mkdir()
    _make_calibre_db(sub / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")


def test_is_calibre_database_accepts_an_unreadable_file(tmp_path):
    """Permission denied means "could not tell", not "not a library". The boot
    runs under a different uid than the app on some mounts (NFS root_squash),
    and refusing on that basis would strand a library the app could read."""
    mod = _load_module()
    path = _make_calibre_db(tmp_path / "metadata.db")
    os.chmod(path, 0o000)
    try:
        if os.access(str(path), os.R_OK):
            pytest.skip("running as root; the file stays readable")
        assert mod.is_calibre_database(str(path)) is True
    finally:
        os.chmod(path, 0o644)


def test_is_calibre_database_accepts_a_locked_database(tmp_path):
    """A busy database is self-evidently a real one. Rejecting it would skip the
    user's actual library whenever something else held a lock at boot."""
    mod = _load_module()
    path = _make_calibre_db(tmp_path / "metadata.db")
    holder = sqlite3.connect(str(path), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert mod.is_calibre_database(str(path)) is True
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_locked_but_empty_file_is_still_rejected(tmp_path):
    """The busy allowance must not readmit #1428: a placeholder is never locked,
    and an unlocked non-database still fails."""
    mod = _load_module()
    path = tmp_path / "metadata.db"
    path.write_bytes(b"")
    assert mod.is_calibre_database(str(path)) is False


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_is_calibre_database_accepts_wal_and_rollback_journals(tmp_path, journal_mode):
    """WAL needs a -shm to read; opening read-only must not fail because of it."""
    mod = _load_module()
    path = tmp_path / "metadata.db"
    con = sqlite3.connect(str(path))
    con.execute(f"PRAGMA journal_mode={journal_mode}")
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, datatype TEXT)")
    con.commit()
    con.close()

    assert mod.is_calibre_database(str(path)) is True


def test_is_calibre_database_accepts_a_hot_wal_database(tmp_path):
    """Sidecars present and a writer still attached — the uncleanly-stopped case."""
    mod = _load_module()
    path = tmp_path / "metadata.db"
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, datatype TEXT)")
    con.commit()
    con.execute("INSERT INTO books DEFAULT VALUES")
    con.commit()
    try:
        assert (tmp_path / "metadata.db-wal").exists()
        assert mod.is_calibre_database(str(path)) is True
    finally:
        con.close()


def test_is_calibre_database_follows_a_symlinked_library(tmp_path):
    mod = _load_module()
    target = _make_calibre_db(tmp_path / "real.db")
    link = tmp_path / "metadata.db"
    os.symlink(target, link)
    assert mod.is_calibre_database(str(link)) is True


def test_is_calibre_database_does_not_block_on_a_fifo(tmp_path):
    """A FIFO named metadata.db would hang the open forever and wedge the boot.
    Regular-files-only settles it before anything is opened."""
    mod = _load_module()
    fifo = tmp_path / "metadata.db"
    os.mkfifo(fifo)
    assert mod.is_calibre_database(str(fifo)) is False


def test_is_calibre_database_rejects_a_directory(tmp_path):
    mod = _load_module()
    (tmp_path / "metadata.db").mkdir()
    assert mod.is_calibre_database(str(tmp_path / "metadata.db")) is False
