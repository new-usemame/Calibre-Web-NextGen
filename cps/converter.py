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


def _run_command_version(path, pattern, argument=None):
    """Fork the binary and scan its output. Blocking; never call this directly
    from a request — go through ``_get_command_version``."""
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


# Successful probes as ``{(path, pattern, argument): (identity, banner)}``.
# Not an lru_cache: the paths are mutable admin settings, an entry has to expire
# when the binary behind it changes, and failures must stay uncached.
_version_probe_cache = {}


def _binary_identity(path):
    """Cheap fingerprint of the file at ``path``, or ``None`` if it is gone.

    Keying the memo on the path alone is not enough: upgrading a binary in place
    leaves the configured path identical, and the stale banner is exactly the
    "reports a version it is not running" bug this memo sits next to. Device +
    inode catches a replaced file, mtime/size catch an overwrite in place.
    """
    try:
        st = os.stat(path)
    except (OSError, ValueError, TypeError):
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _get_command_version(path, pattern, argument=None):
    """Version banner printed by the binary at ``path``, or a translated
    diagnostic (``not installed`` / ``Execution permissions missing``).

    Two things are deliberate, and both belong here rather than at one call site
    because all three probes below reach a request handler.

    **The fork runs off the request greenlet.** ``_run_command_version`` forks a
    subprocess and blocks on ``wait()``, and cps runs gevent *without*
    ``monkey.patch_all()`` — so probing on a request handler stalls every other
    greenlet until the binary finishes starting (the failure mode fixed in
    #1270). Memoising alone does not cover it: the *first* probe after boot, and
    the first after the binary changes, still land on a real page load. The
    admin Version Information table and the SPA ``/stats`` page both get here on
    an ordinary request, so the call goes through the bounded offload pool
    #1270 introduced.

    **The memo expires when the binary does.** The entry is keyed on the probe
    *and* a stat fingerprint of the file behind it, so correcting a wrong path
    and upgrading a binary in place are both picked up without a restart.
    Failures are never cached — installing calibre, or fixing an execute bit,
    must take effect on the next page load rather than at the next container
    start. A misconfigured path therefore re-probes; that costs a fork on the
    offload pool, which is the right trade against telling an admin their fix
    did nothing.
    """
    path = path or ""
    key = (path, pattern, argument)
    identity = _binary_identity(path)
    cached = _version_probe_cache.get(key)
    if cached is not None and cached[0] == identity:
        return cached[1]

    version = parallel.run_blocking(lambda: _run_command_version(path, pattern, argument))

    # The failure sentinels are LazyStrings, not str — that is the discriminator.
    # The empty-string guard is belt-and-braces: _run_command_version falls
    # through to the LazyString on a no-match today, and a future refactor that
    # returned "" instead must not be memoised as a successful blank row.
    if isinstance(version, str) and version:
        _version_probe_cache[key] = (identity, version)
    return version


def get_calibre_version():
    return _get_command_version(config.config_converterpath, r'ebook-convert.*\(calibre', '--version')


def get_unrar_version():
    unrar_version = _get_command_version(config.config_rarfile_location, r'UNRAR.*\d')
    if unrar_version == "not installed":
        unrar_version = _get_command_version(config.config_rarfile_location, r'unrar.*\d', '-V')
    return unrar_version


def get_kepubify_version():
    return _get_command_version(config.config_kepubifypath, r'kepubify\s', '--version')
