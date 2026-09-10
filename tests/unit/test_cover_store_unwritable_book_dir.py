# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A book folder the server cannot add entries to must not block a cover change.

Measured on the household library 2026-09-10: three book folders were created by
a root process over NFS (uid 0, mode 755) while every file inside stayed at the
server's uid. Since the cover store started staging a sibling file (#2127),
``tempfile.mkstemp`` in such a folder raises ``PermissionError`` and the user
was told "Cover-file is not a valid image file, or could not be stored" for a
perfectly good JPEG. Before #2127 the same save worked, because overwriting an
existing, writable ``cover.jpg`` needs no directory write permission.
"""

import io
import os
import stat
from types import SimpleNamespace

from PIL import Image
import pytest
from werkzeug.datastructures import FileStorage


pytestmark = pytest.mark.unit

OLD_COVER = b"the byte-identical previous cover"
INVALID_IMAGE_MESSAGE = "Cover-file is not a valid image file, or could not be stored"


def _jpeg_bytes(color=(21, 84, 160)):
    output = io.BytesIO()
    Image.new("RGB", (8, 11), color).save(output, format="JPEG")
    return output.getvalue()


def _storage(data=None, filename="cover.jpg"):
    return FileStorage(
        stream=io.BytesIO(data if data is not None else _jpeg_bytes()),
        filename=filename,
        content_type="image/jpeg",
    )


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A local library with one book folder plus a separate temp dir."""
    from cps import helper

    root = tmp_path / "library"
    book_dir = root / "Author" / "Book (7)"
    book_dir.mkdir(parents=True)
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    monkeypatch.setattr(helper.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(helper.config, "get_book_path", lambda: str(root))
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(temp_dir))
    return SimpleNamespace(root=root, book_dir=book_dir, temp_dir=temp_dir, book_path="Author/Book (7)")


def _deny_new_entries(directory):
    """Make ``directory`` unwritable for the current user, like a folder owned
    by another uid. Root ignores mode bits, so the tests that depend on this
    cannot prove anything when run as root."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("directory permission bits do not bind root")
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def _restore(directory):
    directory.chmod(0o755)


def _stage_files(directory):
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".stage"))


def test_unwritable_book_dir_with_writable_cover_is_replaced_in_place(library):
    from cps import helper

    cover = library.book_dir / "cover.jpg"
    cover.write_bytes(OLD_COVER)
    _deny_new_entries(library.book_dir)
    try:
        staged, message = helper.save_cover(_storage(), library.book_path)
        assert staged is not None, message
        # Nothing touches the live cover before the metadata owner publishes.
        assert cover.read_bytes() == OLD_COVER
        assert _stage_files(library.book_dir) == []
        assert len(_stage_files(library.temp_dir)) == 1

        published, error = staged.publish()
        assert published, error
        with Image.open(cover) as decoded:
            assert decoded.format == "JPEG"
            assert decoded.size == (8, 11)
        # The stage does not outlive the publish in either location.
        assert _stage_files(library.temp_dir) == []
        assert _stage_files(library.book_dir) == []
    finally:
        _restore(library.book_dir)


def test_unwritable_book_dir_discard_leaves_cover_and_removes_stage(library):
    from cps import helper

    cover = library.book_dir / "cover.jpg"
    cover.write_bytes(OLD_COVER)
    _deny_new_entries(library.book_dir)
    try:
        staged, message = helper.save_cover(_storage(), library.book_path)
        assert staged is not None, message
        assert staged.discard() == (True, None)
        assert cover.read_bytes() == OLD_COVER
        assert _stage_files(library.temp_dir) == []
    finally:
        _restore(library.book_dir)


def test_unwritable_book_dir_without_cover_names_the_permission_problem(library):
    from cps import helper

    _deny_new_entries(library.book_dir)
    try:
        staged, message = helper.save_cover(_storage(), library.book_path)
        assert staged is None
        text = str(message)
        # The user is told what actually went wrong, not that their image is bad.
        assert "permission" in text.lower()
        assert str(library.book_dir) in text
        # Pinned to the dedicated permission branch: it names both uids so an
        # admin can see the mismatch without reading the server log.
        assert "server uid" in text
        assert INVALID_IMAGE_MESSAGE not in text
        assert not (library.book_dir / "cover.jpg").exists()
        assert _stage_files(library.temp_dir) == []
    finally:
        _restore(library.book_dir)


def test_writable_book_dir_still_stages_beside_the_cover(library):
    """The atomic same-directory rename stays the normal path."""
    from cps import helper

    cover = library.book_dir / "cover.jpg"
    cover.write_bytes(OLD_COVER)
    staged, message = helper.save_cover(_storage(), library.book_path)
    assert staged is not None, message
    assert len(_stage_files(library.book_dir)) == 1
    assert _stage_files(library.temp_dir) == []
    assert staged.publish() == (True, None)
    with Image.open(cover) as decoded:
        assert decoded.format == "JPEG"


def test_undecodable_upload_is_still_reported_as_an_invalid_image(library):
    from cps import helper

    cover = library.book_dir / "cover.jpg"
    cover.write_bytes(OLD_COVER)
    staged, message = helper.save_cover(_storage(b"definitely not an image body"), library.book_path)
    assert staged is None
    assert str(message) == INVALID_IMAGE_MESSAGE
    assert cover.read_bytes() == OLD_COVER
    assert _stage_files(library.book_dir) == []


def test_undecodable_upload_into_unwritable_dir_is_an_invalid_image_not_a_permission_error(library):
    """Classification follows the failure, not the directory: bad bytes stay a
    bad-image message even when the fallback stage was used."""
    from cps import helper

    cover = library.book_dir / "cover.jpg"
    cover.write_bytes(OLD_COVER)
    _deny_new_entries(library.book_dir)
    try:
        staged, message = helper.save_cover(_storage(b"definitely not an image body"), library.book_path)
        assert staged is None
        assert str(message) == INVALID_IMAGE_MESSAGE
        assert cover.read_bytes() == OLD_COVER
        assert _stage_files(library.temp_dir) == []
    finally:
        _restore(library.book_dir)
