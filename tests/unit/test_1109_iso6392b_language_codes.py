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


def test_alias_targets_resolve_through_the_language_backend():
    """A cross-check that does not depend on pycountry being installed.

    ``pycountry`` is only a dependency on Python >= 3.12; below that the module
    falls back to ``iso-639``, and test_iso6392b_table_matches_pycountry skips.
    This one runs on either backend: every /T target must resolve through the
    module's own ``get()``, and no /B code may — if a /B code resolved
    directly, it would not need aliasing and the table entry would be wrong.

    The *miss* is asserted semantically rather than as a specific exception.
    pycountry's wrapper happens to raise AttributeError (it calls
    _copy_fields(None)), but iso-639 is free to return None or raise
    KeyError, and pinning one backend's failure mode would make this the
    pycountry-only test it claims not to be.
    """
    def resolves(code):
        try:
            return bool(getattr(isoLanguages.get(part3=code), "name", None))
        except Exception:
            return False

    for b_code, t_code in isoLanguages.ISO6392B_TO_T.items():
        assert resolves(t_code), \
            f"/T target {t_code!r} does not resolve through the backend"
        assert not resolves(b_code), \
            f"/B code {b_code!r} resolves directly, so it does not need aliasing"


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


def test_valid_codes_accepts_bibliographic_in_every_locale():
    """Whether a language code is valid must not depend on the UI locale.

    The per-locale tables are translation data and are incomplete — 'ell' is
    missing from 11 of them. Validating against the locale table therefore
    rejected a Greek book for a Portuguese user. Validity now comes from the
    reference table, so every /B code is accepted under every shipped locale.
    """
    from cps.iso_language_names import LANGUAGE_NAMES

    for locale in LANGUAGE_NAMES:
        for b_code, t_code in isoLanguages.ISO6392B_TO_T.items():
            remainder = []
            out = isoLanguages.get_valid_language_codes_from_code(
                locale, [b_code], remainder
            )
            assert out == [t_code], \
                f"{b_code} rejected under locale {locale!r} (got {out!r})"
            assert remainder == []


def test_valid_codes_accepts_terminological_code_missing_from_locale_table():
    """The same defect on the /T side, which predates the alias work.

    'ell' is not in the pt_BR table, so a book tagged with plain 'ell' was
    refused for a Brazilian-Portuguese user even though nothing about it is
    invalid.
    """
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code("pt_BR", ["ell"], remainder)
    assert out == ["ell"]
    assert remainder == []


# --------------------------------------------------------------------------
# canonical_lang_code() — the normalizer the two paths above share.
# --------------------------------------------------------------------------

def test_canonical_lang_code_maps_bibliographic():
    assert isoLanguages.canonical_lang_code("ger") == "deu"


def test_normalization_precedes_the_locale_table_lookup(monkeypatch):
    """A /B key in a locale table must not capture the code before aliasing.

    The shipped tables are /T-only, so this is about not silently depending on
    that. If the locale lookup ran first and only the leftovers were aliased,
    a table that ever gained a "ger" key would store 'ger' verbatim — a code
    the rest of the stack cannot render — and a book declaring both forms
    would store two rows for one language instead of de-duplicating.
    """
    monkeypatch.setattr(
        isoLanguages, "get_language_names",
        lambda locale: {"ger": "German (B)", "deu": "German (T)"},
    )
    for order in (["ger", "deu"], ["deu", "ger"], ["ger"]):
        remainder = []
        out = isoLanguages.get_valid_language_codes_from_code(
            "en", list(order), remainder
        )
        assert out == ["deu"], f"{order} stored {out!r}, expected ['deu']"
        assert remainder == []


# --------------------------------------------------------------------------
# _resolve_lang_code() — the present-but-empty contract.
# --------------------------------------------------------------------------

def test_empty_table_entry_counts_as_unresolved():
    """A blank name is not a usable label, so it falls back to "Unknown"
    rather than rendering an empty pill on the book page."""
    name, resolved = isoLanguages._resolve_lang_code({"deu": ""}, "deu")
    assert (name, resolved) == (None, False)


def test_empty_alias_target_counts_as_unresolved():
    name, resolved = isoLanguages._resolve_lang_code({"deu": ""}, "ger")
    assert (name, resolved) == (None, False)


def test_present_and_non_empty_entry_resolves():
    name, resolved = isoLanguages._resolve_lang_code({"deu": "Deutsch"}, "ger")
    assert (name, resolved) == ("Deutsch", True)


def test_canonical_lang_code_passes_through_everything_else():
    for code in ("deu", "eng", "", "totallymadeupcode"):
        assert isoLanguages.canonical_lang_code(code) == code


# --------------------------------------------------------------------------
# The invariant that makes _reference_language_codes() a valid oracle.
# --------------------------------------------------------------------------

def test_reference_table_is_the_superset_of_every_locale():
    """Validation resolves against the reference table, so it must not be
    possible for a locale to know a code the reference does not.

    If a future translation adds a code to, say, `de` but not `en`, the
    reference stops being an oracle and this fails rather than silently
    narrowing what uploads accept.
    """
    from cps.iso_language_names import LANGUAGE_NAMES

    reference = set(isoLanguages._reference_language_codes())
    for locale, table in LANGUAGE_NAMES.items():
        extra = set(table) - reference
        assert not extra, \
            f"locale {locale!r} carries codes absent from the reference: {sorted(extra)}"


# --------------------------------------------------------------------------
# The real caller. get_valid_language_codes_from_code() is only reachable in
# production through edit_book_languages(upload_mode=True), which is where
# remainder becomes the ValueError the reporter saw. Pinning the helper alone
# would let a refactor stop passing upload_mode, swap to the name parser, or
# mishandle the remainder loop with every helper test still green.
# --------------------------------------------------------------------------

def _import_editbooks():
    import cps.editbooks as editbooks
    return editbooks


def _upload_languages(locale, languages):
    """Drive edit_book_languages(upload_mode=True); return stored lang codes.

    Everything past the validation branch (the filter-language fixup, the
    session write) is mocked out — this pins the accept/reject boundary, not
    the DB layer.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    editbooks = _import_editbooks()
    book = SimpleNamespace(id=1, languages=[])
    captured = {}

    def fake_modify(input_l, db_field, db_type, session, db_name):
        captured["langs"] = list(input_l)
        return True

    user = MagicMock()
    user.filter_language.return_value = "all"

    with patch.object(editbooks, "get_locale", lambda: locale), \
            patch.object(editbooks, "current_user", user), \
            patch.object(editbooks, "calibre_db", MagicMock()), \
            patch.object(editbooks, "modify_database_object", fake_modify), \
            patch.object(editbooks, "log", MagicMock()):
        editbooks.edit_book_languages(languages, book, upload_mode=True)
    return captured.get("langs", [])


def test_upload_accepts_greek_under_a_locale_missing_the_name():
    """#1109's upload half, at the boundary that actually raised.

    'ell' is absent from the pt_BR name table, so validating against that
    table dropped it into remainder and edit_book_languages turned it into
    ValueError("'ell' is not a valid language") — a Greek book refused for a
    Brazilian-Portuguese user and imported fine for an English one.
    """
    assert _upload_languages("pt_BR", "ell") == ["ell"]


def test_upload_accepts_bibliographic_code_and_stores_terminological():
    assert _upload_languages("en", "ger") == ["deu"]


def test_upload_dedupes_a_book_declaring_both_forms():
    assert _upload_languages("en", "ger,deu") == ["deu"]


def test_upload_still_rejects_a_genuinely_invalid_code():
    """The permissive change must not turn the validator into a rubber stamp."""
    with pytest.raises(ValueError):
        _upload_languages("pt_BR", "zzz")


def test_unknown_locale_does_not_crash_the_validator():
    """get_language_names() is documented to return None for an unrecognised
    locale, and get_language_name() has always guarded that. The validator did
    not, so the same input raised AttributeError on None.items() and 500'd the
    request. Validity does not depend on the locale table, so the book imports.
    """
    remainder = []
    out = isoLanguages.get_valid_language_codes_from_code(
        "xx_YY", ["ger", "eng", "zzz"], remainder
    )
    assert out == ["deu", "eng"]
    assert remainder == ["zzz"]
