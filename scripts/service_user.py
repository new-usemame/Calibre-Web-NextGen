# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Who owns the files ``scripts/`` writes, and whether we can set that owner.

The container runs the Flask app as LinuxServer.io's ``abc`` service account,
so scripts that run as root under s6 hand their output back with
``chown -R abc:abc``. Off Docker there is no ``abc``: the process already runs
as whoever owns the install, the files are already correct, and the chown is
both unnecessary and impossible.

``convert_library.py`` and ``kindle_epub_fixer.py`` already knew this — both
carried their own ``pwd.getpwnam(...) / except KeyError: pass`` guard with the
comment "skip if user doesn't exist, e.g. in CI environments". ``auto_library.py``
and ``ingest_processor.py`` did not, so a source install got a
``CalledProcessError`` traceback printed as an error on first run (#1462
follow-up, reported by @Thovi98 packaging for YunoHost):

.. code-block:: text

    chown: invalid user: 'abc:abc'
    [cwa-auto-library] An error occurred while attempting to recursively set
    ownership of /config to abc:abc. See the following error:
    Command '['chown', '-R', 'abc:abc', '/config']' returned non-zero exit status 1.

Nothing was actually wrong, but the operator cannot tell that from the output.
This module is the single place that answers "is there a service account to
chown to?", so the four scripts stop each having an opinion.

``CWA_SERVICE_USER`` / ``CWA_SERVICE_GROUP`` override the account for packagers
who run under a different one. An account that does not exist is not an error —
it is the normal source-install case, and the chown is skipped.

Deliberately dependency-free, like :mod:`app_paths`: these run before the Flask
stack is usable.
"""

import grp
import os
import pwd
import subprocess

__all__ = [
    "DEFAULT_SERVICE_USER",
    "DEFAULT_SERVICE_GROUP",
    "service_ids",
    "network_share_mode",
    "chown_to_service_user",
]

#: LinuxServer.io's service account, the owner the container expects.
DEFAULT_SERVICE_USER = "abc"
DEFAULT_SERVICE_GROUP = "abc"


def service_ids():
    """Return ``(uid, gid)`` for the service account, or ``None`` when absent.

    ``None`` means "this is not the container" — a source install, a CI runner,
    a dev checkout. Callers skip the chown; they do not report a failure.
    """
    user = os.environ.get("CWA_SERVICE_USER", DEFAULT_SERVICE_USER).strip()
    group = os.environ.get("CWA_SERVICE_GROUP", DEFAULT_SERVICE_GROUP).strip()
    if not user or not group:
        return None
    try:
        return pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid
    except KeyError:
        return None


def network_share_mode():
    """True when NETWORK_SHARE_MODE asks us to leave ownership alone.

    Set for libraries on NFS/SMB, where a chown either fails or fights the
    server's own mapping.
    """
    return os.getenv("NETWORK_SHARE_MODE", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def chown_to_service_user(path, label, recursive=True, respect_network_share_mode=True, log=print):
    """Give ``path`` to the service account, skipping cleanly when we cannot.

    Returns True only when a chown actually ran and succeeded. The three
    skip paths (no service account, NETWORK_SHARE_MODE, chown failed) all
    return False and say which one applied, so a source install's log reads as
    a decision rather than a fault.

    ``log`` takes the caller's own emitter — ``convert_library`` needs its
    ``print_and_log`` so these lines reach the run log like everything else.
    """
    if respect_network_share_mode and network_share_mode():
        log(f"{label} NETWORK_SHARE_MODE=true detected; skipping chown of {path}")
        return False

    ids = service_ids()
    if ids is None:
        user = os.environ.get("CWA_SERVICE_USER", DEFAULT_SERVICE_USER).strip()
        log(
            f"{label} No '{user}' account on this system; leaving ownership of "
            f"{path} as-is. This is expected outside the Docker image, where "
            f"the files already belong to the user running the app."
        )
        return False

    uid, gid = ids
    command = ["chown"]
    if recursive:
        command.append("-R")
    command += [f"{uid}:{gid}", str(path)]
    try:
        subprocess.run(command, check=True)
        return True
    except (subprocess.CalledProcessError, OSError) as error:
        log(
            f"{label} An error occurred while attempting to set ownership of "
            f"{path} to {uid}:{gid}. See the following error:\n{error}"
        )
        return False
