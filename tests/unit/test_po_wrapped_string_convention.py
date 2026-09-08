# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Detect duplicated content left behind by half-replaced wrapped PO strings.

In fork PR #429 (closed unmerged), an edit replaced ``msgstr ""`` with a
complete translation but retained continuation lines containing that same
translation. Gettext concatenates those strings, doubling the message;
``msgfmt`` accepts it, so compilation alone cannot catch this corruption.

The former proxy rejected every non-empty first line with continuations.
That is legitimate GNU gettext/Poedit wrapping, even though pybabel/Weblate
usually use an empty first line. PR #2171 exposed 50 false positives in the
reviewed Swedish catalog: first-line versus joined-continuation similarity
was at most 0.441 in the manager's measurement, while the #429 corrupted
sample scored about 0.98. With boundary whitespace stripped here, the
Swedish maximum is 0.444444 and the #429 sample scores 0.983425.

Compare decoded content instead of enforcing a wrapping style. A similarity
ratio of at least 0.80 flags near-duplication while allowing those ordinary
wraps. This is a heuristic for the #429 edit defect, not a general semantic
validator or a ban on repeated words within a translation.
"""

import ast
from difflib import SequenceMatcher
import glob
from itertools import islice
import os
import re

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRANSLATIONS_DIR = os.path.join(REPO_ROOT, "cps", "translations")

# msgid / msgid_plural / msgstr / msgstr[N] with non-empty quoted content.
_KEYWORD_NONEMPTY = re.compile(r'^(msgid|msgid_plural|msgstr(?:\[\d+\])?) "(.+)"\s*$')
# A bare continuation string line.
_CONTINUATION = re.compile(r'^"')


DUPLICATION_THRESHOLD = 0.80


def wrapped_content_similarities(lines):
    """Yield (line number, source line, ratio) for non-empty wrapped fields.

    Decode PO escapes before comparing the first string with *all* adjacent
    continuation strings joined in gettext order. Strip boundary whitespace
    so an extra space or newline cannot hide a duplicated message.
    """
    for i, line in enumerate(lines):
        match = _KEYWORD_NONEMPTY.match(line)
        if not match:
            continue
        continuation = []
        for following in islice(lines, i + 1, None):
            if not _CONTINUATION.match(following):
                break
            continuation.append(ast.literal_eval(following.strip()))
        if continuation:
            first = ast.literal_eval('"' + match.group(2) + '"').strip()
            rest = "".join(continuation).strip()
            if first and rest:
                ratio = SequenceMatcher(None, first, rest, autojunk=False).ratio()
                yield i + 1, line.rstrip(), ratio


def find_duplicated_wrapped_strings(lines):
    """Return [(line_no, line)] for first strings restated by continuations."""
    return [
        (number, line)
        for number, line, ratio in wrapped_content_similarities(lines)
        if ratio >= DUPLICATION_THRESHOLD
    ]


def _discover_po_files():
    pattern = os.path.join(TRANSLATIONS_DIR, "*", "LC_MESSAGES", "messages.po")
    return sorted(glob.glob(pattern))


PO_FILES = _discover_po_files()


@pytest.mark.unit
def test_detector_flags_half_replaced_wrapped_msgstr():
    """The detector must catch the exact corruption shape from PR #429."""
    corrupted = [
        'msgid "Calibre database unavailable. Please reconfigure the library path."\n',
        'msgstr "Base de données Calibre indisponible. Veuillez reconfigurer le chemin de la bibliothèque."\n',
        '"Base de données de Calibre indisponible. Veuillez reconfigurer le chemin de "\n',
        '"la bibliothèque."\n',
    ]
    violations = find_duplicated_wrapped_strings(corrupted)
    assert len(violations) == 1
    assert violations[0][0] == 2


@pytest.mark.unit
def test_detector_accepts_wellformed_shapes():
    """Empty-first-line wrapping, single-line entries, and adjacent entries
    must not be flagged."""
    wellformed = [
        'msgid "Calibre database unavailable. Please reconfigure the library path."\n',
        'msgstr ""\n',
        '"Base de données de Calibre indisponible. Veuillez reconfigurer le chemin de "\n',
        '"la bibliothèque."\n',
        '\n',
        'msgid "Statistics"\n',
        'msgstr "Statistiques"\n',
        '\n',
        'msgid ""\n',
        '"A long source string that pybabel wrapped across "\n',
        '"two lines."\n',
        'msgstr "Une seule ligne."\n',
    ]
    assert find_duplicated_wrapped_strings(wellformed) == []


@pytest.mark.unit
@pytest.mark.parametrize("po_path", PO_FILES, ids=lambda p: p.split(os.sep)[-3])
def test_no_half_replaced_wrapped_strings(po_path):
    with open(po_path, encoding="utf-8") as f:
        lines = f.readlines()
    violations = find_duplicated_wrapped_strings(lines)
    if violations:
        locale = po_path.split(os.sep)[-3]
        detail = "\n".join(f"  line {n}: {l}" for n, l in violations)
        pytest.fail(
            f"Locale {locale!r}: wrapped fields repeat their first-line content —\n"
            f"{detail}\n"
            f"First-line versus joined-continuation similarity is at least "
            f"{DUPLICATION_THRESHOLD:.0%}. Check for a complete replacement "
            f"whose old continuation lines were left behind (fork PR #429).\n"
        )


@pytest.mark.unit
@pytest.mark.parametrize("keyword", ["msgid", "msgid_plural", "msgstr", "msgstr[0]"])
def test_detector_accepts_gnu_wrapping(keyword):
    """Non-empty first lines are legal, including escaped quotes/newlines."""
    lines = [
        f'{keyword} "Please open the \\"library\\" settings and "\n',
        '"choose a new database path.\\n"\n',
    ]
    assert find_duplicated_wrapped_strings(lines) == []


@pytest.mark.unit
@pytest.mark.parametrize("keyword", ["msgid", "msgid_plural", "msgstr", "msgstr[1]"])
def test_detector_rejects_duplicated_content(keyword):
    """Join every continuation; a later entry must not affect the comparison."""
    lines = [
        f'{keyword} "Please reconfigure the library path."\n',
        '"Please reconfigure "\n',
        '"the library path."\n',
        'msgid "Another message"\n',
        'msgstr "A different translation"\n',
    ]
    assert find_duplicated_wrapped_strings(lines) == [(1, lines[0].rstrip())]
