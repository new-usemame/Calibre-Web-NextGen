#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble isolated changelog fragments into CHANGELOG.md.

PRs write one direct child of ``changelog.d/`` instead of contending on the
single ``CHANGELOG.md`` insertion point.  This script is the only writer which
folds those fragments into the canonical Keep-a-Changelog sections.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import stat
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


CATEGORY_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
CATEGORY_SET = set(CATEGORY_ORDER)
SECTION_HEADING = re.compile(r"^## \[([^]]+)\][^\n]*$", re.MULTILINE)
CATEGORY_HEADING = re.compile(r"^### ([A-Za-z][A-Za-z ]*)$")
FRAGMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
VERSION = re.compile(r"^v\d+\.\d+\.\d+$")


class AssemblyError(ValueError):
    """A user-actionable fragment or changelog format error."""


@dataclass(frozen=True)
class Section:
    label: str
    heading: str
    heading_start: int
    body_start: int
    end: int


def _sections(text: str) -> list[Section]:
    matches = list(SECTION_HEADING.finditer(text))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        if text[body_start : body_start + 1] == "\n":
            body_start += 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            Section(
                label=match.group(1),
                heading=match.group(0),
                heading_start=match.start(),
                body_start=body_start,
                end=end,
            )
        )
    return sections


def _section(text: str, label: str) -> Section:
    found = [section for section in _sections(text) if section.label == label]
    if len(found) != 1:
        raise AssemblyError(
            f"CHANGELOG.md must contain exactly one '## [{label}]' section; found {len(found)}"
        )
    return found[0]


def _entry_key(entry: str) -> str:
    return " ".join(entry.split())


def _parse_body(body: str, source: str) -> dict[str, list[str]]:
    """Parse category headings and bold-lead bullets without rewording them."""
    parsed = {category: [] for category in CATEGORY_ORDER}
    seen_headings: set[str] = set()
    category: str | None = None
    entry: list[str] | None = None

    def finish_entry() -> None:
        nonlocal entry
        if entry is None:
            return
        while entry and not entry[-1].strip():
            entry.pop()
        if not entry:
            raise AssemblyError(f"{source}: empty changelog entry")
        assert category is not None
        parsed[category].append("\n".join(entry))
        entry = None

    for line_number, raw_line in enumerate(body.splitlines(), start=1):
        line = raw_line.rstrip()
        heading = CATEGORY_HEADING.match(line)
        if heading:
            finish_entry()
            candidate = heading.group(1)
            if candidate not in CATEGORY_SET:
                raise AssemblyError(
                    f"{source}:{line_number}: unsupported category '{candidate}'; "
                    f"choose one of {', '.join(CATEGORY_ORDER)}"
                )
            if candidate in seen_headings:
                raise AssemblyError(
                    f"{source}:{line_number}: duplicate '### {candidate}' heading"
                )
            seen_headings.add(candidate)
            category = candidate
            continue

        if line.startswith("### "):
            raise AssemblyError(f"{source}:{line_number}: malformed category heading")
        if line.startswith("## "):
            raise AssemblyError(
                f"{source}:{line_number}: fragments cannot contain release headings"
            )
        if line.startswith("- **"):
            if category is None:
                raise AssemblyError(
                    f"{source}:{line_number}: entry appears before a '### <category>' heading"
                )
            finish_entry()
            entry = [line]
            continue
        if not line:
            if entry is not None:
                entry.append("")
            continue
        if entry is not None and (
            raw_line.startswith("  ") or raw_line.startswith("\t")
        ):
            entry.append(line)
            continue
        raise AssemblyError(
            f"{source}:{line_number}: expected a '### <category>' heading, a '- **' entry, "
            "or an indented continuation"
        )

    finish_entry()
    for heading in seen_headings:
        if not parsed[heading]:
            raise AssemblyError(f"{source}: '### {heading}' contains no entries")
    return parsed


def _merge_entries(
    base: dict[str, list[str]], additions: Iterable[dict[str, list[str]]]
) -> dict[str, list[str]]:
    merged = {category: list(base.get(category, [])) for category in CATEGORY_ORDER}
    seen = {_entry_key(entry) for entries in merged.values() for entry in entries}
    for addition in additions:
        for category in CATEGORY_ORDER:
            for entry in addition.get(category, []):
                key = _entry_key(entry)
                if key in seen:
                    continue
                merged[category].append(entry)
                seen.add(key)
    return merged


def _has_entries(categories: dict[str, list[str]]) -> bool:
    return any(categories[category] for category in CATEGORY_ORDER)


def _render_body(categories: dict[str, list[str]]) -> str:
    blocks: list[str] = []
    for category in CATEGORY_ORDER:
        entries = categories[category]
        if entries:
            blocks.append(f"### {category}\n\n" + "\n\n".join(entries))
    if not blocks:
        return "\n"
    return "\n" + "\n\n".join(blocks) + "\n\n"


def _replace_body(text: str, section: Section, body: str) -> str:
    return text[: section.body_start] + body + text[section.end :]


def _fragment_paths(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise AssemblyError(f"fragment path is not a directory: {directory}")
    paths: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file() or path.name == "README.md":
            continue
        if path.suffix != ".md":
            continue
        if not FRAGMENT_NAME.fullmatch(path.name):
            raise AssemblyError(
                f"{path}: fragment names must match {FRAGMENT_NAME.pattern} and be direct children"
            )
        paths.append(path)
    # Accepted names are ASCII, so Python's stable lexical order is exactly the
    # LC_ALL=C byte order used by the release shell.
    return sorted(paths, key=lambda path: path.name)


def _write_atomic(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def assemble(
    changelog: Path,
    fragments_directory: Path,
    *,
    version: str | None = None,
    release_date: str | None = None,
) -> tuple[int, bool]:
    """Assemble fragments, returning ``(consumed_count, changelog_changed)``."""
    if not changelog.is_file():
        raise AssemblyError(f"changelog does not exist: {changelog}")
    text = changelog.read_text(encoding="utf-8")
    unreleased = _section(text, "Unreleased")
    unreleased_entries = _parse_body(
        text[unreleased.body_start : unreleased.end], "CHANGELOG.md [Unreleased]"
    )

    fragment_paths = _fragment_paths(fragments_directory)
    fragment_entries = [
        _parse_body(path.read_text(encoding="utf-8"), path.name)
        for path in fragment_paths
    ]

    if version is None:
        if release_date is not None:
            raise AssemblyError("--date requires --version")
        if not fragment_paths:
            return 0, False
        merged = _merge_entries(unreleased_entries, fragment_entries)
        updated = _replace_body(text, unreleased, _render_body(merged))
    else:
        if not VERSION.fullmatch(version):
            raise AssemblyError("--version must have the form vX.Y.Z")
        if release_date is None:
            raise AssemblyError("--version requires --date YYYY-MM-DD")
        try:
            dt.date.fromisoformat(release_date)
        except ValueError as exc:
            raise AssemblyError(
                "--date must be a real ISO date in YYYY-MM-DD form"
            ) from exc

        existing = [section for section in _sections(text) if section.label == version]
        if len(existing) > 1:
            raise AssemblyError(f"CHANGELOG.md contains duplicate [{version}] sections")
        if existing:
            target = existing[0]
            expected_heading = f"## [{version}] - {release_date}"
            if target.heading != expected_heading:
                raise AssemblyError(
                    f"existing [{version}] heading is '{target.heading}', expected '{expected_heading}'"
                )
            if _has_entries(unreleased_entries):
                raise AssemblyError(
                    f"[{version}] already exists while [Unreleased] still has entries; "
                    "refusing to guess which release owns them"
                )
            target_entries = _parse_body(
                text[target.body_start : target.end], f"CHANGELOG.md [{version}]"
            )
            merged = _merge_entries(target_entries, fragment_entries)
            updated = _replace_body(text, target, _render_body(merged))
        else:
            merged = _merge_entries(unreleased_entries, fragment_entries)
            if not _has_entries(merged):
                raise AssemblyError(
                    "nothing is present in [Unreleased] or changelog.d to release"
                )
            release = f"## [{version}] - {release_date}\n" + _render_body(merged)
            updated = (
                text[: unreleased.body_start] + "\n" + release + text[unreleased.end :]
            )

    changed = updated != text
    if changed:
        _write_atomic(changelog, updated)
    # Delete only after the canonical write is durable. Entry-level dedupe makes
    # rerunning safe if the process was interrupted between replace and unlink.
    for path in fragment_paths:
        path.unlink()
    return len(fragment_paths), changed


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Deterministically assemble changelog.d fragments into CHANGELOG.md."
    )
    parser.add_argument("--changelog", type=Path, default=root / "CHANGELOG.md")
    parser.add_argument("--fragments-dir", type=Path, default=root / "changelog.d")
    parser.add_argument("--version", help="release section to create, in vX.Y.Z form")
    parser.add_argument("--date", dest="release_date", help="release date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    try:
        consumed, changed = assemble(
            args.changelog,
            args.fragments_dir,
            version=args.version,
            release_date=args.release_date,
        )
    except (AssemblyError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    destination = f"[{args.version}]" if args.version else "[Unreleased]"
    if consumed == 0 and not changed:
        print("No changelog fragments to assemble; CHANGELOG.md is unchanged.")
    else:
        print(
            f"Assembled {consumed} changelog fragment(s) into {destination}; "
            f"CHANGELOG.md {'updated' if changed else 'already contained every entry'}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
