# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #936 — a corrupt Russian translation
("Прочитано: 45% r") reached screen-reader users and every gate passed it.

Root cause, in one line: babel's python-format heuristic reads the literal
percent in ``{pct}% read`` as a format spec (space-flag + ``r`` conversion),
so the POT entry carries BOTH ``python-brace-format`` and ``python-format``.
``msgmerge`` propagates that bogus flag to every locale, and ``msgfmt
--check`` then validates translations against a spec that does not exist.

The consequence is an INVERTED gate: the corrupt Russian msgstr kept the
bogus ``% r`` and therefore PASSED ``--check``, while the correct pt_BR
msgstr ("{pct}% lido") dropped it and FATALED. The locale failing the check
was the correct one. That is why nothing caught this — the corruption was
invisible precisely because it was faithful to a bogus spec.

A msgid is interpolated by exactly one mechanism, so the two flags together
are always a mis-detection. ``scripts/fix_pot_format_flags.py`` strips the
bogus flag after extraction; these tests pin the invariant, the artifact the
user actually receives, and the now-clean ``--check`` baseline.
"""

import glob
import gettext as gettext_module
import os
import re
import shutil
import subprocess

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRANSLATIONS_DIR = os.path.join(REPO_ROOT, "cps", "translations")
POT_PATH = os.path.join(REPO_ROOT, "messages.pot")

PROGRESS_MSGID = "{pct}% read"


def _discover_po_files():
    pattern = os.path.join(TRANSLATIONS_DIR, "*", "LC_MESSAGES", "messages.po")
    return sorted(glob.glob(pattern))


PO_FILES = _discover_po_files()


def _locale_of(po_path):
    return po_path.split(os.sep)[-3]


def _iter_flag_blocks(path):
    """Yield (flags, msgid) for every entry in a .po/.pot that has a flags line.

    Deliberately a small hand parser rather than a new dependency (rule 6):
    entries are blank-line separated, flags live on a leading ``#,`` line.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for block in text.split("\n\n"):
        flag_match = re.search(r"^#,(.*)$", block, re.M)
        if not flag_match:
            continue
        flags = [f.strip() for f in flag_match.group(1).split(",") if f.strip()]
        msgid_match = re.search(r'^msgid (".*")$', block, re.M)
        msgid = msgid_match.group(1) if msgid_match else "<unknown>"
        yield flags, msgid


# --------------------------------------------------------------------------
# 1. The artifact the user actually receives (the reported symptom).
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.skipif(
    shutil.which("msgfmt") is None,
    reason="msgfmt not available on this host (brew install gettext)",
)
def test_russian_reader_progress_renders_without_stray_english(tmp_path):
    """#936: the Russian reader progress bar announced "Прочитано: 45% r".

    Verified through the COMPILED artifact, not the .po source — the .mo is
    what flask_babel loads and what the screen reader ultimately speaks.
    Consumed as ``aria-valuetext`` at frontend/src/pages/Reader.tsx:653, so
    the defect is inaudible to sighted users and invisible in screenshots.
    """
    po_path = os.path.join(TRANSLATIONS_DIR, "ru", "LC_MESSAGES", "messages.po")
    mo_path = tmp_path / "ru.mo"
    subprocess.run(
        ["msgfmt", po_path, "-o", str(mo_path)], check=True, capture_output=True
    )

    with open(mo_path, "rb") as fh:
        translation = gettext_module.GNUTranslations(fh)

    raw = translation.gettext(PROGRESS_MSGID)
    rendered = raw.format(pct=45)

    assert rendered == "Прочитано: 45%", (
        f"Russian reader progress renders {rendered!r}.\n"
        f"  raw msgstr: {raw!r}\n"
        "A trailing latin fragment here is the #936 corruption: the 'r' is a\n"
        "leftover from the English 'read'. Screen readers speak this string."
    )
    # The specific corruption class: leftover latin from the English source.
    assert not re.search(r"[A-Za-z]", rendered), (
        f"Russian msgstr contains latin characters: {rendered!r} — likely an "
        "untranslated fragment of the English msgid."
    )


# --------------------------------------------------------------------------
# 2. The invariant that makes the gate honest (root cause).
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_pot_has_no_entry_flagged_both_python_format_and_brace_format():
    """A msgid is interpolated by ONE mechanism — %-format or str.format.

    Both flags on one entry is always babel mis-detecting a literal percent
    (``% read`` parses as space-flag + ``r`` conversion). Left in place it
    inverts ``msgfmt --check``: a translation that preserves the phantom spec
    passes, one that correctly drops it fatals.

    ``scripts/fix_pot_format_flags.py`` runs after ``pybabel extract`` in
    ``scripts/update_translations.sh`` to keep this true across regeneration.
    """
    offenders = [
        msgid
        for flags, msgid in _iter_flag_blocks(POT_PATH)
        if "python-format" in flags and "python-brace-format" in flags
    ]
    assert offenders == [], (
        "messages.pot entries carry both python-format and python-brace-format:\n"
        + "\n".join(f"  {m}" for m in offenders)
        + "\n\nRun: python3 scripts/fix_pot_format_flags.py messages.pot"
    )


@pytest.mark.unit
@pytest.mark.parametrize("po_path", PO_FILES, ids=_locale_of)
def test_po_has_no_entry_flagged_both_python_format_and_brace_format(po_path):
    """Same invariant per locale — msgmerge propagates POT flags downward."""
    offenders = [
        msgid
        for flags, msgid in _iter_flag_blocks(po_path)
        if "python-format" in flags and "python-brace-format" in flags
    ]
    assert offenders == [], (
        f"{_locale_of(po_path)}/messages.po carries both flags on:\n"
        + "\n".join(f"  {m}" for m in offenders)
        + f"\n\nRun: python3 scripts/fix_pot_format_flags.py {po_path}"
    )


# --------------------------------------------------------------------------
# 3. The gate that would have caught it (now that the baseline is clean).
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.skipif(
    shutil.which("msgfmt") is None,
    reason="msgfmt not available on this host (brew install gettext)",
)
@pytest.mark.parametrize("po_path", PO_FILES, ids=_locale_of)
def test_po_file_passes_msgfmt_check(po_path, tmp_path):
    """Every shipped .po must pass ``msgfmt --check``.

    Sibling test_translations_compile.py gates plain ``msgfmt`` — parity with
    what the Docker build rejects, i.e. "does the .mo ship at all". This gates
    the stricter format-consistency classes that plain msgfmt only warns on.
    Both matter: the former catches a DROPPED locale (the v4.0.47 hu case),
    this one catches a SHIPPED-BUT-WRONG string (#936).

    Gating this only became possible once #936 removed the bogus python-format
    flag: before that, pt_BR — the *correct* translation — was the sole
    failure, so the check could not be turned on without failing on truth.
    """
    result = subprocess.run(
        ["msgfmt", "--check", po_path, "-o", str(tmp_path / "out.mo")],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"msgfmt --check failed for locale {_locale_of(po_path)!r}:\n"
            f"  {po_path}\n"
            f"--- stderr ---\n{result.stderr.rstrip()}\n"
            f"--- fix hint ---\n"
            "  'format specifications ... are not the same' means the msgstr's\n"
            "  placeholders disagree with the msgid's. If the msgid contains a\n"
            "  LITERAL percent next to a letter (e.g. '{pct}% read'), babel may\n"
            "  have mis-flagged it python-format — see #936 and\n"
            "  scripts/fix_pot_format_flags.py.\n"
        )
