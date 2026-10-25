# Calibre-Web Automated - fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork issue #1704: classic-UI shelf page dropped apostrophes from the name.

The server renders the shelf heading as ``Shelf: 'Name'``. caliBlur.js
rewrites it to ``Shelf — Name`` but did so with ``.replace(/\\'/g, "")``,
which removed *every* apostrophe, so ``Test's shelf 1`` became
``Tests shelf 1`` on /shelf/<id> (the sidebar entry, rendered server-side,
was unaffected). Only the wrapping quotes may be removed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBLUR_JS = (REPO_ROOT / "cps" / "static" / "js" / "caliBlur.js").read_text()


def _format_fn_source() -> str:
    m = re.search(r"function formatShelfHeading\(text\) \{.*?\n\}\n", CALIBLUR_JS, re.S)
    assert m, "caliBlur.js must define formatShelfHeading()"
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "heading, expected",
    [
        ("Shelf: 'Test's shelf 1'", "Shelf — Test's shelf 1"),
        ("Shelf: 'Children's Fiction'", "Shelf — Children's Fiction"),
        ("Shelf: 'Plain'", "Shelf — Plain"),
        ("Change order of Shelf: 'Dad's picks'", "Change order of Shelf — Dad's picks"),
        # Apostrophe in a translated label must survive too.
        ("Modifier l'ordre de l'étagère : 'Mes livres'", "Modifier l'ordre de l'étagère  — Mes livres"),
        # fi reorder puts text after the name: the translator's quotes stay and
        # the name is untouched, rather than one quote being stripped.
        ("Muuta hyllyn: 'Kid's' järjestystä", "Muuta hyllyn — 'Kid's' järjestystä"),
        # A name that itself ends in an apostrophe keeps it.
        ("Shelf: 'Dads''", "Shelf — Dads'"),
        # No wrapping quotes: only the colon is rewritten.
        ("Shelf: Plain", "Shelf — Plain"),
    ],
)
def test_heading_keeps_inner_apostrophes(heading, expected):
    script = _format_fn_source() + f"\nconsole.log(JSON.stringify(formatShelfHeading({json.dumps(heading)})));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == expected
