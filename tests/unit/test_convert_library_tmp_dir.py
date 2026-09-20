# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library must own its temp directory and must not fake success.

Two defects that compound into "Convert Library reports every book converted
and the library is unchanged":

1. `ingest_processor.py` ends each run with `shutil.rmtree()` on the shared
   temp conversion directory, removing the directory itself rather than
   emptying it. Ingest recovers because it calls `mkdir(exist_ok=True)` on its
   next run. `convert_library.py` only ever wrote into that path, so after the
   first ingest `ebook-convert` was handed an output path inside a directory
   that no longer existed and produced no file. `cwa-init` recreates the
   directory at container start, which is what made this look intermittent.

2. Every conversion and import command was a `subprocess.Popen` wrapped in
   `except subprocess.CalledProcessError`. `Popen` never raises that, so the
   handler was unreachable and a non-zero exit fell through to the success
   message. A run that added nothing still printed "Conversion ... successful!"
   and "Import ... successfully completed!" for every book.

The second defect is why the first went unnoticed: there was no way to tell
from the log that nothing had happened.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402

CONVERT_LIBRARY_PY = REPO_ROOT / "scripts/convert_library.py"


def _converter(tmp_conversion_dir):
    """A LibraryConverter with only the attributes these paths touch.

    __init__ reads cwa.db and the Calibre library, neither of which exists in
    a unit-test environment.
    """
    converter = convert_library.LibraryConverter.__new__(
        convert_library.LibraryConverter)
    converter.verbose = False
    converter.current_book = 1
    converter.to_convert = []
    converter.tmp_conversion_dir = str(tmp_conversion_dir) + "/"
    return converter


def test_ensure_tmp_conversion_dir_creates_a_missing_directory(tmp_path):
    target = tmp_path / "cwa_conversion_tmp"
    converter = _converter(target)
    assert not target.exists()

    converter.ensure_tmp_conversion_dir()

    assert target.is_dir(), (
        "Convert Library did not create its own temp directory, so every "
        "ebook-convert call writes into a path that does not exist")


def test_empty_tmp_con_dir_recreates_a_directory_removed_mid_run(tmp_path):
    target = tmp_path / "cwa_conversion_tmp"
    converter = _converter(target)
    converter.ensure_tmp_conversion_dir()
    (target / "leftover.epub").write_text("x", encoding="utf-8")

    # An ingest finishing mid-run takes the whole directory with it.
    import shutil
    shutil.rmtree(target)

    converter.empty_tmp_con_dir()

    assert target.is_dir(), (
        "an ingest finishing partway through a Convert Library run leaves the "
        "rest of that run writing into a missing directory")
    assert list(target.iterdir()) == []


def test_run_streaming_raises_on_a_failed_command(tmp_path):
    converter = _converter(tmp_path / "tmp")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        converter._run_streaming(
            [sys.executable, "-c", "import sys; sys.stdout.write('boom\\n'); sys.exit(3)"])

    assert excinfo.value.returncode == 3
    assert "boom" in (excinfo.value.output or ""), (
        "the captured output must reach the handler; the kepub branch prints "
        "e.stderr when it reports a failed conversion")


def test_run_streaming_is_quiet_on_success(tmp_path):
    converter = _converter(tmp_path / "tmp")
    converter._run_streaming([sys.executable, "-c", "print('fine')"])


def test_no_conversion_command_bypasses_the_failure_check():
    """Every subprocess.Popen in the module must live in _run_streaming.

    A new command added with an inline Popen would silently reacquire the
    reports-success-on-failure behaviour, because the surrounding
    `except subprocess.CalledProcessError` blocks would still never fire.
    """
    tree = ast.parse(CONVERT_LIBRARY_PY.read_text(encoding="utf-8"))

    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_streaming":
            allowed = {id(inner) for inner in ast.walk(node)}
            break
    assert allowed, "_run_streaming() not found in convert_library.py"

    stray = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "Popen"
                and isinstance(func.value, ast.Name) and func.value.id == "subprocess"
                and id(node) not in allowed):
            stray.append(node.lineno)

    assert not stray, (
        "subprocess.Popen outside _run_streaming at line(s) %s: a non-zero exit "
        "there is reported as success" % stray)
