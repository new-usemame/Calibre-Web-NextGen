# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests: `#:` source-reference LINE NUMBERS must not reach the
catalogs, because two branches that both shift them conflict on the same line.

The symptom
-----------
A feature branch that adds SPA strings goes CONFLICTING against main within
hours, on a ``.po`` file where not one translated word is in dispute. Observed
2026-08-10 on fork PRs #1508 (reader notes) and #1515 (confirm destructive
actions), both stuck on ``cps/translations/nl/LC_MESSAGES/messages.po``.

Why the sibling fix did not cover this
--------------------------------------
``freeze_pot_creation_date.py`` removed the header half of this conflict class
and measured, on #938, that the ~1345 lines of ``#:`` location churn in the same
file "merged cleanly (unchanged msgid lines separate those hunks)". That is
correct — for #938, where only ONE side moved locations (the bot) while the
translator touched only ``msgstr``.

The case it does not cover is both sides moving the SAME reference line::

    base:   #: cps/cover_picker.py:138 cps/spa_strings.py:459
    main:   #: cps/cover_picker.py:121 cps/spa_strings.py:459   (cover_picker edited)
    branch: #: cps/cover_picker.py:138 cps/spa_strings.py:483   (spa_strings grew)

One line, two different edits. No amount of separating context helps — git has
nothing to choose between. Any branch adding SPA msgids shifts every later
``cps/spa_strings.py`` anchor, so this fires on essentially every feature PR
that touches user-visible strings, which is most of them.

The fix
-------
``pybabel extract --add-location=file`` keeps the filename and drops the line
number. ``#: cps/cover_picker.py cps/spa_strings.py`` is stable no matter where
in either file the string lives; it changes only when the set of *files* using
the string changes, which is rare and meaningful. ``msgmerge`` takes locations
from the POT, so the one flag fans out to all 28 locales.

Why dropping line numbers is safe
---------------------------------
Nothing in this repo reads source locations. The three catalog readers —
``cps/api/i18n.py`` (SPA catalog), ``cps/render_template.py`` (missing-string
notice) and ``scripts/generate_translation_status.py`` (status table) — use only
msgid/msgstr/flags/obsolete. Verified by control on 2026-08-10: extracting the
POT with and without the flag and msgmerging each into ``nl/messages.po`` gave
byte-identical ``(msgid, msgstr)`` pairs; the only difference was the ``#:``
comments. The filename is retained precisely because that half *is* useful to a
translator — this drops the churn, not the provenance.

``test_no_line_numbers_in_*`` is the red/green pin: before the fix there were
85,347 line-numbered ``#:`` lines across the POT and 28 catalogs.
``test_extract_passes_add_location_file`` pins the pipeline flag, so a future
edit that drops it fails here rather than silently reintroducing 85k conflict
candidates on the bot's next run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
POT = REPO / "messages.pot"
CATALOGS = sorted((REPO / "cps" / "translations").glob("*/LC_MESSAGES/messages.po"))
UPDATE_SCRIPT = REPO / "scripts" / "update_translations.sh"

# A reference carrying a line number: `#: some/path.py:123` (possibly several
# space-separated refs on one line). Matching `:<digits>` at a word boundary
# avoids tripping on a bare filename that happens to contain a digit.
LINE_NUMBERED_REF = re.compile(r"^#:.*?\S:\d+(\s|$)", re.MULTILINE)


def _line_numbered_refs(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [m.group(0).strip() for m in LINE_NUMBERED_REF.finditer(text)]


def test_catalogs_exist():
    """Guard against the glob silently matching nothing, which would make every
    other assertion in this file vacuously true."""
    assert POT.is_file(), f"{POT} missing"
    assert len(CATALOGS) >= 20, f"expected the full locale set, found {len(CATALOGS)}"


def test_no_line_numbers_in_pot():
    offenders = _line_numbered_refs(POT)
    assert not offenders, (
        f"messages.pot carries {len(offenders)} line-numbered '#:' references, e.g. "
        f"{offenders[:3]}. Re-run scripts/update_translations.sh — extract must pass "
        "--add-location=file. Line numbers make two branches conflict on the same "
        "reference line when both shift them."
    )


@pytest.mark.parametrize("po", CATALOGS, ids=lambda p: p.parts[-3])
def test_no_line_numbers_in_catalog(po: Path):
    offenders = _line_numbered_refs(po)
    assert not offenders, (
        f"{po.parts[-3]}/messages.po carries {len(offenders)} line-numbered '#:' "
        f"references, e.g. {offenders[:3]}. msgmerge copies locations from the POT, "
        "so regenerate via scripts/update_translations.sh rather than editing by hand."
    )


def test_filenames_are_retained():
    """Dropping the line number must not drop the reference entirely.

    ``--no-location`` would also make this file's other tests pass, while
    throwing away the half a translator actually uses. Pin that we chose
    ``file`` and not ``never``.
    """
    text = POT.read_text(encoding="utf-8")
    refs = [ln for ln in text.splitlines() if ln.startswith("#: ")]
    assert len(refs) > 1000, (
        f"only {len(refs)} '#:' references in messages.pot — source filenames look "
        "stripped. --add-location=file keeps them; --no-location would not."
    )
    assert any(ln.startswith("#: cps/") for ln in refs), (
        "no cps/ source references in messages.pot"
    )


def test_extract_passes_add_location_file():
    """Pin the pipeline flag itself.

    Without this, a future edit could drop ``--add-location=file`` and the two
    tests above would keep passing until the translation bot's next run
    reintroduced every line number at once.
    """
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")
    extract_block = script.split("# 1b.")[0]
    assert "--add-location=file" in extract_block, (
        "scripts/update_translations.sh no longer passes --add-location=file to "
        "pybabel extract; the bot's next run would restore ~85k line-numbered "
        "references and with them the same-line conflict class."
    )
    assert "--no-location" not in extract_block, (
        "--no-location drops source filenames too, which removes the provenance "
        "translators rely on. Use --add-location=file."
    )
