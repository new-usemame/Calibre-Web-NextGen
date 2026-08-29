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
config dir           ``CALIBRE_DBPATH``                     same as ``cps`` (app root)
``dirs.json``        ``CWA_DIRS_JSON``                      ``<app root>/dirs.json``
ingest folder        ``CWA_INGEST_FOLDER``                  ``/cwa-book-ingest``
library directory    ``CWA_CALIBRE_LIBRARY_DIR``            ``/calibre-library``
conversion temp      ``CWA_TMP_CONVERSION_DIR``             ``/config/.cwa_conversion_tmp``
``app.db``           ``CWA_APP_DB_PATH``, ``CALIBRE_DBPATH`` ``<config dir>/app.db``
===================  ====================================  =========================

The three runtime directories use their matching key in ``dirs.json`` between
the environment override and the compiled-in default.

Every default here has to match what ``cps`` resolves to, because the two
halves of the install read and write the same files. Where they disagree, the
disagreement is easy to miss in Docker — the image supplies its layout through
environment values and the shipped ``dirs.json`` — and silent everywhere else:
scripts seed a database the app never opens. Both known cases were fixed
together in the #1462 follow-up.

Deliberately dependency-free and free of any ``cps`` import: ``auto_library.py``
runs before the Flask stack is usable, and ``cover_enforcer.py`` has to survive
an environment where importing ``cps`` fails outright.

**These launch variables are trusted configuration, not user input.** They are read
from the launch environment — the Dockerfile, the s6 service definitions, or a
packager's systemd unit — and they were already trusted that way before this
module existed. Centralising them does widen what one of them reaches:
``CWA_APP_ROOT`` now selects the ``sys.path`` entry used for ``cps`` imports
across every script rather than a couple of them, so whoever can set it can
choose which ``cps`` package is imported. That is the same privilege the
launch environment already had (it picks the interpreter and the code), but do
not plumb any of these through from a request, a config page, or anything a
library user can influence.

The three runtime directories are nevertheless validated before use because
container startup passes them to recursive ownership operations. They must be
absolute, single-line paths without a ``..`` component; an invalid non-blank
configuration fails loudly instead of being interpreted relative to a service's
working directory.

The ``/config`` default is kept exactly as scripts/ already had it. The
container sets ``CALIBRE_DBPATH=/config`` as a Docker ``ENV`` (the #1162 fix),
so that default never fires there — and de-hardcoding the *app* root must not
quietly relocate anybody's *databases*.
"""

import json
import os
import posixpath
import sys
import threading
from pathlib import Path

__all__ = [
    "app_root",
    "config_dir",
    "config_path",
    "processed_books_dir",
    "stray_legacy_config_dir",
    "dirs_json",
    "ingest_folder",
    "calibre_library_dir",
    "tmp_conversion_dir",
    "app_db_path",
    "scripts_dir",
    "script_path",
    "ensure_app_root_on_sys_path",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_INGEST_FOLDER",
    "DEFAULT_LIBRARY_DIR",
    "DEFAULT_TMP_CONVERSION_DIR",
    "RuntimePathError",
]

#: Where the *container* keeps the writable state. The Dockerfile sets
#: ``CALIBRE_DBPATH=/config``, so this is documentation of that layout rather
#: than a fallback — off Docker the fallback is whatever ``cps`` uses, see
#: :func:`_default_config_dir`. Kept as a named constant because "the container
#: puts config here" is worth being able to reference.
DEFAULT_CONFIG_DIR = "/config"

#: Container ingest mount, used when neither the environment nor dirs.json
#: supplies a usable path.
DEFAULT_INGEST_FOLDER = "/cwa-book-ingest"

#: Legacy library root, used as a last resort by :mod:`library_paths`.
DEFAULT_LIBRARY_DIR = "/calibre-library"

#: Container conversion scratch directory, used as the final fallback.
DEFAULT_TMP_CONVERSION_DIR = "/config/.cwa_conversion_tmp"

_DIRS_JSON_LOGGED_KEYS = set()
_DIRS_JSON_LOG_LOCK = threading.Lock()


class RuntimePathError(ValueError):
    """A configured runtime directory is unsafe or cannot name one path."""


def _validated_runtime_dir(value, source):
    """Return a lexical absolute directory or reject launch configuration.

    These values eventually reach recursive ownership operations during
    container startup. Resolving ``..`` relative to ``/config`` or accepting a
    root-equivalent spelling can turn that operation into a walk of ``/``.
    Normalise repeated separators, ``.`` components, and trailing separators
    without resolving symlinks, then require one non-root absolute, single-line
    path with no parent traversal component.
    """
    configured = value.strip()
    path = Path(configured)
    normalised = posixpath.normpath(
        "/" + configured.lstrip("/") if path.is_absolute() else configured
    )
    if (
        not configured
        or "\x00" in configured
        or "\n" in configured
        or "\r" in configured
        or not path.is_absolute()
        or ".." in path.parts
        or normalised == "/"
    ):
        raise RuntimePathError(
            f"{source} must be a non-root absolute path without '..' components; "
            f"got {value!r}"
        )
    return normalised


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

    When it is unset, the fallback has to be whatever ``cps`` would pick, or
    the two halves of the install disagree about where the database lives.
    ``cps/constants.py`` resolves ``CALIBRE_DBPATH`` -> ``~/.calibre-web-automated``
    when the pip ``.HOMEDIR`` marker is present -> ``BASE_DIR`` otherwise. This
    mirrors that.

    It used to be the literal ``/config`` instead, which is right in the image
    and wrong everywhere else. The container is unaffected either way because
    the Dockerfile sets ``CALIBRE_DBPATH=/config`` explicitly (the #1162 fix),
    so the fallback never fires there. Off Docker it fired every time, and the
    two sides landed in different places: ``auto_library.py`` seeded ``app.db``
    into a newly-created ``/config`` at the filesystem root while the app read
    ``<app root>/app.db`` and found nothing. @Thovi98 hit exactly this while
    packaging for YunoHost — "``Created config directory /config``: indeed, but
    it has been created at /config as absolute path" — and the seeding silently
    went to a database the app never opens.
    """
    override = _env_path("CALIBRE_DBPATH")
    if override is None:
        return _default_config_dir()
    if override.suffix == ".db":
        return override.parent
    return override


def config_path(*parts):
    """A file or directory beneath the resolved writable config root."""
    return config_dir().joinpath(*parts)


def processed_books_dir():
    """Root for retained originals, failed conversions and backup archives."""
    return config_path("processed_books")


def _default_config_dir():
    """The directory ``cps`` would use when ``CALIBRE_DBPATH`` is unset.

    Mirrors ``cps/constants.py`` exactly and infers nothing beyond it: the pip
    ``.HOMEDIR`` marker selects ``~/.calibre-web-automated``, everything else
    gets the app root.

    An earlier revision of this fix also deferred to an existing
    ``/config/app.db``, to protect a bare-metal service started with ``cps.py
    -p /config/app.db`` — an argument scripts/ cannot see. That heuristic is
    unsound, and cannot be repaired: ``cps`` reads ``/config`` only when
    ``CALIBRE_DBPATH`` is set, and when it is set this function is never
    reached. So the branch could never make the two sides agree; it could only
    make them disagree in a *new* way. Worse, it fired on exactly the machines
    this fix is for — the old code seeded ``/config/app.db`` at the filesystem
    root, so every install upgrading from a broken build has that file, and
    would have gone on reading a different database from the app.

    An ambiguous layout is reported instead of guessed at, by
    :func:`stray_legacy_config_dir`. See #1462.
    """
    if (app_root() / "cps" / ".HOMEDIR").is_file():
        return Path(os.path.expanduser("~")) / ".calibre-web-automated"
    return app_root()


def stray_legacy_config_dir():
    """``/config`` holding a database nothing is going to read, or ``None``.

    Returns the legacy directory when all of the following hold: no
    ``CALIBRE_DBPATH``, a database at the pre-#1462 ``/config/app.db``, and a
    resolved config dir that is somewhere else and has no database of its own.
    That is an install upgrading from a build whose ``auto_library.py`` seeded
    ``/config`` at the filesystem root, or a service started with an explicit
    ``cps.py -p /config/app.db``.

    Both cases need the operator to say which database is the real one, so
    nothing is moved and nothing is guessed — the caller reports it and stops.
    Setting ``CALIBRE_DBPATH=/config`` adopts the existing one and makes both
    halves of the install agree again.

    ``None`` in the container, where ``CALIBRE_DBPATH`` is always set.
    """
    if _env_path("CALIBRE_DBPATH") is not None:
        return None
    legacy = Path(DEFAULT_CONFIG_DIR)
    resolved = config_dir()
    if resolved == legacy:
        return None
    if not (legacy / "app.db").is_file():
        return None
    if (resolved / "app.db").is_file():
        # This install already has its own database; the /config one is a
        # leftover, not a competing answer. Nothing ambiguous to report.
        return None
    return legacy


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
    """Path to ``dirs.json`` (ingest folder, library dir, conversion tmp dir).

    A relative ``CWA_DIRS_JSON`` is anchored to the app root, not to the
    current directory. The scripts are launched from ``scripts/`` (the
    reporter's build does ``pushd <app root>/scripts``) while the systemd unit
    runs ``cps.py`` with ``WorkingDirectory=<app root>``, so an unanchored
    relative value resolves to two different files and puts the ingest and the
    app on two different libraries. ``cps/constants.py`` anchors it the same
    way, against ``BASE_DIR``.
    """
    override = _env_path("CWA_DIRS_JSON")
    if override is not None:
        return override if override.is_absolute() else app_root() / override
    return app_root() / "dirs.json"


def _configured_dir(key, env_name, default, dirs_json_path=None):
    """Resolve one runtime directory through environment, file, then default."""
    override = os.environ.get(env_name)
    if override is not None and override.strip():
        return _validated_runtime_dir(override, env_name)

    config_path = Path(dirs_json_path) if dirs_json_path else dirs_json()
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            configured_dirs = json.load(config_file)
    except (OSError, ValueError, TypeError):
        configured_dirs = {}

    configured = configured_dirs.get(key) if isinstance(configured_dirs, dict) else None
    if isinstance(configured, str) and configured.strip():
        configured = _validated_runtime_dir(
            configured, f"{key} in {config_path}"
        )
        with _DIRS_JSON_LOG_LOCK:
            if key not in _DIRS_JSON_LOGGED_KEYS:
                _DIRS_JSON_LOGGED_KEYS.add(key)
                print(
                    f"[cwa-paths] Using dirs.json fallback {key}={configured} "
                    f"from {config_path}",
                    file=sys.stderr,
                )
        return configured
    return _validated_runtime_dir(default, f"compiled-in default for {key}")


def ingest_folder(dirs_json_path=None):
    """Configured ingest directory, without adding a trailing separator."""
    return _configured_dir(
        "ingest_folder",
        "CWA_INGEST_FOLDER",
        DEFAULT_INGEST_FOLDER,
        dirs_json_path,
    )


def calibre_library_dir(dirs_json_path=None):
    """Configured Calibre library directory, without a trailing separator."""
    return _configured_dir(
        "calibre_library_dir",
        "CWA_CALIBRE_LIBRARY_DIR",
        DEFAULT_LIBRARY_DIR,
        dirs_json_path,
    )


def tmp_conversion_dir(dirs_json_path=None):
    """Configured conversion scratch directory, without a trailing separator."""
    return _configured_dir(
        "tmp_conversion_dir",
        "CWA_TMP_CONVERSION_DIR",
        DEFAULT_TMP_CONVERSION_DIR,
        dirs_json_path,
    )


def _main(argv=None):
    """Expose the same resolver to shell-based s6 consumers."""
    args = sys.argv[1:] if argv is None else argv
    commands = {
        "ingest_folder": ingest_folder,
        "calibre_library_dir": calibre_library_dir,
        "tmp_conversion_dir": tmp_conversion_dir,
    }
    if len(args) != 1 or args[0] not in {*commands, "all"}:
        print(
            "usage: app_paths.py "
            "{ingest_folder|calibre_library_dir|tmp_conversion_dir|all}",
            file=sys.stderr,
        )
        return 2
    try:
        if args[0] == "all":
            # Resolve every value before printing any of them. Shell callers
            # consume this as a three-line transaction and must never receive
            # a valid-looking prefix followed by a failure.
            resolved = [resolver() for resolver in commands.values()]
            for value in resolved:
                print(value)
        else:
            print(commands[args[0]]())
    except RuntimePathError as error:
        print(f"[cwa-paths] ERROR: {error}", file=sys.stderr)
        return 1
    return 0


def ensure_app_root_on_sys_path():
    """Put the app root on ``sys.path`` so ``import cps...`` works.

    Replaces the hardcoded ``_CPS_ROOT`` literal that four scripts each carried
    their own copy of. Returns the root, as a str, for callers that want it.
    """
    root = str(app_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


if __name__ == "__main__":
    raise SystemExit(_main())
