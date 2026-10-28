# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""fork #2644: the count of bare ``except: pass`` handlers in ``cps/`` and ``scripts/`` can only go down.

A bare ``except:`` catches ``BaseException``, so ``KeyboardInterrupt``,
``SystemExit`` and gevent's ``GreenletExit`` disappear along with ordinary
errors. With ``pass`` as its whole body it also leaves no log line, so a killed
greenlet or a shutdown request is swallowed without a trace. ``except
Exception:`` (or the specific error the block expects) keeps the intent without
eating the signals that stop a worker.

This is a ratchet built the same way as ``test_no_new_datetime_min.py``.
``bare_except_pass_baseline.txt`` records how many such handlers each file had.
The check fails when a file goes over its recorded count (including any file not
in the baseline at all), and when a file drops below it, so the baseline is
lowered in the same change and the freed slot can't be spent again later.

What counts: an ``except:`` with no exception type, and ``except BaseException:``,
whose body is a single ``pass``. A handler that names a narrower type, logs,
returns, re-raises, or does anything else is not counted. ``try``/``except*``
groups always name a type, so they can't match.

To regenerate the baseline after removing handlers::

    python -m tests.fitness.test_no_new_bare_except_pass --write-baseline
"""

import ast
import sys
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("cps", "scripts")
BASELINE = Path(__file__).with_name("bare_except_pass_baseline.txt")


def _catches_everything(handler):
    kind = handler.type
    if kind is None:
        return True
    return (isinstance(kind, ast.Name) and kind.id == "BaseException") or (
        isinstance(kind, ast.Attribute) and kind.attr == "BaseException"
        and isinstance(kind.value, ast.Name) and kind.value.id == "builtins")


def count_bare_except_pass(source):
    """Return how many catch-everything handlers in ``source`` do nothing but ``pass``."""
    with warnings.catch_warnings():
        # Escape-sequence warnings belong to the scanned file, not to this check.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _catches_everything(node)
        and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
    )


def scan(root):
    """Map each ``.py`` file under ``root``'s scanned dirs (posix path relative to ``root``) to its count."""
    counts = {}
    for top in SCANNED_DIRS:
        for path in sorted((root / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                found = count_bare_except_pass(path.read_text(encoding="utf-8"))
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
        "# Bare except: pass handlers per file in cps/ and scripts/ (fork #2644). Counts may only go down.",
        "# Regenerate: python -m tests.fitness.test_no_new_bare_except_pass --write-baseline",
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


def test_no_file_adds_a_bare_except_pass():
    grown, _ = compare(scan(REPO_ROOT), read_baseline(BASELINE))
    assert not grown, (
        "These files have more bare `except: pass` handlers than the baseline allows "
        "(file: allowed -> found). Catch Exception or the specific error you expect, "
        "and log it if the failure matters:\n"
        + "\n".join(f"  {n}: {a} -> {f}" for n, (a, f) in sorted(grown.items()))
    )


def test_baseline_has_no_unspent_slots():
    _, shrunk = compare(scan(REPO_ROOT), read_baseline(BASELINE))
    assert not shrunk, (
        "These files now have fewer bare `except: pass` handlers than the baseline "
        "records (file: recorded -> found). Lower the baseline so the slots can't be "
        "reused: python -m tests.fitness.test_no_new_bare_except_pass --write-baseline\n"
        + "\n".join(f"  {n}: {a} -> {f}" for n, (a, f) in sorted(shrunk.items()))
    )


def _plant(root, name, source):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize("source", [
    "try:\n    x = 1\nexcept:\n    pass\n",
    "try:\n    x = 1\nexcept BaseException:\n    pass\n",
    "import builtins\ntry:\n    x = 1\nexcept builtins.BaseException:\n    pass\n",
    "def f():\n    try:\n        return 1\n    except ValueError:\n        return 2\n    except:\n        pass\n",
    "class C:\n    def m(self):\n        try:\n            pass\n        except:\n            pass\n",
])
@pytest.mark.parametrize("top", SCANNED_DIRS)
def test_a_planted_new_handler_is_caught(tmp_path, source, top):
    _plant(tmp_path, f"{top}/new_module.py", source)
    grown, _ = compare(scan(tmp_path), {})
    assert grown == {f"{top}/new_module.py": (0, 1)}


def test_one_more_handler_in_a_baselined_file_is_caught(tmp_path):
    _plant(tmp_path, "cps/old.py",
           "try:\n    a = 1\nexcept:\n    pass\ntry:\n    b = 1\nexcept:\n    pass\n")
    grown, shrunk = compare(scan(tmp_path), {"cps/old.py": 1})
    assert grown == {"cps/old.py": (1, 2)}
    assert shrunk == {}


def test_a_removed_handler_must_lower_the_baseline(tmp_path):
    _plant(tmp_path, "cps/old.py", "try:\n    a = 1\nexcept Exception:\n    pass\n")
    grown, shrunk = compare(scan(tmp_path), {"cps/old.py": 2})
    assert grown == {}
    assert shrunk == {"cps/old.py": (2, 0)}


@pytest.mark.parametrize("source", [
    # A typed handler is the fix, not the problem.
    "try:\n    x = 1\nexcept Exception:\n    pass\n",
    "try:\n    x = 1\nexcept (OSError, ValueError):\n    pass\n",
    # A bare handler that does something other than pass is out of scope here.
    "import logging\ntry:\n    x = 1\nexcept:\n    logging.exception('x')\n",
    "try:\n    x = 1\nexcept:\n    raise\n",
    "try:\n    x = 1\nexcept:\n    pass\n    x = 2\n",
    # Text that mentions the pattern is not a handler.
    "# except: pass\nNOTE = 'except: pass'\n",
])
def test_handlers_that_are_not_bare_except_pass_are_not_counted(tmp_path, source):
    _plant(tmp_path, "cps/fine.py", source)
    assert scan(tmp_path) == {}


def test_files_outside_cps_and_scripts_are_not_scanned(tmp_path):
    _plant(tmp_path, "tests/unit/test_thing.py", "try:\n    x = 1\nexcept:\n    pass\n")
    assert scan(tmp_path) == {}


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-baseline"]:
        sys.exit("usage: python -m tests.fitness.test_no_new_bare_except_pass --write-baseline")
    write_baseline(BASELINE, scan(REPO_ROOT))
    print(f"wrote {BASELINE}")
