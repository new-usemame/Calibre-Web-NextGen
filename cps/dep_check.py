# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from importlib.metadata import version, requires, PackageNotFoundError
from packaging.requirements import Requirement

def load_dependencies(optional=False):
    deps = list()
    try:
        requirements = requires("calibre-web-automated") or []
    except:
        requirements = []

    for dep in requirements:
        req = Requirement(dep)

        is_extra = req.marker and "extra" in str(req.marker)
        if (not optional and is_extra) or (optional and not is_extra):
            continue

        # Environment markers (sys_platform, python_version) say whether a
        # requirement applies to this interpreter at all. Without this, the three
        # marked entries in pyproject.toml come back reading "not installed" on an
        # interpreter they were never meant to be installed on, which is what
        # forced the About page to hide every "not installed" row -- including the
        # ones that were genuinely missing. Extras are skipped because "extra" is
        # undefined in a bare marker environment and would evaluate False.
        if req.marker and not is_extra and not req.marker.evaluate():
            continue

        try:
            dep_version = version(req.name)
        except (PackageNotFoundError):
            dep_version = "not installed"
        deps.append([dep_version, req])

    return deps
