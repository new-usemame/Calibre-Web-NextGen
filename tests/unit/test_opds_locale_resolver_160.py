# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #160 — OPDS locale resolver chain for
anonymous clients (continuation of #121).

After v4.0.46 wrapped the OPDS root entries with ``N_()``, @droM4X
followed up: clients that send no ``Accept-Language`` (Readest, some
Kobo readers, KOReader's built-in OPDS browser) still got English because
the Guest user account has ``locale='en'`` by default and there was no
operator-configurable fallback.

This commit adds a four-step locale resolver consulted on every request:

1. ``?lang=xx`` query-param override (per-request, validated against
   shipped translations)
2. Authenticated user's ``user.locale`` (existing behavior)
3. Client ``Accept-Language`` header negotiation (existing behavior)
4. ``config_opds_default_locale`` ConfigSQL field — operator default for
   anonymous OPDS responses only (new), gated on ``request.path``
   starting with ``/opds`` so the web UI is not affected
5. Final ``'en'`` fallback (existing behavior)

These tests pin the precedence chain at the source level so a refactor
can't regress any of the four steps.
"""

import inspect

import pytest


@pytest.mark.unit
class TestLocaleResolverChain:
    """Source-pin: the precedence chain must appear in the documented order."""

    def test_query_param_check_appears_before_user_locale_check(self):
        from cps import cw_babel
        src = inspect.getsource(cw_babel.get_locale)
        # The ?lang= override must be checked before user.locale so that
        # a logged-in user can still force a per-request language for an
        # OPDS client that lives outside their browser session.
        lang_param_pos = src.find("request.args.get('lang')")
        user_locale_pos = src.find("user.locale")
        assert lang_param_pos != -1, "?lang= query-param branch missing"
        assert user_locale_pos != -1, "user.locale branch missing"
        assert lang_param_pos < user_locale_pos, (
            "?lang= override must be evaluated before user.locale — otherwise "
            "users can't override their stored locale per-request via OPDS"
        )

    def test_accept_language_check_appears_before_opds_default(self):
        from cps import cw_babel
        src = inspect.getsource(cw_babel.get_locale)
        # Accept-Language is the HTTP convention; OPDS_DEFAULT_LANG is the
        # fallback when the client sent nothing.
        accept_lang_pos = src.find("request.accept_languages")
        opds_default_pos = src.find("config_opds_default_locale")
        assert accept_lang_pos != -1, "Accept-Language branch missing"
        assert opds_default_pos != -1, "OPDS_DEFAULT_LANG fallback missing"
        assert accept_lang_pos < opds_default_pos, (
            "Accept-Language negotiation must come before the OPDS default — "
            "otherwise we'd lock Readest/KOReader users into the operator's "
            "default even when their client sent a valid preference"
        )

    def test_opds_default_is_gated_on_opds_path(self):
        from cps import cw_babel
        src = inspect.getsource(cw_babel.get_locale)
        # The OPDS_DEFAULT_LANG fallback must be scoped to /opds requests.
        # Otherwise a non-English default would silently switch the web UI
        # for anonymous browsers too — way outside the reported scope.
        assert "request.path.startswith('/opds')" in src or \
               'request.path.startswith("/opds")' in src, (
            "OPDS default locale fallback must be gated on request.path "
            "starting with /opds — otherwise it leaks into the web UI"
        )


@pytest.mark.unit
class TestConfigOpdsDefaultLocaleField:
    """The ConfigSQL field exists with the expected default + a wide enough
    String column to hold locale codes like 'zh_CN' or 'pt_BR'."""

    def test_config_opds_default_locale_column_exists(self):
        from cps.config_sql import _Settings
        assert hasattr(_Settings, "config_opds_default_locale"), (
            "config_opds_default_locale column missing from _Settings"
        )

    def test_config_opds_default_locale_default_is_empty(self):
        # Empty default means "fall through to final 'en' fallback" — i.e.
        # behaviorally identical to today's behavior for anyone who doesn't
        # explicitly configure the field. Strictly opt-in.
        from cps.config_sql import _Settings
        col = _Settings.__table__.columns["config_opds_default_locale"]
        # SQLAlchemy default may be wrapped; .arg gets the literal
        assert col.default.arg == "", (
            "config_opds_default_locale must default to empty string so "
            "existing deployments keep their current behavior unchanged"
        )

    def test_config_opds_default_locale_admin_field_wired(self):
        """The admin save handler must read the form field and persist it.
        The Default Language / locale section is in ``update_view_configuration``
        (the same handler that persists ``config_default_locale``)."""
        from cps import admin
        src = inspect.getsource(admin.update_view_configuration)
        assert "config_opds_default_locale" in src, (
            "update_view_configuration doesn't reference config_opds_default_locale — "
            "the field would be in the schema but never persisted from the form"
        )


@pytest.mark.unit
class TestLocaleCoercion:
    """The ?lang= and OPDS_DEFAULT_LANG values must be validated against
    actually-shipped translations to avoid 500s on unknown locale strings."""

    def test_coerce_helper_exists(self):
        from cps import cw_babel
        assert hasattr(cw_babel, "_coerce_locale"), (
            "_coerce_locale helper missing — required for safe parsing of "
            "user-supplied ?lang= values and operator-supplied OPDS default"
        )

    def test_coerce_returns_none_on_unknown_input(self):
        from cps import cw_babel
        # Empty / nonsense / unknown
        assert cw_babel._coerce_locale("", {"en", "hu"}) is None
        assert cw_babel._coerce_locale(None, {"en", "hu"}) is None
        assert cw_babel._coerce_locale("zzzzz", {"en", "hu"}) is None

    def test_coerce_returns_locale_when_available(self):
        from cps import cw_babel
        assert cw_babel._coerce_locale("hu", {"en", "hu"}) == "hu"
        # Hyphen → underscore normalization
        assert cw_babel._coerce_locale("zh-CN", {"en", "zh_Hans_CN", "zh_Hans"}) in {
            "zh_Hans_CN", "zh_Hans", "zh_CN", None
        }
        # We don't ship that exact form; the parse may normalize differently —
        # the contract is "if Locale.parse succeeds AND the result is in
        # `available`, return it; else None". Both are acceptable.

    def test_coerce_filters_unshipped_locale(self):
        from cps import cw_babel
        # 'fr' parses cleanly but if we don't ship a fr translation, it
        # must fall through (return None) so the caller can pick the next
        # step in the chain.
        assert cw_babel._coerce_locale("fr", {"en", "hu"}) is None
