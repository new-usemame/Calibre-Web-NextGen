# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1109: books with ISO 639-2/B language codes showed "Unknown".

LANGUAGE_NAMES is keyed on ISO 639-2/T ('deu', 'fra', 'nld'). The 639-2/B
variants ('ger', 'fre', 'dut') were absent, so any book carrying one missed
the table completely. Two user-visible consequences:

  * display — the book detail page, the language browse list, the edit form
    and the OPDS feed all rendered "Unknown", and every lookup wrote an
    ERROR line into the log people attach to unrelated bug reports;
  * upload — get_valid_language_codes_from_code fell the code through to
    *remainder*, and edit_book_languages turns a remainder entry into
    ValueError("'ger' is not a valid language"), so the book was rejected.

The fix aliases /B to /T at lookup. These tests pin both surfaces, the
completeness of the alias table, and the log level.
"""
import logging

import pytest

from cps import isoLanguages


pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# The alias table itself.
# --------------------------------------------------------------------------

def test_iso6392b_table_matches_pycountry():
    """The hardcoded table must equal pycountry's bibliographic mapping.

    ISO 639-2/B is a closed list, so the table is written out rather than
    derived at import time (deriving it would make a pycountry packaging
    change silently empty the map). This test is the drift alarm for that
    choice: if the two ever disagree, one of them is wrong.
    """
    pycountry = pytest.importorskip("pycountry")
    derived = {
        lang.bibliographic: lang.alpha_3
        for lang in pycountry.languages
        if hasattr(lang, "bibliographic")
    }
    assert isoLanguages.ISO6392B_TO_T == derived


def test_iso6392b_table_is_not_self_mapping():
    """A /B code and its /T target must always differ — a self-map would mean
    the entry is junk and would mask a genuine miss."""
    for b_code, t_code in isoLanguages.ISO6392B_TO_T.items():
        assert b_code != t_code, f"{b_code} maps to itself"


def test_reference_locale_covers_every_alias_target():
    """The English table is the complete one (424 entries) and must hold every
    /T target, or the alias would be pointing at nothing."""
    from cps.iso_language_names import LANGUAGE_NAMES

    names = LANGUAGE_NAMES["en"]
    missing = [t for t in isoLanguages.ISO6392B_TO_T.values() if t not in names]
    assert not missing, f"en table is missing /T targets: {missing}"


def test_alias_never_diverges_from_terminological_in_any_locale():
    """The real guarantee, stated for every shipped locale: a /B code resolves
    exactly as its /T twin does.

    Note this is deliberately *not* "every /B code resolves in every locale".
    The per-locale tables are translation data and are legitimately incomplete
    — 27 of the 28 shipped locales are missing between 1 and 48 of the 424
    codes (``el`` and ``gl`` are the sparse ones), which predates this fix and
    is tracked separately. What must hold is that the alias never makes a
    locale worse and never invents a divergence: where the /T name exists the
    /B code now finds it, and where it does not both fall back together.
    """
    from cps.iso_language_names import LANGUAGE_NAMES

    for locale in LANGUAGE_NAMES:
        for b_code, t_code in isoLanguages.ISO6392B_TO_T.items():
            assert isoLanguages.get_language_name(locale, b_code) == \
                isoLanguages.get_language_name(locale, t_code), \
                f"{b_code}/{t_code} diverge in locale {locale!r}"


# --------------------------------------------------------------------------
# Display path — get_language_name().
# --------------------------------------------------------------------------

@pytest.mark.parametrize("b_code", sorted(isoLanguages.ISO6392B_TO_T))
def test_every_bibliographic_code_resolves(b_code):
    name = isoLanguages.get_language_name("en", b_code)
    assert name != "Unknown", f"{b_code} still resolves to Unknown"
    assert name


@pytest.mark.parametrize(
    "b_code,t_code", sorted(isoLanguages.ISO6392B_TO_T.items())
)
def test_bibliographic_and_terminological_agree(b_code, t_code):
    """'ger' and 'deu' are the same language and must render identically —
    otherwise a library with a mix of both shows two entries for one language."""
    assert isoLanguages.get_language_name("en", b_code) == \
        isoLanguages.get_language_name("en", t_code)


def test_alias_applies_across_locales():
    """The alias is a property of the code, not of English."""
    for locale in ("en", "de", "fr"):
        assert isoLanguages.get_language_name(locale, "ger") == \
            isoLanguages.get_language_name(locale, "deu")


def test_terminological_codes_still_resolve():
    """Regression guard: the alias must not disturb the codes that worked."""
    assert isoLanguages.get_language_name("en", "deu") == "German"
    assert isoLanguages.get_language_name("en", "eng") == "English"


def test_unmappable_code_still_returns_unknown():
    assert isoLanguages.get_language_name("en", "totallymadeupcode") == "Unknown"


def test_bad_locale_still_returns_unknown():
    """A /B code must not rescue a locale that has no names table at all."""
    for locale in (None, "", "garbage", "eng"):
        assert isoLanguages.get_language_name(locale, "ger") == "Unknown"


# --------------------------------------------------------------------------
# Log level — an unmappable code is a metadata problem, not an app fault.
# --------------------------------------------------------------------------

def test_unmappable_code_logs_at_warning_not_error(caplog):
    with caplog.at_level(logging.WARNING, logger="cps.isoLanguages"):
        isoLanguages.get_language_name("en", "totallymadeupcode")
    records = [r for r in caplog.records if "Missing translation" in r.message]
    assert records, "expected a log line for an unmappable code"
    assert all(r.levelno == logging.WARNING for r in records), \
        f"expected WARNING, got {[r.levelname for r in records]}"


def test_resolved_code_logs_nothing(caplog):
    """The whole point of the fix: 'ger' must stop generating log lines."""
    with caplog.at_level(logging.WARNING, logger="cps.isoLanguages"):
        isoLanguages.get_language_name("en", "ger")
    assert not [r for r in caplog.records if "Missing translation" in r.message]


# --------------------------------------------------------------------------
# Validation path — get_valid_language_codes_from_code().
# This is the one that rejected uploads.
# --------------------------------------------------------------------------

def test_valid_codes_accepts_bibliographic_and_returns_terminological():
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code("en", ["ger"], remainder)
    assert out == ["deu"], "a /B code must be accepted, normalized to /T"
    assert remainder == [], "a /B code must not land in the invalid remainder"


def test_valid_codes_mixed_input():
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code(
        "en", ["ger", "eng", "bogus"], remainder
    )
    assert sorted(out) == ["deu", "eng"]
    assert remainder == ["bogus"], "genuinely invalid codes must still be rejected"


def test_valid_codes_does_not_duplicate_when_both_forms_given():
    """A book tagged both 'ger' and 'deu' is one language, not two."""
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code(
        "en", ["ger", "deu"], remainder
    )
    assert out == ["deu"]
    assert remainder == []


def test_valid_codes_still_rejects_unknown():
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code(
        "en", ["totallymadeupcode"], remainder
    )
    assert out == []
    assert remainder == ["totallymadeupcode"]


@pytest.mark.parametrize(
    "b_code,t_code", sorted(isoLanguages.ISO6392B_TO_T.items())
)
def test_valid_codes_accepts_every_bibliographic_code(b_code, t_code):
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code("en", [b_code], remainder)
    assert out == [t_code]
    assert remainder == []


# --------------------------------------------------------------------------
# canonical_lang_code() — the normalizer the two paths above share.
# --------------------------------------------------------------------------

def test_canonical_lang_code_maps_bibliographic():
    assert isoLanguages.canonical_lang_code("ger") == "deu"


def test_canonical_lang_code_passes_through_everything_else():
    for code in ("deu", "eng", "", "totallymadeupcode"):
        assert isoLanguages.canonical_lang_code(code) == code
