# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from babel import negotiate_locale
from flask_babel import Babel, Locale
from babel.core import UnknownLocaleError
from flask import request, has_request_context
from .cw_login import current_user

from . import logger

log = logger.create()

babel = Babel()


def _coerce_locale(raw, available):
    """Parse a raw locale string and return it if it's one we ship a
    translation for. Returns None on any failure — caller falls through."""
    if not raw:
        return None
    if not isinstance(raw, str):
        # JSON bodies can carry a number, list or object here; Locale.parse
        # would raise AttributeError on .replace and 500 the request.
        log.debug('Ignoring non-string locale of type %s', type(raw).__name__)
        return None
    try:
        candidate = str(Locale.parse(raw.replace('-', '_')))
    except (UnknownLocaleError, ValueError) as e:
        log.debug('Could not parse locale "%s": %s', raw, e)
        return None
    if candidate in available:
        return candidate
    return None


def get_locale():
    # If no request context (e.g. background thread), fall back to English
    if not has_request_context():
        return 'en'

    available = get_available_translations()

    # Fork issue #160: per-request ?lang= override. droM4X's specific ask —
    # lets a user point any OPDS client at /opds?lang=hu and force Hungarian
    # even when the client (Readest, some Kobo readers) sends no
    # Accept-Language header. Validated against the locales we actually ship
    # so an unknown value falls through cleanly instead of returning a 500.
    lang_param = request.args.get('lang')
    coerced = _coerce_locale(lang_param, available)
    if coerced:
        return coerced

    # if a user is logged in, use the locale from the user settings
    if current_user is not None and hasattr(current_user, "locale"):
        # if the account is the guest account bypass the config lang settings
        if current_user.name != 'Guest':
            # F-011141: coerce the STORED value too, not just ?lang=. This is
            # the security boundary, deliberately placed on the read side:
            #   - it repairs rows written before validation existed;
            #   - it covers every writer, including ones added later and the
            #     provisioning paths (registration, LDAP, OAuth, reverse proxy)
            #     that copy config_default_locale in without checking it;
            #   - it survives a server dropping a translation it used to ship.
            # Write-time validation still exists, but for data hygiene; a
            # missed writer must not be able to break locale resolution.
            stored = _coerce_locale(current_user.locale, available)
            if stored:
                return stored
            # An unusable stored locale falls through to negotiation rather
            # than 500ing or pinning the user to a language they cannot read.

    preferred = list()
    if request.accept_languages:
        for x in request.accept_languages.values():
            # Skip wildcard '*' from Accept-Language headers (common in internal API requests)
            if x == '*':
                continue
            try:
                preferred.append(str(Locale.parse(x.replace('-', '_'))))
            except (UnknownLocaleError, ValueError) as e:
                log.debug('Could not parse locale "%s": %s', x, e)

    if preferred:
        negotiated = negotiate_locale(preferred, available)
        if negotiated:
            return negotiated

    # Fork issue #160 / #121 follow-up: anonymous OPDS clients commonly send
    # no Accept-Language at all (Readest, KOReader's built-in OPDS browser).
    # When that happens AND no per-request override is set, fall back to the
    # operator-configured OPDS default locale before the final 'en' fallback.
    # Scoped to /opds paths so we don't accidentally lock the web UI into a
    # non-English default for users who haven't configured anything.
    if request.path.startswith('/opds'):
        try:
            from . import config
            opds_default = getattr(config, 'config_opds_default_locale', '') or ''
        except Exception:
            opds_default = ''
        coerced = _coerce_locale(opds_default, available)
        if coerced:
            return coerced

    return negotiate_locale(preferred or ['en'], available)


def get_user_locale_language(user_language):
    return Locale.parse(user_language).get_language_name(get_locale())


def sanitize_locale_for_write(raw):
    """Best-effort hygiene for a locale about to be stored.

    Returns the normalised locale when we can confirm we ship it, ``None`` when
    we can confirm we do not, and the value UNCHANGED when availability cannot
    be determined at all.

    That third case is deliberate and is the whole reason this helper exists.
    "Available" is a runtime Flask-Babel property: it needs an app context with
    the extension registered, which is absent in unit contexts and in some
    provisioning paths.  Refusing a legitimate locale because we could not check
    is a regression; storing an unchecked one is not, because ``get_locale()``
    coerces on READ.  Write-side validation is hygiene, and hygiene must never
    be able to break the thing it is tidying.
    """
    try:
        available = get_available_translations()
    except Exception as e:  # no app context, or Flask-Babel not registered
        log.debug('Locale availability unknown (%s); storing unvalidated, '
                  'get_locale() coerces on read', e)
        return raw
    return _coerce_locale(raw, available)


def effective_locale(raw):
    """The locale a caller will actually be served, for reporting back.

    Serializers hand this the stored value so a client form can only ever hold
    something the server will accept.  Fails open exactly like
    ``sanitize_locale_for_write``: resolution needs a request context and a
    registered Flask-Babel, and a serializer must not 500 because it could not
    look one up.
    """
    try:
        return get_locale()
    except Exception as e:
        log.debug('Locale resolution unavailable (%s); reporting stored value', e)
        return raw


def coerce_stored_locale(raw, available):
    """Validate a locale that is about to be STORED on a user.

    Retained for explicit-set callers and tests.  Production write sites use
    ``sanitize_locale_for_write`` instead, which fails open when availability
    cannot be determined; ``get_locale()`` coerces on read, so this is hygiene
    rather than the security boundary (F-011141).

    Returns the normalised locale (``en-GB`` -> ``en_GB``) when we ship a
    translation for it, and ``None`` otherwise so the caller can keep the value
    it already had rather than storing something unusable.  Note that
    *parseable* is not *available*: a well-formed tag we do not ship is refused.
    """
    return _coerce_locale(raw, available)


def get_available_locale():
    # flask_babel.list_translations() already includes the default locale ('en')
    # whether or not a translation directory exists for it, so don't prepend
    # Locale('en') — that produced "English" twice in the language picker.
    # Sort by display name for a stable, alphabetic dropdown order.
    return sorted(babel.list_translations(), key=lambda x: x.display_name.lower())


def get_available_translations():
    return set(str(item) for item in get_available_locale())
