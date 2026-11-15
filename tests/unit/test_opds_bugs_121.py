# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #121 — three independent OPDS bugs
reported by @droM4X on v4.0.39 against the Readest client.

1. **Log spam.** Every authenticated OPDS request emitted a WARN line
   `OPDS Login failed for user "" IP-address: ...` because OPDS clients
   commonly issue an unauthenticated probe before sending credentials.
   The empty-username probe is not a login failure — the WARN was noise.

2. **Anon+auth multiplexing.** When anonymous browsing was enabled and
   the client also sent (possibly stale) credentials, the server
   returned 401 instead of falling back to the Guest catalog. The user
   expected anon-enabled deployments to behave the same whether the
   client tried to authenticate or not.

3. **Hardcoded English on three OPDS root entries.** "Alphabetical
   Books", "Recently added Books", and "Magic Shelves" rendered in
   English on a Hungarian-locale UI while sibling entries ("Hot Books",
   "Read Books") localized correctly. The entries' titles were bare
   Python literals in a dict — pybabel-extract can't follow
   ``_(variable)`` at extract time, so those three never made it into
   ``messages.pot``. Wrapping with ``N_()`` (lazy_gettext) marks them
   for extraction.

These tests pin the fix at the source level so a refactor can't regress
any of the three.
"""

import inspect

import pytest


@pytest.mark.unit
class TestEmptyUsernameProbeIsNotAnError:
    """Bug 1: empty username must short-circuit cleanly."""

    def test_verify_password_short_circuits_on_empty_username(self):
        from cps import usermanagement
        src = inspect.getsource(usermanagement.verify_password)
        # The very first behavioural branch must filter `not username`
        # and `return None` before any DB query or log line fires.
        assert "if not username:\n        return None" in src, (
            "verify_password must short-circuit on empty username before "
            "hitting the DB or logging — otherwise every OPDS probe spams "
            "a misleading WARN"
        )

    def test_no_warning_logged_for_empty_username(self, mocker):
        """Behavioral: calling verify_password("", "") must not invoke
        log.warning. This is the actual user-visible symptom."""
        from cps import usermanagement
        mock_log = mocker.patch.object(usermanagement, "log")
        # ub.session may not be ready in the test env; the early return
        # avoids touching it at all.
        result = usermanagement.verify_password("", "")
        assert result is None
        mock_log.warning.assert_not_called()


@pytest.mark.unit
class TestAnonFallbackOnFailedAuth:
    """Bug 2: when anon browsing is on and auth fails, fall back to
    Guest instead of 401."""

    def test_decorator_falls_back_to_guest_on_failed_auth_when_anon_enabled(self):
        from cps import usermanagement
        src = inspect.getsource(usermanagement.requires_basic_auth_if_no_ano)
        # The fix introduces a second branch that retries auth with the
        # synthetic Guest credentials specifically when both
        # (a) the first auth attempt returned None/False AND
        # (b) config.config_anonbrowse is enabled.
        assert "config.config_anonbrowse == 1" in src
        # Look for the second Guest-substitution branch — there are now
        # two Guest substitutions, one for "no creds sent" (preserved
        # from before the fix) and one for "creds sent but failed" (new).
        guest_subs = src.count("'username': \"Guest\"")
        assert guest_subs >= 2, (
            "expected two Guest-credential substitutions in the decorator: "
            "one for 'no auth header sent', one for 'auth failed AND anon "
            "enabled' (the issue #121 fallback). Found %d." % guest_subs
        )


@pytest.mark.unit
class TestOpdsRootEntryDefsAreExtractable:
    """Bug 3: every title/description literal must be wrapped with N_()
    so pybabel-extract picks it up into messages.pot. The OPDS root
    dict had bare Python literals in pre-fix code, so newer keys never
    landed in the catalog and rendered as English on translated UIs."""

    def test_opds_module_imports_lazy_gettext_as_N(self):
        import cps.opds as opds_mod
        # N_ must be importable from the module so the entry-defs literals
        # can use it. We assert the symbol exists rather than do a regex
        # over the source because subtle re-exports can hide a missing
        # marker.
        assert hasattr(opds_mod, "N_"), (
            "cps.opds must expose N_ (flask_babel.lazy_gettext) — without "
            "it the dict-resident OPDS title/description literals can't "
            "be picked up by pybabel-extract"
        )

    def test_every_root_entry_title_is_lazy_string(self):
        from cps.opds import OPDS_ROOT_ENTRY_DEFS
        # lazy_gettext returns a LazyString; the cheapest cross-version
        # check is that the value is not a plain `str` instance.
        for key, entry in OPDS_ROOT_ENTRY_DEFS.items():
            assert not isinstance(entry["title"], str), (
                f"OPDS root entry '{key}' has a bare-string title "
                f"({entry['title']!r}) — wrap with N_() so the translation "
                f"extractor picks it up"
            )
            assert not isinstance(entry["description"], str), (
                f"OPDS root entry '{key}' has a bare-string description "
                f"({entry['description']!r}) — wrap with N_() so the "
                f"translation extractor picks it up"
            )

    def test_specific_keys_user_reported_are_marked(self):
        """The three keys @droM4X named specifically."""
        from cps.opds import OPDS_ROOT_ENTRY_DEFS
        for k in ("books", "recent", "magic_shelves"):
            title = OPDS_ROOT_ENTRY_DEFS[k]["title"]
            # str() coercion via __str__ resolves the LazyString to the
            # current-locale translation; in unit-test context with no
            # locale set this is just the source English. The point
            # of this test is that the value is no longer a bare str —
            # see the previous test. Here we also assert the source
            # English matches what the user reported.
            assert str(title) in (
                "Alphabetical Books",
                "Recently added Books",
                "Smart shelves",
            ), f"unexpected title for {k}: {title!r}"

    def test_get_opds_root_entries_resolves_title_through_gettext(self):
        """The render-side translator must still be called on the
        LazyString. Source-pin so a refactor can't drop the gettext
        coercion at the dict-merge step."""
        import inspect as _inspect
        from cps.opds import get_opds_root_entries
        src = _inspect.getsource(get_opds_root_entries)
        assert "_(entry_def['title'])" in src
        assert "_(entry_def['description'])" in src
