# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for #1755's deployment-dependent CWA_DB import."""

import ast
import importlib
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _resolved_path(entry):
    """Resolve a sys.path entry, including ``""`` for the current directory."""
    return Path(entry or os.getcwd()).resolve()


@pytest.mark.unit
def test_cwa_db_loader_repairs_a_sys_path_without_the_app_root(monkeypatch):
    """The old ``scripts.cwa_db`` form fails in the exact path layout we repair.

    ``tests/conftest.py`` adds both the app root and scripts directory, which
    would hide the production failure. Import the loader, then remove both
    entries and every relevant module cache so the control import has no
    ambient path or import-order help. The loader must put both canonical
    paths back exactly once and resolve the real CWA_DB class.
    """
    loader = importlib.import_module("cps.cwa_db_loader")

    monkeypatch.setattr(
        sys,
        "path",
        [
            entry
            for entry in sys.path
            if _resolved_path(entry) not in {REPO_ROOT.resolve(), SCRIPTS_DIR.resolve()}
        ],
    )
    for module_name in (
        "scripts.cwa_db",
        "scripts",
        "cwa_db",
        "app_paths",
        "library_paths",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    with pytest.raises(ModuleNotFoundError, match="No module named 'scripts'"):
        importlib.import_module("scripts.cwa_db")

    module = loader.load_cwa_db()

    assert module.CWA_DB.__name__ == "CWA_DB"
    assert Path(module.__file__).resolve() == SCRIPTS_DIR / "cwa_db.py"
    assert sys.path.count(str(REPO_ROOT)) == 1
    assert sys.path.count(str(SCRIPTS_DIR)) == 1


@pytest.mark.unit
def test_loader_aliases_scripts_name_after_top_level_import(monkeypatch):
    """Importing cwa_db first must not permit a second module execution."""
    loader = importlib.import_module("cps.cwa_db_loader")
    for module_name in ("cwa_db", "scripts.cwa_db", "scripts"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    first = importlib.import_module("cwa_db")
    loaded = loader.load_cwa_db()
    second = importlib.import_module("scripts.cwa_db")

    assert loaded is first is second
    assert first.CWA_DB is second.CWA_DB


@pytest.mark.unit
def test_loader_aliases_top_level_name_after_scripts_import(monkeypatch):
    """Importing scripts.cwa_db first must not permit a second execution."""
    loader = importlib.import_module("cps.cwa_db_loader")
    for module_name in ("cwa_db", "scripts.cwa_db", "scripts"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    first = importlib.import_module("scripts.cwa_db")
    loaded = loader.load_cwa_db()
    second = importlib.import_module("cwa_db")

    assert loaded is first is second
    assert first.CWA_DB is second.CWA_DB


@pytest.mark.unit
def test_cps_modules_use_only_the_canonical_cwa_db_loader():
    """Keep direct CWA DB imports from returning outside the loader."""
    cps_dir = REPO_ROOT / "cps"
    loader_path = cps_dir / "cwa_db_loader.py"
    forbidden_modules = {"cwa_db", "scripts.cwa_db"}
    offenders = []

    for source_path in sorted(cps_dir.rglob("*.py")):
        if source_path == loader_path:
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                offenders.append(f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        offenders.append(f"{source_path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], (
        "CWA DB must be imported through cps.cwa_db_loader; direct imports found at: "
        + ", ".join(offenders)
    )
