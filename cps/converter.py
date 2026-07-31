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


# Successful calibre probes, keyed by the converter path that produced them.
# Not an lru_cache: the key is a mutable admin setting, and failures must stay
# uncached (see get_calibre_version).
_calibre_version_cache = {}


def get_calibre_version():
    """Version banner of the configured ``ebook-convert`` binary.

    Successful probes are memoised per converter path. ``_get_command_version``
    forks a subprocess and blocks on ``wait()``, and cps runs gevent *without*
    ``monkey.patch_all()`` — so an uncached probe on a request handler stalls
    every other greenlet for as long as calibre takes to start (the failure
    mode fixed in #1270). The admin Version Information table and the SPA
    ``/stats`` page both land here on an ordinary page load.

    The cache is keyed on ``config.config_converterpath`` so that correcting a
    wrong path takes effect immediately, and failures are never cached — an
    admin who installs calibre or fixes its execute bit must see the change
    without restarting the container.
    """
    path = config.config_converterpath or ""
    cached = _calibre_version_cache.get(path)
    if cached is not None:
        return cached
    version = _get_command_version(path, r'ebook-convert.*\(calibre', '--version')
    # The failure sentinels are LazyStrings, not str — that is the discriminator.
    if isinstance(version, str):
        _calibre_version_cache[path] = version
    return version


def get_unrar_version():
    unrar_version = _get_command_version(config.config_rarfile_location, r'UNRAR.*\d')
    if unrar_version == "not installed":
        unrar_version = _get_command_version(config.config_rarfile_location, r'unrar.*\d', '-V')
    return unrar_version


def get_kepubify_version():
    return _get_command_version(config.config_kepubifypath, r'kepubify\s', '--version')
