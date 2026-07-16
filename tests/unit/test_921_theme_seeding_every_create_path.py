# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#921: every account-creation path must seed the admin's configured theme.

#736 was "the admin theme picker is inert". #918 routed three create paths
through ``config_theme_code()`` and pinned them with a hand-maintained tuple in
``test_theme_registry.py`` whose docstring said, of adding a create path without
listing it, "that is fine". It is not fine: the tuple omitted
``cps/api/auth.py``, so #921 shipped -- the New UI's own signup path kept copying
``config_theme`` raw -- while the pin stayed green.

Auditing the whole subsystem rather than only the reported path found the same
drift in three more places, two of them wider than #921 itself:

* ``cps/api/auth.py``   -- raw copy. Wrong only at ``config_theme == 0``.
* ``cps/oauth_bb.py``   -- hardcoded ``1``. Wrong at every non-dark choice.
* ``cps/usermanagement.py`` -- hardcoded ``1``. Same.
* ``cps/admin.py`` (LDAP import) -- seeds nothing, so the column default (dark)
  wins. Same.

The two hardcodes are stale upstream code: ``1a5a8bb95`` ("Enforce dark theme
and migrate users at startup") landed when light really was deprecated, and
``a9143e873`` (#845) reintroduced six themes without revisiting them.

So this guard does not take a list on trust. It AST-enumerates every
``ub.User()`` construction under ``cps/`` and requires each one to reach
``config_theme_code()`` -- directly, or through one hop of delegation. A new
create path fails here until it is classified, which is the inversion of the
contract that let #921 through.
"""

import ast
from pathlib import Path

import pytest

# CI selects with `pytest -m "smoke or unit"` (.github/workflows/tests.yml), so an
# unmarked file is collected and then deselected — it never gates anything. The
# #918 guard this one replaces was unmarked, which is why a green CI could coexist
# with #921 shipping. Without this line the guard below is decoration.
pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).parents[2]
CPS_ROOT = REPO_ROOT / "cps"


def _is_ub_user_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "User"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ub"
    )


def _parents(tree):
    table = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            table[child] = parent
    return table


def _enclosing_function(node, parents):
    walker = parents.get(node)
    while walker is not None and not isinstance(walker, (ast.FunctionDef, ast.AsyncFunctionDef)):
        walker = parents.get(walker)
    return walker


def _functions_by_name(tree):
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _theme_assignments(func, var_name):
    """`<var_name>.theme = <expr>` anywhere inside ``func``."""
    found = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "theme"
                and isinstance(target.value, ast.Name)
                and target.value.id == var_name
            ):
                found.append(node.value)
    return found


def _mentions_config_theme_code(expr):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "config_theme_code"
        for node in ast.walk(expr)
    )


def _delegate_seeds_theme(func, var_name, functions):
    """The create site may hand the fresh user to a helper that seeds it --
    ``new_user()`` passes ``content`` to ``_handle_new_user()``. Resolve exactly
    one hop, by name, within the same module."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        passes_user = any(
            isinstance(arg, ast.Name) and arg.id == var_name for arg in node.args
        )
        if not passes_user:
            continue
        callee_name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        callee = functions.get(callee_name)
        if callee is None:
            continue
        for param in callee.args.args:
            for expr in _theme_assignments(callee, param.arg):
                if _mentions_config_theme_code(expr):
                    return True
    return False


def _create_sites():
    sites = []
    for py in sorted(CPS_ROOT.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        parents = _parents(tree)
        functions = _functions_by_name(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and _is_ub_user_call(node.value)):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            func = _enclosing_function(node, parents)
            if func is None:
                continue
            sites.append(
                {
                    "path": py.relative_to(REPO_ROOT).as_posix(),
                    "func": func.name,
                    "var": target.id,
                    "node": func,
                    "functions": functions,
                }
            )
    return sites


CREATE_SITES = _create_sites()

# Every path that builds a `ub.User()`. This is asserted to be EXHAUSTIVE below,
# so a new create path breaks the build until someone puts it here deliberately.
EXPECTED_CREATE_PATHS = {
    ("cps/admin.py", "new_user"): "classic admin form (delegates to _handle_new_user)",
    ("cps/admin.py", "ldap_import_create_user"): "LDAP user import",
    ("cps/api/admin.py", "admin_create_user"): "New UI admin form",
    ("cps/api/auth.py", "auth_register"): "New UI public self-registration (#921)",
    ("cps/oauth_bb.py", "register_user_from_generic_oauth"): "OAuth auto-provisioning",
    ("cps/usermanagement.py", "create_authenticated_user"): "external/proxy auth auto-provisioning",
    ("cps/web.py", "register_post"): "classic public self-registration",
}


def test_the_create_path_registry_is_exhaustive():
    """#918's pin trusted a hand-written list and silently skipped what it did
    not name. Enumerate from the AST instead, so an unclassified create path is
    a failing test rather than a user on the wrong theme."""
    actual = {(site["path"], site["func"]) for site in CREATE_SITES}
    expected = set(EXPECTED_CREATE_PATHS)

    assert actual == expected, (
        "the set of ub.User() create paths changed.\n"
        "  new/unclassified: %s\n"
        "  gone/renamed:     %s\n"
        "Every create path must seed the admin's configured theme; add it to "
        "EXPECTED_CREATE_PATHS and make it call config_theme_code()."
        % (sorted(actual - expected), sorted(expected - actual))
    )


@pytest.mark.parametrize(
    "path, func",
    sorted(EXPECTED_CREATE_PATHS),
    ids=lambda value: value if isinstance(value, str) else str(value),
)
def test_every_create_path_seeds_the_configured_theme(path, func):
    """A new account must inherit Admin -> Theme, however it was created.

    Failure modes this pins, all of which were live when it was written:
      raw copy  -- `content.theme = config.config_theme` turns a legacy 0
                   (light) into a User.theme of 0, which reads back dark.
      hardcode  -- `user.theme = 1` ignores the admin entirely.
      omission  -- no assignment at all, so Column(default=1) decides.
    """
    site = next(
        s for s in CREATE_SITES if s["path"] == path and s["func"] == func
    )

    direct = _theme_assignments(site["node"], site["var"])
    if direct:
        for expr in direct:
            assert _mentions_config_theme_code(expr), (
                "%s:%s seeds a theme without normalising through "
                "config_theme_code() -- `%s.theme = %s`. A raw config_theme "
                "(legacy 0 = light) becomes a User.theme of 0, which reads back "
                "as dark; a hardcoded literal ignores the admin's choice."
                % (path, func, site["var"], ast.unparse(expr))
            )
        return

    assert _delegate_seeds_theme(site["node"], site["var"], site["functions"]), (
        "%s:%s creates a user but never seeds .theme, and hands it to no helper "
        "that does. The column default (dark) then overrides whatever the admin "
        "configured." % (path, func)
    )


def test_self_service_theme_choice_is_not_normalised_through_config_theme_code():
    """The mirror-image mistake. `PATCH /api/v1/account` is a user picking their
    OWN theme from a validated slug, so it must use theme_code() -- pushing it
    through config_theme_code() would re-read their choice as an instance
    default. Seeding and self-service are different operations."""
    source = (REPO_ROOT / "cps/api/account.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assignments = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr == "theme"
    ]
    assert assignments, "account.py no longer assigns .theme — did self-service move?"
    for expr in assignments:
        assert not _mentions_config_theme_code(expr), (
            "account.py routes a user's own theme choice through "
            "config_theme_code(): `%s`" % ast.unparse(expr)
        )
