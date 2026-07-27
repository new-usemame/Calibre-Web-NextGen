# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys

from .iso_language_names import LANGUAGE_NAMES as _LANGUAGE_NAMES
from . import logger
from .string_helper import strip_whitespaces

log = logger.create()


try:
    from pycountry import languages as pyc_languages

    def _copy_fields(l):
        l.part1 = getattr(l, 'alpha_2', None)
        l.part3 = getattr(l, 'alpha_3', None)
        return l

    def get(name=None, part1=None, part3=None):
        if part3 is not None:
            return _copy_fields(pyc_languages.get(alpha_3=part3))
        if part1 is not None:
            return _copy_fields(pyc_languages.get(alpha_2=part1))
        if name is not None:
            return _copy_fields(pyc_languages.get(name=name))
except ImportError as ex:
    if sys.version_info >= (3, 12):
        print("Python 3.12 isn't compatible with iso-639. Please install pycountry.")
    from iso639 import languages
    get = languages.get


def get_language_names(locale):
    """Resolve the localised language-name dictionary for *locale*.

    Tolerates None, strings (e.g. "en", "en_US", "eng"), and babel.core.Locale
    instances. Background-fetch paths (auto_metadata, scheduled jobs) and
    one-off provider invocations frequently pass None or a bare string; the
    previous implementation crashed with AttributeError because
    locale.language was accessed unguarded on those.
    """
    if locale is None:
        return None
    if isinstance(locale, str):
        # Direct match first ("en"), then leading 2-letter component for
        # composites like "en_US" / "en-GB".
        names = _LANGUAGE_NAMES.get(locale)
        if names is None:
            head = locale.split("_", 1)[0].split("-", 1)[0]
            if head and head != locale:
                names = _LANGUAGE_NAMES.get(head)
        return names
    # babel.core.Locale (or any object with .language): try str() first
    # ("en_US") then the bare .language attribute ("en").
    names = _LANGUAGE_NAMES.get(str(locale))
    if names is None:
        lang_attr = getattr(locale, "language", None)
        if lang_attr:
            names = _LANGUAGE_NAMES.get(lang_attr)
    return names


#: ISO 639-2/B (bibliographic) -> ISO 639-2/T (terminological).
#:
#: LANGUAGE_NAMES is keyed on /T only ('deu', 'fra'), but books carry /B codes
#: ('ger', 'fre') often enough to matter — MARC-derived metadata emits /B, and
#: an EPUB's OPF may declare either. Without the alias a /B code misses the
#: table entirely: it displays as "Unknown" everywhere, and on upload
#: get_valid_language_codes_from_code rejects it as invalid.
#:
#: These 20 pairs are the complete set. 639-2/B is a closed list — it exists
#: only for the languages whose English-derived name differs from the native
#: one, and ISO adds no new /B codes — so this is a fixed table rather than
#: something derived at import time. test_iso6392b_table_matches_pycountry
#: pins it against pycountry so drift cannot go unnoticed.
ISO6392B_TO_T = {
    "alb": "sqi", "arm": "hye", "baq": "eus", "bur": "mya", "chi": "zho",
    "cze": "ces", "dut": "nld", "fre": "fra", "geo": "kat", "ger": "deu",
    "gre": "ell", "ice": "isl", "mac": "mkd", "mao": "mri", "may": "msa",
    "per": "fas", "rum": "ron", "slo": "slk", "tib": "bod", "wel": "cym",
}


def _resolve_lang_code(names, lang_code):
    """Look *lang_code* up in *names*, retrying through the 639-2/B alias.

    Returns ``(name, True)`` on a hit and ``(None, False)`` otherwise, so
    callers can tell "resolved" from "fell back" without comparing against the
    "Unknown" sentinel.

    An entry that is present but empty counts as *unresolved*, deliberately:
    a blank language label is not more useful to a reader than "Unknown", and
    treating it as a hit would render an empty pill on the book page.
    """
    name = names.get(lang_code)
    if name:
        return name, True
    alias = ISO6392B_TO_T.get(lang_code)
    if alias is not None:
        name = names.get(alias)
        if name:
            return name, True
    return None, False


def canonical_lang_code(lang_code):
    """Return the ISO 639-2/T form of *lang_code*, unchanged if not a /B code."""
    return ISO6392B_TO_T.get(lang_code, lang_code)


def _reference_language_codes():
    """The complete checked-in reference table, independent of UI locale.

    This is the application's own language data, not an ISO registry: it is
    the one locale table that is complete, and ``test_reference_table_is_the
    _superset_of_every_locale`` pins that it stays a superset of every other.

    The per-locale tables in LANGUAGE_NAMES are *translation* data and are
    legitimately incomplete — 27 of the 28 shipped locales are missing between
    1 and 48 of the 424 codes. Whether a code is a valid language is a
    different question from whether the current locale has a name for it, and
    validation must not confuse the two: ``ell`` is absent from the tables for
    gl, id, ko, no, pt, pt_BR, sk, sl, tr, vi and zh_Hant_TW, so gating on the
    locale table rejects a perfectly good Greek book for those users.
    """
    return _LANGUAGE_NAMES["en"]


def get_language_name(locale, lang_code):
    UNKNOWN_TRANSLATION = "Unknown"
    names = get_language_names(locale)
    if names is None:
        # Don't probe locale.language here — locale may be None or str.
        log.warning("No language-names dictionary for locale: %r", locale)
        return UNKNOWN_TRANSLATION

    name, resolved = _resolve_lang_code(names, lang_code)
    if not resolved:
        # A code we cannot map is a metadata problem, not an application
        # fault. Logging it at ERROR put it in the stream users copy into
        # unrelated bug reports, once per lookup per book.
        log.warning("Missing translation for language name: %s", lang_code)
        return UNKNOWN_TRANSLATION

    return name


def get_language_code_from_name(locale, language_names, remainder=None):
    language_names = set(strip_whitespaces(x).lower() for x in language_names if x)
    lang = list()
    for key, val in get_language_names(locale).items():
        val = val.lower()
        if val in language_names:
            lang.append(key)
            language_names.remove(val)
    if remainder is not None and language_names:
        remainder.extend(language_names)
    return lang


def get_valid_language_codes_from_code(locale, language_names, remainder=None):
    lang = list()
    if "" in language_names:
        language_names.remove("")
    names = get_language_names(locale) or {}
    # An unrecognised locale used to crash here (AttributeError on None.items),
    # even though get_language_names is explicitly documented to return None
    # for one and get_language_name already guards it. Validity does not depend
    # on the locale table anyway, so fall through to the reference and let the
    # book import rather than 500 the request.
    reference = _reference_language_codes()
    # Normalize /B -> /T *before* looking anything up. Doing the locale-table
    # lookup first and only aliasing the leftovers would let a locale table
    # that ever gained a /B key capture 'ger' unchanged — storing a code the
    # rest of the stack cannot render, and defeating the de-duplication of a
    # book that declares both 'ger' and 'deu'. The current tables are /T-only,
    # so this is about not depending on that holding forever.
    for code in list(language_names):
        canonical = canonical_lang_code(code)
        # Validity is a property of the code, not of the current locale's
        # translation coverage, so the reference table is what decides it —
        # otherwise a valid /T code is refused just because the user's locale
        # has no name for it, and upload rejects the book with "'ell' is not
        # a valid language". The locale table is still consulted so a code it
        # somehow carries that the reference lacks stays accepted.
        if canonical in names or canonical in reference:
            if canonical not in lang:
                lang.append(canonical)
            language_names.remove(code)
    if remainder is not None and len(language_names):
        remainder.extend(language_names)
    return lang


def get_lang3(lang):
    try:
        if len(lang) == 2:
            ret_value = get(part1=lang).part3
        elif len(lang) == 3:
            ret_value = lang
        else:
            ret_value = ""
    except (KeyError, AttributeError):
        ret_value = lang
    return ret_value
