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


def test_frontend_e2e_only_pr_does_not_require_a_changelog_entry():
    assert changelog_requirement_errors(["frontend/e2e/shelf-visibility.spec.ts"]) == []


def test_mixing_frontend_e2e_with_shipping_frontend_still_requires_an_entry():
    errors = changelog_requirement_errors(
        ["frontend/e2e/shelf-visibility.spec.ts", "frontend/src/pages/Shelf.tsx"]
    )
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_frontend_unit_tests_only_pr_does_not_require_a_changelog_entry():
    assert changelog_requirement_errors(
        ["frontend/tests/unit/libModuleResolution.test.ts"]
    ) == []


def test_current_frontend_unit_lane_does_not_require_a_changelog_entry():
    assert changelog_requirement_errors(
        ["frontend/unit/e2eUserOwnership.test.ts"]
    ) == []


def test_playwright_config_is_non_shipping_verification_config():
    assert changelog_requirement_errors(["frontend/playwright.config.ts"]) == []


def test_mixing_frontend_tests_with_shipping_frontend_still_requires_an_entry():
    errors = changelog_requirement_errors(
        [
            "frontend/tests/unit/libModuleResolution.test.ts",
            "frontend/src/lib/api.ts",
        ]
    )
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_translation_only_pr_does_not_require_a_changelog_entry():
    assert changelog_requirement_errors(
        ["cps/translations/ru/LC_MESSAGES/messages.po", "messages.pot"]
    ) == []


def test_mixing_a_translation_with_shipping_code_requires_an_entry():
    errors = changelog_requirement_errors(
        ["cps/translations/ru/LC_MESSAGES/messages.po", "cps/web.py"]
    )
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_mixing_a_findings_ledger_with_shipping_code_requires_an_entry():
    errors = changelog_requirement_errors(
        ["findings/kobo/F-66edbc.md", "cps/readingservices.py"]
    )
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_mission_state_only_pr_does_not_require_a_changelog_entry():
    """`state/` holds agent mission records, which have no release-note form.

    This case was unreachable until PR #2034: every earlier `state/`-only
    COMMIT rode inside a PR that also carried shipping code, so that PR's
    fragment satisfied the guard and the gap never surfaced. The first
    genuinely state-only PR went red, and the only way to satisfy the guard
    would have been to invent a user-facing changelog entry for a note about
    which SHAs merged -- the same failure recorded in this module for
    translations (#1896) and the CHANGES-vs-upstream backfill.
    """
    assert changelog_requirement_errors(["state/MISSION.md"]) == []


def test_mixing_mission_state_with_shipping_code_requires_an_entry():
    errors = changelog_requirement_errors(["state/MISSION.md", "cps/spa.py"])
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_other_allowlisted_non_shipping_paths_do_not_require_an_entry():
    for path in (
        "notes/kobo-hardware-run.md",
        "docs/install/compose.md",
        "wiki-src/Contributing.md",
        "examples/.env.example",
        "tests/unit/test_changelog_diff_guard.py",
        "changelog.d/README.md",
        "scripts/check_changelog_diff.py",
        "CHANGES-vs-upstream.md",
    ):
        assert changelog_requirement_errors([path]) == []


def test_upstream_comparison_ledger_alone_needs_no_fragment():
    """A post-release SHA/tag backfill documents changes that already shipped.

    Requiring a fragment there can only be satisfied by inventing a second
    release-note entry for a change the changelog already announced.
    """
    assert changelog_requirement_errors(["CHANGES-vs-upstream.md"]) == []


def test_the_ledger_mixed_with_shipping_code_still_requires_an_entry():
    errors = changelog_requirement_errors(["CHANGES-vs-upstream.md", "cps/web.py"])
    assert len(errors) == 1
    assert "shipping paths" in errors[0]


def test_ci_image_verdict_harness_is_non_shipping():
    assert changelog_requirement_errors(
        [
            ".github/workflows/docker-image-build-dev.yml",
            ".github/workflows/tests.yml",
            "scripts/alias-e2e-image.sh",
            "scripts/check-e2e-image-producer.py",
            "scripts/resolve-e2e-image.sh",
            "tests/unit/test_ci_e2e_commit_gate.py",
        ]
    ) == []


def test_unlisted_github_paths_and_top_level_dotfiles_are_not_blanket_exempt():
    for path in (".github/workflows/release.yml", ".dockerignore", ".editorconfig"):
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


def test_a_rename_out_of_a_shipping_directory_still_reports_the_shipping_path(tmp_path):
    """`git mv cps/x.py wiki-src/x.py` must not read as a wiki-only change.

    With git's rename detection on, `git diff --name-only` reports a detected
    rename by its DESTINATION alone. A module moved out of `cps/` into any
    exempt directory would then reach the classifier as a single non-shipping
    path, and code that vanished from the application would merge with no
    release note -- the exact loss this guard exists to prevent.

    This exercises `_changed_paths` against a real repository rather than a
    hand-written path list, because the defect lives in how the paths are
    OBTAINED, not in how they are classified. A test that passes both paths in
    by hand cannot fail on it.
    """
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True,
        )

    git("init", "-q", ".")
    git("config", "user.email", "guard@test.invalid")
    git("config", "user.name", "guard-test")
    (tmp_path / "cps").mkdir()
    (tmp_path / "wiki-src").mkdir()
    (tmp_path / "cps" / "shipping_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("mv", "cps/shipping_module.py", "wiki-src/shipping_module.py")
    git("commit", "-qm", "move it out of the application")

    # Pass cwd rather than chdir()ing: a test-local chdir is global to the
    # interpreter, and this suite runs under xdist with background retention
    # timers live in the same process.
    paths = GUARD._changed_paths("HEAD~1", "HEAD", cwd=tmp_path)

    assert "cps/shipping_module.py" in paths, (
        "the rename's source was dropped, so the guard cannot see that a "
        f"shipping file left the application; got {paths}"
    )
    assert changelog_requirement_errors(paths), (
        "a diff that removes a module from cps/ must still require a "
        "release-note entry"
    )


def test_a_pull_request_that_changes_no_file_needs_no_changelog_entry():
    """An empty tree diff ships nothing, so there is nothing to announce.

    A `-s ours` back-merge that reconnects an off-main hotfix tag to main is
    exactly this shape. Before the fix, `all([])` was blocked by a
    `changed_paths and` clause that fell through to the error instead, so the
    guard refused such a pull request for "changing shipping paths" while its
    diff was empty.
    """
    assert GUARD.changelog_requirement_errors([]) == []
