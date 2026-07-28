# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The tests/ tree must never shadow an installed distribution.

`tests/docker/` is a directory named after a real PyPI distribution
(`docker`, the Docker SDK). For as long as `tests/` itself sat on
`sys.path`, every directory under it was importable as a *top-level*
module, so `import docker` resolved to `tests/docker/__init__.py`
instead of the SDK. Anything reaching for a submodule then died with a
misleading `ModuleNotFoundError: No module named 'docker.context'` —
`import docker` had succeeded, just against the wrong package.

That is what killed the Docker integration suite: testcontainers' compose
backend imports `docker.context`, so 80 of 118 integration tests errored
at fixture setup. The job is `continue-on-error` outside tier-2 PRs, so
the summary still reported green and the breakage stayed invisible.

These tests pin the structural invariant instead of that one collision,
and they live in the unit lane on purpose — Fast Tests is a hard merge
gate, so the next name clash fails a PR rather than quietly disabling a
suite. See #1017 for the related "integration tests don't run" class.
"""

import ast
import importlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TESTS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_ROOT.parent


def _package_dirs_under_tests():
    """Directories under tests/ that are importable as a package."""
    return sorted(
        p for p in TESTS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith((".", "__"))
    )


def test_tests_root_is_not_on_sys_path():
    """tests/ on sys.path makes every subdirectory a top-level module.

    Helpers under tests/ are imported as `tests.<name>`, which needs only
    the repo root on sys.path. Putting tests/ itself there is what lets
    tests/docker/ outrank the installed Docker SDK.
    """
    on_path = [entry for entry in sys.path if entry and Path(entry).resolve() == TESTS_ROOT]
    assert not on_path, (
        "tests/ is on sys.path (%r), so every directory under it shadows any "
        "installed distribution of the same name. Import tests/ helpers as "
        "`from tests.<module> import ...` instead of inserting TESTS_ROOT."
        % on_path
    )


def test_no_test_directory_shadows_an_installed_distribution():
    """`import <name>` must never resolve inside the tests/ tree.

    Deliberately one test that loops rather than a parametrised case per
    directory: the parameter list would be read off the filesystem at
    collection time, and under `pytest -n auto` any churn in the tree
    between workers desynchronises collection and aborts the whole run.
    """
    shadowed = []
    for package_dir in _package_dirs_under_tests():
        spec = importlib.util.find_spec(package_dir.name)
        if spec is None or not spec.origin:
            continue  # nothing installed under that name — no collision
        origin = Path(spec.origin).resolve()
        if TESTS_ROOT in origin.parents:
            shadowed.append((package_dir.name, str(origin)))

    assert not shadowed, (
        "%s resolve inside the tests tree. Any installed distribution of "
        "those names is now unreachable, and the failure surfaces as a "
        "misleading ModuleNotFoundError for one of its submodules deep "
        "inside a dependency." % shadowed
    )


class _InlineNames(ast.NodeTransformer):
    """Substitute `name -> assigned expression`, one level deep.

    Done on the AST rather than the unparsed text so that a local called
    `parent` cannot be spliced into an unrelated `.parent` attribute
    access — an ast.Attribute's `.attr` is not an ast.Name.
    """

    def __init__(self, assignments):
        self._assignments = assignments

    def visit_Name(self, node):
        replacement = self._assignments.get(node.id)
        return replacement if replacement is not None else node


def _sys_path_arguments(tree):
    """Every expression handed to sys.path.insert/append in a module."""
    assignments = {
        node.targets[0].id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    inline = _InlineNames(assignments)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("insert", "append"):
            continue
        if ast.unparse(node.func.value) != "sys.path":
            continue
        for arg in node.args:
            # `sys.path.insert(0, str(d))` where `d = Path(__file__).parent`
            # appears elsewhere in the module resolves to the full expression.
            resolved = inline.visit(ast.parse(ast.unparse(arg), mode="eval").body)
            yield node.lineno, ast.unparse(resolved)


def _names_the_tests_root(expr, source_file):
    """Does this sys.path expression resolve to tests/ for this file?"""
    if "TESTS_ROOT" in expr:
        return True

    # tests/unit/x.py -> ('unit', 'x.py'): two `.parent` hops, or parents[1].
    hops = len(source_file.relative_to(TESTS_ROOT).parts)

    base = expr.split("__file__", 1)[-1] if "__file__" in expr else ""
    if base and base.count(".parent") == hops and "parents" not in base:
        return True

    match = re.search(r"parents\[(\d+)\]", expr)
    return bool(match) and int(match.group(1)) == hops - 1


def test_no_test_module_puts_the_tests_root_on_sys_path():
    """Catch the anti-pattern statically, in the lane that gates merges.

    The dynamic checks above only see the lane they run in — which is how
    an insert in tests/integration/ survived the first pass of this fix.
    Reading every module in the tree means a reintroduction fails Fast
    Tests rather than quietly re-shadowing the Docker SDK inside a job
    that is advisory anyway.

    Resolves one level of local variable, so `d = Path(__file__).parent`
    followed by `sys.path.insert(0, str(d))` is caught; a more indirect
    spelling would slip through, and the dynamic checks above remain the
    backstop for that.

    Adding the repo root or scripts/ to sys.path is fine and common here —
    only the tests root is the problem.
    """
    offenders = []
    for source_file in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        try:
            source = source_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "sys.path" not in source:
            continue  # cheap prefilter — parsing every module costs ~19s
        try:
            tree = ast.parse(source, filename=str(source_file))
        except SyntaxError:
            continue  # not our invariant to enforce
        offenders += [
            (str(source_file.relative_to(REPO_ROOT)), lineno, expr)
            for lineno, expr in _sys_path_arguments(tree)
            if _names_the_tests_root(expr, source_file)
        ]

    assert not offenders, (
        "%s put the tests root on sys.path. That promotes every directory "
        "under tests/ to a top-level module, so tests/docker/ shadows the "
        "Docker SDK. Import siblings as `from tests.<module> import ...` "
        "instead." % offenders
    )


def test_testcontainers_compose_backend_imports():
    """The exact import the Docker integration fixtures perform.

    tests/conftest.py's `cwa_container` fixture opens with this import; when
    it raises, every Docker and integration test errors at setup rather than
    failing, which is how the suite went dark while CI stayed green.
    """
    pytest.importorskip(
        "testcontainers.compose",
        reason="testcontainers is an integration-lane dependency",
    )
    module = importlib.import_module("testcontainers.compose")
    assert hasattr(module, "DockerCompose")


def test_docker_sdk_is_the_installed_distribution():
    """`import docker` must reach the SDK, not tests/docker/."""
    docker = pytest.importorskip(
        "docker", reason="docker SDK arrives with testcontainers"
    )
    origin = Path(docker.__file__).resolve()
    assert TESTS_ROOT not in origin.parents, (
        "`import docker` resolved to %s — that is tests/docker/, not the "
        "Docker SDK." % origin
    )
    # The submodule testcontainers reaches for, and the one that broke.
    importlib.import_module("docker.context")
