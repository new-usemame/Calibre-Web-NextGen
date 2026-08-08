# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import json
import os
import shutil
import sqlite3
import sys
import subprocess
from pathlib import Path

import app_paths
import library_paths
import service_user


# The tables Calibre-Web reads while starting up. Calibre creates both with the
# library itself, so every real library has them however empty it is — which is
# what makes them a safe test for "is this file actually a Calibre library?".
# ``custom_columns`` is the one whose absence took the whole container down in
# #1428: SELECT id, datatype FROM custom_columns runs on every boot.
REQUIRED_CALIBRE_TABLES = ("books", "custom_columns")


# SQLite saying one of these is a positive answer: the bytes are not a database.
# Every other failure (locked, busy, permission denied, I/O error, a dropped
# network mount) means we could not tell, which is a different thing entirely.
_NOT_A_DATABASE_SIGNALS = ("file is not a database", "malformed", "encrypted")


def _is_definitely_not_a_database(error) -> bool:
    message = str(error).lower()
    return any(signal in message for signal in _NOT_A_DATABASE_SIGNALS)


def is_calibre_database(path) -> bool:
    """True when *path* is a readable SQLite file carrying Calibre's schema.

    Opened read-only so a candidate is never created, migrated or otherwise
    touched by the act of checking it.

    Rejects only on a *positive* answer — the query succeeded and the tables are
    absent, or SQLite reported the bytes are not a database. When the file merely
    could not be inspected (locked by another process, unreadable because the
    boot runs under a different uid than the app, a network mount that blinked)
    the candidate is accepted, because wrongly refusing a real library strands a
    user far worse than the mis-selection this check exists to prevent. A stale
    placeholder is never locked, so this cannot let the #1428 case back in.
    """
    # Regular files only. Rejects a directory, and stops a FIFO named
    # metadata.db from blocking the open forever and wedging the boot -- SQLite's
    # timeout is a busy-handler for locks, not a deadline on opening the file.
    if not os.path.isfile(path):
        return False
    con = None
    try:
        # Path.as_uri() percent-encodes the path. Interpolating it raw breaks on
        # any library whose name contains '?' or '#' — SQLite reads those as the
        # URI's query and fragment, truncates the filename, and the real library
        # is rejected for a reason that has nothing to do with its contents.
        con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=5)
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except (sqlite3.Error, OSError, ValueError) as error:
        return not _is_definitely_not_a_database(error)
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
    return all(table in names for table in REQUIRED_CALIBRE_TABLES)


def main():
    auto_lib = AutoLibrary()
    auto_lib.check_for_app_db()
    if auto_lib.check_for_existing_library():
        auto_lib.set_library_location()
    else: # No existing library found
        auto_lib.make_new_library()
        auto_lib.set_library_location()

    auto_lib.bootstrap_calibre_user_plugins_dir()

    print(f"[cwa-auto-library] Library location successfully set to: {auto_lib.lib_path}")
    sys.exit(0)


class AutoLibrary:
    def __init__(self):
        self.config_dir = str(app_paths.config_dir())
        # Where to look for an existing library, and where make_new_library()
        # seeds a new one. Read from dirs.json rather than assuming the
        # container's mount point: on a bare-metal install there is no
        # /calibre-library and seeding one there fails outright (#1462). The
        # shipped dirs.json still says /calibre-library, so Docker is unchanged.
        self.library_dir = library_paths.get_calibre_library_dir()
        self.dirs_path = str(app_paths.dirs_json())

        self.empty_appdb = str(app_paths.empty_library_file("app.db"))
        self.empty_metadb = str(app_paths.empty_library_file("metadata.db"))

        # Canonical location. app.db always lives at <config dir>/app.db;
        # check_for_app_db() tries it first and only falls back to a full
        # os.walk() of the config dir when it's missing. Resolved by app_paths
        # rather than re-derived here, so this and every other app.db consumer
        # agree by construction (#1462).
        self.DEFAULT_APPDB_PATH = str(app_paths.app_db_path())

        # Kept non-None at all times: update_calibre_web_db() opens this with
        # sqlite3.connect(), which raises on None. check_for_app_db() realigns
        # it to DEFAULT_APPDB_PATH in every branch, but seed it here too.
        self.app_db = self.DEFAULT_APPDB_PATH
        self.metadb_path = None
        self.lib_path = None
        # metadata.db-shaped files that turned out not to be Calibre databases.
        # Kept so the "found something, but nothing usable" case can name them
        # instead of silently seeding an empty library over the top (#1428).
        self.rejected_dbs = []

    @property #getter
    def metadb_path(self):
        return self._metadb_path

    @metadb_path.setter
    def metadb_path(self, path):
        if path is None:
            self._metadb_path = None
            self.lib_path = None
        else:
            self._metadb_path = path
            self.lib_path = os.path.dirname(path)

    @staticmethod
    def ensure_dir_exists(path, what, remedy):
        """Create a directory we are about to seed a database into.

        In the container these are bind mounts and always exist, so this is a
        no-op there. Off Docker they routinely do not: the shipped dirs.json
        still names ``/calibre-library``, so a source install that has not
        edited it used to die inside ``shutil.copyfile`` with a bare
        ``FileNotFoundError`` naming a path the user never chose (#1462).

        Fail with an actionable message naming the file to edit, rather than a
        traceback — we cannot guess where somebody keeps their books, but we
        can say exactly which setting decides it.
        """
        if os.path.isdir(path):
            return
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            print(f"[cwa-auto-library]: ERROR: could not create the {what} '{path}': {error}")
            print(f"[cwa-auto-library]: {remedy}")
            sys.exit(1)
        print(f"[cwa-auto-library] Created {what} {path}")

    #: Directories that are part of the application, never part of its config.
    #: Only reachable off Docker, where the config dir and the app root are the
    #: same directory (``cps`` resolves CONFIG_DIR to BASE_DIR when
    #: CALIBRE_DBPATH is unset, and app_paths follows it).
    NON_CONFIG_DIRS = frozenset({
        "empty_library", "cps", "scripts", "tests", "frontend",
        "node_modules", ".git", ".venv", "__pycache__",
    })

    def _walk_config_dir(self):
        """Walk the config dir without descending into the application itself.

        In the container the config dir is a bind mount holding nothing but
        state, so this walks the same tree it always did. Off Docker the config
        dir IS the app root, and an unpruned walk gets two things wrong: it
        finds the shipped seed database at ``empty_library/app.db`` and
        concludes the install already has an app.db — leaving the real one
        never copied, so sqlite creates an empty file and the first query dies
        on ``no such table: settings`` — and on the way there it walks the whole
        checkout, ``frontend/node_modules`` included.
        """
        for dirpath, dirnames, filenames in os.walk(self.config_dir):
            dirnames[:] = [d for d in dirnames if d not in self.NON_CONFIG_DIRS]
            yield dirpath, dirnames, filenames

    # Checks config_dir for an existing app.db, if one doesn't already exist it copies an empty one from <app root>/empty_library/app.db and sets the permissions
    def check_for_app_db(self):
        # app.db always resolves to the canonical <config dir>/app.db; keep the
        # handle aligned in every branch so update_calibre_web_db() never hands
        # None to sqlite3.connect().
        self.app_db = self.DEFAULT_APPDB_PATH
        # Fast path: the common case is app.db already at its default location.
        # Skip the full os.walk() of config_dir when it's there (#1022). Use
        # isfile(), not exists(): the walk fallback only ever matched regular
        # files, so a directory named "app.db" must not be treated as the DB.
        if os.path.isfile(self.DEFAULT_APPDB_PATH):
            print(f"[cwa-auto-library] app.db found in default location ({self.app_db}).")
            return
        db_files = [
            os.path.join(dirpath, f)
            for (dirpath, dirnames, filenames) in self._walk_config_dir()
            for f in filenames
            if "app.db" in f
        ]
        if len(db_files) == 0:
            print(f"[cwa-auto-library] No app.db found in {self.config_dir}, copying from {self.empty_appdb}")
            self.ensure_dir_exists(
                self.config_dir,
                "config directory",
                "Set CALIBRE_DBPATH to a directory this user can write to.",
            )
            shutil.copyfile(self.empty_appdb, self.DEFAULT_APPDB_PATH)
            service_user.chown_to_service_user(self.config_dir, "[cwa-auto-library]")
            print(f"[cwa-auto-library] app.db successfully copied to {self.config_dir}")
        else:
            return

    # Check for a metadata.db file in the given library dir and returns False if one doesn't exist
    # and True if one does exist, while also updating metadb_path to the path of the found metadata.db file
    # In the case of multiple metadata.db files, the user is notified and the one with the largest filesize is chosen
    def check_for_existing_library(self) -> bool:
        # Find metadata.db files WITHOUT descending into the (potentially huge)
        # per-book folder tree. A Calibre library keeps metadata.db at its root
        # and never nests another library inside its own book folders, so once a
        # directory yields a metadata.db we stop descending into it (topdown
        # walk + dirnames prune). This skips exactly the deep recursion #1022
        # measured spending ~5 minutes on a large library, while still comparing
        # every candidate library root so "largest wins" is preserved.
        #
        # Contract note: because we stop at the first metadata.db down each
        # branch, a metadata.db at the library ROOT is treated as authoritative
        # and a library nested *below* it is not scanned. That is the location
        # Calibre-Web actually mounts from; if your real library lives in a
        # sub-folder, don't also leave a metadata.db at /calibre-library root.
        #
        # Only an exactly-named metadata.db that really carries Calibre's schema
        # counts as a library root, because those are the two things the rest of
        # the system assumes about the directory this picks:
        #
        #   * Calibre-Web opens ``os.path.join(config_calibre_dir, "metadata.db")``
        #     (cps/db.py), so validating any other filename proves nothing about
        #     the file that actually gets opened. Selecting a directory on the
        #     strength of a metadata.db.bak configures a library whose real
        #     metadata.db may not exist at all.
        #   * Matching on the name alone let a 0-byte placeholder, a half-finished
        #     copy or an unrelated SQLite file claim the mount point and prune
        #     away the real library nested below it — the container then
        #     crash-looped on "no such table: custom_columns" (#1428).
        #
        # An unusable candidate is reported and skipped, and the walk keeps
        # descending past it so a real library underneath is still found.
        db_files = []
        for dirpath, dirnames, filenames in os.walk(self.library_dir):
            if "metadata.db" not in filenames:
                continue
            candidate = os.path.join(dirpath, "metadata.db")
            if is_calibre_database(candidate):
                db_files.append(candidate)
                # Don't walk this library's book sub-folders -- that's the slow part.
                dirnames[:] = []
            else:
                self.rejected_dbs.append(candidate)
                required = " / ".join(REQUIRED_CALIBRE_TABLES)
                print(f"[cwa-auto-library]: Ignoring {candidate} - it is not a Calibre database "
                      f"(missing the {required} tables). Continuing to search below it...")
        if not db_files and self.rejected_dbs:
            # Files that look like a library are present but none can be opened
            # as one. Creating a fresh library here would copy an empty
            # metadata.db over the top of them, so stop and say what to fix.
            print("\n[cwa-auto-library]: ERROR: found metadata.db file(s) in "
                  f"'{self.library_dir}' but none of them is a readable Calibre database:\n")
            for db in self.rejected_dbs:
                try:
                    size = os.path.getsize(db)
                except OSError as e:
                    # The file can move or the mount can drop between inspecting
                    # it and reporting it; a diagnostic must not die mid-print.
                    size = f"unavailable ({e.strerror or e})"
                print(f"    - {db} | Size: {size}")
            print("\n[cwa-auto-library]: Nothing has been modified. Remove or restore the file(s) "
                  "above — or point the library mount at the folder holding your real metadata.db — "
                  "then restart the container.")
            sys.exit(1)
        if len(db_files) == 1:
            self.metadb_path = db_files[0]
            print(f"[cwa-auto-library]: Existing library found at {self.lib_path}, mounting now...")
            return True
        elif len(db_files) > 1:
            print("[cwa-auto-library]: Multiple metadata.db files found in library directory:\n")
            for db in db_files:
                print(f"    - {db} | Size: {os.path.getsize(db)}")
            db_sizes = [os.path.getsize(f) for f in db_files]
            index_of_biggest_db = max(range(len(db_sizes)), key=db_sizes.__getitem__)
            self.metadb_path = db_files[index_of_biggest_db]
            print(f"\n[cwa-auto-library]: Automatically mounting the largest database using the following db file - {db_files[index_of_biggest_db]} ...")
            print("\n[cwa-auto-library]: If this is unwanted, please ensure only 1 metadata.db file / only your desired Calibre Database exists in '/calibre-library', then restart the container")
            return True
        else:
            return False

    # Sets the library's location in both dirs.json and the CW db
    def set_library_location(self):
        if self.metadb_path is not None and os.path.exists(self.metadb_path):
            self.update_dirs_json()
            self.update_calibre_web_db()
            return
        else:
            print("[cwa-auto-library]: ERROR: metadata.db found but not mounted")
            sys.exit(1)

    # Uses sql to update CW's app.db with the correct library location (config_calibre_dir in the settings table)
    def update_calibre_web_db(self):
        if os.path.exists(self.metadb_path): # type: ignore
            try:
                print("[cwa-auto-library]: Updating Settings Database with library location...")
                con = sqlite3.connect(self.app_db, timeout=30)
                cur = con.cursor()
                cur.execute(f'UPDATE settings SET config_calibre_dir="{self.lib_path}";')
                con.commit()
                return
            except Exception as e:
                print("[cwa-auto-library]: ERROR: Could not update Calibre Web Database")
                print(e)
                sys.exit(1)
        else:
            print(f"[cwa-auto-library]: ERROR: app.db in {self.app_db} not found")
            sys.exit(1)

    # Update the dirs.json file with the new library location (lib_path))
    def update_dirs_json(self):
        """Updates the location of the calibre library stored in dirs.json with the found library"""
        try:
            print("[cwa-auto-library] Updating dirs.json with new library location...")
            with open(self.dirs_path) as f:
                dirs = json.load(f)
            dirs["calibre_library_dir"] = self.lib_path
            with open(self.dirs_path, 'w') as f:
                json.dump(dirs, f, indent=4)
            return
        except Exception as e:
            print("[cwa-auto-library]: ERROR: Could not update dirs.json")
            print(e)
            sys.exit(1)

    # Uses the empty metadata.db shipped in the app root to create a new library
    def make_new_library(self):
        print("[cwa-auto-library]: No existing library found. Creating new library...")
        self.ensure_dir_exists(
            self.library_dir,
            "library directory",
            f"Set 'calibre_library_dir' in {self.dirs_path} to a directory this user can write to.",
        )
        shutil.copyfile(self.empty_metadb, f"{self.library_dir}/metadata.db")
        service_user.chown_to_service_user(self.library_dir, "[cwa-auto-library]")
        self.metadb_path = f"{self.library_dir}/metadata.db"
        return

    def bootstrap_calibre_user_plugins_dir(self):
        """Create /config/.config/calibre/plugins and auto-register any
        .zip files the operator dropped there. No-op when the env var
        CWA_CALIBRE_USER_PLUGINS isn't set. Closes upstream CWA #243.

        Auto-registration runs `calibre-customize -a` per .zip with
        HOME=/config so calibre persists the plugin into its
        customize.py.json registry. Without this step, just having a
        .zip in the plugins folder doesn't make calibre load it during
        ingest — the user-visible symptom previous CWA users hit on
        upstream #243 ('I copied the plugin folder, nothing happens').
        """
        try:
            app_paths.ensure_app_root_on_sys_path()
            from cps.services import calibre_user_plugins
        except ImportError:
            return
        if not calibre_user_plugins.is_enabled():
            return
        target = calibre_user_plugins.ensure_plugins_dir()
        if target is None:
            print(
                "[cwa-auto-library] CWA_CALIBRE_USER_PLUGINS is enabled but "
                "the plugins directory could not be created (permission "
                "error). Create it manually: "
                f"mkdir -p /config/.config/calibre/plugins",
                flush=True,
            )
            return
        # Always chown the calibre config dir to the service account — the
        # config dir is a local Docker volume regardless of
        # NETWORK_SHARE_MODE (NSM gates the library/ingest paths that may be
        # on NFS, not the local config volume). Without this, plugins
        # extracted by calibre-customize -a end up root-owned and the abc
        # service user can't read them at conversion time.
        calibre_config_dir = app_paths.config_dir() / ".config" / "calibre"
        service_user.chown_to_service_user(
            calibre_config_dir,
            "[cwa-auto-library]",
            respect_network_share_mode=False,
        )

        # Auto-register any .zip files the operator dropped in. First-
        # boot only — once calibre's customize.py.json has entries, we
        # skip the scan to keep boot fast. Operator can add more later
        # via `docker exec calibre-web /app/calibre/calibre-customize -a
        # /config/.config/calibre/plugins/<new>.zip`.
        registered = calibre_user_plugins.auto_register_plugins()
        if registered:
            for name in registered:
                print(f"[cwa-auto-library] Registered Calibre plugin: {name}", flush=True)
            # Calibre extracts plugin contents during registration; some
            # of those files land owned by whichever uid invoked
            # calibre-customize (root, if cont-init ran as root). Re-
            # chown so abc can read them at conversion time. Skipped
            # ONLY for the library dir (NAS) earlier — the calibre config
            # dir is chowned unconditionally because it is always a local
            # volume.
            service_user.chown_to_service_user(
                calibre_config_dir,
                "[cwa-auto-library]",
                respect_network_share_mode=False,
            )
        else:
            zip_count = len(list(target.glob("*.zip")))
            if zip_count == 0:
                print(
                    f"[cwa-auto-library] CWA_CALIBRE_USER_PLUGINS is enabled. "
                    f"Drop your Calibre plugin .zip files into {target} and "
                    f"restart the container; they'll be auto-registered.",
                    flush=True,
                )
            else:
                print(
                    f"[cwa-auto-library] CWA_CALIBRE_USER_PLUGINS is enabled "
                    f"and {zip_count} plugin .zip(s) are in {target}. "
                    f"Already registered (skipping auto-register).",
                    flush=True,
                )


if __name__ == '__main__':
    main()