# SPDX-License-Identifier: GPL-3.0-or-later
"""F-011141: a stored locale must be one we actually ship.

``get_locale()`` returns ``current_user.locale`` verbatim for any logged-in
non-Guest user (cps/cw_babel.py).  The ``?lang=`` per-request override IS
validated, through ``_coerce_locale`` against the available set — but the
stored value was not, and validation on the write side was inconsistent:

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

    def test_the_write_path_consults_the_same_set_as_the_read_path(self):
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
        source = inspect.getsource(cw_babel)
        assert "def coerce_stored_locale" in source
        assert "return _coerce_locale(raw, available)" in source, (
            "coerce_stored_locale must delegate to the same _coerce_locale the "
            "?lang= override uses, or the stored and per-request paths can drift"
        )
        assert "babel.list_translations()" in source, (
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


class TestEveryWriteSiteValidates:
    """No assignment of ``.locale`` may take a request value unchecked.

    Asserted over the source because the three sites live in Flask request
    handlers that a unit test cannot drive without a full app + session; what
    regressed here is whether a value is checked before it is stored.
    """

    ASSIGNMENT = re.compile(r"^\s*(?:content|user|current_user)\.locale\s*=\s*(.+)$")

    # Enumerated repo-wide with `grep -rn "\.locale = " cps/`, not from the
    # diff: the first version of this guard scanned only the two modules the
    # fix had touched, and the SPA writers in cps/api/ went unnoticed while
    # four mutation tests went green. A guard covers exactly the files it lists.
    @pytest.mark.parametrize("module_name", [
        "cps.web", "cps.admin", "cps.api.account", "cps.api.admin",
    ])
    def test_no_locale_assignment_takes_a_raw_form_value(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        offenders = []
        for line in inspect.getsource(module).splitlines():
            match = self.ASSIGNMENT.match(line)
            if not match:
                continue
            rhs = match.group(1)
            # Config defaults and already-stored values are not user input.
            if "config." in rhs or rhs.strip() in ("content.locale", "current_user.locale"):
                continue
            if ("sanitize_locale_for_write" in rhs or "coerce_stored_locale" in rhs
                    or "validated_locale" in rhs):
                continue
            offenders.append(line.strip())
        assert not offenders, (
            "{} assigns .locale from an unvalidated value; every write must go "
            "through cw_babel.coerce_stored_locale so a stored locale can only "
            "ever be one we ship (F-011141):\n  {}".format(
                module_name, "\n  ".join(offenders))
        )
