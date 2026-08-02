# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard against one CHANGELOG entry swallowing another.

Release notes and the in-app What's New page are both generated from
``CHANGELOG.md``. When two PRs land close together, an entry can be authored
directly on top of the previous entry's lead line instead of above it: the new
bullet keeps the old bullet's continuation lines, and the old entry loses its
``- **Symptom.**`` headline entirely. The result is still valid markdown, so
nothing else catches it, but a whole user-facing change silently disappears
from the release notes.

That happened on 2026-08-02: the trailing-slash 404 entry (#1300) was written
over the first line of the "Default book language dropdown" entry (#886/#1299),
producing a single bullet that opened on 404s and then veered into dropdown
translation.

House style puts the credit trailer ("Reported by @user.", "Thanks to @user.")
as the closing sentence of an entry, so a bullet carrying *two* credit trailers
means two entries have been welded together. Verified against the full released
history at the time of writing: 386 bullets, exactly one violation -- the bug
above. A credit followed only by a parenthetical issue link is normal style and
is not what this checks.
"""

import re
from pathlib import Path

import pytest

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

CREDIT = re.compile(
    r"\b(Reported by|Reported through|Thanks to|Contributed by|Patch by)\b"
)


def _bullets(text):
    """Yield (line_number, joined_text) for every top-level ``- `` bullet."""
    lines = text.split("\n")
    bullets = []
    current = None
    for index, line in enumerate(lines):
        if line.startswith("- "):
            if current:
                bullets.append(current)
            current = (index + 1, [line])
        elif current is not None:
            if line.startswith("  ") and line.strip():
                current[1].append(line)
            else:
                bullets.append(current)
                current = None
    if current:
        bullets.append(current)
    return [(ln, " ".join(part.strip() for part in body)) for ln, body in bullets]


@pytest.fixture(scope="module")
def bullets():
    assert CHANGELOG.is_file(), f"CHANGELOG.md not found at {CHANGELOG}"
    found = _bullets(CHANGELOG.read_text(encoding="utf-8"))
    assert found, "parsed zero bullets out of CHANGELOG.md"
    return found


def test_no_bullet_carries_two_credit_trailers(bullets):
    """Two credits in one bullet means two entries were welded together."""
    welded = []
    for line_number, text in bullets:
        credits = CREDIT.findall(text)
        if len(credits) > 1:
            welded.append(f"  CHANGELOG.md:{line_number} -> {credits}\n    {text[:160]}")
    assert not welded, (
        "CHANGELOG bullet(s) contain more than one credit trailer, which means "
        "one entry was written over another entry's lead line and swallowed it. "
        "Split them back into separate '- **Symptom.**' bullets:\n"
        + "\n".join(welded)
    )


def test_every_bullet_in_a_section_opens_with_a_bold_lead(bullets):
    """Entries lead with a bolded plain-English symptom, per the house style.

    A swallowed entry usually shows up here too: the surviving half keeps the
    bold lead while the swallowed half is left as bare prose.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)
    if len(unreleased) == 1:
        pytest.skip("no [Unreleased] section")
    section = unreleased[1].split("\n## [", 1)[0]
    offenders = [
        line
        for line in section.split("\n")
        if line.startswith("- ") and not line.startswith("- **")
    ]
    assert not offenders, (
        "every [Unreleased] entry must open with a bolded symptom lead "
        "('- **What the user sees.**'):\n" + "\n".join(f"  {o}" for o in offenders)
    )


def test_parser_detects_a_synthetic_welded_bullet():
    """Pin the detector itself so a future refactor can't quietly no-op it."""
    welded = (
        "- **First thing broke.** It is fixed now. Reported by @alice.\n"
        "  With the interface in another language, a second unrelated thing was\n"
        "  also broken and is now fixed. Reported by @bob.\n"
    )
    parsed = _bullets(welded)
    assert len(parsed) == 1, "synthetic sample should parse as a single bullet"
    assert len(CREDIT.findall(parsed[0][1])) == 2

    clean = (
        "- **First thing broke.** It is fixed now. Reported by @alice.\n"
        "- **Second thing broke.** It is fixed now. Reported by @bob.\n"
    )
    for _, body in _bullets(clean):
        assert len(CREDIT.findall(body)) == 1


def test_parenthetical_issue_link_after_a_credit_is_not_flagged():
    """'Thanks to @x. ([#908](url))' is normal style, not a welded entry."""
    sample = (
        "- **Something was fixed.** Details here. Thanks to @carol.\n"
        "  ([#908](https://github.com/new-usemame/Calibre-Web-NextGen/issues/908))\n"
    )
    for _, body in _bullets(sample):
        assert len(CREDIT.findall(body)) == 1
