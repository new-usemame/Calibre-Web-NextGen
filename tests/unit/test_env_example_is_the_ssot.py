"""Keep ``examples/.env.example`` in sync with environment-variable reads.

Parsing is deliberately static and narrow enough to avoid treating arbitrary mappings as
the process environment:

* Python: ``os.environ[KEY]``, ``os.environ.get(KEY)``, ``os.getenv(KEY)``,
  ``from os import getenv`` aliases, and the small helper-wrapper list below.
* s6: uppercase ``$VAR``/``${VAR}`` expansions and ``printcontenv VAR`` in the
  extensionless files below ``root/etc/s6-overlay``. Uppercase variables assigned by
  the same script are locals, except ``VAR=${VAR:-default}``, which reads inherited
  configuration while assigning its local default. ``WATCH_FOLDER`` is an explicit
  compatibility input in the ingest service even though that service conditionally
  assigns the same name after checking the inherited value.
* frontend: explicit ``process.env.KEY`` reads in JavaScript/TypeScript sources.

Dynamic Python reads must go through a helper named in ``PYTHON_ENV_HELPERS``. A new
wrapper therefore fails this test until its call seam is reviewed and declared here;
that is intentional, because silently ignoring a dynamic lookup would defeat the SSOT.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / ".env.example"
PYTHON_ROOTS = (ROOT / "cps", ROOT / "scripts")
S6_ROOT = ROOT / "root" / "etc" / "s6-overlay"
FRONTEND_ROOT = ROOT / "frontend"

# name -> (argument index containing the environment key, files defining/calling it)
PYTHON_ENV_HELPERS = {
    "_configured_dir": (1, frozenset({"cps/constants.py", "scripts/app_paths.py"})),
    "_env_path": (0, frozenset({"scripts/app_paths.py"})),
    "_get_ingest_owner_id": (0, frozenset({"cps/editbooks.py"})),
    "environment_flag_enabled": (0, frozenset({"cps/sqlite_utils.py"})),
}

# Dynamic process-environment reads implemented by the helpers above. The source call
# is ignored only inside these exact functions; their statically named call sites are
# collected through PYTHON_ENV_HELPERS.
DYNAMIC_PYTHON_READERS = frozenset(
    {
        ("cps/constants.py", "_configured_dir"),
        ("cps/editbooks.py", "_get_ingest_owner_id"),
        ("cps/sqlite_utils.py", "environment_flag_enabled"),
        ("scripts/app_paths.py", "_configured_dir"),
        ("scripts/app_paths.py", "_env_path"),
    }
)

# These names are intentionally outside the deployment SSOT. Keep this list short:
# each exception must identify the external runtime/tool that owns the value.
SSOT_EXCEPTIONS = {
    "CALIBRE_CONFIG_DIRECTORY": (
        "written by CWNG only into opted-in Calibre child processes, then consumed "
        "by Calibre rather than read from CWNG's launch environment"
    ),
    "CI": "provided and interpreted by the CI/Playwright toolchain",
    "CONFIG_DIR": (
        "an examples/.env.example interpolation helper, not a CWNG process input"
    ),
    "LISTEN_FDS": "provided by systemd's socket-activation protocol",
    "NODE_ENV": "interpreted implicitly by the Node/Vite toolchain",
    "QUERY_CLIENT_TEST_PRE_FIX": "an internal frontend unit-test branch selector",
    "SECRET": "injected transiently by the operator's secret broker into measurement tools",
}

SHELL_ENV_COMPAT_READS = frozenset({"WATCH_FOLDER"})

EXAMPLE_ASSIGNMENT = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
SHELL_REFERENCE = re.compile(r"\$(?:\{([A-Z][A-Z0-9_]*)[^}]*\}|([A-Z][A-Z0-9_]*))")
SHELL_ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*)\s*=")
PRINTCONTENV = re.compile(r"\bprintcontenv\s+([A-Z][A-Z0-9_]*)\b")
FRONTEND_ENV = re.compile(r"\bprocess\.env\.([A-Z][A-Z0-9_]*)\b")


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = (statement.target,)
            value = statement.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


class _PythonEnvVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, tree: ast.Module) -> None:
        self.path = path
        self.relative_path = _relative(path)
        self.constants = _module_string_constants(tree)
        self.os_aliases = {"os"}
        self.getenv_aliases: set[str] = set()
        self.function_stack: list[str] = []
        self.reads: dict[str, set[str]] = {}
        self.unresolved: list[str] = []

        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name == "os":
                        self.os_aliases.add(alias.asname or alias.name)
            elif isinstance(statement, ast.ImportFrom) and statement.module == "os":
                for alias in statement.names:
                    if alias.name == "getenv":
                        self.getenv_aliases.add(alias.asname or alias.name)

    def _resolve_key(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        return None

    def _record(self, node: ast.AST, key_node: ast.AST, seam: str) -> None:
        key = self._resolve_key(key_node)
        location = f"{self.relative_path}:{node.lineno}"
        if key is not None:
            self.reads.setdefault(key, set()).add(location)
            return
        current_function = (
            self.function_stack[-1] if self.function_stack else "<module>"
        )
        if (self.relative_path, current_function) in DYNAMIC_PYTHON_READERS:
            return
        self.unresolved.append(
            f"{location} uses dynamic {seam} key {ast.unparse(key_node)!r}"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if node.args:
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "getenv"
                and isinstance(function.value, ast.Name)
                and function.value.id in self.os_aliases
            ):
                self._record(node, node.args[0], "os.getenv")
            elif isinstance(function, ast.Name) and function.id in self.getenv_aliases:
                self._record(node, node.args[0], "os.getenv alias")
            elif (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and isinstance(function.value, ast.Attribute)
                and function.value.attr == "environ"
                and isinstance(function.value.value, ast.Name)
                and function.value.value.id in self.os_aliases
            ):
                self._record(node, node.args[0], "os.environ.get")
            elif (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and isinstance(function.value, ast.Name)
                and function.value.id in {"environ", "_dirs_environ", "source"}
                and (self.relative_path, self.function_stack[-1])
                in {
                    ("cps/constants.py", "_configured_dir"),
                    (
                        "cps/services/kobo_annotation_stage0.py",
                        "emergency_override_disables",
                    ),
                    ("cps/services/kobo_exchange_capture.py", "enabled"),
                    ("cps/sqlite_utils.py", "environment_flag_enabled"),
                }
            ):
                self._record(node, node.args[0], f"{function.value.id}.get")
            elif isinstance(function, ast.Name) and function.id in PYTHON_ENV_HELPERS:
                argument_index, paths = PYTHON_ENV_HELPERS[function.id]
                if self.relative_path in paths and len(node.args) > argument_index:
                    self._record(
                        node,
                        node.args[argument_index],
                        f"{function.id} helper",
                    )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in self.os_aliases
        ):
            self._record(node, node.slice, "os.environ[]")
        self.generic_visit(node)


def _python_env_reads() -> tuple[dict[str, set[str]], list[str]]:
    reads: dict[str, set[str]] = {}
    unresolved: list[str] = []
    for source_root in PYTHON_ROOTS:
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _PythonEnvVisitor(path, tree)
            visitor.visit(tree)
            unresolved.extend(visitor.unresolved)
            for key, locations in visitor.reads.items():
                reads.setdefault(key, set()).update(locations)
    return reads, unresolved


def _shell_env_reads() -> dict[str, set[str]]:
    reads: dict[str, set[str]] = {}
    for path in sorted(
        candidate for candidate in S6_ROOT.rglob("*") if candidate.is_file()
    ):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        active_lines = [line for line in lines if not line.lstrip().startswith("#")]
        assignments = {
            match.group(1)
            for line in active_lines
            for match in SHELL_ASSIGNMENT.finditer(line)
        }
        for lineno, line in enumerate(lines, 1):
            if line.lstrip().startswith("#"):
                continue
            assigned_on_line = {
                match.group(1) for match in SHELL_ASSIGNMENT.finditer(line)
            }
            names = {
                match.group(1) or match.group(2)
                for match in SHELL_REFERENCE.finditer(line)
            }
            names.update(match.group(1) for match in PRINTCONTENV.finditer(line))
            for name in names:
                # A self-reference while assigning (FOO=${FOO:-x}) consumes the
                # inherited environment. Other variables assigned in this script
                # are local implementation details, even when referenced earlier.
                if (
                    name in assignments
                    and name not in assigned_on_line
                    and name not in SHELL_ENV_COMPAT_READS
                ):
                    continue
                reads.setdefault(name, set()).add(f"{_relative(path)}:{lineno}")
    return reads


def _frontend_env_reads() -> dict[str, set[str]]:
    reads: dict[str, set[str]] = {}
    extensions = frozenset({".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"})
    for path in sorted(FRONTEND_ROOT.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in extensions
            or "node_modules" in path.parts
        ):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in FRONTEND_ENV.finditer(line):
                reads.setdefault(match.group(1), set()).add(
                    f"{_relative(path)}:{lineno}"
                )
    return reads


def _merge_reads(*inventories: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for inventory in inventories:
        for key, locations in inventory.items():
            merged.setdefault(key, set()).update(locations)
    return merged


def _format_keys(keys: set[str], reads: dict[str, set[str]]) -> str:
    return "\n".join(
        f"  - {key}: {', '.join(sorted(reads.get(key, {'no in-scope reader'})))}"
        for key in sorted(keys)
    )


def test_env_example_is_the_single_source_of_truth() -> None:
    python_reads, unresolved = _python_env_reads()
    assert not unresolved, (
        "Environment reads with dynamic keys need a reviewed helper seam:\n"
        + "\n".join(f"  - {entry}" for entry in unresolved)
    )

    reads = _merge_reads(python_reads, _shell_env_reads(), _frontend_env_reads())
    documented_list = EXAMPLE_ASSIGNMENT.findall(EXAMPLE.read_text(encoding="utf-8"))
    duplicates = {key for key in documented_list if documented_list.count(key) > 1}
    documented = set(documented_list)

    missing = set(reads) - documented - set(SSOT_EXCEPTIONS)
    unused = documented - set(reads) - set(SSOT_EXCEPTIONS)

    failures = []
    if duplicates:
        failures.append(
            "Duplicate keys in examples/.env.example:\n"
            + _format_keys(duplicates, reads)
        )
    if missing:
        failures.append(
            "Keys read by code but missing from examples/.env.example:\n"
            + _format_keys(missing, reads)
        )
    if unused:
        failures.append(
            "Keys in examples/.env.example with no in-scope reader:\n"
            + _format_keys(unused, reads)
        )
    assert not failures, "\n\n".join(failures)
