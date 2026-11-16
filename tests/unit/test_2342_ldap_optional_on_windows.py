# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#2342: LDAP must not block an install on Windows, and must stay in the Linux image.

python-ldap has no Windows wheels and needs a C toolchain to build, so a plain
``pip install .`` on Windows failed before the app could run at all. The core
dependencies carry ``sys_platform != 'win32'`` markers on python-ldap and
Flask-SimpleLDAP: the resolver drops them on Windows and keeps them everywhere
else, so the Docker image and CI keep LDAP login with no other change.

The fallback tests come from @theluckystrike's #2583: the app already guards the
import in ``cps/services/__init__.py``, and without the packages it must boot
with ``ldap = None``.
"""

import importlib
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import cps.services


pytestmark = pytest.mark.unit

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_LDAP_PACKAGES = {"python-ldap", "flask-simpleldap"}


def _core_dependencies_for(sys_platform, platform_system):
    """Names pip installs from the core dependency list on the given platform."""
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    env = {"sys_platform": sys_platform, "platform_system": platform_system}
    names = set()
    for line in project["dependencies"]:
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate(env):
            names.add(canonicalize_name(req.name))
    return names


def test_a_windows_install_does_not_pull_in_ldap():
    assert not (_core_dependencies_for("win32", "Windows") & _LDAP_PACKAGES)


@pytest.mark.parametrize("sys_platform,platform_system", [
    ("linux", "Linux"),
    ("darwin", "Darwin"),
])
def test_linux_and_macos_installs_keep_ldap(sys_platform, platform_system):
    assert _LDAP_PACKAGES <= _core_dependencies_for(sys_platform, platform_system)


_real_import = __import__


def _block_ldap_imports(name, *args, **kwargs):
    if name.startswith("cps.services.simpleldap") or name == "flask_simpleldap":
        raise ImportError("no module named 'flask_simpleldap'")
    return _real_import(name, *args, **kwargs)


def test_the_app_imports_with_ldap_none_when_the_packages_are_absent():
    with patch.dict(sys.modules):
        sys.modules["cps.services.simpleldap"] = None
        sys.modules["flask_simpleldap"] = None
        with patch("builtins.__import__", side_effect=_block_ldap_imports):
            services = importlib.reload(cps.services)
            assert services.ldap is None
            assert services.ldapVersion is None
            importlib.import_module("cps")
    importlib.reload(cps.services)
