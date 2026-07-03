# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from uuid import uuid4
import os
import signal
import subprocess

from .file_helper import get_temp_dir
from .subproc_wrapper import process_open
from . import logger, config
from .constants import SUPPORTED_CALIBRE_BINARIES

log = logger.create()

DEFAULT_EMBED_TIMEOUT = 90


def _embed_timeout():
    """Seconds to wait for `calibredb export` before killing it.

    Optional override via CWA_EMBED_TIMEOUT; malformed or non-positive
    values fall back to the default rather than crashing a download.
    """
    try:
        timeout = int(os.environ.get("CWA_EMBED_TIMEOUT", DEFAULT_EMBED_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_EMBED_TIMEOUT
    return timeout if timeout > 0 else DEFAULT_EMBED_TIMEOUT


def _kill_export_tree(p):
    """Kill a timed-out export and its children (calibre-parallel)."""
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (AttributeError, OSError):
        # Windows (no killpg) or the group is already gone
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.communicate(timeout=10)
    except Exception:
        pass


def do_calibre_export(book_id, book_format):
    try:
        quotes = [4, 6]
        tmp_dir = get_temp_dir()
        calibredb_binarypath = get_calibre_binarypath("calibredb")
        temp_file_name = str(uuid4())
        my_env = os.environ.copy()
        # Operator-opt-in: route HOME to /config so any user-installed
        # Calibre plugins under /config/.config/calibre/plugins are picked
        # up during the export. Closes upstream CWA #243.
        from .services import calibre_user_plugins
        calibre_user_plugins.apply_to_env(my_env)
        if config.config_calibre_split:
            my_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = os.path.join(config.config_calibre_dir, "metadata.db")
        library_path = config.get_book_path()
        opf_command = [calibredb_binarypath, 'export', '--dont-write-opf', '--dont-save-cover',
                       '--with-library', library_path,
                       '--to-dir', tmp_dir, '--formats', book_format, "--template", "{}".format(temp_file_name),
                       str(book_id)]
        p = process_open(opf_command, quotes, my_env)
        embed_timeout = _embed_timeout()
        try:
            _, err = p.communicate(timeout=embed_timeout)
        except subprocess.TimeoutExpired:
            _kill_export_tree(p)
            log.error('Metadata embed timed out after %ss for book %s (%s); '
                      'falling back to the original file without embedded metadata',
                      embed_timeout, book_id, book_format)
            return None, None
        if err:
            log.error('Metadata embedder encountered an error: %s', err)

        # calibredb export with --template may create either:
        # 1. A subdirectory with the template name containing the file
        # 2. A file directly with a modified name

        # First check if a subdirectory was created
        export_dir = os.path.join(tmp_dir, temp_file_name)
        if os.path.isdir(export_dir):
            # Look for the book file with the specified format
            for filename in os.listdir(export_dir):
                if filename.lower().endswith('.' + book_format.lower()):
                    # Found the exported file - return the directory and the filename without extension
                    actual_filename = os.path.splitext(filename)[0]
                    return export_dir, actual_filename

            log.warning(f'No {book_format} file found in export directory: {export_dir}')
        else:
            # No subdirectory - look for files directly in tmp_dir
            # STRICT CHECK: Only look for the file we requested
            expected_filename = temp_file_name + '.' + book_format.lower()
            for filename in os.listdir(tmp_dir):
                if filename.lower() == expected_filename.lower():
                    actual_filename = os.path.splitext(filename)[0]
                    return tmp_dir, actual_filename

            log.warning(f'No file named {expected_filename} found in {tmp_dir}')

        # Fallback to original behavior
        return tmp_dir, temp_file_name
    except OSError as ex:
        # ToDo real error handling
        log.error_or_exception(ex)
        return None, None


def get_calibre_binarypath(binary):
    binariesdir = config.config_binariesdir
    if binariesdir:
        try:
            return os.path.join(binariesdir, SUPPORTED_CALIBRE_BINARIES[binary])
        except KeyError as ex:
            log.error("Binary not supported by Calibre-Web NextGen: %s", SUPPORTED_CALIBRE_BINARIES[binary])
            pass
    return ""
