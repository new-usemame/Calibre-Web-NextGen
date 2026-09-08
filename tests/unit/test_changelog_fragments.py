# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural coverage for deterministic changelog-fragment assembly."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(
    os.environ.get(
        "CWNG_CHANGELOG_ASSEMBLER", ROOT / "scripts" / "assemble_changelog.py"
    )
)

BASE_CHANGELOG = """# Changelog

Project preamble.

## [Unreleased]

### Fixed

- **Existing fix.** Existing detail.

## [v1.0.0] - 2026-08-01

### Added

- **First release.** Initial detail.
"""


def _workspace(tmp_path: Path, changelog: str = BASE_CHANGELOG) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    changelog_path = tmp_path / "CHANGELOG.md"
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    changelog_path.write_text(changelog, encoding="utf-8")
    return changelog_path, fragments


def _run(
    changelog: Path, fragments: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--changelog",
            str(changelog),
            "--fragments-dir",
            str(fragments),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_empty_fragment_directory_is_a_byte_identical_noop(tmp_path: Path) -> None:
    changelog, fragments = _workspace(tmp_path)
    before = changelog.read_bytes()

    result = _run(changelog, fragments)

    assert result.returncode == 0, result.stderr
    assert changelog.read_bytes() == before
    assert list(fragments.iterdir()) == []
    assert "unchanged" in result.stdout


def test_one_fragment_moves_into_unreleased_and_is_deleted(tmp_path: Path) -> None:
    changelog, fragments = _workspace(tmp_path)
    fragment = fragments / "reader-back-link.md"
    fragment.write_text(
        "### Fixed\n\n- **Books return to their source list.** The filter is preserved.\n",
        encoding="utf-8",
    )

    result = _run(changelog, fragments)

    assert result.returncode == 0, result.stderr
    text = changelog.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert "**Existing fix.**" in unreleased
    assert "**Books return to their source list.**" in unreleased
    assert not fragment.exists()


def test_many_fragments_use_category_then_c_locale_filename_order(
    tmp_path: Path,
) -> None:
    changelog, fragments = _workspace(tmp_path)
    (fragments / "z-last.md").write_text(
        "### Fixed\n\n- **Zulu fix.** Last filename.\n", encoding="utf-8"
    )
    (fragments / "a-first.md").write_text(
        "### Fixed\n\n- **Alpha fix.** First filename.\n", encoding="utf-8"
    )
    (fragments / "m-added.md").write_text(
        "### Added\n\n- **Middle feature.** Added category.\n", encoding="utf-8"
    )

    result = _run(changelog, fragments)

    assert result.returncode == 0, result.stderr
    text = changelog.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert unreleased.index("### Added") < unreleased.index("### Fixed")
    assert unreleased.index("**Existing fix.**") < unreleased.index("**Alpha fix.**")
    assert unreleased.index("**Alpha fix.**") < unreleased.index("**Zulu fix.**")
    assert [path.name for path in fragments.iterdir()] == []


def test_readme_is_not_a_fragment_and_survives_assembly(tmp_path: Path) -> None:
    changelog, fragments = _workspace(tmp_path)
    readme = fragments / "README.md"
    readme.write_text("format documentation\n", encoding="utf-8")

    result = _run(changelog, fragments)

    assert result.returncode == 0, result.stderr
    assert readme.read_text(encoding="utf-8") == "format documentation\n"


def test_second_assembly_is_idempotent(tmp_path: Path) -> None:
    changelog, fragments = _workspace(tmp_path)
    (fragments / "one.md").write_text(
        "### Changed\n\n- **One setting is clearer.** More detail.\n", encoding="utf-8"
    )

    first = _run(changelog, fragments)
    after_first = changelog.read_bytes()
    second = _run(changelog, fragments)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert changelog.read_bytes() == after_first
    assert (
        changelog.read_text(encoding="utf-8").count("**One setting is clearer.**") == 1
    )


def test_replayed_fragment_is_deduplicated_after_a_post_write_interruption(
    tmp_path: Path,
) -> None:
    changelog, fragments = _workspace(tmp_path)
    body = "### Fixed\n\n- **One durable fix.** More detail.\n"
    fragment = fragments / "durable.md"
    fragment.write_text(body, encoding="utf-8")
    first = _run(changelog, fragments)
    # Model a kill after CHANGELOG.md was replaced but before fragment unlink:
    # the canonical entry exists and the same fragment is still on disk.
    fragment.write_text(body, encoding="utf-8")
    second = _run(changelog, fragments)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert changelog.read_text(encoding="utf-8").count("**One durable fix.**") == 1
    assert not fragment.exists()


def test_release_mode_drains_unreleased_and_fragments_into_one_version(
    tmp_path: Path,
) -> None:
    changelog, fragments = _workspace(tmp_path)
    fragment = fragments / "release-fix.md"
    fragment.write_text(
        "### Fixed\n\n- **Release fix.** Included from a fragment.\n", encoding="utf-8"
    )

    first = _run(
        changelog,
        fragments,
        "--version",
        "v1.0.1",
        "--date",
        "2026-08-24",
    )
    after_first = changelog.read_bytes()
    second = _run(
        changelog,
        fragments,
        "--version",
        "v1.0.1",
        "--date",
        "2026-08-24",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert changelog.read_bytes() == after_first
    text = changelog.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    release = text.split("## [v1.0.1] - 2026-08-24", 1)[1].split("\n## [", 1)[0]
    assert unreleased.strip() == ""
    assert "**Existing fix.**" in release
    assert "**Release fix.**" in release
    assert text.count("## [v1.0.1] - 2026-08-24") == 1
    assert not fragment.exists()


def test_invalid_fragment_fails_without_modifying_or_consuming_files(
    tmp_path: Path,
) -> None:
    changelog, fragments = _workspace(tmp_path)
    fragment = fragments / "invalid.md"
    fragment.write_text("- not under a category\n", encoding="utf-8")
    before = changelog.read_bytes()

    result = _run(changelog, fragments)

    assert result.returncode == 2
    assert "invalid.md" in result.stderr
    assert changelog.read_bytes() == before
    assert fragment.exists()


def test_unsupported_fragment_category_is_rejected(tmp_path: Path) -> None:
    changelog, fragments = _workspace(tmp_path)
    fragment = fragments / "unsupported.md"
    fragment.write_text(
        "### Improvements\n\n- **A vague category.** This cannot be ordered.\n",
        encoding="utf-8",
    )
    before = changelog.read_bytes()

    result = _run(changelog, fragments)

    assert result.returncode == 2
    assert "unsupported category 'Improvements'" in result.stderr
    assert changelog.read_bytes() == before
    assert fragment.exists()


def test_current_repository_changelog_shape_accepts_a_fragment(tmp_path: Path) -> None:
    changelog, fragments = _workspace(
        tmp_path, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    )
    fragment = fragments / "shape-probe.md"
    fragment.write_text(
        "### Fixed\n\n- **The real changelog parser remains usable.** Shape probe.\n",
        encoding="utf-8",
    )

    result = _run(changelog, fragments)

    assert result.returncode == 0, result.stderr
    assert "**The real changelog parser remains usable.**" in changelog.read_text(
        encoding="utf-8"
    )
    assert not fragment.exists()


def test_every_fragment_in_the_repository_can_actually_be_assembled(
    tmp_path: Path,
) -> None:
    """The gates above this one only ever parse fragments the test itself wrote.

    ``check_changelog_diff.py`` makes a PR prove it *added* a correctly *named*
    fragment; nothing made it prove the fragment *parses*. So a malformed body
    merges green and is not discovered until the release train tries to drain
    the directory -- which is where v4.1.44 found ten unshippable fragments at
    once, 11 days and 251 commits after the previous release. Assembly is the
    only consumer of these files, so assembly is the gate.

    Each fragment is assembled alone so one bad file names itself instead of
    masking the rest behind the assembler's first error.
    """
    staged = [
        path
        for path in sorted((ROOT / "changelog.d").glob("*.md"))
        if path.name != "README.md"
    ]

    broken = []
    for index, path in enumerate(staged):
        changelog, fragments = _workspace(tmp_path / f"probe{index}")
        (fragments / path.name).write_bytes(path.read_bytes())
        result = _run(changelog, fragments)
        if result.returncode != 0:
            broken.append(f"  {path.name}: {result.stderr.strip()}")

    assert not broken, (
        f"{len(broken)} of {len(staged)} fragment(s) in changelog.d/ cannot be "
        "assembled, so the release train cannot drain the directory:\n"
        + "\n".join(broken)
    )
