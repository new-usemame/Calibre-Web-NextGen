# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#995: metadata_change_logs / metadata_temp live under CONFIG_DIR, not the app tree.

The move itself is a one-line change; the ways it silently breaks are not, and CI was
green on all three of them. Each test here pins one runtime failure that green unit
tests did not catch:

* ``cps/editbooks.py`` referenced ``constants.METADATA_CHANGE_LOGS`` -- a name that does
  not exist -- so every metadata edit raised ``AttributeError``. No test covered those
  three call sites.
* ``scripts/cover_enforcer.py`` and ``scripts/kindle_epub_fixer.py`` are launched by s6 as
  ``python3 <app>/scripts/<name>.py`` with an empty ``PYTHONPATH``, so ``sys.path[0]`` is
  ``scripts/`` and the project root is *not* importable. An unguarded ``from cps import
  constants`` there is ``ModuleNotFoundError`` on every run, which silently kills metadata
  enforcement and the Kindle EPUB fixer.
* The s6 watcher resolves its watch folder in shell while Python resolves it from
  ``CONFIG_DIR``. If the two stop agreeing, the writer and the watcher part ways and
  enforcement stops with nothing in the logs.
"""

import ast
import re
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# The standalone scripts s6 launches directly, which therefore cannot rely on the
# project root already being on sys.path.
STANDALONE_SCRIPTS = ["cover_enforcer.py", "kindle_epub_fixer.py"]


def _module_level_cps_import_lineno(tree):
    """First line at which the module body imports ``cps`` (None if it never does).

    Descends into module-scope ``try``/``if`` blocks, because a ``cps`` import wrapped in
    ``try: ... except ImportError`` still executes at import time -- but does not descend
    into functions, whose imports run only when called.
    """
    container = (ast.If, ast.Try, ast.With, ast.For, ast.While)

    def scan(body):
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "cps" or alias.name.startswith("cps."):
                        return node.lineno
            elif isinstance(node, ast.ImportFrom):
                # `from cps import x` / `from cps.y import z`; level>0 is a relative
                # import, which these standalone scripts never use.
                if node.level == 0 and node.module and (
                    node.module == "cps" or node.module.startswith("cps.")
                ):
                    return node.lineno
            elif isinstance(node, container):
                for attr in ("body", "orelse", "finalbody"):
                    found = scan(getattr(node, attr, None) or [])
                    if found is not None:
                        return found
                for handler in getattr(node, "handlers", None) or []:
                    found = scan(handler.body)
                    if found is not None:
                        return found
        return None

    return scan(tree.body)


def _is_sys_path_mutation(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False

    # app_paths.ensure_app_root_on_sys_path() is the shared bootstrap (#1462).
    # It does the same sys.path.insert these scripts each used to inline, so it
    # satisfies this ordering contract; the assertion below still requires it to
    # run before the module-level cps import.
    #
    # Pinned to the `app_paths` receiver specifically. Matching the method name
    # alone would accept `anything.ensure_app_root_on_sys_path()`, including a
    # stub that does nothing — which would turn this guard into a name check.
    if func.attr == "ensure_app_root_on_sys_path":
        return isinstance(func.value, ast.Name) and func.value.id == "app_paths"

    if func.attr not in ("insert", "append"):
        return False
    value = func.value
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "path"
        and isinstance(value.value, ast.Name)
        and value.value.id == "sys"
    )


def _sys_path_bootstrap_lineno(tree):
    """First line at which the *module body* mutates ``sys.path``.

    Deliberately does not descend into function or class bodies: a bootstrap inside a
    function runs only when that function is called, so it cannot make a module-level
    ``import cps`` succeed. Counting one would let the ordering assertion pass on code
    that still crashes at import time.
    """
    module_scope = (ast.If, ast.Try, ast.With, ast.For, ast.While)

    def walk_module_scope(body):
        for stmt in body:
            for sub in ast.walk(stmt) if isinstance(stmt, ast.Expr) else [stmt]:
                if _is_sys_path_mutation(getattr(sub, "value", None)):
                    return sub.lineno
            if isinstance(stmt, module_scope):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    nested = getattr(stmt, attr, None) or []
                    for item in nested:
                        inner = getattr(item, "body", None)
                        found = walk_module_scope(inner if inner is not None else [item])
                        if found is not None:
                            return found
        return None

    return walk_module_scope(tree.body)


@pytest.mark.parametrize("script_name", STANDALONE_SCRIPTS)
def test_standalone_script_bootstraps_sys_path_before_importing_cps(script_name):
    """A module-level `cps` import must come after the project root is on sys.path.

    s6 runs these as `python3 <app>/scripts/<name>.py`, so sys.path[0] is scripts/ and
    `cps` is unimportable until the bootstrap runs.
    """
    source = (SCRIPTS_DIR / script_name).read_text(encoding="utf-8")
    tree = ast.parse(source)

    cps_line = _module_level_cps_import_lineno(tree)
    if cps_line is None:
        pytest.skip(f"{script_name} has no module-level cps import")

    bootstrap_line = _sys_path_bootstrap_lineno(tree)
    assert bootstrap_line is not None, (
        f"{script_name} imports cps at module level (line {cps_line}) but never adds the "
        "project root to sys.path. s6 launches it with sys.path[0]=scripts/, so this is "
        "ModuleNotFoundError on every run."
    )
    assert bootstrap_line < cps_line, (
        f"{script_name} imports cps at line {cps_line}, before the sys.path bootstrap at "
        f"line {bootstrap_line}. The import runs first, so it raises ModuleNotFoundError "
        "under s6 regardless of the bootstrap below it."
    )


@pytest.mark.parametrize("script_name", STANDALONE_SCRIPTS)
def test_standalone_script_runs_the_way_s6_launches_it(script_name):
    """Run the real script by absolute path, exactly as the s6 unit does.

    The behavioural counterpart to the AST pin above. `python3 <abs>/scripts/<name>.py`
    is what the metadata-change-detector service executes, and it is what puts scripts/
    (not the project root) at sys.path[0]. ``--help`` makes argparse exit immediately,
    but only *after* the whole module body -- including the cps import and the
    metadata-path globals -- has already executed, which is the part under test.
    """
    script = SCRIPTS_DIR / script_name

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # s6 launches these with an empty PYTHONPATH

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"{script_name} did not settle within the timeout in this environment")

    out = (proc.stdout or "") + (proc.stderr or "")

    assert "No module named 'cps'" not in out, (
        f"{script_name} raises ModuleNotFoundError: No module named 'cps' when launched the "
        f"way s6 launches it (python3 <abs path>, empty PYTHONPATH). The metadata enforcer "
        f"dies on every invocation and metadata changes silently stop reaching the ebook "
        f"files. Output:\n{out}"
    )

    if proc.returncode != 0:
        missing = [
            ln for ln in out.splitlines() if "ModuleNotFoundError" in ln or "ImportError" in ln
        ]
        if missing:
            # Some other dependency is absent from this environment; that is an env gap,
            # not the regression under test.
            pytest.skip(f"unrelated import failure in this environment: {missing[:2]}")
        pytest.skip(f"{script_name} --help exited {proc.returncode} in this environment")


def test_editbooks_only_references_constants_that_exist():
    """Every `constants.<NAME>` used by editbooks.py must exist on cps.constants.

    `constants.METADATA_CHANGE_LOGS` did not, so all three metadata-edit paths raised
    AttributeError at runtime while the unit suite stayed green.
    """
    from cps import constants

    source = (PROJECT_ROOT / "cps" / "editbooks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    referenced = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "constants"
        ):
            referenced.add(node.attr)

    missing = sorted(name for name in referenced if not hasattr(constants, name))
    assert not missing, (
        "cps/editbooks.py references cps.constants attributes that do not exist: "
        f"{missing}. Each one is an AttributeError on the user-visible edit path."
    )


def test_metadata_change_log_paths_are_joined_not_divided():
    """The metadata-log constants are strings, so `/` on them is a TypeError.

    Guards the shape of the fix: even with the attribute renamed correctly,
    `constants.CWA_METADATA_CHANGE_LOGS_DIR / "x.json"` raises
    `unsupported operand type(s) for /: 'str' and 'str'`.
    """
    from cps import constants

    assert isinstance(constants.CWA_METADATA_CHANGE_LOGS_DIR, str)
    assert isinstance(constants.CWA_METADATA_TEMP_DIR, str)

    source = (PROJECT_ROOT / "cps" / "editbooks.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = node.left
            if (
                isinstance(left, ast.Attribute)
                and isinstance(left.value, ast.Name)
                and left.value.id == "constants"
            ):
                pytest.fail(
                    f"cps/editbooks.py line {node.lineno} uses `/` on the string constant "
                    f"constants.{left.attr}. Use os.path.join instead; `str / str` is a "
                    "TypeError."
                )


def test_metadata_dirs_resolve_under_config_dir():
    """The whole point of #995: these resolve under CONFIG_DIR, not the app tree."""
    from cps import constants

    assert constants.CWA_METADATA_TEMP_DIR == os.path.join(
        constants.CONFIG_DIR, "metadata_temp"
    )
    # The change-logs dir is env-overridable; with no override it tracks CONFIG_DIR.
    if "CWA_METADATA_CHANGE_LOGS_DIR" not in os.environ:
        assert constants.CWA_METADATA_CHANGE_LOGS_DIR == os.path.join(
            constants.CONFIG_DIR, "metadata_change_logs"
        )

    app_tree = str(PROJECT_ROOT)
    if constants.CONFIG_DIR != app_tree:
        assert not constants.CWA_METADATA_TEMP_DIR.startswith(
            os.path.join(app_tree, "metadata_temp")
        ), "metadata_temp must not sit in the app tree, which upgrades replace wholesale"


def test_metadata_dirs_are_created_at_runtime_not_only_at_build_time():
    """cwa-init must create them, because setup-cwa.sh cannot.

    setup-cwa.sh runs in a Dockerfile RUN, so it only seeds the image layer. A named
    volume is populated from that layer, but a bind-mounted /config -- the common
    linuxserver-style deployment -- shadows it completely, leaving the dirs absent and
    `calibredb export --to-dir` writing into nothing.
    """
    init_run = (
        PROJECT_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-init/run"
    ).read_text(encoding="utf-8")

    for dir_token in ("metadata_change_logs", "metadata_temp"):
        assert dir_token in init_run, (
            f"cwa-init/run never creates {dir_token}. setup-cwa.sh runs at image build "
            "time only, so on a bind-mounted /config this directory does not exist at "
            "runtime."
        )
        assert re.search(
            r"install\s+-d[^\n]*" + re.escape(dir_token) + r"|"
            + re.escape(dir_token) + r"[^\n]*\"\s*$",
            init_run,
            re.M,
        ) or f'{dir_token}"' in init_run or f"{dir_token}\n" in init_run, (
            f"cwa-init/run mentions {dir_token} but does not appear to create it"
        )


def test_upgrade_migrates_change_logs_off_the_old_app_tree_location():
    """#995 asked for a migration so existing volumes are not orphaned.

    An edit made shortly before an upgrade leaves a change log in the app tree that the
    detector no longer watches. Without a move, that edit's file-level enforcement is
    dropped silently.
    """
    init_run = (
        PROJECT_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-init/run"
    ).read_text(encoding="utf-8")

    assert "/app/calibre-web-automated/metadata_change_logs" in init_run, (
        "nothing migrates change logs off the pre-#995 app-tree location, so anything "
        "pending at upgrade time is silently orphaned"
    )
    assert "mv" in init_run, "the migration must actually move the leftover files"


def test_watcher_and_writer_agree_on_the_change_logs_path():
    """The s6 watcher and the Python writer must honour the same env knobs.

    They are two hardcoded copies of one path. If they drift, the writer writes where
    nobody is watching and enforcement stops with nothing in the logs.
    """
    run_script = (
        PROJECT_ROOT
        / "root/etc/s6-overlay/s6-rc.d/metadata-change-detector/run"
    ).read_text(encoding="utf-8")

    watch_line = next(
        (ln for ln in run_script.splitlines() if ln.strip().startswith("WATCH_FOLDER=")),
        None,
    )
    assert watch_line is not None, "metadata-change-detector/run must set WATCH_FOLDER"

    assert "/app/calibre-web-automated/metadata_change_logs" not in watch_line, (
        "the watcher still points at the app tree while Python writes under CONFIG_DIR"
    )
    assert "CWA_METADATA_CHANGE_LOGS_DIR" in watch_line, (
        "WATCH_FOLDER ignores CWA_METADATA_CHANGE_LOGS_DIR, which Python honours; setting "
        f"it moves the writer and leaves the watcher behind. Got: {watch_line.strip()}"
    )
    # Deliberately the opposite of what looks like the tidy answer. cwa-init and
    # svc-calibre-web-automated both `export CALIBRE_DBPATH=/config` before doing anything,
    # so the app's CONFIG_DIR is /config whatever the operator set -- but this unit does not
    # clobber it. Deriving WATCH_FOLDER from it would make this the one place that can
    # disagree with the writer.
    assert "CALIBRE_DBPATH" not in watch_line, (
        "WATCH_FOLDER derives from CALIBRE_DBPATH, but this unit inherits the operator's "
        "value while cwa-init and svc-calibre-web-automated force it to /config. Setting "
        f"CALIBRE_DBPATH would leave the watcher alone in the wrong place. Got: {watch_line.strip()}"
    )
    assert "/config/metadata_change_logs" in watch_line, (
        f"WATCH_FOLDER must default to /config/metadata_change_logs. Got: {watch_line.strip()}"
    )


def test_cwa_init_does_not_rederive_config_root_after_pinning_it():
    """cwa-init exports CALIBRE_DBPATH=/config, so re-reading it below is a trap.

    Anything resolved from `${CALIBRE_DBPATH}` after that export always yields /config,
    which makes a guard written against an operator-set value read as if it covered a
    case it cannot see.
    """
    init_run = (
        PROJECT_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-init/run"
    ).read_text(encoding="utf-8")
    lines = init_run.splitlines()

    export_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("export CALIBRE_DBPATH=")),
        None,
    )
    assert export_idx is not None, "cwa-init should still pin CALIBRE_DBPATH"

    for i, ln in enumerate(lines[export_idx + 1:], start=export_idx + 2):
        if ln.lstrip().startswith("#"):
            continue
        if "CWA_CHANGE_LOGS_DIR=" in ln or "CWA_TEMP_DIR=" in ln:
            assert "CALIBRE_DBPATH" not in ln, (
                f"line {i} resolves a metadata dir from CALIBRE_DBPATH after cwa-init pinned "
                f"it to /config, so the knob it appears to honour is already gone: {ln.strip()}"
            )
