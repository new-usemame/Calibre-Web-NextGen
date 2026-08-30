"""Mutually exclusive option labels must stay distinguishable in every locale.

F-4079f1: Dutch translated both "Compact" and "Dense" to "Compact", so the
Library View density picker offered two options a Dutch reader could not tell
apart. Nothing caught it, because every string WAS translated — the i18n gate
counts coverage, and coverage is blind to two msgids colliding on one msgstr.

The defect is a property of a SET, not of any single string, so the guard has to
be written over the set. Any group of choices presented together belongs here.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCALES = ("fr", "nl")

# Groups of msgids the interface shows together as mutually exclusive choices.
# A reader must be able to tell every member apart from every other member.
EXCLUSIVE_CHOICE_GROUPS = {
    "catalog density (frontend/src/pages/Catalog.tsx DENSITY_OPTIONS)": (
        "Comfortable",
        "Compact",
        "Dense",
    ),
}


def _catalog(locale):
    """msgid -> msgstr for one locale, ignoring empty and fuzzy entries."""
    po = REPO / "cps" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    text = po.read_text(encoding="utf-8")
    out = {}
    for block in text.split("\n\n"):
        if "#, fuzzy" in block:
            continue
        mid = re.search(r'^msgid "((?:[^"\\]|\\.)*)"', block, re.M)
        mstr = re.search(r'^msgstr "((?:[^"\\]|\\.)*)"', block, re.M)
        if mid and mstr and mid.group(1) and mstr.group(1):
            out[mid.group(1)] = mstr.group(1)
    return out


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("group", sorted(EXCLUSIVE_CHOICE_GROUPS))
def test_exclusive_choices_are_distinguishable(locale, group):
    msgids = EXCLUSIVE_CHOICE_GROUPS[group]
    catalog = _catalog(locale)

    seen = {}
    collisions = []
    for msgid in msgids:
        rendered = catalog.get(msgid, msgid)  # untranslated falls back to English
        if rendered in seen:
            collisions.append((seen[rendered], msgid, rendered))
        seen[rendered] = msgid

    assert not collisions, (
        "{} option(s) in the '{}' group render identically in {}, so a reader "
        "cannot tell them apart (F-4079f1). Give each its own translation in "
        "cps/translations/{}/LC_MESSAGES/messages.po.\n".format(
            len(collisions), group, locale, locale
        )
        + "\n".join(
            "  {!r} and {!r} both render as {!r}".format(a, b, r)
            for a, b, r in collisions
        )
    )


def test_the_group_lists_msgids_that_actually_exist():
    """A typo'd msgid would silently fall back to English and never collide."""
    source = (REPO / "frontend" / "src" / "pages" / "Catalog.tsx").read_text(
        encoding="utf-8"
    )
    block = source.split("const DENSITY_OPTIONS", 1)[1].split("]", 1)[0]
    declared = set(re.findall(r"label: '([^']+)'", block))
    listed = set(EXCLUSIVE_CHOICE_GROUPS[
        "catalog density (frontend/src/pages/Catalog.tsx DENSITY_OPTIONS)"
    ])
    assert declared == listed, (
        "DENSITY_OPTIONS in Catalog.tsx declares {} but the guard checks {}. "
        "The guard is only as good as its list — update it.".format(
            sorted(declared), sorted(listed)
        )
    )
