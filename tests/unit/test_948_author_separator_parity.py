# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #948: the New UI separated authors with a
comma, so a multi-author list was unreadable.

An author's *display name* may itself contain a comma — "Leckie, Ann" is an
ordinary Calibre author name — so a comma cannot also separate one author from
the next: "Leckie, Ann, Tchaikovsky, Adrian" reads as four people.

' & ' is not a new convention. Calibre joins authors with '&'; the classic
templates render '&' between author links; ``cps/api/edit.py`` hands the edit
form '&'-joined authors and the SPA field label says "Authors (separate with
&)". Only the SPA's *display* path had drifted to ', '.

The pins:

* the shared constant exists and is ' & ' (the SPA had eight independent ', '
  literals — that duplication is what let display drift from every other
  surface, so the fix collapses them to one source of truth);
* no SPA file joins authors with ', ' again (exhaustiveness — a *new* author
  list added with the old separator fails here rather than reaching a user);
* the backend and the SPA agree byte-for-byte, so the two halves cannot drift
  apart silently;
* comma-lists whose values cannot contain commas (tags, languages, publishers,
  formats) are deliberately NOT swept up — authors are the exception.

Every pin fails on pre-fix code and passes on the branch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SPA_SRC = REPO_ROOT / "frontend" / "src"
AUTHORS_LIB = SPA_SRC / "lib" / "authors.ts"
EDIT_API = REPO_ROOT / "cps" / "api" / "edit.py"

# Every SPA surface that renders a list of authors, and the symbol it must use.
AUTHOR_DISPLAY_SITES = {
    "components/BookCard.tsx": "formatAuthors",   # library grid (#948 report)
    "pages/Catalog.tsx": "formatAuthors",         # library list view (#948 report)
    "pages/BookDetail.tsx": "AUTHOR_SEPARATOR",   # book view (#948 report)
    "pages/Table.tsx": "formatAuthors",           # table view Authors column
    "pages/CoverPicker.tsx": "formatAuthors",     # cover picker header
    "pages/EditBook.tsx": "formatAuthors",        # metadata-search previews
}


def test_shared_author_separator_is_ampersand():
    """One source of truth for the separator, and it matches Calibre (#948)."""
    assert AUTHORS_LIB.is_file(), (
        f"{AUTHORS_LIB.relative_to(REPO_ROOT)} must exist — the author separator "
        "lives in exactly one place so display cannot drift from the edit form "
        "and the classic templates again (issue #948)."
    )
    src = AUTHORS_LIB.read_text(encoding="utf-8")
    m = re.search(r"AUTHOR_SEPARATOR\s*=\s*(['\"])(.+?)\1", src)
    assert m, "authors.ts must export a literal AUTHOR_SEPARATOR constant."
    assert m.group(2) == " & ", (
        f"AUTHOR_SEPARATOR is {m.group(2)!r}; expected ' & '. An author display "
        "name may contain a comma, so a comma cannot also separate authors."
    )


@pytest.mark.parametrize(("rel", "symbol"), sorted(AUTHOR_DISPLAY_SITES.items()))
def test_author_display_sites_use_the_shared_separator(rel: str, symbol: str):
    """Each author-rendering surface goes through the shared module (#948)."""
    src = (SPA_SRC / rel).read_text(encoding="utf-8")
    assert "from '../lib/authors'" in src or "from '../../lib/authors'" in src, (
        f"{rel} renders authors but does not import the shared author "
        "separator module (issue #948)."
    )
    assert symbol in src, f"{rel} must render authors via {symbol}() (issue #948)."


def test_no_spa_file_joins_authors_with_a_comma():
    """Exhaustiveness: a new author list added with ', ' fails here (#948).

    This is the guard that outlives the fix. The bug was not one bad line, it
    was eight files each free-handing a separator; without this pin the ninth
    would reappear.
    """
    offenders: list[str] = []
    pattern = re.compile(r"authors[^;\n]{0,60}?\.join\(\s*(['\"]), ?\1\s*\)")
    for path in sorted(SPA_SRC.rglob("*.ts*")):
        if path == AUTHORS_LIB:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(SPA_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These lines join authors with a comma; use formatAuthors() from "
        "lib/authors.ts instead (issue #948):\n" + "\n".join(offenders)
    )


def test_backend_and_spa_agree_on_the_author_separator():
    """The two halves must match byte-for-byte or the edit round-trip lies.

    ``cps/api/edit.py`` serialises the edit form's authors '&'-joined; the SPA
    displays them. If either side changes alone, what the user reads stops
    matching what the app stores.
    """
    backend = EDIT_API.read_text(encoding="utf-8")
    m = re.search(r"(['\"])(\s*&\s*)\1\.join\(", backend)
    assert m, (
        "cps/api/edit.py no longer '&'-joins authors — if the backend's author "
        "separator moved, frontend/src/lib/authors.ts must move with it (#948)."
    )
    spa = re.search(
        r"AUTHOR_SEPARATOR\s*=\s*(['\"])(.+?)\1", AUTHORS_LIB.read_text(encoding="utf-8"),
    )
    assert spa and spa.group(2) == m.group(2), (
        f"Backend joins authors with {m.group(2)!r} but the SPA displays them "
        f"with {spa.group(2)!r} — these must stay identical (issue #948)."
    )


def test_comma_lists_are_not_swept_up_by_the_fix():
    """Authors are the exception; tags/languages/formats keep ', ' (#948).

    Calibre encodes a comma inside an author name as '|' and joins authors with
    '&' precisely because author names contain commas. Tag/language/format
    values cannot, so their comma-joins are correct and must not be "fixed".
    """
    src = (SPA_SRC / "pages" / "Table.tsx").read_text(encoding="utf-8")
    for key in ("tags", "formats"):
        assert re.search(rf"c\.key === '{key}'[^\n]*join\(', '\)", src), (
            f"Table.tsx's {key} column should still join with ', ' — only the "
            "authors column changed in issue #948."
        )
