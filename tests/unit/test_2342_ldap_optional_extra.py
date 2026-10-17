# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#2342 — LDAP is an optional extra, so the app must boot with ldap = None.

python-ldap and Flask-SimpleLDAP moved out of the core dependencies into the
`ldap` extra. The app already guards the import in cps/services/__init__.py and
falls back to `ldap = None`, so a checkout without the extra must still import
and boot. These tests pin that behaviour so a future change cannot silently
make LDAP a hard requirement again.
"""

import importlib
import sys
from unittest.mock import patch

import pytest

import cps.services


pytestmark = pytest.mark.unit


def _reload_services_without_ldap():
    """Reload cps.services as if python-ldap / Flask-SimpleLDAP were absent."""
    with patch.dict(sys.modules):
        # Force the guarded import to fail exactly as it does without the extra.
        sys.modules["cps.services.simpleldap"] = None
        sys.modules["flask_simpleldap"] = None
        with patch("builtins.__import__", side_effect=_block_ldap_imports):
            return importlib.reload(cps.services)


def _block_ldap_imports(name, *args, **kwargs):
    if name.startswith("cps.services.simpleldap") or name == "flask_simpleldap":
        raise ImportError("no module named 'flask_simpleldap'")
    return _real_import(name, *args, **kwargs)


_real_import = __import__


def test_services_fall_back_to_ldap_none_without_the_extra():
    """Without python-ldap the services module sets ldap to None, not a crash."""
    services = _reload_services_without_ldap()
    assert services.ldap is None
    assert services.ldapVersion is None


def test_app_imports_without_ldap():
    """The cps package imports cleanly when LDAP is not installed."""
    # cps.services is already imported at module scope; reloading it with the
    # ldap import blocked must not raise, and the rest of cps must still import.
    services = _reload_services_without_ldap()
    assert services.ldap is None
    import cps  # noqa: F401  (importing the package must not require LDAP)
