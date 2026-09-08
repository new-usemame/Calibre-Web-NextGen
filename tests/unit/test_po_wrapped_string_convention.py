# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Detect duplicated content and missing spaces in wrapped PO strings.

In fork PR #429 (closed unmerged), an edit replaced ``msgstr ""`` with a
complete translation but retained continuation lines containing that same
translation. Gettext concatenates those strings, doubling the message;
``msgfmt`` accepts it, so compilation alone cannot catch this corruption.

PR #1264 (German, merged as 704a3131e; CHANGES-vs-upstream.md) exposed a
second defect: missing seam spaces compiled words such as ``werdenentfernt``
and ``aus derBibliothek``. Gettext inserts no separator between chunks.
Check every seam, including those after an empty first line, for two Latin
letters touching. This script-based rule applies in every locale: Chinese
and Japanese characters may meet freely, but Latin-letter seams are checked
even in zh_Hant or ja. It does NOT detect glued words in other scripts,
letter/digit or underscore seams, or missing spaces beside punctuation.
Legitimate intentional splits inside Latin words are also flagged and need
review; this heuristic cannot infer word boundaries from meaning.

The former proxy rejected every non-empty first line with continuations.
That is legitimate GNU gettext/Poedit wrapping, even though pybabel/Weblate
usually use an empty first line. PR #2171 exposed 50 false positives in the
reviewed Swedish catalog: first-line versus joined-continuation similarity
was at most 0.441 in the manager's measurement, while the #429 corrupted
sample scored about 0.98. The manager also measured 937 Swedish seams,
none joining two word characters. These measurements justify accepting the
contribution without normalizing its wrapping.

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
import unicodedata

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


def find_missing_seam_spaces(lines):
    """Report the right-hand line of every seam joining two Latin letters."""
    violations = []
    previous = ""
    for number, line in enumerate(lines, 1):
        match = re.match(r'^(?:msgid|msgid_plural|msgstr(?:\[\d+\])?) (".*")\s*$', line)
        if match:
            previous = ast.literal_eval(match.group(1))
        elif _CONTINUATION.match(line):
            current = ast.literal_eval(line.strip())
            if previous and current and all(
                char.isalpha() and "LATIN" in unicodedata.name(char, "")
                for char in (previous[-1], current[0])
            ):
                violations.append((number, line.rstrip()))
            # Empty chunks insert no separator, so preserve the last character.
            previous += current
        else:
            previous = ""
    return violations


def find_wrapped_content_defects(lines):
    """Return offending lines for duplication (#429) or seam glue (#1264)."""
    return sorted(set(
        find_duplicated_wrapped_strings(lines) + find_missing_seam_spaces(lines)
    ))


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
    violations = find_wrapped_content_defects(corrupted)
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
    assert find_wrapped_content_defects(wellformed) == []


@pytest.mark.unit
@pytest.mark.parametrize("po_path", PO_FILES, ids=lambda p: p.split(os.sep)[-3])
def test_no_half_replaced_wrapped_strings(po_path):
    with open(po_path, encoding="utf-8") as f:
        lines = f.readlines()
    violations = find_wrapped_content_defects(lines)
    if violations:
        locale = po_path.split(os.sep)[-3]
        detail = "\n".join(f"  line {n}: {l}" for n, l in violations)
        pytest.fail(
            f"Locale {locale!r}: wrapped fields duplicate content or join Latin letters without a space —\n"
            f"{detail}\n"
            f"Check for first-line versus joined-continuation similarity of at least "
            f"{DUPLICATION_THRESHOLD:.0%}. Check for a complete replacement "
            f"whose old continuation lines were left behind (fork PR #429), or "
            f"a missing space at a chunk boundary (PR #1264).\n"
        )


@pytest.mark.unit
@pytest.mark.parametrize("keyword", ["msgid", "msgid_plural", "msgstr", "msgstr[0]"])
def test_detector_accepts_gnu_wrapping(keyword):
    """Non-empty first lines are legal, including escaped quotes/newlines."""
    lines = [
        f'{keyword} "Please open the \\"library\\" settings and "\n',
        '"choose a new database path.\\n"\n',
    ]
    assert find_wrapped_content_defects(lines) == []


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
    assert find_wrapped_content_defects(lines) == [(1, lines[0].rstrip())]


@pytest.mark.unit
@pytest.mark.parametrize("keyword", ["msgid", "msgid_plural", "msgstr", "msgstr[1]"])
@pytest.mark.parametrize("empty_first", [False, True])
def test_detector_flags_1264_missing_seam_space(keyword, empty_first):
    """PR #1264: gettext silently compiles 'werdenentfernt' in either style."""
    chunks = ['"Die ausgewählten Bücher werden"\n', '"entfernt."\n']
    lines = ([f'{keyword} ""\n'] + chunks if empty_first
             else [f"{keyword} {chunks[0]}", chunks[1]])
    violations = find_wrapped_content_defects(lines)
    assert violations == [(len(lines), lines[-1].rstrip())]


@pytest.mark.unit
@pytest.mark.parametrize("left,right", [
    ("werden ", "entfernt"),
    ("werden", " entfernt"),
    ("werden\\n", "entfernt"),
    ("OAuth-", "Anmeldung"),
    ("圖書", "館"),
    ("ライブラリ", "から削除"),
])
def test_detector_accepts_separated_or_non_latin_seams(left, right):
    lines = ['msgstr ""\n', f'"{left}"\n', f'"{right}"\n']
    assert find_wrapped_content_defects(lines) == []


@pytest.mark.unit
def test_detector_checks_later_seams_and_accented_latin_letters():
    lines = ['msgstr ""\n', '"Déplacez le livre "\n',
             '"ici"\n', '"également."\n']
    assert find_wrapped_content_defects(lines) == [(4, lines[3].rstrip())]
