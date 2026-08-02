# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Request-scoped select options shared by the /api/v1 admin and account forms.

Both forms render the same two dropdowns — interface language and default book
language — so both have to build them the same way. They used to build them
separately and drifted: the account payload marked the "all" sentinel for
translation, the admin payload did not, so on a Dutch session every label on
the admin settings form was translated except that one option, which still read
"Show All" (#886, reported by @iroQuai).

These two lists are *server*-translated. ``speaking_language()`` already
localises each language name through ``isoLanguages.get_language_name``, and
babel localises ``Locale.display_name``, so the SPA renders ``name`` verbatim.
That is why the sentinel has to be translated here rather than with a
client-side ``t()``: the SPA has no way to tell the one static option apart from
the dynamic names around it, and running ``t()`` over the whole list would treat
every language name as a msgid.

The other direction — the server sending an untranslated msgid for the SPA to
translate at render time — is a real pattern in this codebase, but it is for
fixed backend enums (login types, LDAP levels; see ``_LOGIN_TYPES`` in
``admin_security.py``, anchored in ``cps/spa_strings.py`` and rendered as
``t(o.name)``). Don't mix the two.
"""
from flask_babel import gettext as _

from .. import calibre_db, logger
from ..cw_babel import get_available_locale

log = logger.create()

__all__ = ["locale_options", "book_language_options"]


def locale_options():
    """Interface-language choices — every shipped locale, under its own name."""
    return [{"id": str(loc), "name": loc.display_name}
            for loc in get_available_locale()]


def book_language_options():
    """Default-book-language choices — the "all" sentinel, then every language
    present in the library with its name already localised.

    Fail-soft on the library read: a missing or locked Calibre DB yields the
    sentinel alone rather than a 500 on an otherwise-working settings form. It
    is logged rather than swallowed silently — an empty language list is a
    symptom worth finding in the log, not a state to discover by squinting at a
    dropdown.
    """
    options = [{"id": "all", "name": _("Show All")}]
    try:
        options += [{"id": lang.lang_code, "name": lang.name}
                    for lang in calibre_db.speaking_language()]
    except Exception as ex:
        log.warning("Could not read the library's languages; offering only the "
                    "'all' option: %s", ex)
    return options
