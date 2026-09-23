# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The KOReader library over real HTTP: manifest, placeholders, files, status.

Everything a KOReader device acts on is checked from the device's side of the
wire: which books it is told to hold, that a placeholder is an EPUB the app's
own EPUB reader opens (title, authors, series, cover), that a downloaded file
is byte-for-byte what the manifest described, and that a book outside the
reader's library or rights is refused.
"""

import io
import json
import logging
import zipfile
from datetime import datetime
from urllib.parse import quote

import pytest
from lxml import etree
from PIL import Image

from cps import ub
from cps.progress_syncing.checksums.koreader import calculate_koreader_partial_md5
from cps.services import ereader_scope
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

OPF_NS = {"opf": "http://www.idpf.org/2007/opf",
          "dc": "http://purl.org/dc/elements/1.1/"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    yield built
    built.close()


def _library(world, user="reader", **params):
    response = world.client.get("/kosync/syncs/library", query_string=params,
                                headers=world.device_headers(user))
    return response


def _books(world, user="reader"):
    body = _library(world, user).get_json()
    return {book["book_id"]: book for book in body["books"]}, body


def _standard_library(world):
    reader = world.add_user("reader", denied_tags="secret")
    world.add_book(1, "First Light", authors=("Ann Author", "Bo Second"),
                   series="Dawn", series_index=2.0, author_sort="Author, Ann & Second, Bo")
    world.add_book(2, "Paper Only", formats=("PDF",), cover=False)
    world.add_book(3, "Kindle Only", formats=("AZW3",))
    world.add_book(4, "Put Away")
    world.add_book(5, "Out Of Sight")
    world.add_book(6, "Forbidden", tags=("secret",))
    world.session.add(ub.ArchivedBook(user_id=reader.id, book_id=4,
                                      is_archived=True))
    world.session.add(ub.UserHiddenBook(user_id=reader.id, book_id=5))
    world.session.commit()
    return reader


def test_manifest_lists_the_ereader_scope_as_verifiable_files(world):
    reader = _standard_library(world)

    books, body = _books(world)

    # The e-reader scope, minus what KOReader cannot open.
    held = ereader_scope.held_book_ids(reader, cdb=world.cdb)
    assert set(books) == {1, 2} == held - {3}
    assert body["total"] == 2
    assert body["unsupported"] == 1
    assert body["scope"] == "library"
    assert body["next_cursor"] is None

    first = books[1]
    path = world.book_file(1, "EPUB")
    assert first["filename"] == "First Light - Ann Author [1].epub"
    assert first["format"] == "EPUB"
    assert first["size"] == path.stat().st_size
    assert first["checksum"] == calculate_koreader_partial_md5(str(path))
    assert first["authors"] == ["Ann Author", "Bo Second"]
    # Calibre's own sort form, so the device's Authors view sorts by surname.
    assert first["author_sort"] == "Author, Ann & Second, Bo"
    assert (first["series"], first["series_index"]) == ("Dawn", 2.0)
    assert first["added"] == "2026-09-01T12:00:00Z"
    assert (first["read_status"], first["progress"]) == ("unread", None)
    # Never read anywhere: no reading time at all, rather than a made-up one.
    assert "last_read" not in first
    assert books[2]["filename"] == "Paper Only - Ann Author [2].pdf"
    assert books[2]["series"] is None and books[2]["series_index"] is None


def test_manifest_carries_the_readers_status_position_and_shelves(world):
    reader = _standard_library(world)
    other = world.add_user("other")
    world.shelf(reader, "Beach reads", [2, 1], uuid="shelf-beach")
    world.shelf(reader, "Empty", [])
    world.shelf(other, "Not mine", [1], uuid="shelf-other")
    world.session.add(ub.ReadBook(user_id=reader.id, book_id=1,
                                  read_status=ub.ReadBook.STATUS_FINISHED))
    world.session.commit()
    world.position(reader, 2, 42.5)

    books, body = _books(world)

    assert books[1]["read_status"] == "finished"
    assert books[2]["read_status"] == "reading"
    assert books[2]["progress"] == pytest.approx(0.425)
    assert books[2]["last_read"].endswith("Z")
    empty_id = next(s["id"] for s in body["shelves"] if s["name"] == "Empty")
    assert body["shelves"] == [{"id": "shelf-beach", "name": "Beach reads"},
                               {"id": empty_id, "name": "Empty"}]
    assert books[1]["shelves"] == ["shelf-beach"]
    assert books[2]["shelves"] == ["shelf-beach"]
    assert body["scope_shelves"] == []


def test_last_read_is_when_the_reader_last_moved_in_the_book_on_any_device(world):
    reader = world.add_user("reader")
    for book_id in (1, 2, 3, 4, 5):
        world.add_book(book_id, "Book %d" % book_id)
    at = lambda text: datetime.fromisoformat(text)  # noqa: E731

    # Read forward on a Kobo, then turned back a chapter in KOReader: the
    # shared bookmark keeps the furthest place and its time; the position
    # carrier took the later turn-back.
    world.position(reader, 1, 60.0, at=at("2026-09-10T08:00:00"))
    world.carrier_position(reader, 1, 55.0, at=at("2026-09-12T20:30:00"))
    # Only ever read on a Kobo.
    world.position(reader, 2, 30.0, at=at("2026-09-11T07:15:00"))
    # Opened in the web reader, which leaves only the carrier row.
    world.carrier_position(reader, 3, 5.0, at=at("2026-09-09T22:00:00"))
    # Read on in the web reader after an older KOReader sync: the later
    # bookmark wins over the older carrier row.
    world.carrier_position(reader, 5, 20.0, at=at("2026-09-05T18:00:00"))
    world.position(reader, 5, 40.0, at=at("2026-09-06T21:45:00"))

    books, _body = _books(world)

    assert books[1]["last_read"] == "2026-09-12T20:30:00Z"
    assert books[2]["last_read"] == "2026-09-11T07:15:00Z"
    assert books[3]["last_read"] == "2026-09-09T22:00:00Z"
    assert "last_read" not in books[4]
    assert books[5]["last_read"] == "2026-09-06T21:45:00Z"
    # Another reader's activity on the same book is not this reader's.
    other = world.add_user("other")
    world.carrier_position(other, 2, 90.0, at=at("2026-09-20T10:00:00"))
    assert _books(world)[0][2]["last_read"] == "2026-09-11T07:15:00Z"


def test_shelf_only_scope_names_its_shelves(world):
    reader = world.add_user("reader", shelf_only=True)
    world.add_book(1, "On The Shelf")
    world.add_book(2, "Elsewhere")
    world.shelf(reader, "For my Kindle", [1], kobo_sync=True, uuid="kindle-shelf")
    world.shelf(reader, "Just a list", [2], uuid="list-shelf")

    books, body = _books(world)

    assert set(books) == {1}
    assert body["scope"] == "shelves"
    assert body["scope_shelves"] == [
        {"id": "kindle-shelf", "name": "For my Kindle", "kind": "shelf"}]
    # Collections still list every shelf the reader owns.
    assert {s["id"] for s in body["shelves"]} == {"kindle-shelf", "list-shelf"}


def test_revision_changes_only_when_something_the_device_shows_changes(world):
    reader = _standard_library(world)
    world.add_user("other")
    first = _library(world).get_json()["revision"]

    unchanged = _library(world, if_revision=first).get_json()
    assert unchanged == {"unchanged": True, "revision": first}
    # Another account's activity is not this device's business.
    other = world.session.query(ub.User).filter_by(name="other").one()
    world.session.add(ub.ReadBook(user_id=other.id, book_id=1,
                                  read_status=ub.ReadBook.STATUS_FINISHED))
    world.session.commit()
    assert _library(world, if_revision=first).get_json()["unchanged"] is True

    world.session.add(ub.ReadBook(user_id=reader.id, book_id=1,
                                  read_status=ub.ReadBook.STATUS_FINISHED))
    world.session.commit()
    after_status = _library(world, if_revision=first).get_json()
    assert "unchanged" not in after_status
    assert after_status["revision"] != first

    # A reading-time change alone (a turn-back on one device) reorders the
    # device's "Reading" view, so it is news too.
    world.carrier_position(reader, 1, 10.0, at=datetime(2026, 9, 14, 9, 0, 0))
    after_reading = _library(world, if_revision=after_status["revision"]).get_json()
    assert "unchanged" not in after_reading
    # So is a new author sort: it decides where the book sits under Authors.
    from cps import db
    resorted = world.session.get(db.Books, 1)
    resorted.author_sort = "Second, Bo & Author, Ann"
    world.session.commit()
    after_sort = _library(world, if_revision=after_reading["revision"]).get_json()
    assert "unchanged" not in after_sort
    after_status = after_sort

    rev_before = {b["book_id"]: b["rev"] for b in after_status["books"]}
    retitled = world.session.get(db.Books, 2)
    retitled.title = "Paper Only, Revised"
    world.session.commit()
    after_title = _library(world).get_json()
    rev_after = {b["book_id"]: b["rev"] for b in after_title["books"]}
    assert rev_after[2] != rev_before[2]
    assert rev_after[1] == rev_before[1]
    assert after_title["revision"] != after_status["revision"]


def test_paging_visits_every_book_once_under_the_starting_revision(world):
    world.add_user("reader")
    for book_id in range(1, 6):
        world.add_book(book_id, "Book %d" % book_id)

    seen, revisions, cursor = [], set(), None
    for _page in range(10):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = _library(world, **params).get_json()
        seen += [book["book_id"] for book in body["books"]]
        revisions.add(body["revision"])
        if seen == [1, 2]:
            # A change mid-walk must not be hidden behind a fresh revision.
            from cps import db
            world.session.get(db.Books, 5).title = "Book five, renamed"
            world.session.commit()
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [1, 2, 3, 4, 5]
    assert len(revisions) == 1
    assert _library(world, if_revision=revisions.pop()).get_json().get("unchanged") is None

    assert _library(world, cursor="not-a-cursor").status_code == 400


def _walk(world, user="reader", limit=2, pause=None):
    """Every page of one sync, as the plugin reads them: book ids and revisions."""
    seen, revisions, cursor = [], [], None
    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = _library(world, user, **params).get_json()
        seen += [book["book_id"] for book in body["books"]]
        revisions.append(body["revision"])
        cursor = body["next_cursor"]
        if cursor is None:
            return seen, revisions
        if pause:
            pause()


def test_a_device_reading_every_page_costs_one_manifest(world, monkeypatch):
    """The plugin reads a big library a page (200 books) at a time. Working
    the whole scope out again for every page made one sync of a 1,500-book
    library cost eight manifests, each costing more as the library grows. The
    pages after the first come from the manifest the sync started with."""
    from cps import db
    from cps.services import koreader_library
    world.add_user("reader")
    for book_id in range(1, 8):
        world.add_book(book_id, "Book %d" % book_id)
    builds = []
    build = koreader_library.build_manifest
    monkeypatch.setattr(koreader_library, "build_manifest",
                        lambda user, **kwargs: builds.append(user.name) or build(user, **kwargs))

    seen, revisions = _walk(world)
    assert seen == [1, 2, 3, 4, 5, 6, 7]
    assert len(revisions) == 4 and len(set(revisions)) == 1
    assert builds == ["reader"]

    # Starting a sync always looks at the library as it is now.
    world.session.get(db.Books, 7).title = "Book seven, renamed"
    world.session.commit()
    builds.clear()
    seen, renamed = _walk(world)
    assert seen == [1, 2, 3, 4, 5, 6, 7]
    assert len(set(renamed)) == 1 and renamed[0] != revisions[0]
    assert builds == ["reader"]

    # A server restarted in the middle of a sync, with the library changed
    # meanwhile, finishes it from one new manifest, under the revision the
    # sync started with (the next sync then sees the change).
    started = _library(world, limit=2).get_json()
    world.session.get(db.Books, 6).title = "Book six, renamed"
    world.session.commit()
    monkeypatch.setattr(koreader_library, "_WALKS", koreader_library._Walks())
    builds.clear()
    cursor, seen = started["next_cursor"], [1, 2]
    while cursor:
        body = _library(world, limit=2, cursor=cursor).get_json()
        assert body["revision"] == started["revision"]
        seen += [book["book_id"] for book in body["books"]]
        cursor = body["next_cursor"]
    assert seen == [1, 2, 3, 4, 5, 6, 7]
    assert builds == ["reader"]
    assert "unchanged" not in _library(world, if_revision=started["revision"]).get_json()

    # A sync that pauses longer than a sync takes is still answered, from the
    # library as it is when it carries on.
    clock = [koreader_library._clock()]
    monkeypatch.setattr(koreader_library, "_clock", lambda: clock[0])
    builds.clear()

    def wander_off():
        clock[0] += 3600

    assert _walk(world, pause=wander_off)[0] == [1, 2, 3, 4, 5, 6, 7]
    assert builds == ["reader"] * 4


def test_the_server_keeps_only_a_few_syncs_in_progress(world, monkeypatch):
    """Each kept sync holds a whole manifest, so only the most recent few are
    kept; one pushed out still finishes, from a manifest built for it."""
    from cps.services import koreader_library
    monkeypatch.setattr(koreader_library._WALKS, "capacity", 2)
    names = ["first", "second", "third"]
    for name in names:
        world.add_user(name)
    for book_id in range(1, 6):
        world.add_book(book_id, "Book %d" % book_id)
    builds = []
    build = koreader_library.build_manifest
    monkeypatch.setattr(koreader_library, "build_manifest",
                        lambda user, **kwargs: builds.append(user.name) or build(user, **kwargs))

    cursors = {name: _library(world, name, limit=2).get_json()["next_cursor"] for name in names}
    builds.clear()
    for name in reversed(names):
        body = _library(world, name, limit=2, cursor=cursors[name]).get_json()
        assert [book["book_id"] for book in body["books"]] == [3, 4]
    assert builds == ["first"]


def test_a_page_cursor_opens_only_its_own_readers_library(world):
    """A cursor names the sync it continues; presented by another account it
    must not hand over the manifest that account's sync never computed."""
    world.add_user("reader")
    world.add_user("guarded", denied_tags="secret")
    for book_id in range(1, 6):
        world.add_book(book_id, "Book %d" % book_id,
                       tags=("secret",) if book_id >= 4 else ())
    first = _library(world, "reader", limit=2).get_json()
    assert first["next_cursor"]

    borrowed = _library(world, "guarded", limit=2, cursor=first["next_cursor"]).get_json()
    assert [book["book_id"] for book in borrowed["books"]] == [3]
    assert borrowed["total"] == 3


def _placeholder(world, book_id, user="reader"):
    return world.client.get(
        "/kosync/syncs/library/books/%d/placeholder" % book_id,
        headers=world.device_headers(user))


def test_placeholder_is_an_epub_the_app_reads_with_marker_and_cover(world, tmp_path):
    from cps.epub import get_epub_info

    _standard_library(world)
    books, _body = _books(world)

    response = _placeholder(world, 1)

    assert response.status_code == 200
    assert response.mimetype == "application/epub+zip"
    assert response.headers["X-CWNG-Placeholder-Rev"] == books[1]["rev"]
    data = response.get_data()
    assert len(data) < 1024 * 1024
    archive = zipfile.ZipFile(io.BytesIO(data))
    first = archive.infolist()[0]
    assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)
    assert archive.read("mimetype") == b"application/epub+zip"
    assert json.loads(archive.read("META-INF/cwng-placeholder.json")) == {
        "book_id": 1, "rev": books[1]["rev"]}
    opf = etree.fromstring(archive.read("OEBPS/content.opf"))
    assert opf.xpath("//dc:identifier/text()", namespaces=OPF_NS) == ["urn:cwng:book:1"]
    assert opf.xpath("//opf:meta[@name='cwng:placeholder']/@content",
                     namespaces=OPF_NS) == ["1"]
    for name in archive.namelist():
        if name.endswith(".xhtml"):
            etree.fromstring(archive.read(name))  # well-formed XHTML

    # The app's own EPUB reader (the upload path) sees the book's metadata
    # and pulls out a cover image.
    placeholder_path = tmp_path / "placeholder.epub"
    placeholder_path.write_bytes(data)
    meta = get_epub_info(str(placeholder_path), "placeholder", ".epub", False)
    assert meta.title == "First Light"
    assert meta.author == "Ann Author & Bo Second"
    assert (meta.series, meta.series_id) == ("Dawn", "2")
    cover = Image.open(meta.cover)
    assert cover.format == "JPEG"
    assert cover.height <= 600
    # The badge sits in the bottom-right corner; the library cover is plain blue.
    width, height = cover.size
    assert cover.convert("RGB").getpixel((width // 2, height // 2)) == pytest.approx(
        (20, 90, 160), abs=12)
    corner = cover.convert("RGB").getpixel((width - width // 7, height - width // 7))
    assert max(abs(a - b) for a, b in zip(corner, (20, 90, 160))) > 60


def test_placeholder_without_a_cover_still_has_one(world, tmp_path):
    from cps.epub import get_epub_info

    _standard_library(world)
    data = _placeholder(world, 2).get_data()
    path = tmp_path / "plain.epub"
    path.write_bytes(data)

    meta = get_epub_info(str(path), "plain", ".epub", False)

    assert meta.title == "Paper Only"
    assert Image.open(meta.cover).height <= 600


# libjpeg's base luminance table; a file's own table is this scaled by its
# quality setting, which is how the quality is read back from the bytes.
STD_LUMINANCE = (16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
                 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
                 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99)


def jpeg_quality(image):
    scale = 100.0 * sum(image.quantization[0]) / sum(STD_LUMINANCE)
    return (200 - scale) / 2 if scale <= 100 else 5000 / scale


def busy_cover(size=(1500, 2250)):
    """A full-size cover with gradients, shapes and grain, like a scanned or
    illustrated cover, which JPEG cannot shrink by flat colour alone."""
    import random
    from PIL import ImageDraw, ImageFilter

    rng = random.Random(7)
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)],
                  fill=(int(30 + 180 * y / height), 60, int(200 - 150 * y / height)))
    for _ in range(180):
        x, y, r = rng.randrange(width), rng.randrange(height), rng.randrange(20, 260)
        draw.ellipse((x - r, y - r, x + r, y + r),
                     fill=tuple(rng.randrange(256) for _ in range(3)))
    image = image.filter(ImageFilter.GaussianBlur(3))
    image = Image.blend(image, Image.effect_noise(size, 24).convert("RGB"), 0.15)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def test_placeholder_covers_stay_small_enough_for_a_whole_library(world, tmp_path):
    # A device downloads one placeholder per book: a 2250-pixel cover must
    # arrive about 600 pixels tall and plainly compressed, not as scanned.
    # (Not a power-of-two multiple of 600, so decoding at a reduced scale
    # alone cannot get it there.)
    world.add_user("reader")
    source = busy_cover()
    world.add_book(1, "Big Cover", cover_image=source)

    archive = zipfile.ZipFile(io.BytesIO(_placeholder(world, 1).get_data()))
    data = archive.read("OEBPS/cover.jpg")
    cover = Image.open(io.BytesIO(data))

    assert cover.height <= 600 and cover.width <= 400
    assert 70 <= jpeg_quality(cover) <= 78
    assert len(data) <= 40 * 1024 < len(source)


def test_placeholder_answers_not_modified_for_the_same_rev(world):
    _standard_library(world)
    first = _placeholder(world, 1)
    again = world.client.get(
        "/kosync/syncs/library/books/1/placeholder",
        headers={**world.device_headers("reader"), "If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def _placeholder_cover(world, book_id):
    archive = zipfile.ZipFile(io.BytesIO(_placeholder(world, book_id).get_data()))
    return Image.open(io.BytesIO(archive.read("OEBPS/cover.jpg"))).convert("RGB")


def _image_bytes(size, fmt, shade=40):
    buffer = io.BytesIO()
    Image.new("L", size, shade).save(buffer, fmt)
    return buffer.getvalue()


def test_a_cover_too_big_to_decode_safely_gets_the_plain_cover(world):
    """A 12000x12000 PNG saved as cover.jpg cost 1.7 s and 760 MB of memory for
    one placeholder. A cover that would decode past the cap gets the plain
    cover instead; a big JPEG is still used, because it decodes straight to a
    fraction of its size."""
    world.add_user("reader")
    world.add_book(1, "Huge Png", cover_image=_image_bytes((4400, 4400), "PNG"))
    world.add_book(2, "Huge Jpeg", cover_image=_image_bytes((5000, 5000), "JPEG"))

    plain = _placeholder_cover(world, 1)
    assert plain.getpixel((200, 60)) == pytest.approx((236, 232, 224), abs=12)
    own = _placeholder_cover(world, 2)
    assert own.getpixel((60, 60)) == pytest.approx((40, 40, 40), abs=12)


def test_a_placeholder_is_built_once_per_revision(world, monkeypatch):
    """A second device, a device that lost its copy, or a client that never
    sends If-None-Match asks again for the same placeholder: the bytes built
    the first time are served, and a change to the book builds anew."""
    from cps import db
    from cps.services import koreader_placeholder
    world.add_user("reader")
    world.add_book(1, "Once")
    builds = []
    build = koreader_placeholder.build
    monkeypatch.setattr(koreader_placeholder, "build",
                        lambda **kwargs: builds.append(kwargs["title"]) or build(**kwargs))

    first, second = _placeholder(world, 1), _placeholder(world, 1)
    assert first.status_code == second.status_code == 200
    assert first.get_data() == second.get_data()
    assert builds == ["Once"]

    book = world.session.get(db.Books, 1)
    book.title = "Twice"
    book.last_modified = datetime(2026, 9, 2, 8, 0, 0)
    world.session.commit()
    third = _placeholder(world, 1)
    assert builds == ["Once", "Twice"]
    assert b"Twice" in zipfile.ZipFile(io.BytesIO(third.get_data())).read("OEBPS/content.opf")


def _file(world, book_id, user="reader"):
    return world.client.get("/kosync/syncs/library/books/%d/file" % book_id,
                            headers=world.device_headers(user))


def test_file_is_exactly_what_the_manifest_described(world):
    reader = _standard_library(world)
    books, _body = _books(world)

    response = _file(world, 1)

    assert response.status_code == 200
    payload = response.get_data()
    assert payload == world.book_file(1, "EPUB").read_bytes()
    assert int(response.headers["Content-Length"]) == len(payload) == books[1]["size"]
    assert response.headers["X-CWNG-Checksum"] == books[1]["checksum"]
    assert response.headers["X-CWNG-Filename"] == quote(books[1]["filename"])
    # Progress from this copy finds its book, and the device is known.
    kosync = world.kosync
    assert kosync.get_book_by_checksum(books[1]["checksum"])[0] == 1
    assert world.session.query(ub.Device).filter_by(
        user_id=reader.id, kind="koreader").count() == 1


def test_books_outside_the_readers_library_or_rights_are_refused(world):
    _standard_library(world)
    world.add_user("no-downloads", download=False)
    world.add_user("reader2")

    # Content restriction: invisible, so indistinguishable from absent.
    assert _placeholder(world, 6).status_code == 404
    assert _file(world, 6).status_code == 404
    assert _file(world, 999).status_code == 404
    # A reader's own archived/hidden book stays reachable by id.
    assert _file(world, 4).status_code == 200
    # No download right: no manifest and no files, covers are harmless.
    assert _library(world, "no-downloads").status_code == 403
    assert _file(world, 1, "no-downloads").status_code == 403
    assert _placeholder(world, 1, "no-downloads").status_code == 200
    # Wrong password.
    bad = world.client.get("/kosync/syncs/library",
                           headers=world.basic("reader", "wrong"))
    assert bad.status_code == 401


def test_a_file_name_cannot_lead_outside_the_library(world, tmp_path):
    """The book's folder is checked, and so is the file in it: a format whose
    stored name climbs out of the folder (as a crafted metadata.db can say) is
    refused, and nothing outside the library is read for the manifest either."""
    from cps import db
    world.add_user("reader")
    world.add_book(1, "Climber")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.epub").write_bytes(b"not a book of this library " * 40)
    data = world.session.query(db.Data).filter(db.Data.book == 1).one()
    data.name = "../../../outside/secret"  # from library/<author>/<title (1)>/
    world.session.commit()

    response = _file(world, 1)
    assert response.status_code == 404
    assert b"not a book of this library" not in response.get_data()
    books, _body = _books(world)
    assert books[1]["size"] is None and books[1]["checksum"] is None


def test_my_library_bounds_what_a_device_may_fetch(world):
    reader = world.add_user("reader", my_library=True)
    world.add_book(1, "Mine")
    world.add_book(2, "Not mine")
    world.session.add(ub.UserLibraryBook(user_id=reader.id, book_id=1))
    world.session.commit()

    books, _body = _books(world)

    assert set(books) == {1}
    assert _file(world, 2).status_code == 404
    assert _placeholder(world, 2).status_code == 404


def test_switched_off_koreader_sync_answers_unavailable(world, monkeypatch):
    _standard_library(world)
    monkeypatch.setattr(world.kosync, "is_koreader_sync_enabled", lambda: False)

    assert _library(world).status_code == 503
    assert _file(world, 1).status_code == 503
    assert _placeholder(world, 1).status_code == 503


def test_unreadable_magic_shelf_answers_unavailable_not_a_short_list(
        world, monkeypatch):
    from cps import kobo
    world.add_user("reader", shelf_only=True)
    world.add_book(1, "Anything")
    monkeypatch.setattr(kobo, "get_magic_shelf_book_ids_for_kobo",
                        lambda _user_id: ({1}, False))

    response = _library(world)

    assert response.status_code == 503
    assert response.get_json()["error"] == "scope_unavailable"


def _status(world, body, user="reader"):
    payload = {"device": "Kindle Paperwhite", "device_id": "kindle-1"}
    payload.update(body)
    return world.client.put("/kosync/syncs/read_status", json=payload,
                            headers=world.basic(user))


def test_read_status_from_the_device_is_what_the_website_then_shows(world):
    reader = _standard_library(world)
    world.position(reader, 1, 55.0)

    assert _status(world, {"book_id": 1, "status": "finished"}).status_code == 200
    assert world.read_row(reader, 1).read_status == ub.ReadBook.STATUS_FINISHED
    assert _books(world)[0][1]["read_status"] == "finished"

    assert _status(world, {"book_id": 1, "status": "reading"}).status_code == 200
    world.session.expire_all()
    assert world.read_row(reader, 1).read_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert _books(world)[0][1]["progress"] == pytest.approx(0.55)

    # Unread is a restart everywhere, as marking unread on the website is.
    assert _status(world, {"book_id": 1, "status": "unread"}).status_code == 200
    world.session.expire_all()
    assert world.read_row(reader, 1).read_status == ub.ReadBook.STATUS_UNREAD
    assert _books(world)[0][1]["progress"] is None


def test_a_status_the_library_cannot_save_is_refused_without_its_database_error(
        world, monkeypatch, caplog):
    """The database's own words (table names, SQL, paths) go to the log for
    the admin, not over the wire to whoever holds a device password."""
    from cps import helper
    world.add_user("reader")
    world.add_book(1, "Stuck")
    detail = ("Read status could not set: (sqlite3.OperationalError) disk I/O error "
              "[SQL: UPDATE custom_column_5 SET value=?]")
    monkeypatch.setattr(helper, "edit_book_read_status",
                        lambda book_id, read_status=None: detail)

    response = world.client.put(
        "/kosync/syncs/read_status", headers=world.device_headers("reader"),
        json={"book_id": 1, "status": "finished", "device": "Kindle", "device_id": "kindle-1"})

    assert response.status_code == 500
    body = response.get_json()
    assert body == {"error": "read_status_failed", "message": "Read status could not be saved"}
    assert any("disk I/O error" in record.getMessage() for record in caplog.records)


def test_a_healthy_sync_is_quiet_in_the_log_and_a_refused_sign_in_is_not(world, caplog):
    """A library sync is a burst of requests (manifest, a placeholder per
    book, files); a line per request at INFO buried everything else. A wrong
    password still shows at INFO, as #312 wants."""
    from cps.services import app_passwords
    reader = world.add_user("reader")
    for book_id in (1, 2, 3):
        world.add_book(book_id, "Book %d" % book_id)
    _row, device_password = app_passwords.mint(reader.id, "Kindle", session=world.session)
    world.session.commit()

    caplog.set_level(logging.DEBUG)
    for password in ("secret", device_password):
        headers = world.device_headers("reader", password)
        assert world.client.get("/kosync/syncs/library", headers=headers).status_code == 200
        for book_id in (1, 2, 3):
            assert world.client.get("/kosync/syncs/library/books/%d/placeholder" % book_id,
                                    headers=headers).status_code == 200
        assert world.client.get("/kosync/syncs/library/books/1/file",
                                headers=headers).status_code == 200
    assert [record.getMessage() for record in caplog.records
            if record.levelno >= logging.INFO] == []

    assert world.client.get("/kosync/users/auth",
                            headers=world.basic("reader", "wrong")).status_code == 401
    assert [record.getMessage() for record in caplog.records
            if record.levelno == logging.INFO] == ["KOReader auth: Invalid password for user: reader"]


def test_read_status_resolves_a_document_checksum_and_refuses_what_it_cannot(world):
    reader = _standard_library(world)
    checksum = _books(world)[0][1]["checksum"]
    assert _file(world, 1).status_code == 200  # registers the checksum

    assert _status(world, {"document": checksum, "status": "finished"}).status_code == 200
    assert world.read_row(reader, 1).read_status == ub.ReadBook.STATUS_FINISHED

    assert _status(world, {"book_id": 6, "status": "finished"}).status_code == 404
    assert _status(world, {"document": "f" * 32, "status": "finished"}).status_code == 404
    assert _status(world, {"book_id": 1, "status": "abandoned"}).status_code == 400
    assert _status(world, {"book_id": True, "status": "finished"}).status_code == 400
    missing_device = world.client.put(
        "/kosync/syncs/read_status", json={"book_id": 1, "status": "finished"},
        headers=world.basic("reader"))
    assert missing_device.status_code == 400


READ_COLUMN = 947  # high id: clear of cc classes other tests create


@pytest.fixture
def read_column(monkeypatch):
    """A Calibre bool column designated as the read marker (created before
    the world's tables, so metadata.db gets its table)."""
    from types import SimpleNamespace
    from cps import config, db
    if READ_COLUMN not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes(
            [SimpleNamespace(id=READ_COLUMN, datatype="bool")])
    monkeypatch.setattr(config, "config_read_column", READ_COLUMN, raising=False)
    return db.cc_classes[READ_COLUMN]


def test_custom_read_column_decides_finished_on_both_sides(read_column, world):
    from cps import config
    config.config_read_column = READ_COLUMN  # the world resets it to 0
    reader = world.add_user("reader")
    for book_id in (1, 2, 3, 4):
        world.add_book(book_id, "Book %d" % book_id)
    world.session.add_all([
        read_column(book=1, value=True),
        read_column(book=3, value=True),
        ub.ReadBook(user_id=reader.id, book_id=2,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        # A stale in-progress row under a finished column is still finished.
        ub.ReadBook(user_id=reader.id, book_id=3,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
    ])
    world.session.commit()

    statuses = {book_id: book["read_status"]
                for book_id, book in _books(world)[0].items()}
    assert statuses == {1: "finished", 2: "reading", 3: "finished", 4: "unread"}

    assert _status(world, {"book_id": 1, "status": "reading"}).status_code == 200
    assert _status(world, {"book_id": 4, "status": "finished"}).status_code == 200
    world.session.expire_all()
    statuses = {book_id: book["read_status"]
                for book_id, book in _books(world)[0].items()}
    assert statuses[1] == "reading"
    assert statuses[4] == "finished"
