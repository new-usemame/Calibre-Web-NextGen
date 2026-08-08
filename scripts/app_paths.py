# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Single source of truth for the paths ``scripts/`` needs (#1462).

The container installs the app under ``/app``, and for a long time every script
simply wrote that install path out as a literal. That works in Docker and
nowhere else: @Thovi98's YunoHost package installs to
``/var/www/calibreweb-nextgen/build`` and died on the first script it ran,
looking for a seed database that was sitting in the checkout the whole time.

Nothing here is a new convention. ``CWA_APP_ROOT`` was already honoured by
``set_ownership.sh``, ``CALIBRE_DBPATH`` by ``cover_enforcer.py`` and
``ingest_processor.py``, ``CWA_DIRS_JSON`` by ``library_paths.py``. They were
just applied in four places out of thirty. This module makes them the only way
``scripts/`` resolves a path, so a checkout works wherever it is unpacked.

Resolution order, for every knob:

===================  ====================================  =========================
What                 Environment override                  Default
===================  ====================================  =========================
app root             ``CWA_APP_ROOT``                       parent of this file's dir
config dir           ``CALIBRE_DBPATH``                     ``/config``
``dirs.json``        ``CWA_DIRS_JSON``                      ``<app root>/dirs.json``
``app.db``           ``CWA_APP_DB_PATH``, ``CALIBRE_DBPATH`` ``<config dir>/app.db``
===================  ====================================  =========================

Deliberately dependency-free and free of any ``cps`` import: ``auto_library.py``
runs before the Flask stack is usable, and ``cover_enforcer.py`` has to survive
an environment where importing ``cps`` fails outright.

The ``/config`` default is kept exactly as scripts/ already had it. The
container sets ``CALIBRE_DBPATH=/config`` as a Docker ``ENV`` (the #1162 fix),
so that default never fires there — and de-hardcoding the *app* root must not
quietly relocate anybody's *databases*.
"""

import os
import sys
from pathlib import Path

__all__ = [
    "app_root",
    "config_dir",
    "dirs_json",
    "app_db_path",
    "empty_library_dir",
    "empty_library_file",
    "scripts_dir",
    "script_path",
    "ensure_app_root_on_sys_path",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_LIBRARY_DIR",
]

#: Where the container keeps the writable state. Unchanged from the literal
#: that scripts/ used before #1462.
DEFAULT_CONFIG_DIR = "/config"

#: Legacy library root, used as a last resort by :mod:`library_paths`.
DEFAULT_LIBRARY_DIR = "/calibre-library"


def _env_path(name):
    """Return ``$name`` as a Path, or None when unset/blank."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return Path(raw) if raw else None


def app_root():
    """Directory the application is installed in.

    Derived from this file's location — ``scripts/`` lives directly under the
    app root — so a checkout is self-locating. ``CWA_APP_ROOT`` overrides it for
    packagers who split code from data.
    """
    override = _env_path("CWA_APP_ROOT")
    if override is not None:
        return override.resolve()
    return Path(__file__).resolve().parent.parent


def scripts_dir():
    """Directory holding the runtime scripts."""
    return app_root() / "scripts"


def script_path(name):
    """Absolute path of a sibling script, e.g. ``cover_enforcer.py``."""
    return scripts_dir() / name


def config_dir():
    """Writable config directory holding ``app.db``, ``cwa.db`` and friends.

    Honours ``CALIBRE_DBPATH``, the same knob ``cps/constants.py`` reads. A
    value pointing straight at a ``.db`` file resolves to its parent, matching
    what ``ingest_processor.get_app_db_path()`` has always accepted.
    """
    override = _env_path("CALIBRE_DBPATH")
    if override is None:
        return Path(DEFAULT_CONFIG_DIR)
    if override.suffix == ".db":
        return override.parent
    return override


def app_db_path():
    """Path to ``app.db``.

    Carries over both legacy branches from
    ``scripts/ingest_processor.get_app_db_path()``, which this replaces:
    ``CWA_APP_DB_PATH`` wins outright, and a ``CALIBRE_DBPATH`` that names a
    ``.db`` file is honoured as the database itself when it is called
    ``app.db``, or redirected to ``app.db`` beside it when it is not.
    """
    explicit = _env_path("CWA_APP_DB_PATH")
    if explicit is not None:
        return explicit

    base = _env_path("CALIBRE_DBPATH")
    if base is not None and base.suffix == ".db":
        if base.name != "app.db":
            return base.parent / "app.db"
        return base
    return config_dir() / "app.db"


def dirs_json():
    """Path to ``dirs.json`` (ingest folder, library dir, conversion tmp dir)."""
    override = _env_path("CWA_DIRS_JSON")
    if override is not None:
        return override
    return app_root() / "dirs.json"


def empty_library_dir():
    """Directory holding the seed ``app.db`` / ``metadata.db``."""
    return app_root() / "empty_library"


def empty_library_file(name):
    """Path to a seed database, e.g. ``empty_library_file("app.db")``.

    This is the path #1462 was reported against: ``auto_library.py`` copies it
    into place on first run, and resolving it to ``/app/...`` outside Docker
    aborted the install with ``FileNotFoundError``.
    """
    return empty_library_dir() / name


def ensure_app_root_on_sys_path():
    """Put the app root on ``sys.path`` so ``import cps...`` works.

    Replaces the hardcoded ``_CPS_ROOT`` literal that four scripts each carried
    their own copy of. Returns the root, as a str, for callers that want it.
    """
    root = str(app_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return root
