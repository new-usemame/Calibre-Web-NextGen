# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""fork #2546: the count of ad-hoc Flask apps in test files can only go down.

About 230 test files build their own ``Flask(__name__)`` app instead of sharing
one, and new tests keep copying the pattern from whatever file sits next to
them. Each copy is a private, partial app: it registers only the blueprints and
config that test thought of, so it can pass while the real app (``cps.main``)
is broken, and every one is another place to update when app setup changes.

This is a ratchet, not a cleanup. ``flask_app_baseline.txt`` records how many
Flask app constructions each test file had when the check landed. The check
fails when a file goes over its recorded count (including any file not in the
baseline at all), and when a file drops below it, so the baseline is lowered in
the same change and the freed slot can't be spent again later.

What counts: any call to ``Flask(...)``, ``flask.Flask(...)`` or an imported
alias of either, in a ``test_*.py`` file under ``tests/``. The first argument
doesn't matter; ``Flask("x")`` is the same private app as ``Flask(__name__)``.
Shared helpers that aren't test files (``tests/unit/*_world.py``) are where an
app built once for many tests belongs, so they aren't scanned.

To regenerate the baseline after removing constructions::

    python -m tests.fitness.test_no_new_flask_apps --write-baseline
"""

import ast
import sys
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TESTS_ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("flask_app_baseline.txt")


def count_flask_apps(source):
    """Return how many Flask app constructions ``source`` contains."""
    with warnings.catch_warnings():
        # Escape-sequence warnings belong to the scanned file, not to this check.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    # ``flask.Flask`` under any module alias is caught by the attribute name;
    # ``from flask import Flask as F`` needs the alias recorded.
    class_names = {"Flask"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "flask":
            for alias in node.names:
                if alias.name == "Flask":
                    class_names.add(alias.asname or alias.name)

    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in class_names:
            count += 1
        elif isinstance(func, ast.Attribute) and func.attr == "Flask":
            count += 1
    return count


def scan(root):
    """Map each test file under ``root`` (posix path relative to it) to its count."""
    counts = {}
    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            found = count_flask_apps(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise AssertionError(f"{path}: cannot be parsed: {exc}") from exc
        if found:
            counts[path.relative_to(root).as_posix()] = found
    return counts


def read_baseline(path):
    baseline = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, count = line.rsplit(" ", 1)
            baseline[name] = int(count)
    return baseline


def write_baseline(path, counts):
    lines = [
        "# Flask app constructions per test file (fork #2546). Counts may only go down.",
        "# Regenerate: python -m tests.fitness.test_no_new_flask_apps --write-baseline",
    ]
    lines += [f"{name} {count}" for name, count in sorted(counts.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare(counts, baseline):
    """Return (grown, shrunk): files above their baseline, and files below it."""
    grown = {
        name: (baseline.get(name, 0), found)
        for name, found in counts.items()
        if found > baseline.get(name, 0)
    }
    shrunk = {
        name: (allowed, counts.get(name, 0))
        for name, allowed in baseline.items()
        if counts.get(name, 0) < allowed
    }
    return grown, shrunk


def test_no_test_file_adds_a_flask_app():
    grown, _ = compare(scan(TESTS_ROOT), read_baseline(BASELINE))
    assert not grown, (
        "These test files build more Flask apps than the baseline allows "
        "(file: allowed -> found). Reuse an app from an existing helper or a "
        "fixture instead of constructing a new Flask(...) in the test:\n"
        + "\n".join(f"  tests/{n}: {a} -> {f}" for n, (a, f) in sorted(grown.items()))
    )


def test_baseline_has_no_unspent_slots():
    _, shrunk = compare(scan(TESTS_ROOT), read_baseline(BASELINE))
    assert not shrunk, (
        "These test files now build fewer Flask apps than the baseline records "
        "(file: recorded -> found). Lower the baseline so the slots can't be "
        "reused: python -m tests.fitness.test_no_new_flask_apps --write-baseline\n"
        + "\n".join(f"  tests/{n}: {a} -> {f}" for n, (a, f) in sorted(shrunk.items()))
    )


def _plant(root, name, source):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize("source", [
    "from flask import Flask\napp = Flask(__name__)\n",
    "import flask\napp = flask.Flask('isolated')\n",
    "from flask import Flask as F\napp = F(__name__)\n",
    "import flask as fl\napp = fl.Flask(__name__)\n",
])
def test_a_planted_new_app_is_caught(tmp_path, source):
    _plant(tmp_path, "unit/test_new.py", source)
    grown, _ = compare(scan(tmp_path), {})
    assert grown == {"unit/test_new.py": (0, 1)}


def test_one_more_app_in_a_baselined_file_is_caught(tmp_path):
    _plant(tmp_path, "unit/test_old.py",
           "import flask\na = flask.Flask(__name__)\nb = flask.Flask(__name__)\n")
    grown, shrunk = compare(scan(tmp_path), {"unit/test_old.py": 1})
    assert grown == {"unit/test_old.py": (1, 2)}
    assert shrunk == {}


def test_a_removed_app_must_lower_the_baseline(tmp_path):
    _plant(tmp_path, "unit/test_old.py", "def test_x():\n    pass\n")
    grown, shrunk = compare(scan(tmp_path), {"unit/test_old.py": 2})
    assert grown == {}
    assert shrunk == {"unit/test_old.py": (2, 0)}


@pytest.mark.parametrize("source", [
    # Text that mentions an app is not an app.
    "SNIPPET = 'app = Flask(__name__)'\n",
    # Using an app, or a different class that happens to share a module.
    "from flask import Flask, Blueprint\nbp = Blueprint('x', __name__)\n",
    "def test_x(app):\n    app.test_client()\n",
])
def test_things_that_are_not_new_apps_are_not_counted(tmp_path, source):
    _plant(tmp_path, "unit/test_fine.py", source)
    assert scan(tmp_path) == {}


def test_helpers_that_are_not_test_files_are_not_scanned(tmp_path):
    _plant(tmp_path, "unit/library_world.py",
           "from flask import Flask\napp = Flask(__name__)\n")
    assert scan(tmp_path) == {}


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-baseline"]:
        sys.exit("usage: python -m tests.fitness.test_no_new_flask_apps --write-baseline")
    write_baseline(BASELINE, scan(TESTS_ROOT))
    print(f"wrote {BASELINE}")
