# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #1097 (@chloeroform): one feature, several
names, depending on which surface you are standing on.

@chloeroform's audit listed ``Discover`` vs ``Random`` as one of four pairs and
then pointed out that the two UIs are not the whole story — OPDS is a third
surface. It is, and for this pair OPDS was the only one that disagreed:

* classic sidebar  — ``_('Discover')``            (``cps/render_template.py``)
* classic heading  — ``_('Discover (Random Books)')`` (``cps/templates/index.html``)
* new UI           — ``_("Discover")``            (``cps/spa_strings.py``)
* OPDS feed title  — ``N_('Random Books')``       (``cps/opds.py``)  <-- odd one out

The OPDS *route* was already ``/opds/discover`` and its endpoint already
``feed_discover``; only the string a reader actually displays said something
else.

There is a second, sharper cost on top of the naming split. ``Random Books`` is
marked ``#, fuzzy`` in the ``de``, ``km`` and ``no`` catalogs, and ``msgfmt``
drops fuzzy entries, so those three locales rendered a bare English "Random
Books" sitting among translated siblings in the catalog root. Verified against
the compiled ``.mo`` inside the shipped image, not just the ``.po``::

    de 'Random Books' -> UNTRANSLATED
    de 'Discover'     -> 'Entdecken'

``Discover`` is non-fuzzy and non-empty in all 28 shipped catalogs, so moving
the title onto it both aligns the name across every surface and stops those
three locales falling back to English.

These tests pin:

1. The OPDS root entry for the random/discover feed uses the *same msgid* as
   the classic sidebar link, derived from source on both sides rather than
   hardcoded — so renaming either surface alone trips this.
2. That msgid survives ``msgfmt`` in every shipped catalog (not fuzzy, not
   empty). This is the pin on the user-visible symptom; a fuzzy entry here is
   silently dropped and the feed renders English.
3. The explanatory description is kept, so the feed list still says what the
   entry does.

(1) and (2) both fail on ``main``.
"""

from __future__ import annotations

import glob
import io
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
OPDS_PY = REPO_ROOT / "cps" / "opds.py"
RENDER_TEMPLATE_PY = REPO_ROOT / "cps" / "render_template.py"
TRANSLATIONS = REPO_ROOT / "cps" / "translations"


def _classic_sidebar_discover_msgid() -> str:
    """The msgid the classic sidebar uses for its Discover link.

    Read out of source so that renaming the sidebar label without renaming the
    OPDS one fails test 1 rather than silently re-opening the split.
    """
    source = RENDER_TEMPLATE_PY.read_text(encoding="utf-8")
    match = re.search(
        r"""glyphicon-random["'].*?["']text["']\s*:\s*_\(\s*(['"])(?P<msgid>.+?)\1""",
        source,
        re.DOTALL,
    )
    assert match, (
        "Could not find the classic sidebar's Discover entry in "
        f"{RENDER_TEMPLATE_PY.relative_to(REPO_ROOT)}. If the sidebar was "
        "restructured, update this helper — do not delete the test, the "
        "cross-surface invariant is the point."
    )
    return match.group("msgid")


def _po_entry_state(po_text: str, msgid: str) -> str:
    """``absent`` | ``fuzzy`` | ``empty`` | ``ok`` for *msgid* in a .po file.

    Handles the multi-line ``msgstr ""`` + continuation-line form; a naive
    single-line regex reports those as untranslated and is how this file's
    first draft got the coverage numbers wrong.
    """
    match = re.search(
        r"(?m)^(?P<pre>(?:#[^\n]*\n)*)msgid \"%s\"\n"
        r"(?P<body>msgstr \"(?:[^\"\\]|\\.)*\"\n(?:\"(?:[^\"\\]|\\.)*\"\n)*)"
        % re.escape(msgid),
        po_text,
    )
    if not match:
        return "absent"
    if re.search(r"(?m)^#,[^\n]*\bfuzzy\b", match.group("pre")):
        return "fuzzy"
    joined = "".join(re.findall(r"\"((?:[^\"\\]|\\.)*)\"", match.group("body")))
    return "ok" if joined.strip() else "empty"


def _shipped_locales() -> list[str]:
    return sorted(
        os.path.basename(os.path.dirname(os.path.dirname(path)))
        for path in glob.glob(str(TRANSLATIONS / "*" / "LC_MESSAGES" / "messages.po"))
    )


@pytest.fixture(scope="module")
def random_entry_def():
    from cps.opds import OPDS_ROOT_ENTRY_DEFS

    assert "random" in OPDS_ROOT_ENTRY_DEFS, (
        "OPDS_ROOT_ENTRY_DEFS lost its 'random' key; this test pins the naming "
        "of that entry and cannot run without it."
    )
    return OPDS_ROOT_ENTRY_DEFS["random"]


def test_opds_discover_entry_uses_the_same_msgid_as_the_classic_sidebar(random_entry_def):
    """#1097: OPDS was the only surface calling this feature something else."""
    sidebar_msgid = _classic_sidebar_discover_msgid()
    opds_title = str(random_entry_def["title"])

    assert opds_title == sidebar_msgid, (
        "The OPDS catalog root and the classic sidebar name the same feature "
        f"differently: OPDS says {opds_title!r}, the sidebar says "
        f"{sidebar_msgid!r}. #1097 is about exactly this split — an OPDS "
        "reader and the web UI should not disagree about what a feature is "
        "called. The route (/opds/discover) and endpoint (feed_discover) "
        "already agree with the sidebar."
    )


def test_opds_discover_title_survives_msgfmt_in_every_shipped_catalog(random_entry_def):
    """A fuzzy msgid is dropped by msgfmt and renders English to real users.

    ``Random Books`` was fuzzy in de/km/no, so those locales showed an English
    entry in an otherwise translated catalog root. Verified against the
    compiled ``.mo`` in the shipped image, so this is the real symptom and not
    a .po-only artifact.
    """
    msgid = str(random_entry_def["title"])
    locales = _shipped_locales()
    assert locales, "No shipped catalogs found — the check below would vacuously pass."

    broken = {}
    for locale in locales:
        po_path = TRANSLATIONS / locale / "LC_MESSAGES" / "messages.po"
        state = _po_entry_state(io.open(po_path, encoding="utf-8").read(), msgid)
        if state != "ok":
            broken[locale] = state

    assert not broken, (
        f"The OPDS catalog-root title {msgid!r} does not survive msgfmt in: "
        f"{broken}. A 'fuzzy' or empty entry is dropped from the .mo, so those "
        "locales render the English string among translated siblings. Either "
        "un-fuzzy those entries or pick a msgid with full coverage."
    )


def test_opds_discover_entry_keeps_an_explanatory_description(random_entry_def):
    """'Discover' alone does not say what the feed contains; the description does."""
    description = str(random_entry_def["description"])
    assert description and description != str(random_entry_def["title"]), (
        "The OPDS random/discover entry needs a description distinct from its "
        f"title so a reader's feed list explains it; got {description!r}."
    )
    locales = _shipped_locales()
    broken = {
        locale: state
        for locale in locales
        if (
            state := _po_entry_state(
                io.open(
                    TRANSLATIONS / locale / "LC_MESSAGES" / "messages.po",
                    encoding="utf-8",
                ).read(),
                description,
            )
        )
        != "ok"
    }
    assert not broken, (
        f"The OPDS discover description {description!r} does not survive "
        f"msgfmt in: {broken}."
    )
