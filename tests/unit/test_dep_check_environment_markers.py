# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression pins for environment-marker handling in ``dep_check`` (fork #1442).

``load_dependencies`` filters ``requires()`` output by whether an entry carries an
``extra`` marker, which splits required from optional. Nothing evaluated the OTHER
kind of marker -- the environment ones (``sys_platform``, ``python_version``) that
say whether a requirement applies to this interpreter at all. ``pyproject.toml``
ships three: ``iso-639;python_version<'3.12'``, ``pycountry;python_version>='3.12'``
and ``python-magic-bin;sys_platform=='win32'``.

So on a Linux/Py3.12 image two entries that were never meant to be installed here
came back reading ``not installed``. The About page compensated at the display
layer by dropping every ``not installed`` row -- which also hid the rows that were
genuinely missing.

That mattered more once ``fb3c7e4c8`` removed the startup dependency check: before
it, a missing dependency printed ``please install tabulate`` and exited 8. After
it, with the About row suppressed too, a missing dependency produced no startup
error, no About row, and an ImportError wherever the first import happened to sit.
Each change was reasonable alone; together they turned a loud failure into a
silent one.

Evaluating the marker inside ``load_dependencies`` fixes both halves at the
source: an inapplicable requirement is dropped where it is read, so
``not installed`` means genuinely missing again and the About suppression comes
back out.

The markers below are deterministic rather than host-dependent on purpose -- a
test that asserts "win32 is filtered" only means anything on a non-Windows runner,
and silently changes meaning if the runner ever changes.
"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

CPS = Path(__file__).resolve().parents[2] / "cps"
DEP_CHECK = CPS / "dep_check.py"
ABOUT = CPS / "about.py"

# Always False, on every platform and interpreter.
MARKER_FALSE = 'sys_platform == "definitely-not-a-real-platform"'
# Always True, on every platform and interpreter (the negation of the above).
MARKER_TRUE = 'sys_platform != "definitely-not-a-real-platform"'


def _load_dep_check():
    """Import cps/dep_check.py standalone -- it has no intra-package imports."""
    spec = importlib.util.spec_from_file_location("_cwa_dep_check_under_test", DEP_CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dep_check(monkeypatch):
    """dep_check with ``requires``/``version`` driven by the test, not the venv."""
    mod = _load_dep_check()

    state = {"requirements": [], "installed": {}}

    def fake_requires(_name):
        return list(state["requirements"])

    def fake_version(name):
        try:
            return state["installed"][name]
        except KeyError:
            raise mod.PackageNotFoundError(name)

    monkeypatch.setattr(mod, "requires", fake_requires)
    monkeypatch.setattr(mod, "version", fake_version)
    mod._state = state
    return mod


def _names(deps):
    return [req.name for _version, req in deps]


def _by_name(deps):
    return {req.name: found for found, req in deps}


def test_inapplicable_environment_marker_is_dropped_not_reported_missing(dep_check):
    """The #1442 bug: a requirement that does not apply here read 'not installed'.

    ``python-magic-bin;sys_platform=='win32'`` is not missing on Linux -- it was
    never meant to be installed. Reporting it as missing is what forced the About
    page to hide every 'not installed' row.
    """
    dep_check._state["requirements"] = [
        "flask>=1.0.2",
        f"windows-only-package>=1.0;{MARKER_FALSE}",
    ]
    dep_check._state["installed"] = {"flask": "3.1.0"}

    deps = dep_check.load_dependencies(False)

    assert "windows-only-package" not in _names(deps), (
        "a requirement whose environment marker does not apply to this interpreter "
        "must be dropped at the source, not surfaced as 'not installed'"
    )
    assert _by_name(deps) == {"flask": "3.1.0"}


def test_applicable_environment_marker_is_kept(dep_check):
    """The filter must drop only what does not apply -- not every marked entry."""
    dep_check._state["requirements"] = [
        f"pycountry>=24.6.1;{MARKER_TRUE}",
    ]
    dep_check._state["installed"] = {"pycountry": "24.6.1"}

    deps = dep_check.load_dependencies(False)

    assert _by_name(deps) == {"pycountry": "24.6.1"}


def test_genuinely_missing_dependency_still_reports_not_installed(dep_check):
    """The guard against over-filtering: 'not installed' has to keep its meaning.

    An unmarked requirement that really is absent is the case the About page needs
    to show. If this ever goes green by disappearing from the list instead, the fix
    has traded one silent failure for another.
    """
    dep_check._state["requirements"] = ["tabulate>=0.9.0", "flask>=1.0.2"]
    dep_check._state["installed"] = {"flask": "3.1.0"}

    deps = dep_check.load_dependencies(False)

    assert _by_name(deps) == {"tabulate": "not installed", "flask": "3.1.0"}


def test_missing_dependency_that_does_apply_here_is_reported(dep_check):
    """A marked requirement that DOES apply and is absent is still genuinely missing."""
    dep_check._state["requirements"] = [f"pycountry>=24.6.1;{MARKER_TRUE}"]
    dep_check._state["installed"] = {}

    deps = dep_check.load_dependencies(False)

    assert _by_name(deps) == {"pycountry": "not installed"}


def test_extra_marked_requirements_are_split_off_the_required_side(dep_check):
    """extra== entries belong to the optional side and must not count as required."""
    dep_check._state["requirements"] = [
        "flask>=1.0.2",
        'pytest>=7.4.0;extra == "dev"',
    ]
    dep_check._state["installed"] = {"flask": "3.1.0", "pytest": "8.0.0"}

    assert _names(dep_check.load_dependencies(False)) == ["flask"]
    assert _names(dep_check.load_dependencies(True)) == ["pytest"]


def test_extra_marked_requirements_survive_marker_evaluation(dep_check):
    """Regression guard on the fix itself.

    ``extra`` is undefined in a bare marker environment, so evaluating an
    ``extra ==`` marker raises or reads False. If the new marker check ran over
    extras too, the optional list would empty out.
    """
    dep_check._state["requirements"] = [
        'pytest>=7.4.0;extra == "dev"',
        'black>=23.0.0;extra == "dev"',
    ]
    dep_check._state["installed"] = {"pytest": "8.0.0", "black": "24.1.0"}

    deps = dep_check.load_dependencies(True)

    assert _by_name(deps) == {"pytest": "8.0.0", "black": "24.1.0"}


def test_requires_returning_none_does_not_crash(dep_check):
    """``requires()`` returns None for a package with no declared dependencies."""
    dep_check._state["requirements"] = []
    monkey = lambda _name: None  # noqa: E731
    dep_check.requires = monkey

    assert dep_check.load_dependencies(False) == []


def test_about_page_does_not_suppress_not_installed_rows():
    """The display-layer half: About must show a genuinely missing dependency.

    Source-pinned rather than imported -- cps/about.py builds its module table at
    import time and pulls in the whole app to do it. The invariant is refactor-
    fragile and cheap to state: nothing in about.py may branch on the literal
    'not installed' to skip a row.
    """
    tree = ast.parse(ABOUT.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for const in ast.walk(node)
        if isinstance(const, ast.Constant) and const.value == "not installed"
    ]

    assert not offenders, (
        "cps/about.py compares against 'not installed' at line(s) "
        f"{offenders}; suppressing those rows hides genuinely missing "
        "dependencies, which is the #1442 silent-failure bug"
    )
