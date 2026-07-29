# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the residual French i18n pushback in #615.

The first fixes anchored direct ``t('literal')`` calls and static ``label``
properties, but two gaps remained: fuzzy/empty French catalog entries are
absent from the SPA catalog, and default smart-shelf names are canonical
English database values rendered as if they were already display text.
"""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]

# Release-note copy lives in the SPA but is deliberately English: the page says
# so in its own body ("The interface is translated into your language; these
# update notes are written in English."), and the whats-new-populate skill
# rewrites this file on every release. Gating it would put a French-translation
# step in front of the release train for text we don't translate anyway.
RELEASE_NOTE_SOURCE = "data/whatsNew.ts"

# Placeholders the SPA substitutes at render time, e.g. "{count} files queued".
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def _load_extractor():
    spec = importlib.util.spec_from_file_location(
        "extract_spa_strings", str(ROOT / "scripts" / "extract_spa_strings.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spa_chrome_keys():
    """SPA translation keys that make up the app's own interface.

    Excludes keys that occur *only* in the release-note data file; a key used
    both there and in real chrome stays in the set.
    """
    keys = _load_extractor().extract_frontend_keys()
    return {
        msgid
        for msgid, sources in keys.items()
        if not all(src == RELEASE_NOTE_SOURCE for src in sources)
    }


def _live_catalog(locale):
    """The strings a locale actually serves, mirroring cps/api/i18n.py.

    Fuzzy and empty entries are dropped there (msgfmt semantics), so reading
    the .po naively would count strings the running app never shows.
    """
    from babel.messages.pofile import read_po

    po = ROOT / "cps" / "translations" / locale / "LC_MESSAGES" / "messages.po"
    with open(po, "rb") as handle:
        catalog = read_po(handle)
    return {
        message.id: message.string
        for message in catalog
        if message.id
        and isinstance(message.id, str)
        and not message.fuzzy
        and message.string
        and isinstance(message.string, str)
    }


@pytest.mark.unit
def test_french_spa_chrome_is_fully_translated():
    """Every SPA interface string resolves to French for a French user.

    This is the #615 symptom itself: the SPA's English source strings are the
    msgids, so an untranslated entry doesn't fail loudly — it renders the
    English source. That made a 33%-translated interface look like a handful of
    stray strings, and @hayvan96 had to find them screen by screen. Pinning the
    whole set means the next gap fails here instead of in a user's screenshot.
    """
    missing = sorted(_spa_chrome_keys() - set(_live_catalog("fr")))
    assert missing == [], (
        f"{len(missing)} SPA interface string(s) have no French translation and "
        f"will render in English (#615). Add them to "
        f"cps/translations/fr/LC_MESSAGES/messages.po. Missing: {missing[:15]}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "locale",
    sorted(
        p.parent.parent.name
        for p in (ROOT / "cps" / "translations").glob("*/LC_MESSAGES/messages.po")
    ),
)
def test_spa_placeholders_survive_translation(locale):
    """A translated SPA string keeps the exact placeholders of its msgid.

    The SPA substitutes ``{name}``-style placeholders at render time. Dropping
    one silently loses the value ("Reset password for ?"), and inventing or
    misspelling one renders the braces literally, so a translation can break a
    string while still counting as translated. Only strings the locale actually
    serves are checked — fuzzy/empty entries never reach the browser.
    """
    spa_keys = _spa_chrome_keys()
    offenders = []
    for msgid, msgstr in _live_catalog(locale).items():
        if msgid not in spa_keys:
            continue
        expected = set(_PLACEHOLDER.findall(msgid))
        actual = set(_PLACEHOLDER.findall(msgstr))
        if expected != actual:
            offenders.append((msgid, sorted(expected), sorted(actual)))
    assert offenders == [], (
        f"{locale}: {len(offenders)} SPA translation(s) changed their "
        f"placeholders, which breaks substitution at render time: {offenders[:5]}"
    )


@pytest.mark.unit
def test_system_shelf_api_localizes_display_name_without_mutating_identity(monkeypatch):
    """System shelf identity stays canonical English in app.db, while the
    request-local API representation uses its lazy translated display name.
    User-created shelf names must remain literal user data.
    """
    from cps.api import magicshelves
    from cps import magic_shelf

    system = SimpleNamespace(
        id=7,
        name="Currently Reading",
        icon="📖",
        is_public=0,
        is_system=True,
        user_id=3,
    )
    custom = SimpleNamespace(
        id=8,
        name="Currently Reading",
        icon="🪄",
        is_public=0,
        is_system=False,
        user_id=3,
    )
    monkeypatch.setattr(
        magic_shelf,
        "system_magic_shelf_display_name",
        lambda shelf: "Lecture en cours" if shelf.is_system else shelf.name,
    )

    assert magicshelves._shelf_item(system, 3)["name"] == "Lecture en cours"
    assert magicshelves._shelf_item(system, 3)["is_system"] is True
    assert magicshelves._shelf_item(custom, 3)["name"] == "Currently Reading"
    assert magicshelves._shelf_item(custom, 3)["is_system"] is False
    assert system.name == "Currently Reading"


@pytest.mark.unit
def test_system_shelf_template_names_are_lazy_translatable_but_canonical_names_are_stable():
    """N_()/lazy_gettext marks display names for extraction without replacing
    the stable English names used for migration, deduplication, and matching.
    """
    from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

    expected = {
        "recently_added": "Recently Added",
        "highly_rated": "Highly Rated",
        "currently_reading": "Currently Reading",
        "yet_to_read": "Yet to Read",
        "recent_publications": "Recent Publications",
    }
    assert set(SYSTEM_SHELF_TEMPLATES) == set(expected)
    for key, canonical in expected.items():
        template = SYSTEM_SHELF_TEMPLATES[key]
        assert template["name"] == canonical
        assert not isinstance(template["display_name"], str)


@pytest.mark.unit
def test_translation_update_disables_msgmerge_fuzzy_guessing():
    """New SPA labels must enter catalogs as empty reviewable entries, not as
    semantically unrelated fuzzy guesses that look translated in status stats
    but disappear from the compiled/runtime catalog (#879).
    """
    script = (ROOT / "scripts" / "update_translations.sh").read_text(encoding="utf-8")
    assert script.count("msgmerge --no-fuzzy-matching --update") == 3
    assert "msgmerge --update" not in script
