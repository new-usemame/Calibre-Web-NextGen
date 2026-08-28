"""Keep ``examples/.env.example`` in sync with environment-variable reads.

The scan includes Python and frontend test trees: test-only launch controls are part of
the SSOT just like deployment controls. Parsing uses these deliberately reviewable seams:

* Python under ``cps/`` and ``scripts/``: the AST tracks names bound to ``os``,
  ``os.environ``, or ``os.getenv`` in every lexical scope. String-keyed mapping reads,
  ``get``/``setdefault``/``pop`` calls, and membership tests are collected. A dynamic
  key is an error unless it is inside one of the reviewed helper implementations below.
* s6 shell below ``root/etc/s6-overlay``: uppercase ``$VAR``/``${VAR}`` expansions and
  ``printcontenv VAR`` are scanned in source order. A reference before the first
  assignment in that file is inherited; references only after an assignment are local.
* JavaScript/TypeScript below ``frontend/``: a comment/string-aware tokenizer collects
  ``process.env.KEY``, literal ``process.env["KEY"]``/``['KEY']``, and object
  destructuring from ``process.env``. A computed bracket key or rest destructure is an
  unresolved read and fails with its file and line.

Dynamic Python reads must go through a helper named in ``PYTHON_ENV_HELPERS``. A new
wrapper therefore fails this test until its call seam is reviewed and declared here;
silently ignoring a dynamic lookup would defeat the SSOT.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple


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

# These names are intentionally outside the deployment SSOT. Each is owned by an
# external runtime/tool rather than accepted as CWNG configuration.
SSOT_EXCEPTIONS = {
    "CALIBRE_CONFIG_DIRECTORY": "written by CWNG for Calibre child processes; Calibre owns and consumes it",
    "CI": "provided and interpreted by the CI and Playwright runtimes",
    "CONFIG_DIR": "Compose interpolation helper; CWNG reads the resulting CALIBRE_DBPATH instead",
    "LISTEN_FDS": "provided by systemd's socket-activation protocol",
    "NODE_ENV": "provided and interpreted by the Node and Vite runtimes",
    "SECRET": "injected transiently by the operator's secret broker into measurement tools",
}

EXAMPLE_ASSIGNMENT = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
SHELL_REFERENCE = re.compile(r"\$(?:\{([A-Z][A-Z0-9_]*)[^}]*\}|([A-Z][A-Z0-9_]*))")
SHELL_ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*)\s*=")
PRINTCONTENV = re.compile(r"\bprintcontenv\s+([A-Z][A-Z0-9_]*)\b")


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class _Binding(NamedTuple):
    kind: str
    value: str | None = None


UNKNOWN = _Binding("unknown")
OS_MODULE = _Binding("os")
OS_ENVIRON = _Binding("environ")
OS_GETENV = _Binding("getenv")


class _PythonEnvVisitor(ast.NodeVisitor):
    """Resolve process-environment aliases while walking each lexical scope."""

    _ENVIRON_METHODS = frozenset(
        {"get", "setdefault", "pop", "__getitem__", "__contains__"}
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.relative_path = _relative(path)
        self.scopes: list[dict[str, _Binding]] = [{}]
        self.function_stack: list[str] = []
        self.reads: dict[str, set[str]] = {}
        self.unresolved: list[str] = []

    def _lookup(self, name: str) -> _Binding:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return UNKNOWN

    def _bind(self, target: ast.AST, binding: _Binding) -> None:
        if isinstance(target, ast.Name):
            self.scopes[-1][target.id] = binding
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind(element, UNKNOWN)

    def _expression_binding(self, node: ast.AST | None) -> _Binding:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _Binding("string", node.value)
        if isinstance(node, ast.Attribute):
            owner = self._expression_binding(node.value)
            if owner.kind == "os" and node.attr == "environ":
                return OS_ENVIRON
            if owner.kind == "os" and node.attr == "getenv":
                return OS_GETENV
        if isinstance(node, ast.IfExp):
            body = self._expression_binding(node.body)
            otherwise = self._expression_binding(node.orelse)
            if body == otherwise:
                return body
            # A value that may be os.environ remains an environment mapping even
            # when tests can inject a replacement mapping through the other arm.
            if "environ" in {body.kind, otherwise.kind}:
                return OS_ENVIRON
        return UNKNOWN

    def _resolve_key(self, node: ast.AST) -> str | None:
        binding = self._expression_binding(node)
        if binding.kind == "string":
            return binding.value
        return None

    def _current_function(self) -> str:
        return self.function_stack[-1] if self.function_stack else "<module>"

    def _record(self, node: ast.AST, key_node: ast.AST, seam: str) -> None:
        key = self._resolve_key(key_node)
        location = f"{self.relative_path}:{node.lineno}"
        if key is not None:
            self.reads.setdefault(key, set()).add(location)
            return
        if (self.relative_path, self._current_function()) in DYNAMIC_PYTHON_READERS:
            return
        self.unresolved.append(
            f"{location} uses dynamic {seam} key {ast.unparse(key_node)!r}"
        )

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

        self.function_stack.append(node.name)
        self.scopes.append({})
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            self.scopes[-1][argument.arg] = UNKNOWN
        if node.args.vararg is not None:
            self.scopes[-1][node.args.vararg.arg] = UNKNOWN
        if node.args.kwarg is not None:
            self.scopes[-1][node.args.kwarg.arg] = UNKNOWN
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        self.scopes.append({})
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            self.scopes[-1][bound_name] = OS_MODULE if alias.name == "os" else UNKNOWN

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name
            binding = UNKNOWN
            if node.module == "os" and alias.name == "environ":
                binding = OS_ENVIRON
            elif node.module == "os" and alias.name == "getenv":
                binding = OS_GETENV
            self.scopes[-1][bound_name] = binding

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        binding = self._expression_binding(node.value)
        for target in node.targets:
            self._bind(target, binding)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            binding = self._expression_binding(node.value)
        else:
            binding = UNKNOWN
        self._bind(node.target, binding)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind(node.target, self._expression_binding(node.value))

    def visit_Call(self, node: ast.Call) -> None:
        if node.args:
            function_binding = self._expression_binding(node.func)
            if function_binding.kind == "getenv":
                self._record(node, node.args[0], "os.getenv")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in self._ENVIRON_METHODS
                and self._expression_binding(node.func.value).kind == "environ"
            ):
                self._record(node, node.args[0], f"os.environ.{node.func.attr}")
            elif isinstance(node.func, ast.Name) and node.func.id in PYTHON_ENV_HELPERS:
                argument_index, paths = PYTHON_ENV_HELPERS[node.func.id]
                if self.relative_path in paths and len(node.args) > argument_index:
                    self._record(
                        node,
                        node.args[argument_index],
                        f"{node.func.id} helper",
                    )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.ctx, (ast.Load, ast.Del))
            and self._expression_binding(node.value).kind == "environ"
        ):
            self._record(node, node.slice, "os.environ[]")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        left = node.left
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if (
                isinstance(operator, (ast.In, ast.NotIn))
                and self._expression_binding(comparator).kind == "environ"
            ):
                self._record(node, left, "os.environ membership")
            left = comparator
        self.generic_visit(node)


def _python_env_reads_from_source(
    path: Path, source: str
) -> tuple[dict[str, set[str]], list[str]]:
    tree = ast.parse(source, filename=str(path))
    visitor = _PythonEnvVisitor(path)
    visitor.visit(tree)
    return visitor.reads, visitor.unresolved


def _python_env_reads() -> tuple[dict[str, set[str]], list[str]]:
    reads: dict[str, set[str]] = {}
    unresolved: list[str] = []
    for source_root in PYTHON_ROOTS:
        for path in sorted(source_root.rglob("*.py")):
            found, source_unresolved = _python_env_reads_from_source(
                path, path.read_text(encoding="utf-8")
            )
            unresolved.extend(source_unresolved)
            for key, locations in found.items():
                reads.setdefault(key, set()).update(locations)
    return reads, unresolved


def _shell_env_reads_from_source(path: Path, source: str) -> dict[str, set[str]]:
    reads: dict[str, set[str]] = {}
    assigned: set[str] = set()
    for lineno, line in enumerate(source.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        names = {
            match.group(1) or match.group(2) for match in SHELL_REFERENCE.finditer(line)
        }
        names.update(match.group(1) for match in PRINTCONTENV.finditer(line))
        for name in names - assigned:
            reads.setdefault(name, set()).add(f"{_relative(path)}:{lineno}")
        assigned.update(match.group(1) for match in SHELL_ASSIGNMENT.finditer(line))
    return reads


def _shell_env_reads() -> dict[str, set[str]]:
    reads: dict[str, set[str]] = {}
    for path in sorted(
        candidate for candidate in S6_ROOT.rglob("*") if candidate.is_file()
    ):
        try:
            found = _shell_env_reads_from_source(path, path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        for key, locations in found.items():
            reads.setdefault(key, set()).update(locations)
    return reads


class _JsToken(NamedTuple):
    kind: str
    value: str
    line: int


def _javascript_tokens(source: str) -> list[_JsToken]:
    """Tokenize only the JS/TS lexical forms needed for process.env reads."""

    tokens: list[_JsToken] = []
    index = 0
    line = 1
    while index < len(source):
        character = source[index]
        if character.isspace():
            line += character == "\n"
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            if newline == -1:
                break
            index = newline
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                line += source[index:].count("\n")
                break
            line += source[index : end + 2].count("\n")
            index = end + 2
            continue
        if character in {"'", '"', "`"}:
            quote = character
            token_line = line
            index += 1
            value: list[str] = []
            while index < len(source):
                character = source[index]
                if character == "\\" and index + 1 < len(source):
                    escaped = source[index + 1]
                    line += escaped == "\n"
                    value.append(escaped)
                    index += 2
                    continue
                if character == quote:
                    index += 1
                    break
                line += character == "\n"
                value.append(character)
                index += 1
            tokens.append(
                _JsToken(
                    "string" if quote != "`" else "template",
                    "".join(value),
                    token_line,
                )
            )
            continue
        if character.isalpha() or character in {"_", "$"}:
            token_line = line
            end = index + 1
            while end < len(source) and (
                source[end].isalnum() or source[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(_JsToken("identifier", source[index:end], token_line))
            index = end
            continue
        if source.startswith("...", index):
            tokens.append(_JsToken("punctuation", "...", line))
            index += 3
            continue
        tokens.append(_JsToken("punctuation", character, line))
        index += 1
    return tokens


def _matching_open_brace(tokens: list[_JsToken], close_index: int) -> int | None:
    depth = 0
    for index in range(close_index, -1, -1):
        if tokens[index].value == "}":
            depth += 1
        elif tokens[index].value == "{":
            depth -= 1
            if depth == 0:
                return index
    return None


def _record_frontend_destructure(
    path: Path,
    tokens: list[_JsToken],
    open_index: int,
    close_index: int,
    reads: dict[str, set[str]],
    unresolved: list[str],
) -> None:
    at_property = True
    nested = 0
    index = open_index + 1
    while index < close_index:
        token = tokens[index]
        if nested:
            if token.value in {"{", "[", "("}:
                nested += 1
            elif token.value in {"}", "]", ")"}:
                nested -= 1
            index += 1
            continue
        if token.value == ",":
            at_property = True
            index += 1
            continue
        if not at_property:
            if token.value in {"{", "[", "("}:
                nested = 1
            index += 1
            continue
        if token.value == "...":
            unresolved.append(
                f"{_relative(path)}:{token.line} uses dynamic process.env rest destructuring"
            )
            at_property = False
        elif token.value == "[":
            unresolved.append(
                f"{_relative(path)}:{token.line} uses dynamic process.env destructuring key"
            )
            nested = 1
            at_property = False
        elif token.kind in {"identifier", "string"}:
            reads.setdefault(token.value, set()).add(f"{_relative(path)}:{token.line}")
            at_property = False
        else:
            at_property = False
        index += 1


def _frontend_env_reads_from_source(
    path: Path, source: str
) -> tuple[dict[str, set[str]], list[str]]:
    reads: dict[str, set[str]] = {}
    unresolved: list[str] = []
    tokens = _javascript_tokens(source)
    index = 0
    while index + 2 < len(tokens):
        if not (
            tokens[index].kind == "identifier"
            and tokens[index].value == "process"
            and tokens[index + 1].value == "."
            and tokens[index + 2].kind == "identifier"
            and tokens[index + 2].value == "env"
        ):
            index += 1
            continue

        end = index + 3
        if end + 1 < len(tokens) and tokens[end].value == ".":
            key = tokens[end + 1]
            if key.kind == "identifier":
                reads.setdefault(key.value, set()).add(f"{_relative(path)}:{key.line}")
        elif end < len(tokens) and tokens[end].value == "[":
            if (
                end + 2 < len(tokens)
                and tokens[end + 1].kind == "string"
                and tokens[end + 2].value == "]"
            ):
                key = tokens[end + 1]
                reads.setdefault(key.value, set()).add(f"{_relative(path)}:{key.line}")
            else:
                unresolved.append(
                    f"{_relative(path)}:{tokens[end].line} uses dynamic process.env bracket key"
                )
        elif (
            index >= 2
            and tokens[index - 1].value == "="
            and tokens[index - 2].value == "}"
        ):
            open_index = _matching_open_brace(tokens, index - 2)
            if open_index is not None:
                _record_frontend_destructure(
                    path,
                    tokens,
                    open_index,
                    index - 2,
                    reads,
                    unresolved,
                )
        index = end
    return reads, unresolved


def _frontend_env_reads() -> tuple[dict[str, set[str]], list[str]]:
    reads: dict[str, set[str]] = {}
    unresolved: list[str] = []
    extensions = frozenset({".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"})
    for path in sorted(FRONTEND_ROOT.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in extensions
            or "node_modules" in path.parts
        ):
            continue
        found, source_unresolved = _frontend_env_reads_from_source(
            path, path.read_text(encoding="utf-8")
        )
        unresolved.extend(source_unresolved)
        for key, locations in found.items():
            reads.setdefault(key, set()).update(locations)
    return reads, unresolved


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


def _ssot_failures(reads: dict[str, set[str]], documented_list: list[str]) -> list[str]:
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
    return failures


def test_reported_bypass_probes_are_named_as_missing() -> None:
    python_reads, python_unresolved = _python_env_reads_from_source(
        ROOT / "scripts" / "ssot_python_probe.py",
        """
def probe():
    import os as operating_system
    return operating_system.environ.get("CWA_FAKE_LOCAL_ALIAS")
""",
    )
    frontend_reads, frontend_unresolved = _frontend_env_reads_from_source(
        ROOT / "frontend" / "ssot_frontend_probe.ts",
        'process.env["CWA_FAKE_FRONTEND_BRACKET"];\n'
        "const { CWA_FAKE_FRONTEND_DESTRUCTURED } = process.env;\n",
    )
    shell_reads = _shell_env_reads_from_source(
        ROOT / "root" / "etc" / "s6-overlay" / "ssot_shell_probe",
        'echo "${CWA_FAKE_SH_INHERITED}"\nCWA_FAKE_SH_INHERITED=local\n',
    )

    assert python_unresolved == []
    assert frontend_unresolved == []
    reads = _merge_reads(python_reads, frontend_reads, shell_reads)
    failure = "\n\n".join(_ssot_failures(reads, []))
    for key in {
        "CWA_FAKE_FRONTEND_BRACKET",
        "CWA_FAKE_FRONTEND_DESTRUCTURED",
        "CWA_FAKE_LOCAL_ALIAS",
        "CWA_FAKE_SH_INHERITED",
    }:
        assert key in failure


def test_python_ast_covers_aliases_mapping_operations_and_dynamic_keys() -> None:
    reads, unresolved = _python_env_reads_from_source(
        ROOT / "scripts" / "ssot_python_forms.py",
        """
import os
from os import environ as imported_env
from os import getenv as imported_getenv

MODULE_KEY = "CWA_MODULE_CONSTANT"
env = os.environ
env.setdefault("CWA_SETDEFAULT", "value")
env.pop("CWA_POP", None)
present = "CWA_MEMBERSHIP" in imported_env
value = env[MODULE_KEY]
other = imported_getenv("CWA_IMPORTED_GETENV")

def local_alias(dynamic_key):
    import os as operating_system
    local_env = operating_system.environ
    local_env.get("CWA_LOCAL_GET")
    return local_env[dynamic_key]
""",
    )

    assert set(reads) == {
        "CWA_IMPORTED_GETENV",
        "CWA_LOCAL_GET",
        "CWA_MEMBERSHIP",
        "CWA_MODULE_CONSTANT",
        "CWA_POP",
        "CWA_SETDEFAULT",
    }
    assert len(unresolved) == 1
    assert "scripts/ssot_python_forms.py:18" in unresolved[0]
    assert "dynamic os.environ[] key 'dynamic_key'" in unresolved[0]


def test_frontend_tokenizer_covers_supported_forms_without_comment_noise() -> None:
    reads, unresolved = _frontend_env_reads_from_source(
        ROOT / "frontend" / "ssot_frontend_forms.ts",
        """
process.env.CWA_DOT;
process.env["CWA_DOUBLE_QUOTED"];
process.env['CWA_SINGLE_QUOTED'];
const { CWA_SHORT, CWA_SOURCE: localName, CWA_DEFAULT = false } = process.env;
// process.env.CWA_COMMENT
const text = "process.env.CWA_STRING";
process.env[key];
const { ...remaining } = process.env;
""",
    )

    assert set(reads) == {
        "CWA_DEFAULT",
        "CWA_DOT",
        "CWA_DOUBLE_QUOTED",
        "CWA_SHORT",
        "CWA_SINGLE_QUOTED",
        "CWA_SOURCE",
    }
    assert len(unresolved) == 2
    assert all("frontend/ssot_frontend_forms.ts:" in item for item in unresolved)


def test_shell_scan_uses_first_occurrence_order() -> None:
    reads = _shell_env_reads_from_source(
        ROOT / "root" / "etc" / "s6-overlay" / "ssot_shell_forms",
        """
echo "$CWA_BEFORE"
CWA_BEFORE=local
CWA_LOCAL=local
echo "$CWA_LOCAL"
CWA_SELF=${CWA_SELF:-default}
""",
    )

    assert set(reads) == {"CWA_BEFORE", "CWA_SELF"}


def test_env_example_is_the_single_source_of_truth() -> None:
    python_reads, python_unresolved = _python_env_reads()
    frontend_reads, frontend_unresolved = _frontend_env_reads()
    unresolved = python_unresolved + frontend_unresolved
    assert not unresolved, (
        "Environment reads with unresolved dynamic keys need a reviewed static seam:\n"
        + "\n".join(f"  - {entry}" for entry in unresolved)
    )

    reads = _merge_reads(python_reads, _shell_env_reads(), frontend_reads)
    documented_list = EXAMPLE_ASSIGNMENT.findall(EXAMPLE.read_text(encoding="utf-8"))
    failures = _ssot_failures(reads, documented_list)
    assert not failures, "\n\n".join(failures)
