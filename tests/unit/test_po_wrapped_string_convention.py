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

import codecs
from difflib import SequenceMatcher
import glob
import os
import re
import unicodedata

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRANSLATIONS_DIR = os.path.join(REPO_ROOT, "cps", "translations")

# Capture whole C strings, allowing indentation and arbitrary keyword spacing.
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_KEYWORD = re.compile(
    rf'^\s*(?:msgid|msgid_plural|msgstr(?:\[\d+\])?)\s+({_QUOTED})\s*$'
)
_CONTINUATION = re.compile(rf'^\s*({_QUOTED})\s*$')
_C_ESCAPE = re.compile(r'\\([0-7]{1,3}|x[0-9a-fA-F]+|.)')
_SIMPLE_ESCAPES = {
    'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11,
    '\\': 92, '"': 34, "'": 39, '?': 63,
}
DUPLICATION_THRESHOLD = 0.80


def decode_po_bytes(quoted):
    """Decode C escapes to bytes before interpreting the catalog's UTF-8."""
    content = quoted[1:-1]
    result = bytearray()
    offset = 0
    for match in _C_ESCAPE.finditer(content):
        result.extend(content[offset:match.start()].encode("utf-8"))
        escape = match.group(1)
        if escape[0] in '01234567':
            value = int(escape, 8)
        elif escape.startswith('x'):
            value = int(escape[1:], 16)
        else:
            value = _SIMPLE_ESCAPES[escape]
        result.append(value)
        offset = match.end()
    result.extend(content[offset:].encode("utf-8"))
    return bytes(result)


def scan_po_fields(lines):
    """Yield decoded chunks as (physical line number, source line, text).

    Blank lines are lexical whitespace, not field boundaries. A new keyword
    or comment ends the preceding field; obsolete/commented strings are not
    active fields. Decode incrementally so escaped UTF-8 bytes can also span
    chunks. A character belongs to the chunk starting its byte sequence, so
    a boundary inside a UTF-8 character is not mistaken for a word seam.
    """
    chunks = []
    pending_owner = None
    decoder = codecs.getincrementaldecoder("utf-8")()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        keyword = _KEYWORD.fullmatch(line)
        continuation = _CONTINUATION.fullmatch(line)
        if keyword or not continuation:
            if chunks:
                decoder.decode(b"", final=True)
                yield chunks
            chunks = []
            pending_owner = None
            decoder = codecs.getincrementaldecoder("utf-8")()
        if keyword or (continuation and chunks):
            quoted = (keyword or continuation).group(1)
            text = decoder.decode(decode_po_bytes(quoted))
            if pending_owner is not None and text:
                owner_number, owner_line, owner_text = chunks[pending_owner]
                chunks[pending_owner] = (owner_number, owner_line, owner_text + text[0])
                text = text[1:]
                pending_owner = None
            if decoder.getstate()[0] and pending_owner is None:
                pending_owner = len(chunks)
            chunks.append((number, line.rstrip(), text))
    if chunks:
        decoder.decode(b"", final=True)
        yield chunks


def wrapped_content_similarities(fields):
    """Compare first strings with all continuations, stripping boundary space."""
    for chunks in fields:
        number, line, first = chunks[0]
        first = first.strip()
        rest = "".join(text for _, _, text in chunks[1:]).strip()
        if first and rest:
            ratio = SequenceMatcher(None, first, rest, autojunk=False).ratio()
            yield number, line, ratio


def find_duplicated_wrapped_strings(fields):
    """Return [(line_no, line)] for first strings restated by continuations."""
    return [
        (number, line)
        for number, line, ratio in wrapped_content_similarities(fields)
        if ratio >= DUPLICATION_THRESHOLD
    ]


def find_missing_seam_spaces(fields):
    """Report the right-hand line of every seam joining two Latin letters."""
    violations = []
    for chunks in fields:
        previous = ""
        for number, line, current in chunks:
            if previous and current and all(
                char.isalpha() and "LATIN" in unicodedata.name(char, "")
                for char in (previous[-1], current[0])
            ):
                violations.append((number, line))
            # Empty chunks insert no separator, so preserve the last character.
            previous += current
    return violations


def find_wrapped_content_defects(lines):
    """Parse once; report duplication (#429) or seam glue (#1264)."""
    fields = list(scan_po_fields(lines))
    return sorted(set(
        find_duplicated_wrapped_strings(fields) + find_missing_seam_spaces(fields)
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


@pytest.mark.unit
@pytest.mark.parametrize("lines,offending_line", [
    (['msgstr  "Hello."\n', '"Hello."\n'], 1),
    (['  msgstr "Hello."\n', '  "Hello."\n'], 1),
    (['msgstr "Hello."\n', '\n', '"Hello."\n'], 1),
    (['msgstr ""\n', '"Le caf\\303\\251"\n', '"ferme."\n'], 3),
], ids=["extra_space", "indentation", "blank_line", "octal_utf8"])
def test_detector_flags_parser_bypasses(lines, offending_line):
    assert find_wrapped_content_defects(lines) == [
        (offending_line, lines[offending_line - 1].rstrip())
    ]


@pytest.mark.unit
def test_detector_accepts_correct_indented_catalog():
    lines = [
        '  msgid\t"The café is "\n',
        '\n',
        '  "closed."\n',
        '  msgstr  "Le café "\n',
        '  "est fermé."\n',
    ]
    assert find_wrapped_content_defects(lines) == []


@pytest.mark.unit
@pytest.mark.parametrize("chunks", [
    ['"Le caf\\xC3\\xA9"\n', '"ferme."\n'],
    ['"Le caf\\303"\n', '"\\251"\n', '"ferme."\n'],
])
def test_detector_flags_utf8_byte_escapes(chunks):
    lines = ['msgstr ""\n'] + chunks
    assert find_wrapped_content_defects(lines) == [(len(lines), lines[-1].rstrip())]


@pytest.mark.unit
def test_detector_accepts_escaped_separators_and_quotes():
    lines = [
        'msgstr "Le \\"café\\"\\t"\n',
        '"est fermé.\\n"\n',
        '"Chemin C:\\\\livres"\n',
    ]
    assert find_wrapped_content_defects(lines) == []
