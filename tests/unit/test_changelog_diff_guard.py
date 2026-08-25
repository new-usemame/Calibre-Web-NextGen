# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin the PR-level guard against structural CHANGELOG loss."""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = Path(
    os.environ.get("CWNG_CHANGELOG_GUARD", ROOT / "scripts" / "check_changelog_diff.py")
)
SPEC = importlib.util.spec_from_file_location("cwng_changelog_guard", GUARD_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)
changelog_requirement_errors = GUARD.changelog_requirement_errors
pull_request_errors = GUARD.pull_request_errors
pull_request_regressions = GUARD.pull_request_regressions
structural_regressions = GUARD.structural_regressions


def test_missing_release_heading_is_rejected():
    base = (
        """## [Unreleased]\n\n## [v4.1.27] - 2026-08-02\n\n### Fixed\n- **A fix.**\n"""
    )
    stale_branch = """## [Unreleased]\n\n### Fixed\n- **A new fix.**\n- **A fix.**\n"""
    errors = structural_regressions(base, stale_branch)
    assert any("v4.1.27" in error for error in errors)


def test_missing_historical_en_dash_release_heading_is_rejected():
    base = """## [v4.0.147] – 2026-06-05\n"""
    errors = structural_regressions(base, "")
    assert any("v4.0.147" in error for error in errors)


def test_net_release_bullet_loss_is_rejected():
    base = """### Fixed\n- **First fix.**\n- **Second fix.**\n"""
    swallowed = """### Fixed\n- **First fix now contains both fixes.**\n"""
    errors = structural_regressions(base, swallowed)
    assert any("loses 1" in error for error in errors)


def test_removing_an_accidentally_duplicated_entry_is_allowed():
    """Two PRs can move the same entry into the same section concurrently.

    #1508 and #1530 both relocated the Discover bullet into the existing Fixed
    block minutes apart; git merged both insertions without conflict and left
    [Unreleased] carrying it twice. The headings were correct, so
    test_no_section_repeats_a_kind_heading stayed green and nothing noticed.
    Deleting the copy loses no entry, and the guard must not block the repair.
    """
    duplicated = (
        """## [Unreleased]\n\n### Fixed\n"""
        """- **A fix.** Body text.\n"""
        """- **A fix.** Body text.\n"""
    )
    deduped = """## [Unreleased]\n\n### Fixed\n- **A fix.** Body text.\n"""
    assert structural_regressions(duplicated, deduped) == []


def test_a_duplicate_immediately_before_a_heading_is_still_a_duplicate():
    """A bullet must end at the next heading, not swallow it.

    The obvious way to split entries -- `re.split(r"\\n(?=- \\*\\*)", text)` --
    makes the LAST bullet of a section absorb everything up to the next bullet,
    including the `### Added` line that follows it. Two byte-identical entries
    then normalise to strings differing by a trailing " ### Added" and compare
    as distinct, so the checker reports zero duplicates on a file that has one.

    That position is not an edge case, it is *the* case: a merge appends to the
    end of the section it touches, so a merge-created duplicate lands there.
    CWNG FRONTEND hit exactly this on 2026-08-10 -- a duplicate-checker returned
    "0 duplicated" for a file with two identical entries at lines 45 and 53, and
    the branch was pushed on that reading.

    A duplicate in the MIDDLE of a section passes either implementation and
    proves nothing, which is why this one is pinned separately.
    """
    duplicated = (
        """## [Unreleased]\n\n### Fixed\n"""
        """- **A fix.** Body text.\n"""
        """- **A fix.** Body text.\n"""
        """\n### Added\n\n- **Something else.** More.\n"""
    )
    deduped = (
        """## [Unreleased]\n\n### Fixed\n"""
        """- **A fix.** Body text.\n"""
        """\n### Added\n\n- **Something else.** More.\n"""
    )
    assert structural_regressions(duplicated, deduped) == []

    # Control, same position: a genuinely different entry disappearing there
    # must still be rejected, or the test above would pass on a guard that had
    # simply stopped looking.
    two_real = (
        """## [Unreleased]\n\n### Fixed\n"""
        """- **A fix.** Body.\n- **A different fix.** Other body.\n"""
        """\n### Added\n"""
    )
    one_gone = """## [Unreleased]\n\n### Fixed\n- **A fix.** Body.\n\n### Added\n"""
    assert any("loses 1" in e for e in structural_regressions(two_real, one_gone))


def test_a_rewrapped_duplicate_is_still_the_same_entry():
    """Line wrapping must not make a duplicate look like a distinct entry."""
    duplicated = (
        """### Fixed\n"""
        """- **A fix.** Body text that wraps.\n"""
        """- **A fix.** Body\n  text that wraps.\n"""
    )
    deduped = """### Fixed\n- **A fix.** Body text that wraps.\n"""
    assert structural_regressions(duplicated, deduped) == []


def test_wording_edits_reordering_and_release_sectioning_are_allowed():
    base = """## [Unreleased]\n\n### Fixed\n- **First wording.**\n- **Second wording.**\n"""
    proposed = """## [Unreleased]\n\n## [v4.1.28] - 2026-08-03\n\n### Fixed\n- **Rewritten second wording.**\n- **Rewritten first wording.**\n"""
    assert structural_regressions(base, proposed) == []


def test_stale_pr_that_did_not_edit_changelog_is_allowed():
    branch_point = """## [Unreleased]\n\n### Fixed\n- **Old fix.**\n"""
    current_target = branch_point + "\n## [v4.1.27] - 2026-08-02\n"
    assert pull_request_regressions(current_target, branch_point, branch_point) == []


def test_stale_pr_that_did_edit_changelog_must_preserve_current_releases():
    branch_point = """## [Unreleased]\n\n### Fixed\n- **Old fix.**\n"""
    current_target = branch_point + "\n## [v4.1.27] - 2026-08-02\n"
    proposed = branch_point.replace("- **Old fix.**", "- **PR fix.**\n- **Old fix.**")
    errors = pull_request_regressions(current_target, branch_point, proposed)
    assert any("v4.1.27" in error for error in errors)


def test_pr_can_satisfy_the_changelog_rule_with_a_fragment():
    assert (
        changelog_requirement_errors(
            ["cps/web.py", "tests/unit/test_web.py", "changelog.d/reader-back-link.md"]
        )
        == []
    )


def test_direct_changelog_edits_remain_accepted_during_cutover():
    assert changelog_requirement_errors(["cps/web.py", "CHANGELOG.md"]) == []


def test_pr_cannot_satisfy_the_changelog_rule_with_neither_form():
    errors = changelog_requirement_errors(["cps/web.py"])
    assert len(errors) == 1
    assert "CHANGELOG.md" in errors[0]
    assert "changelog.d/<pr-or-slug>.md" in errors[0]


def test_findings_ledger_only_pr_does_not_require_a_changelog_entry():
    assert changelog_requirement_errors(["findings/kobo/F-66edbc.md"]) == []


def test_mixing_a_findings_ledger_with_shipping_code_requires_an_entry():
    errors = changelog_requirement_errors(
        ["findings/kobo/F-66edbc.md", "cps/readingservices.py"]
    )
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_other_allowlisted_non_shipping_paths_do_not_require_an_entry():
    for path in (
        "notes/kobo-hardware-run.md",
        "docs/install/compose.md",
        "tests/unit/test_changelog_diff_guard.py",
        "changelog.d/README.md",
        "scripts/check_changelog_diff.py",
    ):
        assert changelog_requirement_errors([path]) == []


def test_github_paths_and_top_level_dotfiles_are_not_blanket_exempt():
    for path in (".github/workflows/tests.yml", ".dockerignore", ".editorconfig"):
        assert changelog_requirement_errors([path])


def test_changelog_directory_readme_is_documentation_not_a_fragment():
    errors = changelog_requirement_errors(["cps/web.py", "changelog.d/README.md"])
    assert errors


def test_non_shipping_exemption_does_not_bypass_structural_regressions():
    base = """## [Unreleased]\n\n### Fixed\n- **First fix.**\n- **Second fix.**\n"""
    damaged = """## [Unreleased]\n\n### Fixed\n- **First fix.**\n"""
    errors = pull_request_errors(
        ["findings/kobo/F-66edbc.md"], base, base, damaged
    )
    assert any("loses 1" in error for error in errors)


def test_fragments_are_direct_markdown_children_with_safe_names():
    assert changelog_requirement_errors(["changelog.d/fix-123.md"]) == []
    assert changelog_requirement_errors(["changelog.d/nested/fix.md"])
    assert changelog_requirement_errors(["changelog.d/fix.txt"])


def test_pr_ci_invokes_guard_with_complete_git_history():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    fast_tests = workflow.split("  fast-tests:", 1)[1].split("\n  #", 1)[0]
    changed_paths = workflow.split("  changed_paths:", 1)[1].split("\n  #", 1)[0]
    assert "fetch-depth: 0" in fast_tests
    assert "fetch-depth: 0" in changed_paths
    assert (
        'python3 scripts/check_changelog_diff.py "$BASE_SHA" "$HEAD_SHA"'
        in changed_paths
    )
