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
        self.config_dir = "/config"
        self.library_dir = "/calibre-library"
        self.dirs_path = "/app/calibre-web-automated/dirs.json"

        self.empty_appdb = "/app/calibre-web-automated/empty_library/app.db"
        self.empty_metadb = "/app/calibre-web-automated/empty_library/metadata.db"

        # Canonical location. app.db always lives at /config/app.db; check_for_app_db()
        # tries it first and only falls back to a full os.walk() of /config when
        # it's missing.
        self.DEFAULT_APPDB_PATH = f"{self.config_dir}/app.db"

        # Kept non-None at all times: update_calibre_web_db() opens this with
        # sqlite3.connect(), which raises on None. check_for_app_db() realigns
        # it to DEFAULT_APPDB_PATH in every branch, but seed it here too.
        self.app_db = self.DEFAULT_APPDB_PATH
        self.metadb_path = None
        self.lib_path = None

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

    # Checks config_dir for an existing app.db, if one doesn't already exist it copies an empty one from /app/calibre-web-automated/empty_library/app.db and sets the permissions
    def check_for_app_db(self):
        # app.db always resolves to the canonical /config/app.db; keep the
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
        files_in_config = [os.path.join(dirpath,f) for (dirpath, dirnames, filenames) in os.walk(self.config_dir) for f in filenames]
        db_files = [f for f in files_in_config if "app.db" in f]
        if len(db_files) == 0:
            print(f"[cwa-auto-library] No app.db found in {self.config_dir}, copying from {self.empty_appdb}")
            shutil.copyfile(self.empty_appdb, self.DEFAULT_APPDB_PATH)
            try:
                nsm = os.getenv("NETWORK_SHARE_MODE", "false").strip().lower() in ("1", "true", "yes", "on")
                if not nsm:
                    subprocess.run(["chown", "-R", "abc:abc", self.config_dir], check=True)
                else:
                    print(f"[cwa-auto-library] NETWORK_SHARE_MODE=true detected; skipping chown of {self.config_dir}", flush=True)
            except subprocess.CalledProcessError as e:
                print(f"[cwa-auto-library] An error occurred while attempting to recursively set ownership of {self.config_dir} to abc:abc. See the following error:\n{e}", flush=True)
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
        db_files = []
        for dirpath, dirnames, filenames in os.walk(self.library_dir):
            # Regular files only (os.walk already excludes directories here), and
            # ignore the SQLite sidecars created by WAL/journal modes.
            matches = [
                f for f in filenames
                if "metadata.db" in f
                and not (f.endswith("-wal") or f.endswith("-shm") or f.endswith("-journal"))
            ]
            if matches:
                for f in matches:
                    db_files.append(os.path.join(dirpath, f))
                # Don't walk this library's book sub-folders -- that's the slow part.
                dirnames[:] = []
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

    # Uses the empty metadata.db in /app/calibre-web-automated to create a new library
    def make_new_library(self):
        print("[cwa-auto-library]: No existing library found. Creating new library...")
        shutil.copyfile(self.empty_metadb, f"{self.library_dir}/metadata.db")
        try:
            nsm = os.getenv("NETWORK_SHARE_MODE", "false").strip().lower() in ("1", "true", "yes", "on")
            if not nsm:
                subprocess.run(["chown", "-R", "abc:abc", self.library_dir], check=True)
            else:
                print(f"[cwa-auto-library] NETWORK_SHARE_MODE=true detected; skipping chown of {self.library_dir}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[cwa-auto-library] An error occurred while attempting to recursively set ownership of {self.library_dir} to abc:abc. See the following error:\n{e}", flush=True)
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
            _CPS_ROOT = "/app/calibre-web-automated"
            if _CPS_ROOT not in sys.path:
                sys.path.insert(0, _CPS_ROOT)
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
        # Always chown the calibre config dir to abc:abc — /config is a
        # local Docker volume regardless of NETWORK_SHARE_MODE (NSM gates
        # the library/ingest paths that may be on NFS, not the local
        # config volume). Without this, plugins extracted by calibre-
        # customize -a end up root-owned and the abc service user can't
        # read them at conversion time.
        try:
            subprocess.run(
                ["chown", "-R", "abc:abc", "/config/.config/calibre"],
                check=False,
            )
        except Exception as e:
            print(f"[cwa-auto-library] chown of {target} failed: {e}", flush=True)

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
            # ONLY for /calibre-library (library_dir, NAS) earlier — for
            # /config/.config/calibre we chown unconditionally because
            # /config is always a local volume.
            try:
                subprocess.run(
                    ["chown", "-R", "abc:abc", "/config/.config/calibre"],
                    check=False,
                )
            except Exception:
                pass
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