# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""File-move helpers that survive cross-ownership filesystem mounts.

Container deployments commonly bind-mount the temp upload area
(`/tmp/calibre_web`) and the library storage (`/books`) onto filesystems with
different ownership semantics — LinuxServer-style PUID/PGID containers, NFS
shares mounted with squashed root, SMB shares with mode restrictions. When
`shutil.move` falls back from `os.rename` (Errno 18 EXDEV) to `shutil.copy2`,
the `copystat` step's `os.chmod` can fail with Errno 1 EPERM (or Errno 13
EACCES on some network filesystems) even though the file data was copied
successfully. This module's :func:`copy_with_metadata_fallback` retries the
data-only :func:`shutil.copyfile` when `copy2` raises, so the upload completes
instead of surfacing a confusing permission error to the user.

Canonical report: ``janeczku/calibre-web#3437``.

NFSv4 shares expose the ``system.nfs4_acl`` extended attribute, which can be
read but not written back (``setxattr`` fails with Errno 524), so every
``copy2``/``copytree`` onto such a share fails after the data is already copied
(#2921). :func:`copy_metadata_best_effort` copies times, extended attributes
and mode one at a time and skips only what the destination refuses, and
:func:`move_path` moves a book folder by rename where it can and by a copy that
uses it where it cannot.
"""

from __future__ import annotations

import os
import shutil
import stat

from .. import logger

log = logger.create()


def copy_with_metadata_fallback(src: str, dst: str) -> None:
    """Copy ``src`` to ``dst``, preserving metadata when the filesystem allows.

    Tries :func:`shutil.copy2` first (data + mode + atime/mtime + xattr). If
    the copy2 metadata step raises :class:`OSError` — typically EPERM on chmod
    when ``dst`` lives on a differently-owned filesystem — falls back to
    :func:`shutil.copyfile`, which copies the bytes only. The destination then
    receives default permissions from the calling process's umask, which is
    acceptable for the upload path because the file is about to be re-processed
    by the Calibre ingest pipeline.

    Raises whatever the fallback :func:`shutil.copyfile` raises if the data
    copy itself fails (e.g., ENOSPC on dst filesystem, EACCES on dst dir).
    """
    try:
        shutil.copy2(src, dst)
    except OSError as ex:
        log.info(
            "copy2 metadata preservation failed (%s); falling back to data-only copy",
            ex,
        )
        shutil.copyfile(src, dst)
        copy_metadata_best_effort(src, dst)


def copy_metadata_best_effort(src: str, dst: str) -> None:
    """Copy times, extended attributes and mode from ``src`` to ``dst``.

    Each piece, and each extended attribute, is tried on its own; one the
    destination refuses is logged and skipped instead of failing the copy.
    """
    st = os.stat(src)
    try:
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError as ex:
        log.debug("Could not copy times to %s: %s", dst, ex)
    _copy_xattrs_best_effort(src, dst)
    try:
        os.chmod(dst, stat.S_IMODE(st.st_mode))
    except OSError as ex:
        log.debug("Could not copy mode to %s: %s", dst, ex)


def _copy_xattrs_best_effort(src: str, dst: str) -> None:
    if not hasattr(os, "listxattr"):
        return
    try:
        names = os.listxattr(src)
    except OSError as ex:
        log.debug("Could not list extended attributes of %s: %s", src, ex)
        return
    for name in names:
        try:
            os.setxattr(dst, name, os.getxattr(src, name))
        except OSError as ex:
            log.debug("Skipped extended attribute %s on %s: %s", name, dst, ex)


def copytree_best_effort(src: str, dst: str) -> None:
    """Copy the folder ``src`` to ``dst`` (which may already exist), keeping
    whatever metadata the destination accepts."""
    os.makedirs(dst, exist_ok=True)
    with os.scandir(src) as it:
        entries = list(it)
    for entry in entries:
        target = os.path.join(dst, entry.name)
        if entry.is_symlink():
            os.symlink(os.readlink(entry.path), target)
        elif entry.is_dir():
            copytree_best_effort(entry.path, target)
        else:
            copy_with_metadata_fallback(entry.path, target)
    copy_metadata_best_effort(src, dst)


def move_path(src: str, dst: str) -> str:
    """Move the file or folder ``src`` to the path ``dst``, which must not
    already be a folder.

    The parent of ``dst`` is created first, so a move inside one filesystem is
    a plain rename even to a new author folder. When the rename is refused
    (another filesystem), the data is copied with
    :func:`copy_with_metadata_fallback` and the source removed. A folder copy
    that fails part way is removed again, so the old folder stays the only one.
    """
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        os.rename(src, dst)
        return dst
    except OSError as ex:
        if not os.path.isdir(src) or os.path.islink(src):
            log.info("Rename %s to %s failed (%s); copying instead", src, dst, ex)
            copy_with_metadata_fallback(src, dst)
            os.unlink(src)
            return dst
        if os.path.lexists(dst):
            raise
        log.info("Rename %s to %s failed (%s); copying the folder instead", src, dst, ex)
    try:
        copytree_best_effort(src, dst)
    except OSError:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    shutil.rmtree(src)
    return dst
