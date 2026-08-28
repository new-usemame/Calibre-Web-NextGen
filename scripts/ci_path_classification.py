# SPDX-License-Identifier: GPL-3.0-or-later
"""Classify pull-request paths for the CI lanes that must run.

The concurrency class deliberately starts from architectural entry points and
walks their local Python imports.  It does not enumerate request-handler call
sites: a newly extracted engine helper becomes protected as soon as one of the
entry points imports it.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
from typing import Iterable


CONCURRENCY_ROOTS = {
    "cps/__init__.py",
    "cps/annotations.py",
    "cps/db.py",
    "cps/server.py",
    "cps/ub.py",
}

# Prefixes cover package boundaries and future siblings whose names carry the
# same architectural contract.  Helpers imported by these roots are discovered
# from the AST below instead of being copied into this list.
CONCURRENCY_PREFIXES = (
    "cps/api/annotations",
    "cps/gevent",
    "cps/services/annotation_sync/",
)

HARNESS_PATHS = {
    ".github/workflows/docker-image-build-dev.yml",
    ".github/workflows/tests.yml",
    "scripts/ci_path_classification.py",
}


def _path_to_module(path: str) -> str | None:
    candidate = PurePosixPath(path)
    if candidate.suffix != ".py" or not candidate.parts or candidate.parts[0] != "cps":
        return None
    parts = list(candidate.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _relative_base(module: str, is_package: bool, level: int) -> list[str]:
    package = module.split(".") if is_package else module.split(".")[:-1]
    # ``from .x`` stays in the current package; every extra dot climbs once.
    climbs = max(level - 1, 0)
    return package[: max(len(package) - climbs, 0)]


def _local_imports(path: Path, module: str) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # Other gates own syntax/import failures.  Classification must remain
        # conservative and keep the root itself protected.
        return set()

    imports: set[str] = set()
    is_package = path.name == "__init__.py"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("cps"))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            base_parts = _relative_base(module, is_package, node.level)
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
        else:
            base = node.module or ""

        if base.startswith("cps"):
            imports.add(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            child = ".".join(part for part in (base, alias.name) if part)
            if child.startswith("cps"):
                imports.add(child)
    return imports


def concurrency_paths(repo_root: Path) -> set[str]:
    """Return the architectural roots plus their local import closure."""
    module_paths: dict[str, Path] = {}
    for path in (repo_root / "cps").rglob("*.py"):
        relative = path.relative_to(repo_root).as_posix()
        module = _path_to_module(relative)
        if module:
            module_paths[module] = path

    root_modules = {
        module
        for path in CONCURRENCY_ROOTS
        if (module := _path_to_module(path)) is not None
    }
    root_modules.update(
        module
        for module, path in module_paths.items()
        if path.relative_to(repo_root).as_posix().startswith(CONCURRENCY_PREFIXES)
    )

    discovered = set(root_modules)
    # cps/__init__.py is itself load-bearing, but walking its imports means
    # "concurrency-shaped" degenerates into almost every application module:
    # the app factory intentionally wires the whole program.  The other roots
    # are the engine/request surfaces whose dependencies we want to derive.
    pending = [(module, 0) for module in root_modules if module != "cps"]
    while pending:
        module, depth = pending.pop()
        path = module_paths.get(module)
        if path is None or depth >= 2:
            continue
        for imported in _local_imports(path, module):
            # ``from cps.constants import ROLE_ADMIN`` records both the module
            # and a possible child name.  Keep actual Python modules only;
            # otherwise class/function names turn into thousands of imaginary
            # paths and make every unrelated edit look concurrency-shaped.
            if imported not in module_paths or imported in discovered:
                continue
            discovered.add(imported)
            pending.append((imported, depth + 1))

    paths = set(CONCURRENCY_ROOTS)
    for module in discovered:
        path = module_paths.get(module)
        if path is not None:
            paths.add(path.relative_to(repo_root).as_posix())
    paths.update(
        path.relative_to(repo_root).as_posix()
        for path in module_paths.values()
        if path.relative_to(repo_root).as_posix().startswith(CONCURRENCY_PREFIXES)
    )
    return paths


def classify_paths(paths: Iterable[str], repo_root: Path) -> dict[str, bool]:
    changed = {
        clean[2:] if clean.startswith("./") else clean
        for path in paths
        if (clean := path.strip())
    }
    concurrency = concurrency_paths(repo_root)

    frontend = any(
        path.startswith(("frontend/", "cps/static/app/")) or path in HARNESS_PATHS
        for path in changed
    )
    build = any(
        path == "Dockerfile"
        or ("/" not in path and "requirements" in path and path.endswith(".txt"))
        or path.startswith(("root/", "cps/", "scripts/", "tests/docker/", "tests/integration/"))
        and (
            not path.startswith("cps/")
            or path.endswith(".py")
        )
        or path == ".github/workflows/tests.yml"
        for path in changed
    )
    concurrency_touched = any(
        path in concurrency or path.startswith(CONCURRENCY_PREFIXES)
        for path in changed
    )
    # Mirrors the dev channel's historical paths-ignore policy, but as data an
    # alias job can consume.  Image-neutral main commits still get an immutable
    # sha tag pointing at the unchanged :dev manifest; they do not rebuild or
    # move :dev and therefore do not restart the canary deployment.
    image = any(
        not (
            path.endswith(".md")
            or path.startswith(("docs/", "tests/", ".github/"))
        )
        for path in changed
    )
    return {
        "frontend": frontend,
        "build": build,
        "concurrency": concurrency_touched,
        "image": image,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--github-output",
        type=Path,
        help="append key=value records for a GitHub Actions step",
    )
    parser.add_argument("paths", nargs="*", help="paths; stdin is used when omitted")
    args = parser.parse_args()
    paths = args.paths or [line for line in __import__("sys").stdin.read().splitlines()]
    result = classify_paths(paths, args.repo_root.resolve())

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={'true' if value else 'false'}\n")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
