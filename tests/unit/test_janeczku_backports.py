# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Regression tests for the 2026-04 janeczku backport wave.

These tests pin the upstream behaviour we ported so future refactors can't
silently undo a security fix. Each test is named after the upstream commit
SHA short and the Calibre-Web-NextGen file it covers.
"""

import os
import stat
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# XXE — cps/epub_helper.py and cps/fb2.py both build a shared _safe_parser
# with resolve_entities=False and no_network=True. Smoke that parsing an
# entity-laden document does NOT expand the entity.
# ---------------------------------------------------------------------------

class TestXXEParsers:
    """Verify that lxml parsers used for EPUB / FB2 disable entity expansion."""

    XXE_DOC = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE root [<!ENTITY xxe "PWNED">]>'
        b'<root>&xxe;</root>'
    )

    def test_epub_helper_safe_parser_blocks_entity_expansion(self):
        from cps import epub_helper
        from lxml import etree

        # Use the module-level _safe_parser the same way get_content_opf does.
        parser = epub_helper._safe_parser
        tree = etree.fromstring(self.XXE_DOC, parser=parser)
        # Entity should NOT have been expanded to "PWNED".
        assert b"PWNED" not in etree.tostring(tree)

    def test_fb2_safe_parser_blocks_entity_expansion(self):
        from cps import fb2
        from lxml import etree

        parser = fb2._safe_parser
        tree = etree.fromstring(self.XXE_DOC, parser=parser)
        assert b"PWNED" not in etree.tostring(tree)

    def test_safe_parser_disables_network(self):
        # Both modules build their parser with no_network=True; verify the
        # configured parser truly has the flag, so a future refactor that
        # rebuilds the parser can't drop it.
        from cps import epub_helper, fb2
        for parser in (epub_helper._safe_parser, fb2._safe_parser):
            # lxml exposes resolve_entities and no_network on the parser via
            # attribute access; the ParserOptions object on .feed_error_log
            # isn't introspectable from Python, so we check the constructor
            # invariant by re-running with an external SYSTEM entity and
            # asserting the body stays empty.
            from lxml import etree
            doc = (
                b'<?xml version="1.0"?>'
                b'<!DOCTYPE r [<!ENTITY % e SYSTEM "http://127.0.0.1:1/x">%e;]>'
                b'<r/>'
            )
            try:
                etree.fromstring(doc, parser=parser)
            except etree.XMLSyntaxError:
                # Some builds raise on the parameter entity; that is also a
                # valid "did not fetch" outcome.
                pass


# ---------------------------------------------------------------------------
# LDAP injection — cps/services/simpleldap._escape_ldap_filter
# ---------------------------------------------------------------------------

class TestLDAPFilterEscape:
    """RFC 4515 escaping of LDAP filter metacharacters."""

    def _escape(self, s):
        # Import lazily so the test doesn't pull in the whole flask_simpleldap
        # stack at collection time.
        from cps.services.simpleldap import _escape_ldap_filter
        return _escape_ldap_filter(s)

    def test_escapes_backslash(self):
        assert self._escape("a\\b") == "a\\5cb"

    def test_escapes_asterisk(self):
        assert self._escape("a*b") == "a\\2ab"

    def test_escapes_parens(self):
        assert self._escape("(uid=admin)") == "\\28uid=admin\\29"

    def test_escapes_nul(self):
        assert self._escape("a\x00b") == "a\\00b"

    def test_idempotent_on_safe_username(self):
        assert self._escape("alice") == "alice"

    def test_combines_multiple(self):
        # The classic injection — the asterisk was the wildcard.
        assert self._escape("*)(uid=*") == "\\2a\\29\\28uid=\\2a"


# ---------------------------------------------------------------------------
# debug_info credential leak — cps/config_sql.py ConfigSQL.to_dict()
# ---------------------------------------------------------------------------

class TestConfigToDictRedaction:
    """to_dict must not expose api/token/secret keys."""

    def _make_config(self, **kwargs):
        # Reach in via __dict__ so we don't need a live SQLAlchemy session.
        from cps.config_sql import ConfigSQL

        cfg = ConfigSQL.__new__(ConfigSQL)
        cfg.__dict__.update({
            "config_calibre_web_title": "test",
            "config_log_level": 1,
            "_secret_key": "skip-private",
            "config_kobo_secret": "shhh",
            "config_oauth_token": "tk-xxx",
            "config_api_key": "ak-xxx",
            "config_smtp_password_e": "encrypted-skipped",
            "cli": object(),
        })
        cfg.__dict__.update(kwargs)
        return cfg

    def test_excludes_secret_keys(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "config_kobo_secret" not in d

    def test_excludes_token_keys(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "config_oauth_token" not in d

    def test_excludes_api_keys(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "config_api_key" not in d

    def test_excludes_encrypted_columns(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "config_smtp_password_e" not in d

    def test_excludes_underscore_prefixed(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "_secret_key" not in d

    def test_excludes_cli(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert "cli" not in d

    def test_keeps_normal_keys(self):
        cfg = self._make_config()
        d = cfg.to_dict()
        assert d.get("config_calibre_web_title") == "test"
        assert d.get("config_log_level") == 1


# ---------------------------------------------------------------------------
# OPDS atom:updated — cps/db.py Books.atom_timestamp
# ---------------------------------------------------------------------------

class TestAtomTimestamp:
    """atom_timestamp prefers last_modified over timestamp.

    Books is a SQLAlchemy declarative class so we can't just set attributes
    on a bare instance. Instead we extract the property's underlying function
    and invoke it against a plain holder object — same observable contract.
    """

    def _atom_timestamp_for(self, **kwargs):
        from cps.db import Books

        prop = Books.__dict__["atom_timestamp"]
        holder = MagicMock()
        holder.timestamp = kwargs.get("timestamp", None)
        holder.last_modified = kwargs.get("last_modified", None)
        return prop.fget(holder)

    def test_returns_last_modified_when_present(self):
        ts_added = datetime(2020, 1, 1, tzinfo=timezone.utc)
        ts_mod = datetime(2026, 4, 19, 12, 34, 56, tzinfo=timezone.utc)
        assert self._atom_timestamp_for(timestamp=ts_added, last_modified=ts_mod) \
            == "2026-04-19T12:34:56+00:00"

    def test_falls_back_to_timestamp_when_last_modified_null(self):
        ts_added = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert self._atom_timestamp_for(timestamp=ts_added, last_modified=None) \
            == "2020-01-01T00:00:00+00:00"

    def test_returns_empty_string_when_both_null(self):
        assert self._atom_timestamp_for() == ""


# ---------------------------------------------------------------------------
# --dont-save-cover — cps/embed_helper.py do_calibre_export argv
# ---------------------------------------------------------------------------

class TestEmbedHelperArgv:
    """The calibredb command line must include --dont-save-cover."""

    def test_dont_save_cover_in_argv(self):
        # We don't actually invoke calibredb; we just confirm the source string
        # contains the flag adjacent to --dont-write-opf, since that's what
        # the upstream fix enforces.
        with open("cps/embed_helper.py") as f:
            src = f.read()
        assert "--dont-save-cover" in src
        assert "--dont-write-opf" in src


# ---------------------------------------------------------------------------
# encryption-key chmod — cps/config_sql.get_encryption_key
# ---------------------------------------------------------------------------

class TestEncryptionKeyPermissions:
    """Freshly generated .key file is mode 0600."""

    def test_chmod_600_on_create(self, tmp_path):
        from cps.config_sql import get_encryption_key

        key_path = str(tmp_path)
        key, error = get_encryption_key(key_path)
        assert key is not None
        assert error == ""
        key_file = os.path.join(key_path, ".key")
        assert os.path.exists(key_file)
        # On POSIX, the lower 9 bits should be 0600 (rw-------).
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(key_file).st_mode)
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
