# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1438 replaced a hardcoded path with a constant it never imported.

"Remove hardcoded /app paths from cps/" rewrote ``cps/web.py``::

    -    with open('/app/calibre-web-automated/dirs.json', 'r') as f:
    +    with open(DIRS_JSON, 'r') as f:

without adding ``from .constants import DIRS_JSON``. The name appeared exactly
once in the file — as a use. Nothing failed at import time, because a global is
resolved when the line *runs*, so the container booted and served pages.

What it broke was everything downstream of ``cwa_get_library_location()``, and
both callers bury the failure in a bare ``except``::

    _probe_metadata_db()          -> except Exception: return False
    cwa_get_num_books_in_library() -> except Exception: return 0

So ``/health`` computed ``db_up=False`` and answered 503 ``degraded`` forever
while reporting every service "up", the Docker HEALTHCHECK went permanently
unhealthy, and the library book count rendered 0. The suite that boots the
container caught it; the summary gate reported that failure as a pass.

Two tests, two altitudes:

* the mechanism, pinned exactly — ``DIRS_JSON`` must resolve as a global in
  ``cps/web.py``, which is false the moment anyone uses it without importing it;
* the class, swept repo-wide — no function anywhere in ``cps/`` may reference a
  module-level global that the module never binds. That is the shape that
  shipped this bug, and it is invisible to every test that imports a module
  without calling the specific function that names the missing global.
"""
import ast
import builtins
import dis
import os
import types

import pytest

CPS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cps")

# Always present in a module namespace at runtime, never statically "bound".
MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__path__",
}


def _iter_code(code):
    """Yield ``code`` and every nested code object (functions, comprehensions)."""
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _iter_code(const)


def _module_bound_names(source, path):
    """Names the module binds at any scope, discovered statically.

    Deliberately over-inclusive: every import, def, class, assignment target
    and ``global`` declaration counts. A false *negative* here just means the
    sweep stays quiet, so over-inclusion keeps this test from going flaky on
    dynamic-but-legitimate binding patterns.
    """
    names = set()
    tree = ast.parse(source, str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    names.add("*STAR*")
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


def _undefined_globals(path):
    """Return global names ``path`` loads at runtime but never binds.

    Uses ``LOAD_GLOBAL`` from the compiled bytecode rather than an AST name
    walk, so attribute accesses, locals, parameters and string annotations
    (a forward reference like ``"ExtractedCover | None"`` is never executed)
    do not register as uses.
    """
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    code = compile(source, str(path), "exec")
    bound = _module_bound_names(source, path)
    # A star-import can bind anything; the sweep cannot reason about it.
    if "*STAR*" in bound:
        return set()
    loaded = set()
    for block in _iter_code(code):
        for ins in dis.get_instructions(block):
            if ins.opname == "LOAD_GLOBAL" and isinstance(ins.argval, str):
                loaded.add(ins.argval)
    return {
        name for name in loaded
        if name not in bound
        and name not in MODULE_DUNDERS
        and not hasattr(builtins, name)
    }


def _cps_python_files():
    for dirpath, _dirnames, filenames in os.walk(CPS_ROOT):
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def test_web_module_resolves_dirs_json():
    """The exact regression: ``DIRS_JSON`` is used in web.py, so it must be bound.

    Red against the #1438 tree, where the constant was referenced but never
    imported and ``cwa_get_library_location()`` raised ``NameError`` on call.
    """
    path = os.path.join(CPS_ROOT, "web.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    assert "DIRS_JSON" in source, (
        "web.py no longer references DIRS_JSON — if the path lookup moved, move "
        "this test with it rather than deleting the guard."
    )
    assert "DIRS_JSON" not in _undefined_globals(path), (
        "cps/web.py uses DIRS_JSON without binding it. cwa_get_library_location() "
        "will raise NameError when called, and both callers swallow it — "
        "/health reports 'degraded' 503 forever and the library count renders 0. "
        "Add `from .constants import DIRS_JSON` (the import cps/cwa_functions.py "
        "already uses)."
    )


def test_library_location_reader_has_no_undefined_globals():
    """``cwa_get_library_location`` itself must not name an unbound global.

    Narrower than the sweep and independent of it: this stays meaningful even
    if the repo-wide test is ever scoped down, because this one function is
    what /health and the book count both route through.
    """
    path = os.path.join(CPS_ROOT, "web.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, path)
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "cwa_get_library_location"),
        None,
    )
    assert func is not None, "cwa_get_library_location() not found in cps/web.py"

    bound = _module_bound_names(source, path)
    code = compile(ast.Module(body=[func], type_ignores=[]), path, "exec")
    used = {
        ins.argval
        for block in _iter_code(code)
        for ins in dis.get_instructions(block)
        if ins.opname == "LOAD_GLOBAL" and isinstance(ins.argval, str)
    }
    missing = sorted(
        n for n in used
        if n not in bound and n not in MODULE_DUNDERS and not hasattr(builtins, n)
    )
    assert not missing, (
        f"cwa_get_library_location() references unbound global(s): {missing}. "
        "Its callers catch bare Exception, so this fails silently as a degraded "
        "health check and a zero book count rather than as an error."
    )


@pytest.mark.parametrize("path", sorted(_cps_python_files()), ids=lambda p: os.path.relpath(p, CPS_ROOT))
def test_no_undefined_module_globals(path):
    """Repo-wide: no module in ``cps/`` may load a global it never binds.

    This is the class, not the instance. A name that only resolves on a code
    path nobody exercises in unit tests is exactly how #1438 reached a release:
    import succeeded, the app served traffic, and the failure surfaced as a
    swallowed exception three layers away from the typo.
    """
    missing = sorted(_undefined_globals(path))
    assert not missing, (
        f"{os.path.relpath(path, CPS_ROOT)} loads global(s) it never binds: "
        f"{missing}. Import them, or bind them at module level — a bare "
        "NameError here surfaces wherever the caller happens to catch Exception."
    )
