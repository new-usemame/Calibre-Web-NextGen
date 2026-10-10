# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ready-made KOReader plugin: the plugin plus the sign-in it needs.

The website's "ready-made plugin" download is the NextGen Sync plugin folder
with ``setup.json`` inside it: the server address, the account name and a new
app password. The plugin reads that file on its first start, signs in, and
deletes it, so copying one folder over USB is the whole setup.

The plugin is taken from ``static/koplugin.zip``, the archive the image builds
for the plain plugin download, so both downloads carry the same plugin. (The
``koreader/`` source tree is deleted from the image; nothing here reads it.)
"""

import io
import json
import os
import zipfile

from .. import constants

PLUGIN_FOLDER = "cwngsync.koplugin"
SETUP_FILE = "setup.json"


class PluginUnavailable(Exception):
    """This installation has no built plugin archive to ship."""


def _static_archive():
    return os.path.join(constants.STATIC_DIR, "koplugin.zip")


def plugin_files():
    """Every file of the plugin as ``(path inside the plugin folder, bytes)``.

    Hidden files and folders (editor, VCS or agent state that a local image
    build may have swept up) and any stray ``setup.json`` are left out.
    """
    path = _static_archive()
    files = []
    if os.path.isfile(path):
        prefix = PLUGIN_FOLDER + "/"
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename
                if info.is_dir() or not name.startswith(prefix):
                    continue
                relative = name[len(prefix):]
                parts = relative.split("/")
                if (not relative or relative == SETUP_FILE or ".." in parts
                        or any(part.startswith(".") for part in parts)):
                    continue
                files.append((relative, archive.read(info)))
    if not any(relative == "_meta.lua" for relative, _data in files):
        raise PluginUnavailable("The KOReader plugin is not part of this installation.")
    return files


def build(setup):
    """The download: the plugin folder with ``setup.json`` added, as zip bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, data in plugin_files():
            archive.writestr("%s/%s" % (PLUGIN_FOLDER, relative), data)
        archive.writestr("%s/%s" % (PLUGIN_FOLDER, SETUP_FILE),
                         json.dumps(setup, indent=2, sort_keys=True) + "\n")
    return buffer.getvalue()
