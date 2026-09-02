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
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


CONCURRENCY_ROOTS = {
    "cps/__init__.py",
    "cps/annotations.py",
    "cps/db.py",
    "cps/kobo.py",
    "cps/server.py",
    "cps/ub.py",
    "cps/web.py",
}

# Prefixes cover package boundaries and future siblings whose names carry the
# same architectural contract.  Helpers imported by these roots are discovered
# from the AST below instead of being copied into this list.
CONCURRENCY_PREFIXES = (
    "cps/api/",
    "cps/gevent",
    "cps/services/annotation_sync/",
)

HARNESS_PATHS = {
    ".github/workflows/docker-image-build-dev.yml",
    ".github/workflows/tests.yml",
    "scripts/ci_path_classification.py",
}

# These backend/Classic files decide whether a browser lands in the SPA. A PR
# can change the entire client entry path without touching frontend/, so they
# belong to the same browser-test gate even though they are not SPA sources.
UI_ROUTING_PATHS = {
    "cps/spa.py",
    "cps/web.py",
    "cps/templates/layout.html",
}

# These files do not enter the ordinary build context, but can still change
# image bytes and therefore override .dockerignore:
#
# * Dockerfile* is read by the builder separately from the filtered context.
# * .dockerignore decides which context bytes the builder receives.
# * docker-image-build-dev.yml supplies BUILD_DATE, VERSION, and PBS_SOURCE as
#   build arguments, so changing it can change the produced image even though
#   .github/ is excluded from the context.
IMAGE_INPUTS_OUTSIDE_CONTEXT = {
    ".dockerignore",
    ".github/workflows/docker-image-build-dev.yml",
}


class DockerIgnorePatternError(ValueError):
    """A .dockerignore pattern cannot be interpreted with Docker semantics."""


def _clean_dockerignore_pattern(line: str, *, first: bool = False) -> str | None:
    """Apply moby/patternmatcher ignorefile preprocessing on POSIX paths."""
    if first:
        line = line.removeprefix("\ufeff")
    # Docker recognizes comments before trimming, so an indented # is a
    # literal pattern rather than a comment.
    if line.startswith("#"):
        return None
    pattern = line.strip()
    if not pattern:
        return None

    negated = pattern.startswith("!")
    if negated:
        pattern = pattern[1:].strip()
        if not pattern:
            raise DockerIgnorePatternError('illegal exclusion pattern: "!"')

    # Docker uses filepath.Clean before discarding one leading slash. On the
    # Linux builders used by this project, posixpath.normpath is the equivalent
    # lexical normalization (including removal of trailing slashes).
    pattern = posixpath.normpath(pattern)
    # Go filepath.Clean collapses repeated leading separators before Docker
    # removes the remaining one. posixpath preserves exactly two leading
    # slashes, so lstrip is needed for parity with Docker on that edge case.
    pattern = pattern.lstrip("/")
    if pattern in ("", "."):
        return None
    return f"!{pattern}" if negated else pattern


def _dockerignore_regex(pattern: str) -> re.Pattern[str]:
    """Compile one cleaned pattern like moby/patternmatcher on Linux.

    Docker extends Go filepath.Match with ``**``. A ``**/`` consumes zero or
    more complete directories, a terminal ``**`` consumes every suffix, and
    ordinary ``*``/``?`` never cross a slash.
    """
    # Moby treats a leading ``**`` followed only by literals as a suffix
    # match. In particular, ``**file`` matches both ``dir/file`` and
    # ``prefixfile``; spelling it as a generic directory glob would miss the
    # latter.
    suffix = pattern[2:] if pattern.startswith("**") else ""
    if pattern.startswith("**") and not any(
        token in suffix for token in ("*", "?", "[", "\\")
    ):
        if suffix.startswith("/"):
            return re.compile(r"^(?:.*/)?" + re.escape(suffix[1:]) + r"$")
        return re.compile(r"^.*" + re.escape(suffix) + r"$")

    regex = ["^"]
    index = 0
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    index += 1
                if index == len(pattern):
                    regex.append(".*")
                else:
                    regex.append("(?:.*/)?")
                continue
            regex.append("[^/]*")
        elif char == "?":
            regex.append("[^/]")
        elif char == "\\":
            index += 1
            if index == len(pattern):
                raise DockerIgnorePatternError(
                    f"bad .dockerignore pattern {pattern!r}: trailing escape"
                )
            regex.append(re.escape(pattern[index]))
        elif char == "[":
            in_class = True
            regex.append(char)
        elif char == "]":
            in_class = False
            regex.append(char)
        elif in_class:
            regex.append(char)
        else:
            regex.append(re.escape(char))
        index += 1
    if in_class:
        raise DockerIgnorePatternError(
            f"bad .dockerignore pattern {pattern!r}: unterminated character class"
        )
    try:
        return re.compile("".join(regex) + "$")
    except re.error as exc:
        raise DockerIgnorePatternError(
            f"bad .dockerignore pattern {pattern!r}: {exc}"
        ) from exc


def _dockerignore_patterns(text: str) -> list[tuple[bool, re.Pattern[str]]]:
    patterns: list[tuple[bool, re.Pattern[str]]] = []
    for index, line in enumerate(text.splitlines()):
        cleaned = _clean_dockerignore_pattern(line, first=index == 0)
        if cleaned is None:
            continue
        negated = cleaned.startswith("!")
        pattern = cleaned[1:] if negated else cleaned
        patterns.append((negated, _dockerignore_regex(pattern)))
    return patterns


def _dockerignore_excludes(
    path: str, patterns: list[tuple[bool, re.Pattern[str]]]
) -> bool:
    """Match a path or any parent, with Docker's last-applicable-rule wins."""
    clean = posixpath.normpath(path.removeprefix("./"))
    if clean in ("", "."):
        return False
    parts = clean.split("/")
    candidates = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    excluded = False
    for negated, pattern in patterns:
        if any(pattern.fullmatch(candidate) for candidate in candidates):
            excluded = not negated
    return excluded


def _image_paths_changed_with_dockerignore(
    paths: Iterable[str], dockerignore_text: str | None
) -> bool:
    changed = _clean_paths(paths)
    if not changed:
        return False
    # Missing policy is not permission to alias an image: fail closed until a
    # repository supplies the SSOT that proves a path is outside the context.
    if dockerignore_text is None:
        return True
    patterns = _dockerignore_patterns(dockerignore_text)
    return any(
        path in IMAGE_INPUTS_OUTSIDE_CONTEXT
        or ("/" not in path and path.startswith("Dockerfile"))
        or not _dockerignore_excludes(path, patterns)
        for path in changed
    )


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
    """Return the architectural roots plus their downward import closure.

    Import discovery follows dependencies imported *by* a root.  It cannot
    discover modules that import a root (reverse dependents), so top-level
    request surfaces such as ``web.py`` and ``kobo.py`` remain explicit roots.
    The depth limit bounds how far each root's dependencies fan out; it is not
    a substitute for naming those reverse-dependent entry points.
    """
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
    depths = {module: 0 for module in root_modules}
    # cps/__init__.py is itself load-bearing, but walking its imports means
    # "concurrency-shaped" degenerates into almost every application module:
    # the app factory intentionally wires the whole program.  The other roots
    # are the engine/request surfaces whose dependencies we want to derive.
    pending = [(module, 0) for module in sorted(root_modules) if module != "cps"]
    while pending:
        module, depth = pending.pop()
        path = module_paths.get(module)
        if path is None or depth >= 2:
            continue
        for imported in sorted(_local_imports(path, module)):
            # ``from cps.constants import ROLE_ADMIN`` records both the module
            # and a possible child name.  Keep actual Python modules only;
            # otherwise class/function names turn into thousands of imaginary
            # paths and make every unrelated edit look concurrency-shaped.
            if imported not in module_paths:
                continue
            imported_depth = depth + 1
            previous_depth = depths.get(imported)
            if previous_depth is not None and previous_depth <= imported_depth:
                continue
            depths[imported] = imported_depth
            discovered.add(imported)
            pending.append((imported, imported_depth))

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


def _clean_paths(paths: Iterable[str]) -> set[str]:
    return {
        clean[2:] if clean.startswith("./") else clean
        for path in paths
        if (clean := path.strip())
    }


def image_paths_changed(paths: Iterable[str], repo_root: Path = Path.cwd()) -> bool:
    """Whether paths can affect image bytes under the build-context policy."""
    try:
        dockerignore_text: str | None = (repo_root / ".dockerignore").read_text(
            encoding="utf-8"
        )
    except FileNotFoundError:
        dockerignore_text = None
    return _image_paths_changed_with_dockerignore(paths, dockerignore_text)


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_file(repo_root: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout
    # A history that predates .dockerignore has no policy proving neutrality.
    if (
        "does not exist" in result.stderr
        or "exists on disk, but not in" in result.stderr
    ):
        return None
    result.check_returncode()
    return None  # pragma: no cover - check_returncode always raises


def latest_image_commit(repo_root: Path, head: str) -> str:
    """Newest first-parent commit at/before ``head`` that affects the image.

    A neutral commit may reuse an earlier image only when this exact commit's
    immutable tag is what ``:dev`` currently names.  Walking first-parent
    history makes intervening image-relevant main commits impossible to skip.
    """
    commits = _git(repo_root, "rev-list", "--first-parent", head).splitlines()
    for commit in commits:
        commit_and_parents = _git(
            repo_root, "rev-list", "--parents", "-n", "1", commit
        ).split()
        if len(commit_and_parents) == 1:
            changed = _git(
                repo_root,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                commit,
            ).splitlines()
        else:
            # Main is normally squash-merged, but compare an actual merge to
            # its first parent too; plain `diff-tree` can emit no paths for a
            # merge commit and would let the alias skip relevant bytes.
            changed = _git(
                repo_root, "diff", "--name-only", commit_and_parents[1], commit
            ).splitlines()
        dockerignore_text = _git_file(repo_root, commit, ".dockerignore")
        if _image_paths_changed_with_dockerignore(changed, dockerignore_text):
            return commit
    raise ValueError(f"no image-relevant commit is reachable from {head}")


def classify_paths(paths: Iterable[str], repo_root: Path) -> dict[str, bool]:
    changed = _clean_paths(paths)
    concurrency = concurrency_paths(repo_root)

    frontend = any(
        path.startswith(("frontend/", "cps/static/app/"))
        or path in HARNESS_PATHS
        or path in UI_ROUTING_PATHS
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
        # tests/conftest.py decides whether the Docker integration tests can
        # authenticate at all, so a change to it can silently disable the whole
        # lane while every individual test file is untouched. Changing one
        # integration test already runs the lane; changing the fixture all of
        # them depend on must too.
        or path == "tests/conftest.py"
        for path in changed
    )
    concurrency_touched = any(
        path in concurrency or path.startswith(CONCURRENCY_PREFIXES)
        for path in changed
    )
    # .dockerignore is the build-context SSOT. Image-neutral main commits still
    # get an immutable sha tag pointing at the unchanged :dev manifest; they do
    # not rebuild or move :dev and therefore do not restart the canary.
    image = image_paths_changed(changed, repo_root)
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
    parser.add_argument(
        "--latest-image-commit",
        metavar="REV",
        help="print the newest image-relevant first-parent commit at/before REV",
    )
    parser.add_argument("paths", nargs="*", help="paths; stdin is used when omitted")
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    if args.latest_image_commit:
        if args.github_output or args.paths:
            parser.error("--latest-image-commit cannot be combined with path classification")
        try:
            print(latest_image_commit(repo_root, args.latest_image_commit))
        except (subprocess.CalledProcessError, ValueError) as exc:
            parser.error(str(exc))
        return 0

    paths = args.paths or [line for line in __import__("sys").stdin.read().splitlines()]
    result = classify_paths(paths, repo_root)

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={'true' if value else 'false'}\n")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
