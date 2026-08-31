# SPDX-License-Identifier: GPL-3.0-or-later
"""F-011141: a stored locale must be one we actually ship.

``get_locale()`` USED TO return ``current_user.locale`` verbatim for any
logged-in non-Guest user.  The ``?lang=`` per-request override was validated
through ``_coerce_locale`` against the available set; the stored value was not,
and validation on the write side was inconsistent:

* ``cps/admin.py`` (the per-field ajax editor) checked
  ``in get_available_translations()`` before assigning;
* the self-service profile save and both admin user-editor paths assigned
  whatever arrived in the form.

So any authenticated user could persist an arbitrary string as their own
locale, and every later request resolved it verbatim.

The fix routes every write through the same coercion the read side already
uses, so the two cannot drift apart again.
"""

from __future__ import annotations

import inspect
import re

import pytest

from cps import cw_babel


pytestmark = pytest.mark.unit


class TestCoercion:
    """Executing tests — the behaviour, not the source text."""

    def test_the_live_helper_delegates_to_the_shared_coercion(self):
        """The lockout hazard, pinned without booting the app.

        Validating a stored locale against a set that excludes the default
        would lock every English user out of their own profile.  The safe
        invariant is not "en is hardcoded somewhere" but that the write sites
        and ``get_locale()`` resolve availability from the SAME function, so
        one cannot narrow without the other.  ``get_available_locale`` is that
        function, and its own comment records why the default is included:
        ``flask_babel.list_translations()`` already contains it.

        (Deliberately not asserted by calling ``create_app()``: it starts the
        scheduler and never returns, which hangs the suite.)
        """
        # Anchor the helper the WRITE SITES ACTUALLY CALL. The earlier version
        # pinned coerce_stored_locale, which after the restructure has zero
        # production callers -- a test guarding dead code.
        source = inspect.getsource(cw_babel.sanitize_locale_for_write)
        assert "_coerce_locale(raw, available)" in source, (
            "sanitize_locale_for_write must delegate to the same _coerce_locale "
            "the ?lang= override uses, or the stored and per-request paths drift"
        )
        assert "babel.list_translations()" in inspect.getsource(cw_babel), (
            "availability must still come from flask_babel, which includes the "
            "default locale; hardcoding a narrower list would lock users out"
        )

    def test_a_hyphenated_tag_is_normalised_not_rejected(self):
        """Browsers and form posts send en-GB; Babel stores en_GB."""
        available = {"en", "en_GB"}
        assert cw_babel.coerce_stored_locale("en-GB", available) == "en_GB"

    @pytest.mark.parametrize("hostile", [
        "../../../etc/passwd",
        "en; rm -rf /",
        "not_a_locale_at_all",
        "{{7*7}}",
        "",
        None,
    ])
    def test_anything_we_do_not_ship_is_refused(self, hostile):
        # An explicit set keeps this a test of the coercion rather than of
        # whatever translations happen to be installed.
        assert cw_babel.coerce_stored_locale(hostile, {"en", "fr", "nl"}) is None

    def test_a_wellformed_locale_we_do_not_ship_is_still_refused(self):
        """Parseable is not the same as available — this is the subtle half."""
        assert cw_babel.coerce_stored_locale("zu_ZA", {"en", "fr"}) is None


class TestTheReadPathIsTheBoundary:
    """The write sites are hygiene; this is what actually holds.

    Validating on write can only ever cover the writers someone remembered.
    It cannot repair a row written before the validation existed, nor a value
    copied in from an unvalidated ``config_default_locale`` by the registration,
    LDAP, OAuth or reverse-proxy provisioning paths, nor a writer added next
    year.  Coercing on read covers all of them at once.
    """

    @pytest.mark.parametrize("stored,shipped,accept,expected", [
        # the finding's own payloads must never reach a caller
        ("../../../etc/passwd", {"en", "de"}, "de", "de"),
        ("{{7*7}}",             {"en", "de"}, "de", "de"),
        # junk rows: no 500, no "None" masquerading as a locale
        (None,                  {"en", "de"}, "de", "de"),
        (123,                   {"en", "de"}, "de", "de"),
        # a good stored value still wins
        ("de",                  {"en", "de"}, "en", "de"),
        # hyphenated legacy row normalises rather than falling through
        ("pt-BR",               {"en", "pt_BR"}, "en", "pt_BR"),
        # the translation was dropped from the image: negotiate, do not strand
        ("hu",                  {"en", "de"}, "de", "de"),
        ("hu",                  {"en", "de"}, None, "en"),
    ])
    def test_get_locale_resolves_a_hostile_or_stale_stored_row(
            self, stored, shipped, accept, expected, monkeypatch):
        """The executing test the two source scans below cannot be.

        Those scans catch DELETION of the coercion. They stay green against a
        BYPASS -- `if stored: return current_user.locale` keeps every string
        they look for. Only driving the function discriminates.
        """
        import flask

        app = flask.Flask(__name__)
        monkeypatch.setattr(cw_babel, "get_available_translations", lambda: shipped)

        class _User:
            name = "someone"
            is_anonymous = False
        _User.locale = stored
        monkeypatch.setattr(cw_babel, "current_user", _User())

        headers = {"Accept-Language": accept} if accept else {}
        with app.test_request_context("/", headers=headers):
            assert cw_babel.get_locale() == expected

    def test_get_locale_coerces_the_stored_value_not_just_the_lang_param(self):
        source = inspect.getsource(cw_babel.get_locale)
        assert "_coerce_locale(current_user.locale" in source, (
            "get_locale() must coerce the STORED locale against the available "
            "set. Without it, any row written by a missed or future writer -- "
            "or written before this validation existed -- is returned verbatim "
            "and poisons every later request (F-011141)"
        )

    def test_an_unusable_stored_locale_falls_through_rather_than_pinning_the_user(self):
        source = inspect.getsource(cw_babel.get_locale)
        stored_at = source.index("_coerce_locale(current_user.locale")
        assert "request.accept_languages" in source[stored_at:], (
            "an unusable stored locale must fall through to negotiation; "
            "returning it, or raising, strands the user in a language they "
            "cannot read with no way to reach their own profile page"
        )

    @pytest.mark.parametrize("junk", [123, ["en"], {"a": 1}, object()])
    def test_a_non_string_locale_is_refused_rather_than_raising(self, junk):
        """SPA writers hand this raw JSON, which can be any type."""
        assert cw_babel.coerce_stored_locale(junk, {"en"}) is None


class TestWriteHygieneFailsOpenNotClosed:
    """Hygiene must never be able to refuse a legitimate value.

    "Available" is a runtime Flask-Babel property: it needs an app context with
    the extension registered, and that is absent in unit contexts and in some
    provisioning paths.  An earlier version of this fix called
    ``get_available_translations()`` directly at the write sites and broke seven
    tests with ``KeyError: 'babel'`` and one refusal of a perfectly good ``de``.

    Storing an unchecked value is recoverable because ``get_locale()`` coerces
    on read.  Refusing a good one is not.  So when availability cannot be
    determined, the value passes through unchanged.
    """

    def test_availability_failure_passes_the_value_through(self, monkeypatch):
        monkeypatch.setattr(cw_babel, "get_available_translations",
                            lambda: (_ for _ in ()).throw(KeyError("babel")))
        assert cw_babel.sanitize_locale_for_write("de") == "de"

    def test_a_known_bad_value_is_still_refused_when_we_can_check(self, monkeypatch):
        monkeypatch.setattr(cw_babel, "get_available_translations", lambda: {"en", "fr"})
        assert cw_babel.sanitize_locale_for_write("not_a_locale") is None
        assert cw_babel.sanitize_locale_for_write("fr") == "fr"

    def test_the_sanitised_variable_is_actually_sanitised(self):
        """Close the hole a mutant walked through.

        The assignment guard below skips any right-hand side mentioning
        ``validated_locale``, so a change that keeps the variable NAME while
        deleting the call -- ``validated_locale = to_save["locale"]`` -- passed
        every test. The name is not the check; the call is.
        """
        import importlib

        offenders = []
        for module_name in ("cps.web", "cps.admin", "cps.api.account", "cps.api.admin"):
            source = inspect.getsource(importlib.import_module(module_name))
            for line in source.splitlines():
                stripped = line.strip()
                if not stripped.startswith("validated_locale ="):
                    continue
                if "sanitize_locale_for_write(" not in stripped:
                    offenders.append("{}: {}".format(module_name, stripped))
        assert not offenders, (
            "validated_locale is assigned without actually sanitising anything; "
            "the variable name is not the guarantee:\n  " + "\n  ".join(offenders))

    def test_the_write_sites_use_the_failopen_helper_not_the_strict_one(self):
        """The strict two-argument form at a write site is the bug above."""
        import importlib

        offenders = []
        for module_name in ("cps.web", "cps.admin", "cps.api.account", "cps.api.admin"):
            source = inspect.getsource(importlib.import_module(module_name))
            for line in source.splitlines():
                if "coerce_stored_locale(" in line and "sanitize_locale_for_write" not in line:
                    offenders.append("{}: {}".format(module_name, line.strip()))
        assert not offenders, (
            "a write site calls the strict coercer directly; it raises when "
            "Flask-Babel is unavailable and refuses valid locales:\n  "
            + "\n  ".join(offenders))


class TestTheSpaCannotTrapAUserWithALegacyBadRow:
    """The trap this fix would otherwise have set for its own beneficiaries.

    The account GET seeds the React form's language <select>, and the form
    posts that value back on every save. Returning the raw stored locale meant
    a user with a legacy bad row saw a control reading "English" while its
    state held the bad string, and any save -- changing their email, say --
    was rejected wholesale with "Unsupported locale".

    Reporting the EFFECTIVE locale closes it at the source: the form can only
    ever hold a value the server will accept.
    """

    def test_the_account_serializer_reports_the_effective_locale(self):
        source = inspect.getsource(inspect.getmodule(
            __import__("cps.api.account", fromlist=["x"])))
        assert '"locale": effective_locale(' in source, (
            "the account GET must report the locale the user actually gets; "
            "returning the raw row re-creates the save trap (F-011141)"
        )
        assert '"locale": current_user.locale' not in source, (
            "raw stored locale is still being served to the SPA form"
        )


class TestEveryWriteSiteValidates:
    """No assignment of ``.locale`` may store a request value unchecked.

    Walked with ``ast`` rather than scanned line by line.  The line-based
    version missed ``new_user.locale = (`` entirely -- a multi-line right-hand
    side whose first line carries no evidence either way -- and a full revert of
    that site stayed green across the whole suite.  The AST sees the assignment
    and its complete value expression regardless of formatting.
    """

    @pytest.mark.parametrize("module_name", [
        "cps.web", "cps.admin", "cps.api.account", "cps.api.admin",
    ])
    def test_no_locale_assignment_takes_a_raw_form_value(self, module_name):
        import ast
        import importlib

        module = importlib.import_module(module_name)
        tree = ast.parse(inspect.getsource(module))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Attribute) and t.attr == "locale"
                       for t in node.targets):
                continue
            rhs = ast.unparse(node.value)
            # Config defaults and already-stored values are not user input --
            # but only when the RHS IS one, not merely mentions one. The
            # substring form waved through
            #   data.get("locale") or config.config_default_locale or "en"
            # because it contained "config." somewhere.
            stripped_rhs = rhs.strip()
            if stripped_rhs.startswith("config.") or stripped_rhs in (
                    "content.locale", "current_user.locale"):
                continue
            if ("sanitize_locale_for_write" in rhs or "coerce_stored_locale" in rhs
                    or "validated_locale" in rhs):
                continue
            offenders.append(rhs)
        assert not offenders, (
            "{} assigns .locale from an unvalidated value; every write must go "
            "through cw_babel.sanitize_locale_for_write so a stored locale can "
            "only ever be one we ship (F-011141):\n  {}".format(
                module_name, "\n  ".join(offenders))
        )
