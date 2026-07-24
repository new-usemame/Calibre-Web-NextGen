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
    (library / "metadata.db").write_bytes(b"tiny")  # small root DB
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
    (small / "metadata.db").write_bytes(b"x" * 16)
    shutil.copyfile(EMPTY_METADB, big / "metadata.db")
    assert os.path.getsize(big / "metadata.db") > os.path.getsize(small / "metadata.db")

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(big / "metadata.db")


def test_no_library_anywhere_returns_false(lib):
    _mod, al, _cfg, _library = lib
    assert al.check_for_existing_library() is False
