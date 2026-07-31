# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import re

from flask_babel import lazy_gettext as N_

from . import config, logger
from .services import parallel
from .subproc_wrapper import process_wait


log = logger.create()

# strings getting translated when used
_NOT_INSTALLED = N_('not installed')
_EXECUTION_ERROR = N_('Execution permissions missing')


def _get_command_version(path, pattern, argument=None):
    if os.path.exists(path):
        command = [path]
        if argument:
            command.append(argument)
        try:
            match = process_wait(command, pattern=pattern)
            if isinstance(match, re.Match):
                return match.string
        except Exception as ex:
            log.warning("%s: %s", path, ex)
            return _EXECUTION_ERROR
    return _NOT_INSTALLED


# Successful calibre probes as ``{path: (identity, banner)}``. Not an lru_cache:
# the key is a mutable admin setting, the entry has to expire when the binary
# behind it changes, and failures must stay uncached (see get_calibre_version).
_calibre_version_cache = {}


def _binary_identity(path):
    """Cheap fingerprint of the file at ``path``, or ``None`` if it is gone.

    Keying the memo on the path alone is not enough: upgrading calibre in place
    leaves the configured path identical, and the stale banner is exactly the
    "reports a version it is not running" bug this memo sits next to. Device +
    inode catches a replaced file, mtime/size catch an overwrite in place.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def get_calibre_version():
    """Version banner of the configured ``ebook-convert`` binary.

    Two things are deliberate here.

    **The probe runs off the request greenlet.** ``_get_command_version`` forks
    a subprocess and blocks on ``wait()``, and cps runs gevent *without*
    ``monkey.patch_all()`` — so probing on a request handler stalls every other
    greenlet until calibre finishes starting (the failure mode fixed in #1270).
    Memoising alone does not cover this: the *first* probe after boot, and the
    first after the binary changes, still land on a real page load. Both the
    admin Version Information table and the SPA ``/stats`` page reach this on an
    ordinary request, so the call goes through the same bounded offload pool
    that #1270 introduced.

    **The memo expires when the binary does.** The entry is keyed on the
    converter path *and* a stat fingerprint of the file behind it, so correcting
    a wrong path and upgrading calibre in place are both picked up without a
    restart. Failures are never cached — installing calibre, or fixing its
    execute bit, must take effect on the next page load rather than at the next
    container start.
    """
    path = config.config_converterpath or ""
    identity = _binary_identity(path)
    cached = _calibre_version_cache.get(path)
    if cached is not None and cached[0] == identity:
        return cached[1]

    version = parallel.run_blocking(
        lambda: _get_command_version(path, r'ebook-convert.*\(calibre', '--version')
    )

    # The failure sentinels are LazyStrings, not str — that is the discriminator.
    # The empty-string guard is belt-and-braces: _get_command_version falls
    # through to the LazyString on a no-match today, and a future refactor that
    # returned "" instead must not be memoised as a successful blank row.
    if isinstance(version, str) and version:
        _calibre_version_cache[path] = (identity, version)
    return version


def get_unrar_version():
    unrar_version = _get_command_version(config.config_rarfile_location, r'UNRAR.*\d')
    if unrar_version == "not installed":
        unrar_version = _get_command_version(config.config_rarfile_location, r'unrar.*\d', '-V')
    return unrar_version


def get_kepubify_version():
    return _get_command_version(config.config_kepubifypath, r'kepubify\s', '--version')
