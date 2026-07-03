# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for #561 — a hung `calibredb export` (podofo PDF hang)
must not pin the request forever, and every caller of do_calibre_export must
degrade to the original file when the export returns (None, None).

Red on main: do_calibre_export calls p.communicate() with no timeout (the
fake process below then hangs the export path instead of raising), and three
of four callers crash with TypeError when handed (None, None).
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_embed_helper(monkeypatch_modules=None):
    """Load cps/embed_helper.py without triggering the heavy cps package init."""
    module_path = REPO_ROOT / "cps" / "embed_helper.py"

    cps_pkg = types.ModuleType("cps")
    cps_pkg.__path__ = [str(REPO_ROOT / "cps")]

    logger_mod = types.ModuleType("cps.logger")
    logger_mod.create = lambda *_a, **_k: types.SimpleNamespace(
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
        error=lambda *a, **k: None,
        error_or_exception=lambda *a, **k: None,
    )

    constants_mod = types.ModuleType("cps.constants")
    constants_mod.SUPPORTED_CALIBRE_BINARIES = {"calibredb": "calibredb"}

    # A real, existing dir: on un-fixed code the fake process "completes" and
    # do_calibre_export falls through to `return tmp_dir, temp_file_name`,
    # which must be visibly different from the fixed (None, None).
    file_helper_mod = types.ModuleType("cps.file_helper")
    file_helper_mod.get_temp_dir = lambda: "/tmp"

    subproc_mod = types.ModuleType("cps.subproc_wrapper")
    subproc_mod.process_open = lambda *a, **k: None  # tests patch this

    config_mod = types.SimpleNamespace(
        config_calibre_split=False,
        config_calibre_dir="/library",
        config_binariesdir="/opt/calibre",
        get_book_path=lambda: "/library",
    )
    cps_pkg.logger = logger_mod
    cps_pkg.config = config_mod

    services_pkg = types.ModuleType("cps.services")
    services_pkg.__path__ = []
    plugins_mod = types.ModuleType("cps.services.calibre_user_plugins")
    plugins_mod.apply_to_env = lambda env: None
    services_pkg.calibre_user_plugins = plugins_mod

    shims = {
        "cps": cps_pkg,
        "cps.logger": logger_mod,
        "cps.constants": constants_mod,
        "cps.file_helper": file_helper_mod,
        "cps.subproc_wrapper": subproc_mod,
        "cps.services": services_pkg,
        "cps.services.calibre_user_plugins": plugins_mod,
    }
    if monkeypatch_modules:
        shims.update(monkeypatch_modules)

    saved = {name: sys.modules.get(name) for name in shims}
    sys.modules.update(shims)
    try:
        spec = importlib.util.spec_from_file_location("cps.embed_helper", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cps.embed_helper"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        sys.modules.pop("cps.embed_helper", None)


class _HungProcess:
    """Fake Popen whose communicate() raises TimeoutExpired only when a
    timeout is passed — on un-fixed code (no timeout arg) it 'hangs' by
    returning a successful-looking result, so the export path proceeds and
    the test fails."""

    pid = 4242

    def __init__(self):
        self.killed = False
        self.kill_calls = []

    def communicate(self, timeout=None):
        if timeout is None:
            # main-branch behavior: would block forever; simulate "completed
            # eventually" so the un-fixed code path visibly lacks the fallback
            return "", ""
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="calibredb export", timeout=timeout)
        return "", ""

    def kill(self):
        self.killed = True
        self.kill_calls.append("kill")


def test_timeout_returns_none_none_and_kills_tree(monkeypatch):
    embed_helper = _load_embed_helper()
    proc = _HungProcess()
    monkeypatch.setattr(embed_helper, "process_open", lambda *a, **k: proc)
    monkeypatch.setenv("CWA_EMBED_TIMEOUT", "1")

    killpg_calls = []

    def fake_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        proc.killed = True

    monkeypatch.setattr(embed_helper.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(embed_helper.os, "killpg", fake_killpg, raising=False)

    result = embed_helper.do_calibre_export(581, "pdf")

    assert result == (None, None), (
        "A timed-out export must return (None, None) so callers can degrade "
        "to the original file — got %r" % (result,)
    )
    assert killpg_calls, "The hung export tree must be killed as a process group"


def test_timeout_falls_back_to_kill_when_killpg_unavailable(monkeypatch):
    embed_helper = _load_embed_helper()
    proc = _HungProcess()
    monkeypatch.setattr(embed_helper, "process_open", lambda *a, **k: proc)
    monkeypatch.setenv("CWA_EMBED_TIMEOUT", "1")

    def raise_oserror(*_a):
        raise OSError("no such process group")

    monkeypatch.setattr(embed_helper.os, "getpgid", raise_oserror, raising=False)

    result = embed_helper.do_calibre_export(581, "pdf")

    assert result == (None, None)
    assert proc.kill_calls, "p.kill() must be the fallback when killpg fails"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, 90),
        ("90", 90),
        ("15", 15),
        ("garbage", 90),
        ("-5", 90),
        ("0", 90),
    ],
)
def test_embed_timeout_env_parsing(monkeypatch, env_value, expected):
    embed_helper = _load_embed_helper()
    if env_value is None:
        monkeypatch.delenv("CWA_EMBED_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CWA_EMBED_TIMEOUT", env_value)
    assert embed_helper._embed_timeout() == expected


# ---------------------------------------------------------------------------
# Source pins — refactor-fragile invariants that must not silently disappear
# ---------------------------------------------------------------------------


def test_process_open_starts_new_session():
    source = (REPO_ROOT / "cps" / "subproc_wrapper.py").read_text()
    assert "start_new_session" in source, (
        "process_open must run children in their own process group so a hung "
        "calibredb/calibre-parallel tree stays killable (#561)"
    )


def test_communicate_has_timeout():
    source = (REPO_ROOT / "cps" / "embed_helper.py").read_text()
    tree = ast.parse(source)
    timeout_communicates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "communicate"
        and any(kw.arg == "timeout" for kw in node.keywords)
    ]
    assert timeout_communicates, (
        "do_calibre_export must bound p.communicate() with a timeout — an "
        "unbounded wait is the #561 hang"
    )


def _branch_guards_none(path, callsite_snippet, guard_snippet):
    source = (REPO_ROOT / path).read_text()
    assert callsite_snippet in source, f"expected callsite missing from {path}"
    callsite_idx = source.index(callsite_snippet)
    window = source[callsite_idx:callsite_idx + 600]
    assert guard_snippet in window, (
        f"{path} must guard the (None, None) export-failure return within the "
        f"caller (looked for {guard_snippet!r} after the callsite)"
    )


def test_helper_download_branch_degrades_to_original():
    # normal (non-GDrive) download branch
    _branch_guards_none(
        "cps/helper.py",
        'elif book_format != "kepub" and config.config_binariesdir and config.config_embed_metadata:',
        "if not filename or not download_name:",
    )


def test_helper_gdrive_branch_degrades_to_original():
    _branch_guards_none(
        "cps/helper.py",
        'elif book_format != "kepub" and config.config_binariesdir:',
        "if not filename or not download_name:",
    )


def test_mail_gdrive_branch_degrades_to_original():
    _branch_guards_none(
        "cps/tasks/mail.py",
        "df.GetContentFile(datafile)",
        "if data_path and data_file",
    )


def test_convert_kepubify_degrades_to_original():
    _branch_guards_none(
        "cps/tasks/convert.py",
        "helper.do_calibre_export(self.book_id, format_old_ext[1:])",
        "if tmp_dir and temp_file_name:",
    )
