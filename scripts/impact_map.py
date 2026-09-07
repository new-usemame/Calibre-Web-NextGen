#!/usr/bin/env python3
"""Build and query the static CWNG impact map.

This tool never imports ``cps``.  It parses Python source, joins route records
to a pinned runtime-reconciliation oracle, and reports every call that it
cannot resolve as data.  The resulting graph is a recall-oriented hint, not a
proof that an omitted dependency does not exist.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_MAP = Path("state/modernization/impact-map.json")
DEFAULT_ORACLE = Path("state/modernization/impact-map-route-oracle.json")
DEFAULT_CASES = Path("state/modernization/impact-map-recall-cases.json")
DEFAULT_RECALL = Path("state/modernization/impact-map-recall.json")

CONFIDENCE_TAXONOMY = {
    "exact_local_symbol": {
        "rank": 4,
        "meaning": "A bare name resolves to a module-level function or class in the same module.",
    },
    "exact_import_symbol": {
        "rank": 4,
        "meaning": "An explicit import binding resolves to a known internal function or class.",
    },
    "exact_import_module_attribute": {
        "rank": 4,
        "meaning": "An imported internal module plus attribute chain resolves to a known symbol.",
    },
    "class_member_coarse": {
        "rank": 2,
        "meaning": "A member call is known to target an imported or local class, but methods are folded into the class node.",
    },
    "attribute_name_guess": {
        "rank": 1,
        "meaning": "Only the final attribute name matched a unique module-level symbol; receiver identity is unknown.",
    },
    "route_reconciled": {
        "rank": 4,
        "meaning": "A current static route record matches the pinned static snapshot whose key joined to the runtime union.",
    },
    "route_unreconciled": {
        "rank": 0,
        "meaning": "A current static route is absent from the pinned reconciliation input; runtime liveness is unknown.",
    },
    "import_internal": {
        "rank": 4,
        "meaning": "A Python import statement resolves to a module or symbol inside cps.",
    },
}

CENSUS_CONTEXT = {
    "measured_files": 227,
    "approximate_call_sites": 36554,
    "shapes": {
        "name": {"count": 16236, "share": 0.444, "note": "includes the separately counted getattr subset"},
        "attribute_on_name": {"count": 12899, "share": 0.353},
        "attribute_on_call": {"count": 3969, "share": 0.109},
        "attribute_chain": {"count": 3416, "share": 0.093},
        "getattr_subset": {"count": 564, "share": 0.015},
        "indirect_call": {"count": 34, "share": 0.001},
    },
    "interpretation": "Static recall cannot exceed roughly 80%; import-resolved and local-name calls form a substantially smaller confident core near 45%.",
}

RUNTIME_ONLY_KINDS = {
    "static": (
        "flask_builtin_static",
        "Flask creates this application static-file endpoint at runtime; it has no cps route decorator.",
    ),
    "generic.login": (
        "flask_dance_provider_login",
        "Flask-Dance registers the generic OAuth provider login endpoint dynamically.",
    ),
    "generic.authorized": (
        "flask_dance_provider_callback",
        "Flask-Dance registers the generic OAuth provider callback endpoint dynamically.",
    ),
    "github.login": (
        "flask_dance_provider_login",
        "Flask-Dance registers the GitHub OAuth provider login endpoint dynamically.",
    ),
    "github.authorized": (
        "flask_dance_provider_callback",
        "Flask-Dance registers the GitHub OAuth provider callback endpoint dynamically.",
    ),
    "google.login": (
        "flask_dance_provider_login",
        "Flask-Dance registers the Google OAuth provider login endpoint dynamically.",
    ),
    "google.authorized": (
        "flask_dance_provider_callback",
        "Flask-Dance registers the Google OAuth provider callback endpoint dynamically.",
    ),
}


def stable_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_json(path: Path, data: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        rendered = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    else:
        rendered = stable_json(data)
    path.write_text(rendered, encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "cps"], cwd=repo_root, check=True,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def git_object_sha(repo_root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", revision], cwd=repo_root, check=True,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def module_name_for(relative: str) -> str:
    path = relative[:-3] if relative.endswith(".py") else relative
    if path.endswith("/__init__"):
        path = path[: -len("/__init__")]
    return path.replace("/", ".")


def module_node_id(module: str) -> str:
    return f"module:{module}"


def symbol_node_id(module: str, name: str) -> str:
    return f"symbol:{module}:{name}"


def route_node_id(endpoint: str, rule: str) -> str:
    digest = hashlib.sha1(f"{endpoint}\0{rule}".encode()).hexdigest()[:12]
    return f"route:{endpoint}:{digest}"


def source_location(relative: str, node: ast.AST) -> dict[str, Any]:
    return {
        "file": relative,
        "line": int(getattr(node, "lineno", 1)),
        "column": int(getattr(node, "col_offset", 0)) + 1,
    }


@dataclass(frozen=True)
class Binding:
    kind: str
    target: str | None
    provenance: str


@dataclass
class ModuleInfo:
    relative: str
    module: str
    tree: ast.Module
    is_package: bool
    symbols: dict[str, str] = field(default_factory=dict)
    globals: dict[str, Binding] = field(default_factory=dict)


def iter_python_files(repo_root: Path) -> Iterable[Path]:
    cps = repo_root / "cps"
    excluded = {"static", "templates", "translations", "__pycache__"}
    for root, dirs, files in os.walk(cps):
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for filename in sorted(files):
            if filename.endswith(".py"):
                yield Path(root) / filename


def collect_modules(repo_root: Path) -> tuple[dict[str, ModuleInfo], list[dict[str, Any]]]:
    modules: dict[str, ModuleInfo] = {}
    parse_blind_spots: list[dict[str, Any]] = []
    for path in iter_python_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        module = module_name_for(relative)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            parse_blind_spots.append({
                "kind": "parse_error",
                "module": module,
                "file": relative,
                "line": int(getattr(exc, "lineno", 1) or 1),
                "column": int(getattr(exc, "offset", 1) or 1),
                "shape": "file",
                "callee": None,
                "reason": type(exc).__name__,
                "candidate_targets": [],
            })
            continue
        info = ModuleInfo(
            relative=relative,
            module=module,
            tree=tree,
            is_package=path.name == "__init__.py",
        )
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                info.symbols[statement.name] = symbol_node_id(module, statement.name)
        modules[module] = info
    return modules, parse_blind_spots


def resolve_from_module(info: ModuleInfo, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = info.module if info.is_package else info.module.rpartition(".")[0]
    if not package:
        return None
    requested = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(requested, package)
    except (ImportError, ValueError):
        return None


def binding_for_imported_name(
    modules: dict[str, ModuleInfo], base: str | None, imported: str,
) -> Binding:
    if base:
        child_module = f"{base}.{imported}"
        if child_module in modules:
            return Binding("module", module_node_id(child_module), "explicit_import")
        if base in modules and imported in modules[base].symbols:
            return Binding("symbol", modules[base].symbols[imported], "explicit_import")
    return Binding("external", None, "external_import")


def binding_for_import_module(modules: dict[str, ModuleInfo], imported: str, alias: str | None) -> tuple[str, Binding]:
    bound_name = alias or imported.split(".")[0]
    target_module = imported if alias else imported.split(".")[0]
    if target_module in modules:
        return bound_name, Binding("module", module_node_id(target_module), "explicit_import")
    return bound_name, Binding("external", None, "external_import")


def populate_global_bindings(modules: dict[str, ModuleInfo]) -> None:
    for info in modules.values():
        for name, target in info.symbols.items():
            info.globals[name] = Binding("symbol", target, "local_definition")
        for statement in info.tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    name, binding = binding_for_import_module(modules, alias.name, alias.asname)
                    info.globals[name] = binding
            elif isinstance(statement, ast.ImportFrom):
                base = resolve_from_module(info, statement)
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    info.globals[alias.asname or alias.name] = binding_for_imported_name(
                        modules, base, alias.name,
                    )


def flatten_attribute(node: ast.Attribute) -> tuple[ast.AST, list[str]]:
    attributes: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    attributes.reverse()
    return current, attributes


def callee_text(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        root, attributes = flatten_attribute(node)
        if isinstance(root, ast.Name):
            return ".".join([root.id, *attributes])
        if isinstance(root, ast.Call):
            return f"{callee_text(root.func)}(...).{'.'.join(attributes)}"
        return f"<{type(root).__name__}>.{'.'.join(attributes)}"
    if isinstance(node, ast.Call):
        return f"{callee_text(node.func)}(...)"
    if isinstance(node, ast.Subscript):
        return f"{callee_text(node.value)}[...]"
    return f"<{type(node).__name__}>"


def call_shape(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return "name"
    if isinstance(function, ast.Attribute):
        if isinstance(function.value, ast.Name):
            return "attribute_on_name"
        if isinstance(function.value, ast.Call):
            return "attribute_on_call"
        if isinstance(function.value, ast.Attribute):
            return "attribute_chain"
        return "attribute_on_expression"
    if isinstance(function, ast.Call):
        return "call_result"
    if isinstance(function, ast.Subscript):
        return "subscript"
    return "expression"


def local_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    arguments = getattr(node, "args", None)
    if arguments is not None:
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            names.add(arg.arg)
        if arguments.vararg:
            names.add(arguments.vararg.arg)
        if arguments.kwarg:
            names.add(arguments.kwarg.arg)

    class Stores(ast.NodeVisitor):
        def visit_Name(self, child: ast.Name) -> None:
            if isinstance(child.ctx, ast.Store):
                names.add(child.id)

        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            names.add(child.name)
            if child is node:
                self.generic_visit(child)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, child: ast.ClassDef) -> None:
            names.add(child.name)
            if child is node:
                self.generic_visit(child)

        def visit_Lambda(self, child: ast.Lambda) -> None:
            if child is node:
                self.generic_visit(child)

    Stores().visit(node)
    return names


class GraphVisitor(ast.NodeVisitor):
    def __init__(
        self,
        info: ModuleInfo,
        modules: dict[str, ModuleInfo],
        symbols_by_name: dict[str, list[str]],
    ) -> None:
        self.info = info
        self.modules = modules
        self.symbols_by_name = symbols_by_name
        self.caller_stack = [module_node_id(info.module)]
        self.local_stack: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.blind_spots: list[dict[str, Any]] = []
        self.shape_counts: Counter[str] = Counter()
        self.out_of_scope_counts: Counter[str] = Counter()
        self.getattr_calls = 0

    @property
    def caller(self) -> str:
        return self.caller_stack[-1]

    def add_edge(
        self, target: str, kind: str, confidence: str, node: ast.AST,
        *, detail: str | None = None,
    ) -> None:
        location = source_location(self.info.relative, node)
        edge = {
            "source": self.caller,
            "target": target,
            "kind": kind,
            "confidence": confidence,
            "location": location,
        }
        if detail:
            edge["detail"] = detail
        self.edges.append(edge)

    def add_blind(
        self, node: ast.Call, reason: str, candidates: Iterable[str] = (),
    ) -> None:
        location = source_location(self.info.relative, node)
        self.blind_spots.append({
            "kind": "unresolved_call",
            "module": self.info.module,
            "caller": self.caller,
            **location,
            "shape": call_shape(node),
            "callee": callee_text(node.func),
            "reason": reason,
            "candidate_targets": sorted(set(candidates)),
        })

    def add_out_of_scope(self, reason: str) -> None:
        """Count a known non-cps target without misrepresenting it as an unknown dependency."""
        self.out_of_scope_counts[reason] += 1

    def enter_callable(self, node: ast.AST, caller: str) -> None:
        self.caller_stack.append(caller)
        self.local_stack.append({"names": local_names(node), "imports": {}})
        self.generic_visit(node)
        self.local_stack.pop()
        self.caller_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        caller = self.caller
        if len(self.caller_stack) == 1 and node.name in self.info.symbols:
            caller = self.info.symbols[node.name]
        self.enter_callable(node, caller)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        caller = self.caller
        if len(self.caller_stack) == 1 and node.name in self.info.symbols:
            caller = self.info.symbols[node.name]
        self.caller_stack.append(caller)
        self.generic_visit(node)
        self.caller_stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.enter_callable(node, self.caller)

    def record_import_binding(self, name: str, binding: Binding, node: ast.AST) -> None:
        if self.local_stack:
            self.local_stack[-1]["imports"][name] = binding
        if binding.target:
            self.add_edge(binding.target, "import", "import_internal", node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name, binding = binding_for_import_module(self.modules, alias.name, alias.asname)
            self.record_import_binding(name, binding, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = resolve_from_module(self.info, node)
        for alias in node.names:
            if alias.name == "*":
                self.blind_spots.append({
                    "kind": "wildcard_import",
                    "module": self.info.module,
                    "caller": self.caller,
                    **source_location(self.info.relative, node),
                    "shape": "import",
                    "callee": base,
                    "reason": "wildcard_import_has_unknown_bindings",
                    "candidate_targets": [],
                })
                continue
            binding = binding_for_imported_name(self.modules, base, alias.name)
            self.record_import_binding(alias.asname or alias.name, binding, node)

    def lookup_name(self, name: str) -> Binding | None:
        for scope in reversed(self.local_stack):
            if name in scope["imports"]:
                return scope["imports"][name]
            if name in scope["names"]:
                return Binding("local_runtime", None, "local_assignment_or_parameter")
        return self.info.globals.get(name)

    def resolve_module_attribute(self, module: str, attributes: list[str]) -> str | None:
        if not attributes:
            return None
        for split in range(len(attributes) - 1, -1, -1):
            candidate_module = ".".join([module, *attributes[:split]])
            symbol_name = attributes[split] if split < len(attributes) else None
            trailing = attributes[split + 1 :]
            if candidate_module not in self.modules or symbol_name is None:
                continue
            symbol = self.modules[candidate_module].symbols.get(symbol_name)
            if symbol and not trailing:
                return symbol
            if symbol and trailing:
                parsed = symbol.split(":", 2)
                if len(parsed) == 3:
                    node = next(
                        (item for item in self.modules[candidate_module].tree.body
                         if isinstance(item, ast.ClassDef) and item.name == parsed[2]),
                        None,
                    )
                    if node is not None:
                        return symbol
        return None

    def resolve_name_call(self, node: ast.Call, function: ast.Name) -> bool:
        binding = self.lookup_name(function.id)
        if binding and binding.kind == "symbol" and binding.target:
            confidence = (
                "exact_local_symbol"
                if binding.provenance == "local_definition"
                else "exact_import_symbol"
            )
            self.add_edge(binding.target, "call", confidence, node)
            return True
        if binding and binding.kind == "external":
            self.add_out_of_scope("external_import_call")
            return False
        if binding and binding.kind == "module":
            self.add_blind(node, "module_object_called_directly")
            return False
        if binding and binding.kind == "local_runtime":
            self.add_blind(node, "local_value_or_parameter_call")
            return False
        if function.id in dir(builtins):
            self.add_out_of_scope("builtin_call")
            return False
        self.add_blind(node, "unbound_or_dynamic_name")
        return False

    def resolve_attribute_call(self, node: ast.Call, function: ast.Attribute) -> bool:
        root, attributes = flatten_attribute(function)
        if isinstance(root, ast.Name):
            binding = self.lookup_name(root.id)
            if binding and binding.kind == "module" and binding.target:
                module = binding.target.removeprefix("module:")
                target = self.resolve_module_attribute(module, attributes)
                if target:
                    self.add_edge(target, "call", "exact_import_module_attribute", node)
                    return True
                self.add_blind(node, "imported_module_attribute_not_a_module_level_symbol")
                return False
            if binding and binding.kind == "symbol" and binding.target:
                self.add_edge(
                    binding.target, "call", "class_member_coarse", node,
                    detail="member methods are represented by their class node",
                )
                self.add_blind(node, "class_member_folded_to_class", [binding.target])
                return False
            if binding and binding.kind == "external":
                self.add_out_of_scope("external_import_attribute_call")
                return False

        candidates = self.symbols_by_name.get(attributes[-1], []) if attributes else []
        if len(candidates) == 1:
            self.add_edge(candidates[0], "call", "attribute_name_guess", node)
            self.add_blind(node, "receiver_unknown_attribute_name_guess", candidates)
            return False
        reason = (
            "attribute_name_has_multiple_candidates"
            if candidates else "receiver_or_attribute_target_unknown"
        )
        self.add_blind(node, reason, candidates[:20])
        return False

    def visit_Call(self, node: ast.Call) -> None:
        shape = call_shape(node)
        self.shape_counts[shape] += 1
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            self.getattr_calls += 1
        if isinstance(node.func, ast.Name):
            self.resolve_name_call(node, node.func)
        elif isinstance(node.func, ast.Attribute):
            self.resolve_attribute_call(node, node.func)
        else:
            self.add_blind(node, "indirect_callable_expression")
        self.generic_visit(node)


def discover_routes(info: ModuleInfo) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.handle_function(node)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if not (isinstance(function, ast.Attribute) and function.attr == "route"):
                    continue
                blueprint = function.value.id if isinstance(function.value, ast.Name) else None
                rule = literal_string(decorator.args[0]) if decorator.args else None
                records.append({
                    "file": info.relative,
                    "line": decorator.lineno,
                    "blueprint_var": blueprint,
                    "rule": rule,
                    "methods": literal_methods(decorator),
                    "func": node.name,
                    "endpoint": None,
                })

        def visit_Call(self, node: ast.Call) -> None:
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "add_url_rule":
                blueprint = function.value.id if isinstance(function.value, ast.Name) else None
                rule = literal_string(node.args[0]) if node.args else None
                endpoint = keyword_string(node, "endpoint")
                view = keyword_name(node, "view_func")
                if view is None and len(node.args) >= 3:
                    view = expression_name(node.args[2])
                records.append({
                    "file": info.relative,
                    "line": node.lineno,
                    "blueprint_var": blueprint,
                    "rule": rule,
                    "methods": literal_methods(node),
                    "func": view or "<add_url_rule>",
                    "endpoint": endpoint,
                })
            self.generic_visit(node)

    Visitor().visit(info.tree)
    return records


def literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return literal_string(keyword.value)
    return None


def expression_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def keyword_name(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return expression_name(keyword.value)
    return None


def literal_methods(node: ast.Call) -> list[str]:
    for keyword in node.keywords:
        if keyword.arg != "methods" or not isinstance(keyword.value, (ast.List, ast.Tuple)):
            continue
        values = [literal_string(item) for item in keyword.value.elts]
        if all(value is not None for value in values):
            return sorted(value for value in values if value is not None)
    return ["GET"]


def static_semantic_key(record: dict[str, Any]) -> tuple[Any, ...]:
    function = record.get("func")
    if function != "<add_url_rule>":
        function_key = function
    else:
        function_key = "<add_url_rule>"
    return (
        record.get("file"), record.get("blueprint_var"), record.get("rule"),
        tuple(record.get("methods") or ["GET"]), function_key,
    )


def join_rule(prefix: str | None, rule: str) -> str:
    if not prefix:
        return rule
    return prefix.rstrip("/") + "/" + rule.lstrip("/")


def runtime_only_metadata(endpoint: str) -> tuple[str, str]:
    return RUNTIME_ONLY_KINDS.get(
        endpoint,
        ("runtime_registered", "The pinned runtime union contains no matching cps static route record."),
    )


def add_routes(
    modules: dict[str, ModuleInfo], oracle: dict[str, Any], nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]], blind_spots: list[dict[str, Any]],
) -> dict[str, Any]:
    reconciliation = oracle["reconciliation"]
    mapping = reconciliation["var_to_blueprint"]
    static_only_pairs = {
        (record.get("endpoint"), record.get("rule"))
        for record in reconciliation.get("static_only", [])
    }
    snapshot_records = oracle["static_routes"]["records"]
    snapshot_keys = Counter(static_semantic_key(record) for record in snapshot_records)
    current_records = [record for info in modules.values() for record in discover_routes(info)]
    current_records.sort(key=lambda item: (item["file"], item["line"], item.get("rule") or ""))
    current_keys = Counter(static_semantic_key({
        **record,
        "func": "<add_url_rule>" if record.get("endpoint") is not None else record.get("func"),
    }) for record in current_records)

    drift_current = list((current_keys - snapshot_keys).elements())
    drift_oracle = list((snapshot_keys - current_keys).elements())
    for key in drift_current:
        blind_spots.append({
            "kind": "route_oracle_drift",
            "module": module_name_for(key[0]),
            "caller": module_node_id(module_name_for(key[0])),
            "file": key[0], "line": 1, "column": 1,
            "shape": "route",
            "callee": key[2],
            "reason": "current_route_absent_from_pinned_oracle",
            "candidate_targets": [],
        })
    for key in drift_oracle:
        blind_spots.append({
            "kind": "route_oracle_drift",
            "module": module_name_for(key[0]),
            "caller": module_node_id(module_name_for(key[0])),
            "file": key[0], "line": 1, "column": 1,
            "shape": "route",
            "callee": key[2],
            "reason": "pinned_route_absent_from_current_source",
            "candidate_targets": [],
        })

    route_count = 0
    reconciled_count = 0
    static_only_count = 0
    for record in current_records:
        blueprint_var = record.get("blueprint_var")
        rule = record.get("rule")
        if not blueprint_var or blueprint_var not in mapping or rule is None:
            blind_spots.append({
                "kind": "route_binding",
                "module": module_name_for(record["file"]),
                "caller": module_node_id(module_name_for(record["file"])),
                "file": record["file"], "line": record["line"], "column": 1,
                "shape": "route",
                "callee": rule,
                "reason": "route_blueprint_or_rule_unresolved",
                "candidate_targets": [],
            })
            continue
        blueprint = mapping[blueprint_var]
        endpoint_name = record.get("endpoint") or record["func"]
        endpoint = f"{blueprint['name']}.{endpoint_name}"
        full_rule = join_rule(blueprint.get("url_prefix"), rule)
        comparable = {**record, "func": "<add_url_rule>" if record.get("endpoint") is not None else record["func"]}
        snapshot_matched = snapshot_keys[static_semantic_key(comparable)] > 0
        reconciled = snapshot_matched and (endpoint, full_rule) not in static_only_pairs
        static_only = snapshot_matched and not reconciled
        status = (
            "reconciled_live" if reconciled
            else "reconciled_static_only" if static_only
            else "current_unreconciled"
        )
        route_id = route_node_id(endpoint, full_rule)
        nodes.append({
            "id": route_id,
            "kind": "route",
            "endpoint": endpoint,
            "rule": full_rule,
            "methods": record["methods"],
            "file": record["file"],
            "line": record["line"],
            "runtime_status": status,
            "live": True if reconciled else False if static_only else None,
            "oracle_id": oracle["oracle_id"] if snapshot_matched else None,
        })
        route_count += 1
        reconciled_count += int(reconciled)
        static_only_count += int(static_only)
        module = module_name_for(record["file"])
        target = symbol_node_id(module, record["func"])
        if target in modules[module].symbols.values():
            edges.append({
                "source": route_id,
                "target": target,
                "kind": "route_handler",
                "confidence": "route_reconciled" if reconciled else "route_unreconciled",
                "location": {"file": record["file"], "line": record["line"], "column": 1},
            })
        else:
            blind_spots.append({
                "kind": "route_binding",
                "module": module,
                "caller": route_id,
                "file": record["file"], "line": record["line"], "column": 1,
                "shape": "route",
                "callee": record["func"],
                "reason": "route_handler_not_a_module_level_symbol",
                "candidate_targets": [],
            })

    runtime_only_count = 0
    for record in reconciliation["runtime_only"]:
        endpoint = record["endpoint"]
        kind, explanation = runtime_only_metadata(endpoint)
        nodes.append({
            "id": route_node_id(endpoint, record["rule"]),
            "kind": "route",
            "endpoint": endpoint,
            "rule": record["rule"],
            "methods": None,
            "file": None,
            "line": None,
            "runtime_status": "runtime_only",
            "runtime_only_kind": kind,
            "runtime_only_explanation": explanation,
            "seen_in_shapes": record.get("seen_in_shapes", []),
            "live": True,
            "oracle_id": oracle["oracle_id"],
        })
        runtime_only_count += 1

    return {
        "current_static_routes": route_count,
        "reconciled_current_routes": reconciled_count,
        "reconciled_static_only_routes": static_only_count,
        "runtime_only_routes": runtime_only_count,
        "current_only_route_drift": len(drift_current),
        "oracle_only_route_drift": len(drift_oracle),
    }


def annotate_live_route_reachability(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    downstream: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge["kind"] in {"call", "route_handler"}:
            downstream[edge["source"]].append(edge["target"])
    live_routes = [node["id"] for node in nodes if node["kind"] == "route" and node.get("live") is True]
    route_counts: Counter[str] = Counter()
    for route in live_routes:
        seen = {route}
        queue = deque([route])
        while queue:
            source = queue.popleft()
            route_counts[source] += 1
            for target in downstream.get(source, []):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
    for node in nodes:
        count = route_counts[node["id"]]
        node["live_route_count"] = count
        node["reached_from_live_route"] = count > 0
    for edge in edges:
        count = route_counts[edge["source"]]
        edge["live_route_count"] = count
        edge["reached_from_live_route"] = count > 0


def blind_spot_summary(
    blind_spots: list[dict[str, Any]], shape_counts: Counter[str],
    module_call_counts: Counter[str], modules: dict[str, ModuleInfo],
) -> dict[str, Any]:
    by_module: dict[str, dict[str, Any]] = {}
    unresolved_by_module = Counter(
        spot["module"] for spot in blind_spots if spot["kind"] == "unresolved_call"
    )
    for module in sorted(modules):
        unresolved = unresolved_by_module[module]
        calls = module_call_counts[module]
        by_module[module] = {
            "call_sites": calls,
            "unresolved_calls": unresolved,
            "unresolved_fraction": round(unresolved / calls, 6) if calls else 0.0,
        }
    reason_counts = Counter(spot["reason"] for spot in blind_spots)
    kind_counts = Counter(spot["kind"] for spot in blind_spots)
    return {
        "total": len(blind_spots),
        "unresolved_calls": sum(unresolved_by_module.values()),
        "by_kind": dict(sorted(kind_counts.items())),
        "by_reason": dict(sorted(reason_counts.items())),
        "by_module": by_module,
        "call_shapes": dict(sorted(shape_counts.items())),
    }


def build_map(repo_root: Path, oracle_path: Path) -> dict[str, Any]:
    oracle = load_json(oracle_path)
    if oracle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported route oracle schema: {oracle.get('schema_version')!r}")
    modules, blind_spots = collect_modules(repo_root)
    populate_global_bindings(modules)

    nodes: list[dict[str, Any]] = []
    for module, info in sorted(modules.items()):
        nodes.append({
            "id": module_node_id(module), "kind": "module", "module": module,
            "file": info.relative, "line": 1,
        })
        definitions: dict[str, list[ast.AST]] = defaultdict(list)
        for statement in info.tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions[statement.name].append(statement)
        for name, statements in sorted(definitions.items()):
            kinds = {"class" if isinstance(statement, ast.ClassDef) else "function" for statement in statements}
            nodes.append({
                "id": info.symbols[name],
                "kind": next(iter(kinds)) if len(kinds) == 1 else "symbol",
                "module": module, "name": name,
                "file": info.relative, "line": min(statement.lineno for statement in statements),
                "definitions": [
                    {"line": statement.lineno, "kind": "class" if isinstance(statement, ast.ClassDef) else "function"}
                    for statement in statements
                ],
            })

    symbols_by_name: dict[str, list[str]] = defaultdict(list)
    for info in modules.values():
        for name, target in info.symbols.items():
            symbols_by_name[name].append(target)

    edges: list[dict[str, Any]] = []
    shape_counts: Counter[str] = Counter()
    getattr_calls = 0
    out_of_scope_counts: Counter[str] = Counter()
    module_call_counts: Counter[str] = Counter()
    for module, info in sorted(modules.items()):
        visitor = GraphVisitor(info, modules, symbols_by_name)
        visitor.visit(info.tree)
        edges.extend(visitor.edges)
        blind_spots.extend(visitor.blind_spots)
        shape_counts.update(visitor.shape_counts)
        module_call_counts[module] = sum(visitor.shape_counts.values())
        out_of_scope_counts.update(visitor.out_of_scope_counts)
        getattr_calls += visitor.getattr_calls

    route_counts = add_routes(modules, oracle, nodes, edges, blind_spots)
    summary = blind_spot_summary(blind_spots, shape_counts, module_call_counts, modules)
    summary["getattr_calls"] = getattr_calls
    summary["known_out_of_scope_calls"] = {
        "total": sum(out_of_scope_counts.values()),
        "by_reason": dict(sorted(out_of_scope_counts.items())),
    }

    nodes.sort(key=lambda item: item["id"])
    edges.sort(key=lambda item: (
        item["source"], item["target"], item["kind"],
        item["location"]["file"], item["location"]["line"], item["location"]["column"],
    ))
    blind_spots.sort(key=lambda item: (
        item.get("file") or "", item.get("line") or 0, item.get("column") or 0,
        item["kind"], item.get("callee") or "",
    ))
    annotate_live_route_reachability(nodes, edges)

    exact_calls = sum(1 for edge in edges if edge["kind"] == "call" and edge["confidence"].startswith("exact_"))
    guessed_calls = sum(1 for edge in edges if edge["kind"] == "call" and edge["confidence"] in {"attribute_name_guess", "class_member_coarse"})
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "recall_oriented_static_impact_hint",
        "generated_from": {
            "repo_sha": git_sha(repo_root),
            "cps_tree_sha": git_object_sha(repo_root, "HEAD:cps"),
            "route_oracle_id": oracle["oracle_id"],
            "route_oracle_repo_sha": oracle["source_repo_sha"],
            "route_oracle_static_records": oracle["static_routes"]["count"],
            "route_oracle_runtime_union": oracle["reconciliation"]["runtime_union_distinct_keys"],
        },
        "confidence_taxonomy": CONFIDENCE_TAXONOMY,
        "measurement_context": CENSUS_CONTEXT,
        "counts": {
            "modules": len(modules),
            "nodes": len(nodes),
            "edges": len(edges),
            "call_sites": sum(shape_counts.values()),
            "exact_internal_call_edges": exact_calls,
            "guessed_or_coarse_call_edges": guessed_calls,
            "known_out_of_scope_calls": sum(out_of_scope_counts.values()),
            "blind_spots": len(blind_spots),
            **route_counts,
        },
        "blind_spot_summary": summary,
        "nodes": nodes,
        "edges": edges,
        "blind_spots": blind_spots,
    }


def pin_route_oracle(
    static_path: Path, reconciliation_path: Path, output: Path,
    oracle_id: str, source_repo_sha: str,
) -> dict[str, Any]:
    static = load_json(static_path)
    reconciliation = load_json(reconciliation_path)
    if static.get("count") != len(static.get("records", [])):
        raise ValueError("static route count does not match records")
    if static.get("errors"):
        raise ValueError("static route oracle contains extraction errors")
    if reconciliation.get("static_total_records") != static["count"]:
        raise ValueError("reconciliation/static route record counts disagree")
    data = {
        "schema_version": SCHEMA_VERSION,
        "oracle_id": oracle_id,
        "source_repo_sha": source_repo_sha,
        "static_routes": static,
        "reconciliation": reconciliation,
    }
    write_json(output, data)
    return data


def node_matches(node: dict[str, Any], target: str) -> bool:
    normalized = target.replace("\\", "/")
    if node["id"] == target:
        return True
    if normalized.endswith(".py") or ".py:" in normalized:
        file_part, _, symbol = normalized.partition(":")
        if node.get("file") != file_part:
            return False
        return not symbol or node.get("name") == symbol
    if target.startswith("/"):
        return node.get("rule") == target
    if node.get("endpoint") == target:
        return True
    if ":" in target and not target.startswith(("module:", "symbol:", "route:")):
        module, symbol = target.rsplit(":", 1)
        module = module.removesuffix(".py").replace("/", ".")
        return node.get("module") == module and node.get("name") == symbol
    return node.get("name") == target or node.get("module") == target


def graph_indexes(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    nodes = {node["id"]: node for node in data["nodes"]}
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in data["edges"]:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    return nodes, outgoing, incoming


def traverse(
    starts: set[str], adjacency: dict[str, list[dict[str, Any]]], direction: str,
    depth: int | None,
) -> list[dict[str, Any]]:
    seen = set(starts)
    queue = deque((node, 0) for node in sorted(starts))
    results: list[dict[str, Any]] = []
    while queue:
        current, distance = queue.popleft()
        if depth is not None and distance >= depth:
            continue
        for edge in adjacency.get(current, []):
            other = edge["target"] if direction == "downstream" else edge["source"]
            if other in seen:
                continue
            seen.add(other)
            results.append({
                "node": other,
                "distance": distance + 1,
                "via": {
                    "kind": edge["kind"],
                    "confidence": edge["confidence"],
                    "location": edge["location"],
                },
            })
            queue.append((other, distance + 1))
    return results


def query_map(data: dict[str, Any], target: str, depth: int | None = 2) -> dict[str, Any]:
    nodes, outgoing, incoming = graph_indexes(data)
    matched = {node_id for node_id, node in nodes.items() if node_matches(node, target)}
    if not matched:
        raise KeyError(f"no file, symbol, route, or node matched {target!r}")
    downstream = traverse(matched, outgoing, "downstream", depth)
    upstream = traverse(matched, incoming, "upstream", depth)
    all_upstream = traverse(matched, incoming, "upstream", None)
    reaching_routes = sorted({
        item["node"] for item in all_upstream
        if nodes[item["node"]]["kind"] == "route" and nodes[item["node"]].get("live") is True
    } | {
        node_id for node_id in matched
        if nodes[node_id]["kind"] == "route" and nodes[node_id].get("live") is True
    })
    modules = {
        nodes[node_id].get("module") or module_name_for(nodes[node_id]["file"])
        for node_id in matched if nodes[node_id].get("file")
    }
    relevant_blind = [spot for spot in data["blind_spots"] if spot.get("module") in modules]
    reason_counts = Counter(spot["reason"] for spot in relevant_blind)
    return {
        "target": target,
        "matched_nodes": [nodes[node_id] for node_id in sorted(matched)],
        "reached_by": [{**item, "node_data": nodes[item["node"]]} for item in upstream],
        "reaches": [{**item, "node_data": nodes[item["node"]]} for item in downstream],
        "live_routes_reaching_target": [nodes[node_id] for node_id in reaching_routes],
        "blind_spots": {
            "count": len(relevant_blind),
            "by_reason": dict(sorted(reason_counts.items())),
            "records": relevant_blind,
        },
        "depth": depth,
        "warning": "Absence from this static map is not evidence of no impact; inspect blind_spots.",
    }


def render_query_text(result: dict[str, Any], blind_limit: int) -> str:
    lines = [f"target: {result['target']}", "matched:"]
    for node in result["matched_nodes"]:
        location = f" {node.get('file')}:{node.get('line')}" if node.get("file") else ""
        lines.append(f"  {node['id']}{location}")
    lines.append("reached by:")
    for item in result["reached_by"]:
        via = item["via"]
        lines.append(f"  d={item['distance']} {item['node']} [{via['kind']}/{via['confidence']}] at {via['location']['file']}:{via['location']['line']}")
    lines.append("reaches:")
    for item in result["reaches"]:
        via = item["via"]
        lines.append(f"  d={item['distance']} {item['node']} [{via['kind']}/{via['confidence']}] at {via['location']['file']}:{via['location']['line']}")
    routes = result["live_routes_reaching_target"]
    lines.append(f"live routes reaching target: {len(routes)}")
    for route in routes[:50]:
        lines.append(f"  {route['endpoint']} {route['rule']} ({route['runtime_status']})")
    blind = result["blind_spots"]
    lines.append(f"blind spots in matched module(s): {blind['count']}")
    for reason, count in blind["by_reason"].items():
        lines.append(f"  {reason}: {count}")
    for spot in blind["records"][:blind_limit]:
        lines.append(f"  {spot.get('file')}:{spot.get('line')} {spot.get('shape')} {spot.get('callee')} — {spot['reason']}")
    if len(blind["records"]) > blind_limit:
        lines.append(f"  ... {len(blind['records']) - blind_limit} more; use --format json for all records")
    lines.append(result["warning"])
    return "\n".join(lines) + "\n"


def shortest_path(
    starts: set[str], targets: set[str], outgoing: dict[str, list[dict[str, Any]]], max_depth: int,
) -> list[dict[str, Any]] | None:
    queue = deque((start, []) for start in sorted(starts))
    seen = set(starts)
    while queue:
        current, path = queue.popleft()
        if current in targets:
            return path
        if len(path) >= max_depth:
            continue
        for edge in outgoing.get(current, []):
            if edge["kind"] not in {"call", "route_handler"}:
                continue
            target = edge["target"]
            if target in seen:
                continue
            seen.add(target)
            queue.append((target, [*path, edge]))
    return None


def commit_changed_paths(repo_root: Path, commit: str) -> set[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
        cwd=repo_root, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return {line for line in result.stdout.splitlines() if line}


def selector_paths(selector: str, matched: set[str], nodes: dict[str, dict[str, Any]]) -> set[str]:
    paths = {nodes[node_id]["file"] for node_id in matched if nodes[node_id].get("file")}
    literal = selector.partition(":")[0].replace("\\", "/")
    if literal.endswith((".py", ".tsx", ".ts", ".js", ".mjs", ".html")):
        paths.add(literal)
    return paths


def evaluate_recall(data: dict[str, Any], cases: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    nodes, outgoing, _incoming = graph_indexes(data)
    results = []
    for case in cases["cases"]:
        commit = case["commit"]
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo_root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        affected = {node_id for node_id, node in nodes.items() if node_matches(node, case["affected_site"])}
        changed = {node_id for node_id, node in nodes.items() if node_matches(node, case["changed_symbol"])}
        historical_paths = commit_changed_paths(repo_root, commit) if exists else set()
        declared_paths = (
            selector_paths(case["affected_site"], affected, nodes)
            | selector_paths(case["changed_symbol"], changed, nodes)
        )
        evidence_paths_present = bool(declared_paths) and declared_paths <= historical_paths
        path = shortest_path(affected, changed, outgoing, int(case.get("max_depth", 4))) if affected and changed else None
        hit = exists and evidence_paths_present and path is not None
        if not exists:
            miss_reason = "historical_commit_not_available"
        elif not evidence_paths_present:
            miss_reason = "historical_diff_does_not_touch_declared_sites"
        elif not affected:
            miss_reason = "affected_site_not_present_in_current_map"
        elif not changed:
            miss_reason = "changed_symbol_not_present_in_current_map"
        elif path is None:
            miss_reason = case.get("expected_miss_reason", "no_static_call_path")
        else:
            miss_reason = None
        results.append({
            **case,
            "commit_exists": exists,
            "declared_evidence_paths": sorted(declared_paths),
            "evidence_paths_present": evidence_paths_present,
            "hit": hit,
            "miss_reason": miss_reason,
            "path": [
                {
                    "source": edge["source"], "target": edge["target"],
                    "kind": edge["kind"], "confidence": edge["confidence"],
                    "location": edge["location"],
                }
                for edge in (path or [])
            ],
        })
    hits = sum(result["hit"] for result in results)
    total = len(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "historical_recall_evaluation",
        "map_repo_sha": data["generated_from"]["repo_sha"],
        "case_set": cases.get("case_set"),
        "total": total,
        "hits": hits,
        "misses": total - hits,
        "hit_rate": round(hits / total, 6) if total else 0.0,
        "hit_rate_percent": round(100 * hits / total, 2) if total else 0.0,
        "results": results,
    }


def resolve_path(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = ap.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="generate impact-map.json from cps")
    build.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    build.add_argument("--output", type=Path, default=DEFAULT_MAP)

    pin = sub.add_parser("pin-routes", help="copy validated route/reconciliation data into a portable oracle")
    pin.add_argument("--static-routes", type=Path, required=True)
    pin.add_argument("--reconciliation", type=Path, required=True)
    pin.add_argument("--output", type=Path, default=DEFAULT_ORACLE)
    pin.add_argument("--oracle-id", required=True)
    pin.add_argument("--source-repo-sha", required=True)

    query = sub.add_parser("query", help="query by file, symbol, endpoint, route rule, or node id")
    query.add_argument("target")
    query.add_argument("--map", dest="map_path", type=Path, default=DEFAULT_MAP)
    query.add_argument("--depth", type=int, default=2)
    query.add_argument("--format", choices=["text", "json"], default="text")
    query.add_argument("--blind-limit", type=int, default=20)

    recall = sub.add_parser("recall", help="evaluate the map against curated historical changes")
    recall.add_argument("--map", dest="map_path", type=Path, default=DEFAULT_MAP)
    recall.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    recall.add_argument("--output", type=Path, default=DEFAULT_RECALL)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.command == "pin-routes":
        data = pin_route_oracle(
            args.static_routes.resolve(), args.reconciliation.resolve(),
            resolve_path(repo_root, args.output), args.oracle_id, args.source_repo_sha,
        )
        print(f"pinned {data['static_routes']['count']} static routes to {resolve_path(repo_root, args.output)}")
        return 0
    if args.command == "build":
        data = build_map(repo_root, resolve_path(repo_root, args.oracle))
        output = resolve_path(repo_root, args.output)
        write_json(output, data, compact=True)
        print(
            f"wrote {output}: {data['counts']['nodes']} nodes, {data['counts']['edges']} edges, "
            f"{data['counts']['blind_spots']} blind spots"
        )
        return 0
    if args.command == "query":
        data = load_json(resolve_path(repo_root, args.map_path))
        try:
            result = query_map(data, args.target, args.depth)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.format == "json":
            print(stable_json(result), end="")
        else:
            print(render_query_text(result, args.blind_limit), end="")
        return 0
    if args.command == "recall":
        data = load_json(resolve_path(repo_root, args.map_path))
        cases = load_json(resolve_path(repo_root, args.cases))
        report = evaluate_recall(data, cases, repo_root)
        output = resolve_path(repo_root, args.output)
        write_json(output, report)
        print(
            f"historical recall: {report['hits']}/{report['total']} "
            f"({report['hit_rate_percent']:.2f}%); misses={report['misses']}"
        )
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
