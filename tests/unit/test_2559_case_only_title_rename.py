# SPDX-License-Identifier: GPL-3.0-or-later
"""#2559: changing only the letter case of a title must not delete the book file.

On a case-insensitive filesystem (default APFS on macOS, NTFS, exFAT, SMB) the
new-case folder and file names already "exist" because they are the old ones.
The folder went down the merge-into-existing branch, and the file rename then
removed the "existing destination" -- the only copy of the book -- before moving
a source that was no longer there.
"""
import os
from types import SimpleNamespace

import pytest

from cps import helper


def _filesystem_is_case_insensitive(directory):
    probe = os.path.join(directory, "CaseProbe")
    open(probe, "w").close()
    try:
        return os.path.exists(os.path.join(directory, "caseprobe"))
    finally:
        os.remove(probe)


def _library(tmp_path, title):
    """A one-book library laid out the way Calibre stores it."""
    book_dir = tmp_path / "Holly Black" / f"{title} (237)"
    book_dir.mkdir(parents=True)
    file_name = f"{title} - Holly Black"
    (book_dir / f"{file_name}.epub").write_bytes(b"the only copy of the book")
    (book_dir / "cover.jpg").write_bytes(b"cover")
    data = SimpleNamespace(name=file_name, format="EPUB")
    book = SimpleNamespace(id=237, title=title, path=f"Holly Black/{title} (237)", data=[data])
    return book, data


def _retitle(monkeypatch, tmp_path, book, new_title):
    book.title = new_title
    monkeypatch.setattr(helper.calibre_db, "get_book", lambda _id: book)
    monkeypatch.setattr(helper, "config", SimpleNamespace(config_unicode_filename=False))
    return helper.update_dir_structure_file(book.id, str(tmp_path), "", None, None)


def _files_under(tmp_path):
    return sorted(
        os.path.relpath(os.path.join(root, name), tmp_path)
        for root, _dirs, names in os.walk(tmp_path)
        for name in names
    )


@pytest.mark.unit
def test_case_only_retitle_keeps_the_file_on_a_case_insensitive_filesystem(tmp_path, monkeypatch):
    if not _filesystem_is_case_insensitive(str(tmp_path)):
        pytest.skip("needs a case-insensitive filesystem (the reporter's macOS APFS library)")
    book, data = _library(tmp_path, "The Folk of the Air trilogy")

    error = _retitle(monkeypatch, tmp_path, book, "The Folk of the Air Trilogy")

    assert error is False
    expected = "Holly Black/The Folk of the Air Trilogy (237)/The Folk of the Air Trilogy - Holly Black.epub"
    # os.walk reports names exactly as stored, so this also proves the
    # folder and file took the new case rather than keeping the old one.
    assert expected in _files_under(tmp_path)
    with open(tmp_path / expected, "rb") as handle:
        assert handle.read() == b"the only copy of the book"
    assert book.path == "Holly Black/The Folk of the Air Trilogy (237)"
    assert data.name == "The Folk of the Air Trilogy - Holly Black"


@pytest.mark.unit
def test_case_only_retitle_renames_folder_and_file_on_any_filesystem(tmp_path, monkeypatch):
    book, data = _library(tmp_path, "The Folk of the Air trilogy")

    error = _retitle(monkeypatch, tmp_path, book, "The Folk of the Air Trilogy")

    assert error is False
    assert _files_under(tmp_path) == [
        "Holly Black/The Folk of the Air Trilogy (237)/The Folk of the Air Trilogy - Holly Black.epub",
        "Holly Black/The Folk of the Air Trilogy (237)/cover.jpg",
    ]
    assert data.name == "The Folk of the Air Trilogy - Holly Black"


@pytest.mark.unit
def test_a_different_existing_destination_file_is_still_replaced(tmp_path, monkeypatch):
    """A genuinely different file at the destination keeps the old overwrite behaviour."""
    book, data = _library(tmp_path, "Old Title")
    stale = tmp_path / "Holly Black" / "Old Title (237)" / "New Title - Holly Black.epub"
    stale.write_bytes(b"stale leftover")

    error = _retitle(monkeypatch, tmp_path, book, "New Title")

    assert error is False
    moved = tmp_path / "Holly Black" / "New Title (237)" / "New Title - Holly Black.epub"
    assert moved.read_bytes() == b"the only copy of the book"
    assert data.name == "New Title - Holly Black"
