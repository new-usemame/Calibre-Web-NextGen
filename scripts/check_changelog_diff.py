#!/usr/bin/env python3
"""Reject pull-request changelog diffs that swallow released structure."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


# The canonical file used an en dash through much of v4.0 and a hyphen later.
# This guard cares about heading identity, not date typography.
RELEASE_HEADING = re.compile(r"^## \[(v\d+\.\d+\.\d+)\]", re.MULTILINE)
ENTRY_LEAD = re.compile(r"^- \*\*", re.MULTILINE)
FRAGMENT_PATH = re.compile(r"^changelog\.d/[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
NON_SHIPPING_PATH_PREFIXES = (
    # Translation refreshes ship, but a .po/.pot-only diff is not a release-note
    # event an outside translator should have to author (operator ruling
    # 2026-08-27; #1896 was forced to invent a fragment).
    "cps/translations/",
    "docs/",
    # Example configuration files document; they do not ship.
    "examples/",
    "findings/",
    "frontend/e2e/",
    "notes/",
    "tests/",
    "wiki-src/",
)
NON_SHIPPING_PATHS = frozenset(
    {
        # The upstream-comparison ledger records changes that have ALREADY
        # shipped, with their squash SHA and containing release tag. A
        # post-release backfill of those fields is bookkeeping about the past,
        # so demanding a release-note fragment for it can only be satisfied by
        # inventing a duplicate entry for a change the changelog already
        # carries (OBSERVED 2026-08-28: the v4.1.42 backfill PR went red here).
        "CHANGES-vs-upstream.md",
        "changelog.d/README.md",
        "messages.pot",
        "scripts/check_changelog_diff.py",
    }
)


def _distinct_entries(text: str) -> set[str]:
    """The set of distinct top-level release-note bullets in ``text``.

    A bullet runs from its ``- **`` line to the next bullet or heading, and is
    normalised on whitespace so that a re-wrap is not read as a different entry.

    DISTINCT, not a count, because two concurrent PRs can each move the same
    entry into the same section: git merges both insertions cleanly and the file
    ends up carrying one bullet twice. Removing the copy takes the raw count
    down by one while losing nothing, and a count-based guard fires on that
    repair -- red on the correct fix, with no way to say so. Comparing distinct
    bodies keeps every case the count caught: swallowing two entries into one
    still drops a distinct body, and a pure re-wording still contributes exactly
    one body before and after.
    """
    entries: set[str] = set()
    for block in re.split(r"^(?=- \*\*)|^(?=#)", text, flags=re.MULTILINE):
        if not block.startswith("- **"):
            continue
        entries.add(" ".join(block.split()))
    return entries


def structural_regressions(base: str, proposed: str) -> list[str]:
    """Return user-facing errors for structure lost from ``base``."""
    errors: list[str] = []

    # Counts, not a set difference: re-wording an entry replaces one body with
    # another, so every body in `base` legitimately disappears on a re-word and
    # a set difference would reject the edit this guard exists to allow.
    base_entries = len(_distinct_entries(base))
    proposed_entries = len(_distinct_entries(proposed))
    if proposed_entries < base_entries:
        errors.append(
            "CHANGELOG.md loses "
            f"{base_entries - proposed_entries} top-level '- **' release-note "
            "bullet(s). Rewrite or move entries instead of deleting them."
        )

    base_releases = set(RELEASE_HEADING.findall(base))
    proposed_releases = set(RELEASE_HEADING.findall(proposed))
    missing_releases = sorted(base_releases - proposed_releases)
    if missing_releases:
        errors.append(
            "CHANGELOG.md removes existing release heading(s): "
            + ", ".join(missing_releases)
            + ". A stale branch may have overwritten a release section; rebase "
            "or merge the current target branch before proceeding."
        )

    return errors


def pull_request_regressions(
    target: str, branch_point: str, proposed: str
) -> list[str]:
    """Check a PR only when that branch actually edited ``CHANGELOG.md``."""
    if branch_point == proposed:
        return []
    return structural_regressions(target, proposed)


def _is_non_shipping_path(path: str) -> bool:
    return path in NON_SHIPPING_PATHS or path.startswith(
        NON_SHIPPING_PATH_PREFIXES
    )


def changelog_requirement_errors(changed_paths: list[str]) -> list[str]:
    """Require a changelog entry unless every changed path is non-shipping."""
    if "CHANGELOG.md" in changed_paths:
        return []
    if any(
        FRAGMENT_PATH.fullmatch(path) and path != "changelog.d/README.md"
        for path in changed_paths
    ):
        return []
    if changed_paths and all(_is_non_shipping_path(path) for path in changed_paths):
        return []
    return [
        "This PR changes shipping paths but neither CHANGELOG.md nor "
        "changelog.d/<pr-or-slug>.md. Add a categorized changelog fragment; "
        "changelog.d/README.md documents the format."
    ]


def pull_request_errors(
    changed_paths: list[str],
    target: str,
    branch_point: str,
    proposed: str,
) -> list[str]:
    """Return every requirement and structural error for a pull request."""
    errors = changelog_requirement_errors(changed_paths)
    errors.extend(pull_request_regressions(target, branch_point, proposed))
    return errors


def _file_at(ref: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:CHANGELOG.md"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "git show failed"
        raise RuntimeError(f"cannot read CHANGELOG.md at {ref}: {detail}")
    return result.stdout


def _merge_base(base_ref: str, head_ref: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", base_ref, head_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip() or "git merge-base returned no commit"
        raise RuntimeError(
            f"cannot find merge base for {base_ref} and {head_ref}: {detail}"
        )
    return result.stdout.strip()


def _changed_paths(branch_point: str, head_ref: str, cwd=None) -> list[str]:
    # --no-renames, deliberately. With rename detection on, `git mv` out of a
    # shipping directory is reported by its DESTINATION only, so
    # `git mv cps/thing.py docs/thing.py` reaches the classifier as a lone
    # `docs/` path -- non-shipping -- and a module that vanished from the
    # application merges with no release note. Splitting the rename into its
    # delete and its add keeps the shipping side visible to the guard.
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", branch_point, head_ref],
        check=False,
        capture_output=True,
        # `cwd` exists so a test can point this at a throwaway repository
        # WITHOUT chdir()ing the process. A test-local chdir is global to the
        # interpreter and perturbs anything running on a background thread.
        cwd=cwd,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip() or "git diff failed"
        raise RuntimeError(
            f"cannot list PR paths between {branch_point} and {head_ref}: {detail}"
        )
    return [
        raw.decode("utf-8", errors="surrogateescape")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject structural CHANGELOG.md loss between two git refs."
    )
    parser.add_argument("base_ref")
    parser.add_argument("head_ref")
    args = parser.parse_args(argv)

    try:
        branch_point = _merge_base(args.base_ref, args.head_ref)
        errors = pull_request_errors(
            _changed_paths(branch_point, args.head_ref),
            _file_at(args.base_ref),
            _file_at(branch_point),
            _file_at(args.head_ref),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("CHANGELOG integrity guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "CHANGELOG integrity guard passed: "
        "the entry requirement is satisfied or every changed path is "
        "non-shipping, and no PR-authored release structure was lost."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
