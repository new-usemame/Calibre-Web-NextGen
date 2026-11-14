# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""File copies for the standalone ingest scripts, with the app's own
metadata-tolerant helper (#2921).

``shutil.copy2`` writes extended attributes back after the data; NFSv4 refuses
``system.nfs4_acl`` with Errno 524, so ``copy2`` raises for a copy that is
complete. ``cps/services/file_move.py`` keeps what the destination accepts and
skips what it refuses. It is loaded here by path, because importing it through
the ``cps`` package would initialise the whole app (database, web server and
its SIGTERM handler) inside a script.
"""

import importlib.util
import os
import shutil

import app_paths

_copy_with_metadata_fallback = None


def _load():
    path = os.path.join(str(app_paths.app_root()), "cps", "services", "file_move.py")
    spec = importlib.util.spec_from_file_location("cwng_library_file_move", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.copy_with_metadata_fallback


def copy_file(src, dst) -> None:
    """Copy the file ``src`` to the file path ``dst`` like ``shutil.copy2``,
    skipping metadata the destination refuses."""
    global _copy_with_metadata_fallback
    if _copy_with_metadata_fallback is None:
        try:
            _copy_with_metadata_fallback = _load()
        except (OSError, ImportError) as e:
            print(f"[library-copy] WARN: metadata-tolerant copy unavailable, using copy2: {e}", flush=True)
            _copy_with_metadata_fallback = shutil.copy2
    _copy_with_metadata_fallback(str(src), str(dst))
