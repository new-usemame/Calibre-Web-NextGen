# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork issue #1074 — a guest clicking "Read" got a 500.

``read_book()`` carried a redundant ``import json`` inside its
``if current_user.is_authenticated:`` branch. Python binds local-vs-global per
*name* for the whole function at compile time, so that one nested import turned
``json`` into a local for every line of the function. A signed-in user executed
the import and never noticed; a guest skipped the branch and then hit the
unconditional ``json.dumps(reader_settings)`` at the end of the epub path::

    UnboundLocalError: cannot access local variable 'json' where it is not
    associated with a value

Two tests, two altitudes:

* the mechanism, pinned exactly — ``json`` must resolve as a *global* in the
  affected functions, which is false the moment anyone re-adds a local import;
* the class, swept repo-wide — no function anywhere in ``cps/`` may shadow a
  module-level import from inside a conditional and then use that name on a path
  which skips the import. That is the shape that shipped this bug, and it is
  invisible to every test that only exercises the signed-in path.
"""
import ast
import inspect
import os
import pytest

CPS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cps")


def _function_source(path, func_name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name}() not found in {path}")


def _local_import_names(func_node):
    """Names bound by an ``import`` *inside* func_node's own scope.

    Nested defs are skipped: their imports bind in their own scope, not this one,
    so counting them would flag functions that are perfectly correct.
    """
    names = set()
    stack = list(func_node.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        stack.extend(ast.iter_child_nodes(node))
    return names


# ── The mechanism ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_read_book_resolves_json_as_a_global():
    """The guest path reaches ``json.dumps`` without executing any import.

    ``co_varnames`` is the compiler's own answer to "is this name local?", so it
    is the exact quantity that broke. A local ``import json`` anywhere in the
    function puts ``json`` in here and re-breaks every guest.
    """
    from cps import web
    # read_book is wrapped twice (@login_required_if_no_ano, @viewer_required);
    # inspect.unwrap follows the whole chain to the function that owns the bug.
    code = inspect.unwrap(web.read_book).__code__
    assert "json" not in code.co_varnames, (
        "read_book() binds 'json' locally — a nested 'import json' is back. "
        "A guest skips the authenticated branch and then UnboundLocalErrors on "
        "json.dumps(reader_settings). Use the module-level import (cps/web.py:8)."
    )
    assert "json" in code.co_names, "read_book() no longer resolves 'json' globally"


@pytest.mark.unit
@pytest.mark.parametrize("func_name", ["read_book", "login_post"])
def test_web_functions_do_not_shadow_the_module_level_json(func_name):
    """No function-local ``import json`` in the two functions that had one.

    ``login_post`` never actually broke — both of its copies sat immediately
    above their only use — but it is the same trap one edit away from firing,
    and ``cps/web.py`` already imports json at module scope.
    """
    node = _function_source(os.path.join(CPS_ROOT, "web.py"), func_name)
    assert "json" not in _local_import_names(node), (
        f"{func_name}() re-imports json locally; cps/web.py imports it at module "
        f"scope already, and the local copy shadows it for the whole function."
    )


# ── The class ────────────────────────────────────────────────────────────────

def _conditionally_shadowed_uses(path):
    """Yield (func, name, import_lines, unguarded_use_lines) for the bug shape.

    The shape: a name is imported at module scope, re-imported inside a
    conditional (``if`` / ``try`` / loop) in some function, and *used* in that
    same function on a line outside every such conditional. Whoever takes the
    branch is fine; whoever skips it gets an UnboundLocalError.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    module_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_level.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                module_level.add(alias.asname or alias.name)

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        guarded = {}   # name -> list of (import_node, innermost_conditional)

        def descend(node, enclosing):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(child, (ast.Import, ast.ImportFrom)) and enclosing is not None:
                    for alias in child.names:
                        name = (alias.asname or (alias.name.split(".")[0]
                                if isinstance(child, ast.Import) else alias.name))
                        guarded.setdefault(name, []).append((child, enclosing))
                nxt = child if isinstance(child, (ast.If, ast.Try, ast.While, ast.For)) else enclosing
                descend(child, nxt)

        descend(func, None)

        uses = {}
        stack = list(func.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                uses.setdefault(node.id, []).append(node.lineno)
            stack.extend(ast.iter_child_nodes(node))

        for name, entries in guarded.items():
            if name not in module_level or name not in uses:
                continue
            spans = []
            for imp, block in entries:
                end = max((getattr(n, "lineno", block.lineno) for n in ast.walk(block)),
                          default=block.lineno)
                spans.append((imp.lineno, end))
            unguarded = sorted({ln for ln in uses[name]
                                if not any(lo <= ln <= hi for lo, hi in spans)})
            if unguarded:
                yield (func.name, name, sorted(imp.lineno for imp, _ in entries), unguarded)


@pytest.mark.unit
def test_no_conditionally_shadowed_module_import_in_cps():
    """Sweep every module under ``cps/`` for the shape that produced #1074."""
    offenders = []
    for root, dirs, files in os.walk(CPS_ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "translations", "static")]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                hits = list(_conditionally_shadowed_uses(path))
            except SyntaxError:
                continue
            rel = os.path.relpath(path, os.path.dirname(CPS_ROOT))
            for func, sym, imports, used in hits:
                offenders.append(
                    f"{rel}:{func}() imports '{sym}' inside a conditional at "
                    f"{imports} but uses it unguarded at {used}")

    assert not offenders, (
        "A conditional re-import shadows a module-level import for the whole "
        "function; any path that skips the conditional raises UnboundLocalError "
        "(fork #1074). Drop the local import and use the module-level one:\n  "
        + "\n  ".join(offenders))
