# SPDX-License-Identifier: GPL-3.0-or-later
"""The app.db creation failure path must report the cause, not crash on it (#1482).

#1482 replaced the ``shutil.copyfile`` seeding of ``app.db`` with a call to
``create_appdb()``, which builds the database from the ORM. That import can fail
on a bare-metal install where ``cps`` is not importable, so the new code wraps it:

    try:
        create_appdb(self.app_db)
    except ImportError as error:
        print("[cwa-auto-library]: ERROR: Could not create new app.db")
        print(e)          # <-- binds `error`, prints `e`
        sys.exit(1)

The handler bound the exception to ``error`` and then printed ``e``, which is not
defined in that scope. So the one path that exists to explain *why* a fresh
container could not build its database raised ``NameError`` from inside the
handler instead: the operator lost the actual ImportError message, and the
process died on an unhandled traceback rather than the intended ``exit(1)``.

Only reachable on first boot with no ``/config/app.db``, so an existing install
never sees it and the household canary cannot surface it — it strands *new*
deployments specifically.

Pinned here: when ``create_appdb`` raises ImportError, ``check_for_app_db``
exits with status 1 and the printed output carries the original message.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_LIB = REPO_ROOT / "scripts" / "auto_library.py"
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_library_1482", AUTO_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fresh_install(tmp_path, monkeypatch):
    """An AutoLibrary on a config dir with no app.db — the first-boot branch."""
    mod = _load_module()
    cfg = tmp_path / "config"
    library = tmp_path / "calibre-library"
    cfg.mkdir()
    library.mkdir()

    al = mod.AutoLibrary()
    al.config_dir = str(cfg)
    al.library_dir = str(library)
    al.DEFAULT_APPDB_PATH = str(cfg / "app.db")
    al.dirs_path = str(tmp_path / "dirs.json")
    Path(al.dirs_path).write_text('{"calibre_library_dir": "/calibre-library"}')

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    return mod, al


def test_import_failure_exits_one_instead_of_raising_nameerror(fresh_install, monkeypatch, capsys):
    """A failing create_appdb must produce exit(1), not NameError from the handler."""
    mod, al = fresh_install

    def _boom(_path):
        raise ImportError("No module named 'cps'")

    monkeypatch.setattr(mod, "create_appdb", _boom)

    with pytest.raises(SystemExit) as exit_info:
        al.check_for_app_db()

    assert exit_info.value.code == 1

    out = capsys.readouterr().out
    assert "Could not create new app.db" in out
    # The whole point of the handler: the operator gets the actual reason.
    assert "No module named 'cps'" in out, (
        "the ImportError message was swallowed — the handler printed an undefined "
        "name instead of the bound exception"
    )


def test_no_except_handler_references_an_unbound_exception_name():
    """Repo-wide guard for the class of bug, not just this instance.

    An ``except ... as X`` whose body loads a *different* exception-ish name is
    always wrong: Python unbinds ``X`` at the end of the block, so the stray name
    is either undefined or a stale leftover from another handler.
    """
    import ast

    source = AUTO_LIB.read_text()
    tree = ast.parse(source)
    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        loaded = {
            n.id
            for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        stray = {n for n in loaded if n in {"e", "err", "error", "ex", "exc"}} - {node.name}
        if stray:
            offenders.append((node.lineno, node.name, sorted(stray)))

    assert offenders == [], (
        "except handler(s) reference an exception name they did not bind: "
        + "; ".join(f"line {ln}: binds {bound!r}, loads {stray}" for ln, bound, stray in offenders)
    )
