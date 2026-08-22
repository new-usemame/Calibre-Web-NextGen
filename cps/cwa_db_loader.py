# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Canonical import path for the CWA settings database module."""

import importlib
import sys
from types import ModuleType

from . import constants


_IMPORT_PATHS = (constants.BASE_DIR, constants.SCRIPTS_DIR)
_MODULE_NAMES = ("cwa_db", "scripts.cwa_db")


def _repair_import_paths() -> None:
    """Make both historical CWA_DB import conventions resolvable once."""
    # Reversing preserves BASE_DIR-before-SCRIPTS_DIR when both are newly
    # inserted; paths that already exist keep their current position.
    for path in reversed(_IMPORT_PATHS):
        if path not in sys.path:
            sys.path.insert(1, path)


def _requested_module_is_missing(error: ModuleNotFoundError, module_name: str) -> bool:
    """Distinguish a missing target from a dependency missing inside it."""
    return error.name in {module_name, module_name.partition(".")[0]}


def _alias_module_names(module: ModuleType) -> ModuleType:
    """Make both historical names resolve to one module object."""
    for module_name in _MODULE_NAMES:
        sys.modules[module_name] = module
    return module


def load_cwa_db() -> ModuleType:
    """Return the module defining ``CWA_DB`` under either historical name.

    Reuse an existing module before importing, then alias both historical names
    to it so later imports cannot execute ``scripts/cwa_db.py`` a second time.
    Importing stays lazy to preserve the existing application startup order and
    the best-effort error handling around request-local CWA_DB imports.
    """
    _repair_import_paths()

    for module_name in _MODULE_NAMES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "CWA_DB"):
            return _alias_module_names(module)

    last_error = None
    for module_name in _MODULE_NAMES:
        try:
            return _alias_module_names(importlib.import_module(module_name))
        except ModuleNotFoundError as error:
            if not _requested_module_is_missing(error, module_name):
                raise
            last_error = error

    if last_error is not None:
        raise last_error
    raise ImportError("Unable to import the CWA database module")


# Repair only; loading cwa_db itself remains deferred until load_cwa_db().
_repair_import_paths()
