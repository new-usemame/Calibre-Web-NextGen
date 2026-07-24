# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural tests for the default-location fast path in ``scripts/auto_library.py`` (#1022).

``check_for_app_db`` and ``check_for_existing_library`` used to unconditionally
``os.walk`` the entire ``/config`` and ``/calibre-library`` trees on every boot.
On a large library the library walk was measured spending ~5 minutes before the
service reported "Existing library found" (#1022, reported from #868). The fix
tries the canonical default locations first and only falls back to the walk when
they're missing.

Two things are pinned here:

* **The perf win.** When ``app.db`` / ``metadata.db`` are already at their
  default locations, ``os.walk`` must NOT be called. The stubbed ``os.walk``
  raises, so any regression that reintroduces the unconditional walk fails.

* **The crash the naive fast path introduced.** The first cut of this change
  (community PR #1075) initialised ``self.app_db = None`` and only reassigned it
  in the "found at default" branch. On a *fresh* container — no ``app.db``
  anywhere — the copy branch left ``self.app_db`` as ``None``, and the later
  ``sqlite3.connect(self.app_db)`` in ``update_calibre_web_db`` blew up into
  ``sys.exit(1)`` = a boot crash-loop on every brand-new deployment (the exact
  path the reporter's existing-library case never exercises). These tests drive
  the real fresh-install flow end to end and assert it completes.

The library ships ``empty_library/app.db`` (a real SQLite file with the
``settings`` table) and ``empty_library/metadata.db``; the tests use those as the
seed copies, so ``update_calibre_web_db`` runs real SQL rather than a mock.
"""

from __future__ import annotations

import importlib.util
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
    al.DEFAULT_METADB_PATH = str(library / "metadata.db")
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
    raise AssertionError("os.walk was called — the default-location fast path did not short-circuit")


# --------------------------------------------------------------------------- #
# The crash the naive fast path introduced (fresh install).
# --------------------------------------------------------------------------- #

def test_fresh_install_keeps_app_db_non_none(lib):
    """On a fresh container, check_for_app_db must leave a usable app.db handle.

    Regression guard: the None-initialised fast path left self.app_db == None
    after copying the empty db, which crashed the later sqlite3.connect.
    """
    _mod, al, _cfg, _library = lib
    assert not os.path.exists(al.DEFAULT_APPDB_PATH)

    al.check_for_app_db()

    assert al.app_db is not None
    assert al.app_db == al.DEFAULT_APPDB_PATH
    assert os.path.exists(al.app_db), "empty app.db should have been copied to the default location"


def test_fresh_install_full_flow_does_not_crash(lib):
    """The end-to-end fresh-install path (copy app.db -> new library -> write
    settings) completes without SystemExit — this is the boot crash #1075 shipped."""
    _mod, al, _cfg, library = lib

    al.check_for_app_db()
    assert al.check_for_existing_library() is False  # nothing at default, empty tree
    al.make_new_library()
    assert al.metadb_path == al.DEFAULT_METADB_PATH

    # The regression detonated here: update_calibre_web_db -> sqlite3.connect(None)
    # -> except -> sys.exit(1). Assert it now runs the UPDATE instead.
    al.update_calibre_web_db()

    con = sqlite3.connect(al.app_db)
    try:
        value = con.execute("SELECT config_calibre_dir FROM settings").fetchone()[0]
    finally:
        con.close()
    assert value == al.lib_path == str(library)


def test_app_db_subfolder_only_keeps_canonical_handle(lib):
    """If app.db exists only in a sub-folder (not the default), the handle still
    resolves to the canonical /config/app.db — matching pre-#1075 behaviour, and
    never None."""
    _mod, al, cfg, _library = lib
    sub = cfg / "backup"
    sub.mkdir()
    shutil.copyfile(EMPTY_APPDB, sub / "app.db")
    assert not os.path.exists(al.DEFAULT_APPDB_PATH)

    al.check_for_app_db()

    assert al.app_db is not None
    assert al.app_db == al.DEFAULT_APPDB_PATH


# --------------------------------------------------------------------------- #
# The perf win: default present => no walk.
# --------------------------------------------------------------------------- #

def test_app_db_at_default_skips_walk(lib, monkeypatch):
    _mod, al, _cfg, _library = lib
    shutil.copyfile(EMPTY_APPDB, al.DEFAULT_APPDB_PATH)
    monkeypatch.setattr(os, "walk", _walk_must_not_run)

    al.check_for_app_db()  # must not walk

    assert al.app_db == al.DEFAULT_APPDB_PATH


def test_metadb_at_default_skips_walk(lib, monkeypatch):
    _mod, al, _cfg, library = lib
    shutil.copyfile(EMPTY_METADB, al.DEFAULT_METADB_PATH)
    monkeypatch.setattr(os, "walk", _walk_must_not_run)

    assert al.check_for_existing_library() is True
    assert al.metadb_path == al.DEFAULT_METADB_PATH
    assert al.lib_path == str(library)


# --------------------------------------------------------------------------- #
# The fallback the fast path must not break.
# --------------------------------------------------------------------------- #

def test_metadb_subfolder_still_found_via_walk(lib):
    """No metadata.db at the default location, one in a sub-folder: the walk
    fallback must still find and mount it."""
    _mod, al, _cfg, library = lib
    sub = library / "MyLibrary"
    sub.mkdir()
    shutil.copyfile(EMPTY_METADB, sub / "metadata.db")
    assert not os.path.exists(al.DEFAULT_METADB_PATH)

    assert al.check_for_existing_library() is True
    assert al.metadb_path == str(sub / "metadata.db")
    assert al.lib_path == str(sub)


def test_no_library_anywhere_returns_false(lib):
    _mod, al, _cfg, _library = lib
    assert al.check_for_existing_library() is False
