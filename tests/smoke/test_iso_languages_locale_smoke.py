# -*- coding: utf-8 -*-
"""Regression tests for cps.isoLanguages.get_language_name(s).

The previous implementation crashed with
    AttributeError: 'NoneType' object has no attribute 'language'
when called with a None locale (background-fetch / scheduler paths) or with
a string locale that did not exactly match a key in _LANGUAGE_NAMES (e.g.
"en_US"). This affected every metadata provider that resolved a language
name — DNB was the loud one.

These tests pin the contract: "any input, no exception; missing data is
returned as 'Unknown' or None".
"""

import pytest

from cps import isoLanguages


# ---------------------------------------------------------------------------
# get_language_names() — must never raise on bad locale input.
# ---------------------------------------------------------------------------

class _FakeLocale:
    """Minimal stand-in for babel.core.Locale."""
    def __init__(self, value, language):
        self._value = value
        self.language = language

    def __str__(self):
        return self._value


@pytest.mark.parametrize(
    "locale,should_be_dict",
    [
        # Happy paths ---------------------------------------------------------
        ("en",                 True),
        (_FakeLocale("en_US", "en"), True),   # str() doesn't match, .language does
        (_FakeLocale("en", "en"),    True),   # str() matches directly
        # Defensive paths -----------------------------------------------------
        (None,                 False),       # used to raise; must return None
        ("en_US",              True),        # composite must fall back to "en"
        ("en-GB",              True),        # hyphen variant must fall back to "en"
        ("eng",                False),       # unknown 3-letter code must return None
        ("",                   False),       # empty string must return None
        (_FakeLocale("zz_ZZ", None),    False),  # unknown locale, no .language
        (_FakeLocale("zz_ZZ", "zz"),    False),  # unknown locale + unknown lang
    ],
)
def test_get_language_names_tolerates_arbitrary_input(locale, should_be_dict):
    result = isoLanguages.get_language_names(locale)
    if should_be_dict:
        assert isinstance(result, dict) and result, \
            f"expected a non-empty dict for locale={locale!r}, got {result!r}"
    else:
        assert result is None, \
            f"expected None for locale={locale!r}, got {result!r}"


# ---------------------------------------------------------------------------
# get_language_name() — never raise; return "Unknown" on bad input.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locale", [None, "", "garbage", "eng"])
def test_get_language_name_returns_unknown_for_bad_locale(locale):
    # Smoke: the call must not raise (was AttributeError pre-fix).
    out = isoLanguages.get_language_name(locale, "ger")
    assert out == "Unknown"


def test_get_language_name_resolves_with_string_locale():
    out = isoLanguages.get_language_name("en", "ger")
    assert out and out != "Unknown"


def test_get_language_name_resolves_with_composite_locale_string():
    """en_US must fall back to en."""
    out = isoLanguages.get_language_name("en_US", "ger")
    assert out and out != "Unknown"


def test_get_language_name_resolves_with_locale_object():
    """babel.core.Locale-like objects keep working."""
    out = isoLanguages.get_language_name(_FakeLocale("en_US", "en"), "ger")
    assert out and out != "Unknown"


def _shipped_ui_locales():
    """Every UI catalog the app ships, named the way get_locale() names it."""
    import os
    root = os.path.join(os.path.dirname(isoLanguages.__file__), "translations")
    return sorted(
        d for d in os.listdir(root)
        if os.path.isfile(os.path.join(root, d, "LC_MESSAGES", "messages.po"))
    )


@pytest.mark.parametrize("ui_locale", _shipped_ui_locales())
def test_every_shipped_ui_locale_has_a_language_name_table(ui_locale):
    """A UI catalog shipped without a language-name table showed every book's
    language as "Unknown" (Catalan and Arabic, PR #2652). A new catalog must
    arrive with its table, so the next one fails here instead of in the UI.
    """
    from babel import Locale

    names = isoLanguages.get_language_names(Locale.parse(ui_locale))
    assert isinstance(names, dict) and names, ui_locale
    assert isoLanguages.get_language_name(Locale.parse(ui_locale), "fra") != "Unknown"


def test_catalan_ui_names_languages_in_catalan():
    """Language names in the Catalan UI are Catalan, not English."""
    assert isoLanguages.get_language_name("ca", "cat") == "Català"
    assert isoLanguages.get_language_name("ca", "spa") == "Espanyol"
    assert isoLanguages.get_language_name("ca", "fra") == "Francès"


def test_arabic_ui_names_languages_in_arabic():
    assert isoLanguages.get_language_name("ar", "ara") == "العربية"


def test_get_language_name_unknown_lang_code_returns_unknown_not_raise():
    out = isoLanguages.get_language_name("en", "totallymadeupcode")
    assert out == "Unknown"
