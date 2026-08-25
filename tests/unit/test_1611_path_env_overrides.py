# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #1611 — per-key runtime path overrides."""

import importlib
import json
import logging
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
S6_DIR = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d"
sys.path.insert(0, str(SCRIPTS_DIR))

PATH_CASES = (
    ("ingest_folder", "CWA_INGEST_FOLDER", "/cwa-book-ingest"),
    ("calibre_library_dir", "CWA_CALIBRE_LIBRARY_DIR", "/calibre-library"),
    ("tmp_conversion_dir", "CWA_TMP_CONVERSION_DIR", "/config/.cwa_conversion_tmp"),
)
PATH_ENV_VARS = tuple(case[1] for case in PATH_CASES)


@pytest.fixture()
def resolvers(monkeypatch):
    """Return fresh scripts and cps resolvers with an isolated environment."""
    for name in ("CWA_DIRS_JSON", *PATH_ENV_VARS):
        monkeypatch.delenv(name, raising=False)

    app_paths = importlib.reload(importlib.import_module("app_paths"))
    from cps import constants

    getattr(constants, "_DIRS_JSON_LOGGED_KEYS", set()).clear()
    return app_paths, constants


def _write_dirs(path, values):
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def _answers(module):
    return {key: getattr(module, key)() for key, _env, _default in PATH_CASES}


@pytest.mark.parametrize("key,env_name,default", PATH_CASES)
def test_each_env_var_overrides_its_file_value_on_both_sides(
    resolvers, monkeypatch, tmp_path, key, env_name, default
):
    app_paths, constants = resolvers
    values = {case_key: f"/from-file/{case_key}" for case_key, _env, _default in PATH_CASES}
    dirs_file = _write_dirs(tmp_path / "dirs.json", values)
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))
    monkeypatch.setenv(env_name, f"  /from-env/{key}  ")

    assert getattr(app_paths, key)() == f"/from-env/{key}"
    assert getattr(constants, key)() == f"/from-env/{key}"

    for other_key, _other_env, _other_default in PATH_CASES:
        if other_key != key:
            assert getattr(app_paths, other_key)() == values[other_key]
            assert getattr(constants, other_key)() == values[other_key]


def test_env_unset_honours_hand_edited_dirs_json_on_both_sides(
    resolvers, monkeypatch, tmp_path
):
    app_paths, constants = resolvers
    values = {key: f"/edited/{key}" for key, _env, _default in PATH_CASES}
    dirs_file = _write_dirs(tmp_path / "dirs.json", values)
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))

    assert _answers(app_paths) == values
    assert _answers(constants) == values


def test_missing_file_yields_defaults_at_every_python_read_site(
    resolvers, monkeypatch, tmp_path
):
    _app_paths, constants = resolvers
    missing = tmp_path / "missing-dirs.json"
    monkeypatch.setenv("CWA_DIRS_JSON", str(missing))
    monkeypatch.setattr(constants, "DIRS_JSON", str(missing))

    from cps import cwa_functions, web
    import convert_library
    import cover_enforcer
    import ingest_processor
    import library_paths

    monkeypatch.setattr(cover_enforcer, "dirs_json", str(missing))

    assert web.cwa_get_library_location() == "/calibre-library"
    assert cwa_functions.get_ingest_dir() == "/cwa-book-ingest"
    assert cwa_functions.get_tmp_conversion_dir() == "/config/.cwa_conversion_tmp/"
    expected_dirs = (
        "/cwa-book-ingest/",
        "/calibre-library/",
        "/config/.cwa_conversion_tmp/",
    )
    assert convert_library.LibraryConverter.get_dirs(None, str(missing)) == expected_dirs
    assert ingest_processor.NewBookProcessor.get_dirs(None, str(missing)) == expected_dirs
    assert cover_enforcer.Book.get_calibre_library(None) == "/calibre-library"
    assert cover_enforcer.Enforcer.get_calibre_library(None) == "/calibre-library"
    assert library_paths.get_calibre_library_dir(str(missing)) == "/calibre-library"


@pytest.mark.parametrize(
    "contents",
    (
        "not JSON",
        json.dumps(["not", "an", "object"]),
        json.dumps({"ingest_folder": None, "calibre_library_dir": None,
                    "tmp_conversion_dir": None}),
        json.dumps({"ingest_folder": "", "calibre_library_dir": "   ",
                    "tmp_conversion_dir": ""}),
    ),
    ids=("not-json", "json-list", "null-values", "blank-values"),
)
def test_corrupt_or_unusable_file_values_yield_defaults_on_both_sides(
    resolvers, monkeypatch, tmp_path, contents
):
    app_paths, constants = resolvers
    dirs_file = tmp_path / "dirs.json"
    dirs_file.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))

    expected = {key: default for key, _env, default in PATH_CASES}
    assert _answers(app_paths) == expected
    assert _answers(constants) == expected


def test_cps_and_scripts_resolvers_agree_for_same_env_and_file(
    resolvers, monkeypatch, tmp_path
):
    app_paths, constants = resolvers
    dirs_file = _write_dirs(
        tmp_path / "dirs.json",
        {
            "ingest_folder": "/file-ingest",
            "calibre_library_dir": "/file-library",
            "tmp_conversion_dir": "/file-tmp",
        },
    )
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))
    monkeypatch.setenv("CWA_INGEST_FOLDER", "/env-ingest")
    monkeypatch.setenv("CWA_TMP_CONVERSION_DIR", "/env-tmp")

    assert _answers(app_paths) == _answers(constants) == {
        "ingest_folder": "/env-ingest",
        "calibre_library_dir": "/file-library",
        "tmp_conversion_dir": "/env-tmp",
    }


def test_read_site_trailing_separator_shapes_are_preserved(
    resolvers, monkeypatch, tmp_path
):
    _app_paths, constants = resolvers
    dirs_file = _write_dirs(
        tmp_path / "dirs.json",
        {
            "ingest_folder": "/shape-ingest",
            "calibre_library_dir": "/shape-library",
            "tmp_conversion_dir": "/shape-tmp",
        },
    )
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))

    from cps import cwa_functions, web
    import convert_library
    import cover_enforcer
    import ingest_processor
    import library_paths

    monkeypatch.setattr(cover_enforcer, "dirs_json", str(dirs_file))

    assert cwa_functions.get_ingest_dir() == "/shape-ingest"
    assert web.cwa_get_library_location() == "/shape-library"
    assert cwa_functions.get_tmp_conversion_dir() == "/shape-tmp/"
    expected_dirs = ("/shape-ingest/", "/shape-library/", "/shape-tmp/")
    assert convert_library.LibraryConverter.get_dirs(None, str(dirs_file)) == expected_dirs
    assert ingest_processor.NewBookProcessor.get_dirs(None, str(dirs_file)) == expected_dirs
    assert cover_enforcer.Book.get_calibre_library(None) == "/shape-library"
    assert cover_enforcer.Enforcer.get_calibre_library(None) == "/shape-library"
    assert library_paths.get_calibre_library_dir(str(dirs_file)) == "/shape-library"


def test_file_fallback_is_reported_once_per_key_per_process(
    resolvers, monkeypatch, tmp_path, caplog, capsys
):
    app_paths, constants = resolvers
    values = {key: f"/logged/{key}" for key, _env, _default in PATH_CASES}
    dirs_file = _write_dirs(tmp_path / "dirs.json", values)
    monkeypatch.setenv("CWA_DIRS_JSON", str(dirs_file))
    monkeypatch.setattr(constants, "DIRS_JSON", str(dirs_file))

    _answers(app_paths)
    _answers(app_paths)
    script_lines = [
        line for line in capsys.readouterr().err.splitlines()
        if line.startswith("[cwa-paths]")
    ]
    assert len(script_lines) == 3

    caplog.set_level(logging.INFO, logger="cps.constants")
    _answers(constants)
    _answers(constants)
    app_records = [r for r in caplog.records if r.name == "cps.constants"]
    assert len(app_records) == 3

    for key, _env, _default in PATH_CASES:
        assert sum(key in line and values[key] in line for line in script_lines) == 1
        assert sum(
            key in record.getMessage() and values[key] in record.getMessage()
            for record in app_records
        ) == 1


def _auto_library_writer(tmp_path, monkeypatch, env_value, discovered):
    import auto_library

    monkeypatch.setenv("CWA_CALIBRE_LIBRARY_DIR", env_value)
    writer = auto_library.AutoLibrary.__new__(auto_library.AutoLibrary)
    writer.dirs_path = str(tmp_path / "dirs.json")
    writer.lib_path = discovered
    original = {
        "ingest_folder": "/file-ingest",
        "calibre_library_dir": "/file-library",
        "tmp_conversion_dir": "/file-tmp",
    }
    Path(writer.dirs_path).write_text(json.dumps(original), encoding="utf-8")
    return writer, original


def test_auto_library_env_override_is_visible_and_leaves_file_untouched(
    tmp_path, monkeypatch, capsys
):
    writer, original = _auto_library_writer(
        tmp_path, monkeypatch, "/env-library", "/env-library"
    )

    writer.update_dirs_json()

    assert json.loads(Path(writer.dirs_path).read_text(encoding="utf-8")) == original
    output = capsys.readouterr().out
    assert "CWA_CALIBRE_LIBRARY_DIR" in output
    assert "authoritative" in output
    assert "unchanged" in output


def test_auto_library_stops_on_discovery_conflicting_with_env_override(
    tmp_path, monkeypatch, capsys
):
    writer, original = _auto_library_writer(
        tmp_path, monkeypatch, "/env-library", "/discovered-library"
    )

    with pytest.raises(SystemExit) as excinfo:
        writer.update_dirs_json()

    assert excinfo.value.code == 1
    assert json.loads(Path(writer.dirs_path).read_text(encoding="utf-8")) == original
    output = capsys.readouterr().out
    assert "CWA_CALIBRE_LIBRARY_DIR" in output
    assert "/env-library" in output
    assert "/discovered-library" in output


def test_shell_consumers_delegate_path_resolution_to_app_paths_cli():
    consumers = {
        REPO_ROOT / "scripts/set_ownership.sh": "all",
        S6_DIR / "cwa-init/run": "ingest_folder",
        S6_DIR / "cwa-ingest-service/run": "ingest_folder",
        S6_DIR / "cwa-checksum-backfill/run": "calibre_library_dir",
    }
    for path, command in consumers.items():
        source = path.read_text(encoding="utf-8")
        assert "app_paths.py" in source, f"{path.relative_to(REPO_ROOT)} bypasses app_paths"
        assert command in source, f"{path.relative_to(REPO_ROOT)} does not resolve {command}"

    assert "/app/calibre-web-automated/dirs.json" not in (
        S6_DIR / "cwa-ingest-service/run"
    ).read_text(encoding="utf-8")
    assert "json.load(config_file).get(\"calibre_library_dir\")" not in (
        S6_DIR / "cwa-checksum-backfill/run"
    ).read_text(encoding="utf-8")
