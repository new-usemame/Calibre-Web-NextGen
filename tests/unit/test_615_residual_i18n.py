# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for the residual French i18n pushback in #615.

The first fixes anchored direct ``t('literal')`` calls and static ``label``
properties, but two gaps remained: fuzzy/empty French catalog entries are
absent from the SPA catalog, and default smart-shelf names are canonical
English database values rendered as if they were already display text.
"""
import ast
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


def _anchored_spa_msgids():
    """Every msgid the project has declared it ships for SPA translation.

    ``cps/spa_strings.py`` is that declaration: pybabel does not scan ``.tsx``,
    so a string reaches the catalogs only by being anchored there. Anchored
    therefore means "we translate this"; absent means "English by design"
    (release-note entry copy) — which is exactly the boundary the gate wants.
    """
    source = (ROOT / "cps" / "spa_strings.py").read_text(encoding="utf-8")
    return {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("_", "N_")
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def _spa_chrome_keys():
    """SPA translation keys that make up the app's own interface.

    Derived from what the project *ships* for translation, not from how the
    frontend happens to spell the call. Deriving it from ``t()`` call sites
    alone is what let #1223 through: a string only counts there if its literal
    sits at the call, so anything data-authored or computed is invisible —
    the filter-operator list, theme and sort names, the Ko-fi banner and the
    What's New buttons all render through a variable. 89 anchored strings were
    unchecked that way, and fr/nl certified 732/732 with 43 and 76 of them
    still English. #1221 was the same drift in miniature (system shelf names
    arriving via ``N_()`` in ``cps/magic_shelf.py``) and got a bespoke test;
    two instances argue for fixing the derivation instead of adding a third.

    Frontend-extracted keys stay unioned in as defence in depth: a ``t()``
    literal nobody anchored is missing from every catalog, and this set is
    where that should fail. Release-note *entry copy* is still excluded —
    it is English by design and is never anchored — while the deep-link
    button labels beside it are anchored, so they are gated like any chrome.
    """
    keys = _load_extractor().extract_frontend_keys()
    frontend = {
        msgid
        for msgid, sources in keys.items()
        if not all(src == RELEASE_NOTE_SOURCE for src in sources)
    }
    return frontend | _anchored_spa_msgids()


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


# Locales we have deliberately completed for the new UI and now hold at 100%.
# Adding one here means every future SPA string must be translated into it
# before CI goes green, so the list is opt-in per locale rather than "every
# locale we ship" — gating all 28 would put a 28-translation step in front of
# every new label (#1217).
#
# Russian and Polish are at 100% too but are intentionally NOT gated: both are
# maintained by community translators on their own cadence, and gating them
# would stall our release train on someone else's availability rather than on
# our own work. The distinction is who holds the catalog, not how complete it
# is — fr and nl we filled ourselves (#615, #886), so they are ours to keep at
# 100%; ru (@sinyawskiy) and pl (@bywciu, #1249) are not. Don't add a locale
# here just because the README shows it at 100%.
COMPLETE_LOCALES = ("fr", "nl")


@pytest.mark.unit
@pytest.mark.parametrize("locale", COMPLETE_LOCALES)
def test_completed_locale_spa_chrome_is_fully_translated(locale):
    """Every SPA interface string resolves for a user on a completed locale.

    This is the #615 symptom itself: the SPA's English source strings are the
    msgids, so an untranslated entry doesn't fail loudly — it renders the
    English source. That made a 33%-translated interface look like a handful of
    stray strings, and @hayvan96 had to find them screen by screen. Dutch then
    reproduced it from the other end (#886): @iroQuai reported "mixed language"
    labels on a catalog that was only 10% filled. Pinning the whole set means
    the next gap fails here instead of in a user's screenshot.
    """
    missing = sorted(_spa_chrome_keys() - set(_live_catalog(locale)))
    assert missing == [], (
        f"{len(missing)} SPA interface string(s) have no {locale} translation "
        f"and will render in English (#615/#1217). Add them to "
        f"cps/translations/{locale}/LC_MESSAGES/messages.po. Missing: {missing[:15]}"
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
@pytest.mark.parametrize("locale", COMPLETE_LOCALES)
def test_completed_locale_translates_system_shelf_names(locale):
    """System smart-shelf names are translated for a completed locale.

    These render on the library sidebar next to the SPA chrome, but they enter
    the catalog from ``cps/magic_shelf.py`` via ``N_()`` rather than from a
    frontend ``t()`` call, so ``_spa_chrome_keys()`` cannot see them and the
    coverage gate above skipped them. Dutch shipped at 100% SPA coverage with
    all five of these still empty, which left "Currently Reading" sitting in
    English in an otherwise Dutch sidebar — the exact mixed-language symptom
    #886 was filed about.
    """
    from cps.magic_shelf import SYSTEM_SHELF_TEMPLATES

    catalog = _live_catalog(locale)
    untranslated = sorted(
        template["name"]
        for template in SYSTEM_SHELF_TEMPLATES.values()
        if template["name"] not in catalog
    )
    assert untranslated == [], (
        f"{locale}: {len(untranslated)} system smart-shelf name(s) render in "
        f"English in an otherwise translated sidebar (#886). Add them to "
        f"cps/translations/{locale}/LC_MESSAGES/messages.po: {untranslated}"
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
