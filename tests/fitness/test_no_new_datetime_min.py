# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""fork #2643: the count of ``datetime.min`` reads in ``cps/`` and ``scripts/`` can only go down.

``datetime.min`` keeps being used as a "no value yet" stand-in. It is naive, so
comparing it with an aware datetime raises ``TypeError``, and shifting it by a
negative timezone offset overflows with ``OverflowError``. Both have bitten the
Kobo sync cursors. ``None`` (or an explicit, documented sentinel) says "no value"
without either trap.

This is a ratchet, not a cleanup, built the same way as
``test_no_new_flask_apps.py``. ``datetime_min_baseline.txt`` records how many
``datetime.min`` reads each file had when the check landed. The check fails when
a file goes over its recorded count (including any file not in the baseline at
all), and when a file drops below it, so the baseline is lowered in the same
change and the freed slot can't be spent again later.

What counts: reading ``.min`` off the ``datetime`` class however it was
imported: ``datetime.min`` after ``from datetime import datetime`` (or an alias
of it), and ``datetime.datetime.min`` under any alias of the module. The aware
form ``datetime.min.replace(tzinfo=...)`` counts too: it avoids the comparison
trap but still overflows on a negative offset. ``date.min``, ``time.min`` and
``timedelta.min`` are other types and are not counted. Mentions in comments and
strings are not reads.

To regenerate the baseline after removing uses::

    python -m tests.fitness.test_no_new_datetime_min --write-baseline
"""

import ast
import sys
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("cps", "scripts")
BASELINE = Path(__file__).with_name("datetime_min_baseline.txt")


def count_datetime_min(source):
    """Return how many ``datetime.min`` reads ``source`` contains."""
    with warnings.catch_warnings():
        # Escape-sequence warnings belong to the scanned file, not to this check.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    # Names bound to the datetime class, and names bound to the datetime module.
    class_names = set()
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "datetime":
                    class_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "datetime":
                    module_names.add(alias.asname or alias.name)

    count = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "min"):
            continue
        owner = node.value
        if isinstance(owner, ast.Name) and owner.id in class_names:
            count += 1
        elif (isinstance(owner, ast.Attribute) and owner.attr == "datetime"
              and isinstance(owner.value, ast.Name) and owner.value.id in module_names):
            count += 1
    return count


def scan(root):
    """Map each ``.py`` file under ``root``'s scanned dirs (posix path relative to ``root``) to its count."""
    counts = {}
    for top in SCANNED_DIRS:
        for path in sorted((root / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                found = count_datetime_min(path.read_text(encoding="utf-8"))
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
        "# datetime.min reads per file in cps/ and scripts/ (fork #2643). Counts may only go down.",
        "# Regenerate: python -m tests.fitness.test_no_new_datetime_min --write-baseline",
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


def test_no_file_adds_a_datetime_min():
    grown, _ = compare(scan(REPO_ROOT), read_baseline(BASELINE))
    assert not grown, (
        "These files read datetime.min more often than the baseline allows "
        "(file: allowed -> found). Use None for 'no value' and handle it where "
        "the value is read, instead of a naive datetime.min stand-in:\n"
        + "\n".join(f"  {n}: {a} -> {f}" for n, (a, f) in sorted(grown.items()))
    )


def test_baseline_has_no_unspent_slots():
    _, shrunk = compare(scan(REPO_ROOT), read_baseline(BASELINE))
    assert not shrunk, (
        "These files now read datetime.min less often than the baseline records "
        "(file: recorded -> found). Lower the baseline so the slots can't be "
        "reused: python -m tests.fitness.test_no_new_datetime_min --write-baseline\n"
        + "\n".join(f"  {n}: {a} -> {f}" for n, (a, f) in sorted(shrunk.items()))
    )


def _plant(root, name, source):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize("source", [
    "from datetime import datetime\nx = datetime.min\n",
    "from datetime import datetime, timezone\nx = datetime.min.replace(tzinfo=timezone.utc)\n",
    "from datetime import datetime as dt\nx = dt.min\n",
    "import datetime\nx = datetime.datetime.min\n",
    "import datetime as _dt\nx = _dt.datetime.min\n",
])
@pytest.mark.parametrize("top", SCANNED_DIRS)
def test_a_planted_new_use_is_caught(tmp_path, source, top):
    _plant(tmp_path, f"{top}/new_module.py", source)
    grown, _ = compare(scan(tmp_path), {})
    assert grown == {f"{top}/new_module.py": (0, 1)}


def test_one_more_use_in_a_baselined_file_is_caught(tmp_path):
    _plant(tmp_path, "cps/old.py",
           "from datetime import datetime\na = datetime.min\nb = datetime.min\n")
    grown, shrunk = compare(scan(tmp_path), {"cps/old.py": 1})
    assert grown == {"cps/old.py": (1, 2)}
    assert shrunk == {}


def test_a_removed_use_must_lower_the_baseline(tmp_path):
    _plant(tmp_path, "cps/old.py", "x = None\n")
    grown, shrunk = compare(scan(tmp_path), {"cps/old.py": 2})
    assert grown == {}
    assert shrunk == {"cps/old.py": (2, 0)}


@pytest.mark.parametrize("source", [
    # Text that mentions datetime.min is not a read.
    "# reset the cursor to datetime.min\nNOTE = 'datetime.min'\n",
    # Other types' .min, and min() the builtin.
    "from datetime import date, time, timedelta\nx = (date.min, time.min, timedelta.min)\n",
    "import datetime\nx = datetime.date.min\n",
    "values = [3, 1]\nx = min(values)\n",
    # A local object that happens to have a .min attribute.
    "class Range:\n    min = 0\nx = Range.min\n",
])
def test_things_that_are_not_datetime_min_are_not_counted(tmp_path, source):
    _plant(tmp_path, "cps/fine.py", source)
    assert scan(tmp_path) == {}


def test_files_outside_cps_and_scripts_are_not_scanned(tmp_path):
    _plant(tmp_path, "tests/unit/test_cursor.py",
           "from datetime import datetime\nx = datetime.min\n")
    assert scan(tmp_path) == {}


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-baseline"]:
        sys.exit("usage: python -m tests.fitness.test_no_new_datetime_min --write-baseline")
    write_baseline(BASELINE, scan(REPO_ROOT))
    print(f"wrote {BASELINE}")
