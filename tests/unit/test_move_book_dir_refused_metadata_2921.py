# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Moving a book folder must not fail on metadata the destination refuses (#2921).

Reporter: library on an NFSv4 share. Changing a book's author to a new author
logged ``[Errno 524]`` for the epub, cover, metadata.opf and the folder, and
the edit reported "Rename title ... failed". Their own tests showed the data
copy works and only ``setxattr(dst, "system.nfs4_acl", ...)`` is refused, which
``shutil.copystat`` (inside ``copy2`` and ``copytree``) turns into a failure.

Two behaviours are checked through ``move_files_on_change``:

* a move inside one library filesystem to an author folder that does not exist
  yet must complete even when metadata copying is refused;
* a move that really has to copy (the rename is refused with EXDEV) must keep
  the files, remove the old folder, keep the modification time and every
  extended attribute the destination accepts, and skip only the refused one.
"""

import errno
import os
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cps.helper import move_files_on_change


ENOTSUPP = 524  # Linux kernel-internal "operation not supported", leaked by NFSv4


def _refuse_copystat(*_args, **_kwargs):
    raise OSError(ENOTSUPP, "Unknown error 524")


def _library_with_book(tmp_path):
    library = tmp_path / "calibre-library"
    book_dir = library / "Unknown" / "Example Book (1234)"
    book_dir.mkdir(parents=True)
    contents = {
        "Example Book - Unknown.epub": b"PK\x03\x04 epub bytes",
        "cover.jpg": b"\xff\xd8\xff cover bytes",
        "metadata.opf": b"<package/>",
    }
    for name, data in contents.items():
        (book_dir / name).write_bytes(data)
    return library, book_dir, contents


def _move_to_new_author(library, book_dir):
    localbook = SimpleNamespace(id=1234, path="Unknown/Example Book (1234)")
    result = move_files_on_change(
        str(library), "Example Author", "Example Book (1234)", localbook,
        None, None, str(book_dir),
    )
    return result, localbook


def _assert_moved(result, localbook, library, book_dir, contents):
    assert result is False, f"move reported an error: {result!r}"
    new_dir = library / "Example Author" / "Example Book (1234)"
    for name, data in contents.items():
        assert (new_dir / name).read_bytes() == data
    assert not book_dir.exists()
    assert localbook.path == "Example Author/Example Book (1234)"
    return new_dir


def test_move_to_new_author_completes_when_metadata_copy_is_refused(tmp_path):
    library, book_dir, contents = _library_with_book(tmp_path)

    with patch.object(shutil, "copystat", _refuse_copystat):
        result, localbook = _move_to_new_author(library, book_dir)

    _assert_moved(result, localbook, library, book_dir, contents)


@pytest.mark.skipif(sys.platform != "linux", reason="shutil copies extended attributes through os.*xattr only on Linux")
def test_copying_move_keeps_data_times_and_accepted_xattrs_and_skips_refused_one(tmp_path):
    library, book_dir, contents = _library_with_book(tmp_path)
    epub = book_dir / "Example Book - Unknown.epub"
    os.utime(epub, (1_600_000_000, 1_600_000_000))

    accepted = {}

    def listxattr(path, *a, **k):
        return ["system.nfs4_acl", "user.origin"] if os.path.isfile(path) else []

    def getxattr(path, name, *a, **k):
        return b"acl-bytes" if name == "system.nfs4_acl" else b"keep-me"

    def setxattr(path, name, value, *a, **k):
        if name == "system.nfs4_acl":
            raise OSError(ENOTSUPP, "Unknown error 524")
        accepted[(os.path.basename(path), name)] = value

    def rename_refused(src, dst, *a, **k):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    with patch.object(os, "rename", rename_refused), \
         patch.object(os, "listxattr", listxattr, create=True), \
         patch.object(os, "getxattr", getxattr, create=True), \
         patch.object(os, "setxattr", setxattr, create=True):
        result, localbook = _move_to_new_author(library, book_dir)

    new_dir = _assert_moved(result, localbook, library, book_dir, contents)
    assert os.stat(new_dir / "Example Book - Unknown.epub").st_mtime == pytest.approx(1_600_000_000)
    for name in contents:
        assert accepted.get((name, "user.origin")) == b"keep-me"


def test_move_to_new_author_on_one_filesystem_is_a_rename_not_a_copy(tmp_path):
    library, book_dir, contents = _library_with_book(tmp_path)
    epub_inode = os.stat(book_dir / "Example Book - Unknown.epub").st_ino

    result, localbook = _move_to_new_author(library, book_dir)

    new_dir = _assert_moved(result, localbook, library, book_dir, contents)
    assert os.stat(new_dir / "Example Book - Unknown.epub").st_ino == epub_inode
