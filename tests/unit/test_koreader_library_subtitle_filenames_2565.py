# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2565: two volumes whose titles differ only after the subtitle colon.

The file a book is stored under on the server is cut to 42 characters
(``get_valid_filename(title, chars=42)``, kept short for Windows path limits),
and the KOReader library used to reuse that stored name for the file it puts on
the device. Two volumes of one work whose titles share their first 42
characters therefore got device file names that were the same all the way to
the ``[id]`` suffix, and on a device that lists a file by its name (PDFs, whose
placeholder cannot carry a title) they looked identical.

The device name is the device's own business, so it is built from the book's
full title and first author rather than from the storage name.
"""

import pytest
from sqlalchemy import text

from cps import db
from cps.utils.filename_sanitizer import get_valid_filename_shared
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

# 41 characters, so the 42-character storage cut lands exactly on the colon
# and the stored name is "A Treatise Concerning Human Understanding_ - Author".
MAIN = "A Treatise Concerning Human Understanding"
VOLUME_ONE = MAIN + ": Subtitle, Volume I: Assessing the Foundations"
VOLUME_TWO = MAIN + ": Subtitle, Volume II: Delineating the Limits"


@pytest.fixture(autouse=True)
def _share_place_switch_off(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    yield built
    built.close()


def _add_volume(world, book_id, title, author="Ann Author", fmt="EPUB"):
    """A book stored exactly as production names it: title and author each cut
    to 42 characters (scripts/ingest_processor.py, cps/helper.py)."""
    # The author's folder is made from a short stand-in; only the recorded
    # names are long (a test machine cannot create a path component this long).
    world.add_book(book_id, "Stored %d" % book_id, authors=("Ann Author",), formats=(fmt,))
    book = world.session.get(db.Books, book_id)
    book.authors[0].name = author
    book.title = title
    stored = (get_valid_filename_shared(title, chars=42) + " - "
              + get_valid_filename_shared(author, chars=42))
    world.session.query(db.Data).filter(db.Data.book == book_id).one().name = stored
    world.session.commit()
    return stored


def _filenames(world):
    body = world.client.get("/kosync/syncs/library",
                            headers=world.device_headers("reader")).get_json()
    return {book["book_id"]: book["filename"] for book in body["books"]}


def test_the_storage_name_really_does_drop_the_volume(world):
    """The precondition from the issue: the stored names are identical."""
    world.add_user("reader")
    one = _add_volume(world, 1, VOLUME_ONE)
    two = _add_volume(world, 2, VOLUME_TWO)
    assert one == two == "A Treatise Concerning Human Understanding_ - Ann Author"


@pytest.mark.parametrize("fmt", ["EPUB", "PDF"])
def test_volumes_that_differ_after_the_colon_get_different_device_names(world, fmt):
    world.add_user("reader")
    _add_volume(world, 1, VOLUME_ONE, fmt=fmt)
    _add_volume(world, 2, VOLUME_TWO, fmt=fmt)

    names = _filenames(world)

    ext = fmt.lower()
    stems = [names[i].rsplit(" [%d].%s" % (i, ext), 1)[0] for i in (1, 2)]
    # Different before the id is added, not merely because of it.
    assert stems[0] != stems[1], names
    assert "Volume I_" in stems[0] and "Volume II_" in stems[1], names
    assert names[1].endswith(" [1].%s" % ext) and names[2].endswith(" [2].%s" % ext)


def test_device_name_keeps_the_title___author_shape_the_plugin_parses(world):
    world.add_user("reader")
    _add_volume(world, 1, VOLUME_ONE)

    name = _filenames(world)[1]

    # cwng_catalog.lua's parseFilename reads "Title - Author [id].ext".
    assert name == ("A Treatise Concerning Human Understanding_ Subtitle, "
                    "Volume I_ Assessing the Foundations - Ann Author [1].epub")


def test_a_short_title_keeps_exactly_the_name_it_always_had(world):
    """No churn: a book whose storage name was not cut keeps its device name,
    so devices rename nothing for the great majority of the library."""
    world.add_user("reader")
    _add_volume(world, 1, "First Light")

    assert _filenames(world)[1] == "First Light - Ann Author [1].epub"


def test_a_name_that_is_not_a_cut_of_the_title_is_left_alone(world):
    """A custom or imported storage name (one that does not start the way the
    title does) was never a truncation, so it is not rewritten from the title."""
    world.add_user("reader")
    _add_volume(world, 1, VOLUME_ONE)
    world.session.query(db.Data).filter(db.Data.book == 1).one().name = "scan_0042 - Ann Author"
    world.session.commit()

    assert _filenames(world)[1] == "scan_0042 - Ann Author [1].epub"


def test_the_author_part_stays_as_the_server_stored_it(world):
    """The first author is whatever the stored name already says (the server
    picks it by link order); it is not re-derived from the author list, which
    would rename multi-author books for a reason unrelated to this bug."""
    world.add_user("reader")
    world.add_book(1, "Stored 1", authors=("Ann Author", "Bo Second"))
    book = world.session.get(db.Books, 1)
    book.title = VOLUME_ONE
    world.session.query(db.Data).filter(db.Data.book == 1).one().name = (
        get_valid_filename_shared(VOLUME_ONE, chars=42) + " - Bo Second")
    world.session.commit()

    assert _filenames(world)[1].endswith("Foundations - Bo Second [1].epub")


def test_a_long_author_cannot_crowd_the_title_out_of_the_name(world):
    world.add_user("reader")
    author = ("Bartholomew " * 30).strip()
    _add_volume(world, 1, VOLUME_ONE, author=author)
    _add_volume(world, 2, VOLUME_TWO, author=author)

    names = _filenames(world)

    assert len(names[1].encode("utf-8")) <= 255, names[1]
    assert "Volume I_" in names[1] and "Volume II_" in names[2], names


def test_a_long_title_in_any_script_stays_under_the_device_limit(world):
    """The restored title is cut by bytes on a letter boundary, like the
    names before it: Russian and Chinese letters are two or three bytes."""
    world.add_user("reader")
    russian = "Война и мир, том первый и второй, с примечаниями переводчика " * 4
    chinese = "三体问题地球往事三部曲之一黑暗森林死神永生" * 8
    for book_id, title in ((1, russian), (2, chinese)):
        _add_volume(world, book_id, title)

    for book_id, name in _filenames(world).items():
        assert len(name.encode("utf-8")) <= 255, name
        assert name.endswith(" - Ann Author [%d].epub" % book_id), name


def test_progress_under_a_listed_name_finds_its_book_without_a_download(world):
    """A device in *filename* document matching (Sync settings) identifies a
    book by the MD5 of its file name. The server used to record that digest
    only when it served the download, but a book this change renames is moved
    in place on the device, never downloaded again, so its progress would stop
    reaching the server. Every name the library lists is matchable."""
    import hashlib

    world.add_user("reader")
    _add_volume(world, 1, VOLUME_ONE)
    _add_volume(world, 2, VOLUME_TWO)
    names = _filenames(world)

    for book_id in (1, 2):
        digest = hashlib.md5(names[book_id].encode("utf-8")).hexdigest()
        response = world.client.put(
            "/kosync/syncs/progress", headers=world.device_headers("reader"),
            json={"document": digest, "progress": "/body/DocFragment[3]/body/p[1].0",
                  "percentage": 0.25, "device": "KOReader", "device_id": "kindle-1"})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json().get("calibre_book_id") == book_id, names[book_id]


def test_listing_twice_records_each_name_once(world):
    world.add_user("reader")
    _add_volume(world, 1, VOLUME_ONE)
    _filenames(world)
    _filenames(world)

    rows = world.session.execute(text(
        "SELECT COUNT(*) FROM book_format_checksums WHERE book = 1 "
        "AND version = 'koreader_filename'")).scalar()
    assert rows == 1
