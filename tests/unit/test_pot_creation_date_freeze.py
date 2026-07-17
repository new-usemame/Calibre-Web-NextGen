# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests: community translation PRs must stop going CONFLICTING
within a day of being opened.

The symptom
-----------
A translator opens a PR touching one ``.po``. Within about a day GitHub marks
it CONFLICTING, CI stops being meaningful, and the contribution has to be
re-authored by hand onto a fresh branch. It happened to the same contributor
four times in eight days (#718→#721, #820→#822, #844→#860, #895→#929) and
again on #938 — each time costing the contributor their credit and us a
manual adoption.

The root cause is a two-line adjacency, proven on #938
------------------------------------------------------
``update-translations.yml`` re-runs ``pybabel extract`` on every push to main.
Extract stamps a fresh ``POT-Creation-Date`` even when not one msgid changed,
``msgmerge`` rides it into every locale, and the bot commits it. Meanwhile
Poedit stamps ``PO-Revision-Date`` whenever the translator saves.

Measured on #938 against its merge base (703c96f4f)::

    base:         POT-Creation-Date: 2026-07-16 02:13+0000
                  PO-Revision-Date:  2026-07-14 09:12+0300
    main changed: POT-Creation-Date  ONLY   (bot bump)
    PR   changed: PO-Revision-Date   ONLY   (Poedit save)

Two sides, two *different* lines — that should merge cleanly. It does not,
because the lines are ADJACENT with no unchanged line between them, so git
cannot split them into separate hunks and reports a conflict. That single
hunk was #938's *only* conflict; the other ~1345 lines of ``#:`` location
churn merged fine, because unchanged msgid lines sit between them and give
git the separating context the header pair lacks.

So the conflict is manufactured entirely by a timestamp nobody reads. Nothing
in this repo parses ``POT-Creation-Date``: it is absent from every consumer,
``msgfmt --check`` passes without the line at all, and gettext resolves
plurals/charset from ``Plural-Forms``/``Content-Type`` in the compiled ``.mo``.
Freezing it makes main's header byte-stable, which removes this whole conflict
class rather than re-authoring PRs after the fact.

Scope, stated honestly: this kills the *spurious* conflict — "went CONFLICTING
despite zero real divergence". A PR left open long enough for genuine msgid
drift (e.g. #949) still conflicts on content and still needs adoption. That is
a real conflict; this one never was.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


# Fast Tests runs `pytest -m "smoke or unit"`. Without this marker the file is
# collected and then silently excluded — green CI, zero coverage. That is the
# #966 failure mode (57 dead i18n gates), and it caught this file too.
pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "freeze_pot_creation_date.py"
UPDATE_SH = REPO_ROOT / "scripts" / "update_translations.sh"


def _header(pot_date: str, revision_date: str) -> str:
    """A minimal but realistic .po/.pot header block.

    The two date lines are adjacent here exactly as gettext emits them — that
    adjacency is the whole bug, so the fixture must not "tidy" it.
    """
    return (
        'msgid ""\n'
        'msgstr ""\n'
        '"Project-Id-Version: Calibre-Web Automated\\n"\n'
        '"Report-Msgid-Bugs-To: https://github.com/crocodilestick/Calibre-Web-Automated\\n"\n'
        f'"POT-Creation-Date: {pot_date}\\n"\n'
        f'"PO-Revision-Date: {revision_date}\\n"\n'
        '"Last-Translator: ZIZA\\n"\n'
        '"Language: ru\\n"\n'
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: 8bit\\n"\n'
        '\n'
        '#: cps/templates/index.html:12\n'
        'msgid "Books"\n'
        'msgstr "Книги"\n'
    )


def _run_freeze(target: Path, reference: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(SCRIPT), str(target), str(reference)],
        capture_output=True,
        text=True,
    )


def _pot_creation_date(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith('"POT-Creation-Date:'):
            return line
    return None


def _merge_conflicts(base: str, ours: str, theirs: str, tmp_path: Path) -> bool:
    """True when a 3-way merge of the three texts conflicts.

    ``git merge-file`` is the same 3-way engine GitHub uses to decide PR
    mergeability, and needs no repository — so this reproduces the #938
    symptom directly rather than asserting on an intermediate.
    """
    b = tmp_path / "base.po"
    o = tmp_path / "ours.po"
    t = tmp_path / "theirs.po"
    b.write_text(base)
    o.write_text(ours)
    t.write_text(theirs)
    result = subprocess.run(
        ["git", "merge-file", "-p", str(o), str(b), str(t)],
        capture_output=True,
        text=True,
    )
    # git merge-file exits >0 with the number of conflicts; 0 means clean.
    return result.returncode != 0


BASE_POT_DATE = "2026-07-16 02:13+0000"
BOT_POT_DATE = "2026-07-17 01:20-0400"
BASE_REV_DATE = "2026-07-14 09:12+0300"
TRANSLATOR_REV_DATE = "2026-07-16 08:15+0300"


class TestConflictSymptom:
    """The user-visible symptom, reproduced and then removed."""

    def test_bot_date_bump_conflicts_with_translator_save(self, tmp_path):
        """RED without the fix: this is #938, exactly.

        Main bumps only POT-Creation-Date, the translator touches only
        PO-Revision-Date, and the merge still conflicts.
        """
        base = _header(BASE_POT_DATE, BASE_REV_DATE)
        main_side = _header(BOT_POT_DATE, BASE_REV_DATE)  # bot bumped the date
        pr_side = _header(BASE_POT_DATE, TRANSLATOR_REV_DATE)  # Poedit saved

        assert _merge_conflicts(base, main_side, pr_side, tmp_path), (
            "Expected the #938 conflict to reproduce. If this fails the "
            "fixture no longer models the real header adjacency."
        )

    def test_frozen_date_lets_translator_save_merge_cleanly(self, tmp_path):
        """GREEN with the fix: main's header is byte-stable, so the
        translator's PO-Revision-Date change applies with no conflict."""
        base = _header(BASE_POT_DATE, BASE_REV_DATE)
        main_side = _header(BASE_POT_DATE, BASE_REV_DATE)  # frozen: no bump
        pr_side = _header(BASE_POT_DATE, TRANSLATOR_REV_DATE)

        assert not _merge_conflicts(base, main_side, pr_side, tmp_path), (
            "Freezing POT-Creation-Date must remove the conflict entirely."
        )

    def test_real_content_changes_still_merge_normally(self, tmp_path):
        """The freeze must not paper over genuine divergence — a real msgstr
        conflict must still be reported, not silently swallowed."""
        base = _header(BASE_POT_DATE, BASE_REV_DATE)
        ours = base.replace('msgstr "Книги"', 'msgstr "Книжки"')
        theirs = base.replace('msgstr "Книги"', 'msgstr "Литература"')

        assert _merge_conflicts(base, ours, theirs, tmp_path), (
            "Two different translations of the same msgid must still conflict."
        )


class TestFreezeScript:
    """The unit under test: restore the reference POT-Creation-Date."""

    def test_restores_previous_creation_date(self, tmp_path):
        target = tmp_path / "messages.pot"
        reference = tmp_path / "previous.pot"
        target.write_text(_header(BOT_POT_DATE, BASE_REV_DATE))
        reference.write_text(_header(BASE_POT_DATE, BASE_REV_DATE))

        result = _run_freeze(target, reference)

        assert result.returncode == 0, result.stderr
        assert f'"POT-Creation-Date: {BASE_POT_DATE}\\n"' in target.read_text()
        assert BOT_POT_DATE not in target.read_text()

    def test_leaves_every_other_line_untouched(self, tmp_path):
        """Only the one line may move. A freeze that rewrites anything else
        would trade one churn source for another."""
        target = tmp_path / "messages.pot"
        reference = tmp_path / "previous.pot"
        before = _header(BOT_POT_DATE, BASE_REV_DATE)
        target.write_text(before)
        reference.write_text(_header(BASE_POT_DATE, TRANSLATOR_REV_DATE))

        _run_freeze(target, reference)

        after = target.read_text()
        before_lines = [l for l in before.splitlines() if "POT-Creation-Date" not in l]
        after_lines = [l for l in after.splitlines() if "POT-Creation-Date" not in l]
        assert before_lines == after_lines
        # The reference's PO-Revision-Date must NOT bleed into the target.
        assert TRANSLATOR_REV_DATE not in after

    def test_is_idempotent(self, tmp_path):
        target = tmp_path / "messages.pot"
        reference = tmp_path / "previous.pot"
        target.write_text(_header(BOT_POT_DATE, BASE_REV_DATE))
        reference.write_text(_header(BASE_POT_DATE, BASE_REV_DATE))

        _run_freeze(target, reference)
        once = target.read_text()
        _run_freeze(target, reference)
        assert target.read_text() == once

    def test_noop_when_reference_has_no_creation_date(self, tmp_path):
        """A malformed/absent reference must leave the fresh POT alone rather
        than blanking a header field."""
        target = tmp_path / "messages.pot"
        reference = tmp_path / "previous.pot"
        target.write_text(_header(BOT_POT_DATE, BASE_REV_DATE))
        reference.write_text('msgid ""\nmsgstr ""\n"Language: ru\\n"\n')

        result = _run_freeze(target, reference)

        assert result.returncode == 0, result.stderr
        assert f'"POT-Creation-Date: {BOT_POT_DATE}\\n"' in target.read_text()

    def test_noop_when_reference_missing(self, tmp_path):
        """First-ever extract has no committed POT to freeze against."""
        target = tmp_path / "messages.pot"
        target.write_text(_header(BOT_POT_DATE, BASE_REV_DATE))

        result = _run_freeze(target, tmp_path / "does-not-exist.pot")

        assert result.returncode == 0, result.stderr
        assert f'"POT-Creation-Date: {BOT_POT_DATE}\\n"' in target.read_text()

    def test_missing_target_is_an_error(self, tmp_path):
        """Silently succeeding on a missing target would let a broken
        extract slip through the pipeline unnoticed."""
        reference = tmp_path / "previous.pot"
        reference.write_text(_header(BASE_POT_DATE, BASE_REV_DATE))

        result = _run_freeze(tmp_path / "nope.pot", reference)

        assert result.returncode != 0

    def test_preserves_utf8_content(self, tmp_path):
        target = tmp_path / "messages.pot"
        reference = tmp_path / "previous.pot"
        target.write_text(_header(BOT_POT_DATE, BASE_REV_DATE), encoding="utf-8")
        reference.write_text(_header(BASE_POT_DATE, BASE_REV_DATE), encoding="utf-8")

        _run_freeze(target, reference)

        assert 'msgstr "Книги"' in target.read_text(encoding="utf-8")


class TestPipelineWiring:
    """A correct script that nothing calls fixes nothing (#701 lesson: verify
    the feature is actually consumed)."""

    def test_freeze_script_exists_and_is_executable_python(self):
        assert SCRIPT.is_file()
        assert SCRIPT.read_text().startswith("#!")

    def test_update_translations_snapshots_pot_before_extract(self):
        body = UPDATE_SH.read_text()
        assert "POT_PREV" in body, (
            "update_translations.sh must snapshot the committed POT before "
            "pybabel extract overwrites it."
        )

    def test_update_translations_invokes_the_freeze(self):
        body = UPDATE_SH.read_text()
        assert "freeze_pot_creation_date.py" in body, (
            "The freeze must run in the real pipeline, not just exist."
        )

    def test_freeze_runs_before_msgmerge_fans_the_pot_out(self):
        """Order is load-bearing: msgmerge copies the POT header into every
        locale, so freezing after it would leave 28 .po files already churned."""
        body = UPDATE_SH.read_text()
        freeze_at = body.index("freeze_pot_creation_date.py")
        msgmerge_at = body.index("msgmerge --no-fuzzy-matching --update")
        assert freeze_at < msgmerge_at, (
            "freeze_pot_creation_date.py must run BEFORE the msgmerge loop."
        )


class TestNoConsumersJustifyingTheFreeze:
    """The freeze is only safe because the field is unread. Pin that, so a
    future change that starts depending on POT-Creation-Date fails loudly
    here instead of quietly reading a frozen timestamp as fresh."""

    def test_no_source_file_reads_pot_creation_date(self):
        result = subprocess.run(
            [
                "grep", "-rIl", "POT-Creation-Date",
                "--include=*.py", "--include=*.sh", "--include=*.yml",
                "--include=*.yaml", "--include=*.ts", "--include=*.tsx",
                str(REPO_ROOT / "cps"),
                str(REPO_ROOT / "scripts"),
                str(REPO_ROOT / ".github"),
            ],
            capture_output=True,
            text=True,
        )
        # Split on newlines, never whitespace: the checkout path legitimately
        # contains spaces, and splitting on those shatters each path into
        # fragments that then masquerade as extra "consumers".
        hits = {
            Path(line).name
            for line in result.stdout.splitlines()
            if line.strip()
        }
        # The freeze machinery itself is the only legitimate reader.
        allowed = {"freeze_pot_creation_date.py", "update_translations.sh"}
        assert hits <= allowed, (
            f"Unexpected POT-Creation-Date consumer(s): {sorted(hits - allowed)}. "
            "The freeze assumes nothing reads this field."
        )
