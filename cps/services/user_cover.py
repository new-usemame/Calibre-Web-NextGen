# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user cover storage and delivery-copy helpers.

The global Calibre ``cover.jpg`` remains authoritative.  Personal JPEGs live
under CONFIG_DIR and are selected only with an explicit user id; no caller may
derive a user from a book or from a global setting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
import posixpath
import uuid
import zipfile

from PIL import Image, ImageOps
from flask import request, send_from_directory
from werkzeug.datastructures import FileStorage

from .. import constants, cw_advocate, logger, ub
from . import cover_extract


log = logger.create()
_ROOT_NAME = "user-covers"
_SUPPORTED_UPLOAD_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp", "image/gif",
}


def root_dir() -> str:
    return os.path.join(constants.CONFIG_DIR, _ROOT_NAME)


def cover_directory(user_id: int) -> str:
    return os.path.join(root_dir(), str(int(user_id)))


def cover_filename(book_id: int, version: str | None = None) -> str:
    """Return the immutable filename for one committed personal-cover version."""
    suffix = "-{}".format(version) if version else ""
    return "{}{}.jpg".format(int(book_id), suffix)


def cover_path(user_id: int, book_id: int, version: str | None = None) -> str:
    return os.path.join(cover_directory(user_id), cover_filename(book_id, version))


def version_token(row_or_datetime) -> str:
    value = getattr(row_or_datetime, "updated_at", row_or_datetime)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return str(int(value.timestamp() * 1_000_000))
    return "0"


def path_for_row(row) -> str:
    return cover_path(row.user_id, row.book_id, version_token(row))


def next_updated_at(existing=None) -> datetime:
    """Return a cache/file version strictly newer than the existing row."""
    value = datetime.now(timezone.utc)
    previous = getattr(existing, "updated_at", None)
    if isinstance(previous, datetime):
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        if value <= previous:
            value = previous + timedelta(microseconds=1)
    return value


def cover_url(row) -> str:
    return "/api/v1/books/{}/my-cover/image?c={}".format(
        int(row.book_id), version_token(row))


def row_for_user(user_id, book_id, *, session=None):
    """Return the preference row even when its file has gone missing."""
    if user_id is None:
        return None
    session = session or ub.session
    try:
        row = (session.query(ub.UserBookCover)
               .filter(ub.UserBookCover.user_id == int(user_id),
                       ub.UserBookCover.book_id == int(book_id)).first())
    except Exception:
        return None
    return row


def override_for_user(user_id, book_id, *, session=None):
    """Return a usable row, never a DB pointer to missing bytes."""
    row = row_for_user(user_id, book_id, session=session)
    if row is None or not os.path.isfile(path_for_row(row)):
        return None
    return row


def overrides_for_user(user_id, book_ids, *, session=None):
    if user_id is None:
        return {}
    ids = {int(book_id) for book_id in book_ids}
    if not ids:
        return {}
    session = session or ub.session
    try:
        rows = (session.query(ub.UserBookCover)
                .filter(ub.UserBookCover.user_id == int(user_id),
                        ub.UserBookCover.book_id.in_(ids)).all())
    except Exception:
        return {}
    return {
        int(row.book_id): row for row in rows
        if os.path.isfile(path_for_row(row))
    }


def kobo_resource_version_for_user(user_id, *, session=None) -> str | None:
    """Version Kobo's image URL template without versioning entitlements.

    Kobo uses ``CoverImageId`` in both its local image cache and the book
    metadata that CWNG fingerprints for replay suppression. A digest of this
    user's usable preferences belongs on the image *template* instead: set or
    clear changes the fetched URL, while the held book's entitlement remains
    byte-identical and its device ledger stays intact.
    """
    if user_id is None:
        return None
    session = session or ub.session
    try:
        rows = (session.query(ub.UserBookCover)
                .filter(ub.UserBookCover.user_id == int(user_id))
                .order_by(ub.UserBookCover.book_id)
                .all())
    except Exception:
        return None
    components = [
        "{}:{}".format(int(row.book_id), version_token(row))
        for row in rows
        if os.path.isfile(path_for_row(row))
    ]
    if not components:
        return None
    return hashlib.sha256("|".join(components).encode("ascii")).hexdigest()[:16]


def _read_limited(stream, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("cover image exceeds the configured size limit")
    return b"".join(chunks)


def _jpeg_storage(raw: bytes) -> FileStorage:
    """Fully decode untrusted image bytes and normalize them to RGB JPEG."""
    with Image.open(io.BytesIO(raw)) as source:
        from .. import helper
        helper.validate_cover_dimensions(*source.size)
        source.load()
        normalized = ImageOps.exif_transpose(source)
        if normalized.mode not in ("RGB", "L"):
            background = Image.new("RGB", normalized.size, "white")
            if "A" in normalized.getbands():
                background.paste(normalized, mask=normalized.getchannel("A"))
            else:
                background.paste(normalized.convert("RGB"))
            normalized = background
        elif normalized.mode != "RGB":
            normalized = normalized.convert("RGB")
        output = io.BytesIO()
        normalized.save(output, format="JPEG", quality=92, optimize=True)
    output.seek(0)
    return FileStorage(
        stream=output, filename="personal-cover.jpg", content_type="image/jpeg")


def stage_upload(user_id: int, book_id: int, updated_at: datetime, file_storage):
    from .. import helper

    content_type = (getattr(file_storage, "content_type", "") or "").split(";", 1)[0].lower()
    if content_type and content_type not in _SUPPORTED_UPLOAD_TYPES:
        return None, "The uploaded file is not a supported cover image."
    max_bytes, _max_mb = helper._get_cover_download_limit()
    try:
        raw = _read_limited(file_storage.stream, max_bytes)
        normalized = _jpeg_storage(raw)
    except Exception as error:
        log.warning("Personal cover upload rejected: %s", error)
        return None, "The uploaded file is not a usable cover image."
    return helper.save_cover_from_filestorage(
        cover_directory(user_id),
        cover_filename(book_id, version_token(updated_at)),
        normalized,
    )


def stage_bytes(user_id: int, book_id: int, updated_at: datetime, raw: bytes):
    return stage_upload(
        user_id, book_id, updated_at,
        FileStorage(stream=io.BytesIO(raw), filename="cover", content_type="image/jpeg"),
    )


def stage_url(user_id: int, book_id: int, updated_at: datetime, url: str):
    from .. import helper

    max_bytes, _max_mb = helper._get_cover_download_limit()
    response = None
    try:
        response = cw_advocate.get(
            url, timeout=(10, 30), allow_redirects=True, stream=True)
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
        if content_type and content_type not in _SUPPORTED_UPLOAD_TYPES:
            raise ValueError("remote response is not an image")
        raw = _read_limited(response.raw, max_bytes)
        normalized = _jpeg_storage(raw)
    except Exception as error:
        log.warning("Personal cover URL rejected: %s", error)
        return None, "The cover URL could not be loaded as an image."
    finally:
        if response is not None:
            response.close()
    return helper.save_cover_from_filestorage(
        cover_directory(user_id),
        cover_filename(book_id, version_token(updated_at)),
        normalized,
    )


def send_override(row):
    """Serve only the current user's row with private versioned caching."""
    response = send_from_directory(
        cover_directory(row.user_id), os.path.basename(path_for_row(row)),
        mimetype="image/jpeg",
    )
    expected = version_token(row)
    if request.args.get("c") == expected:
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-cache"
    response.vary.add("Cookie")
    return response


def remove_file(user_id: int, book_id: int, row=None, *, version=None) -> None:
    target = (
        path_for_row(row)
        if row is not None
        else cover_path(user_id, book_id, version)
    )
    try:
        os.remove(target)
    except FileNotFoundError:
        return
    except OSError as error:
        # The row is already gone, so this is a harmless orphan, never a reason
        # to restore a preference the user explicitly cleared.
        log.warning("Could not remove unreferenced personal cover: %s", error)


def _cover_member(source_path: str):
    """Return (member path, extension) for an EPUB/KEPUB manifest cover."""
    with zipfile.ZipFile(source_path, "r") as archive:
        container = archive.read("META-INF/container.xml")
        from lxml import etree
        tree = etree.fromstring(container)
        roots = tree.xpath(
            "//*[local-name()='rootfile']/@full-path")
        if not roots:
            return None
        opf_path = roots[0]
        package = etree.fromstring(archive.read(opf_path))
        href = cover_extract._find_epub_cover_href(package)
        if not href:
            return None
        member = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
        if member.startswith("../") or member.startswith("/"):
            return None
        return member, os.path.splitext(member)[1].lower()


def _encode_for_member(jpeg_path: str, extension: str) -> bytes | None:
    formats = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
        ".webp": "WEBP", ".bmp": "BMP", ".gif": "GIF",
    }
    target_format = formats.get(extension)
    if target_format is None:
        return None
    with Image.open(jpeg_path) as image:
        image.load()
        output = io.BytesIO()
        image.save(output, format=target_format, quality=92)
        return output.getvalue()


def materialize_delivery_copy(user_id, book_id, source_path: str, book_format: str):
    """Return a private EPUB/KEPUB copy with this user's cover embedded.

    The shared library archive is opened read-only.  Replacing exactly the
    manifest-declared cover image preserves KEPUB KoboSpan anchors and every
    other package member byte-for-byte.
    """
    row = override_for_user(user_id, book_id)
    if row is None or (book_format or "").lower() not in ("epub", "kepub"):
        return None
    try:
        cover_member = _cover_member(source_path)
        if cover_member is None:
            log.warning("Book %s has no replaceable embedded cover", book_id)
            return None
        member_name, extension = cover_member
        replacement = _encode_for_member(path_for_row(row), extension)
        if replacement is None:
            return None

        from .. import helper
        destination_dir = helper.get_temp_dir()
        os.makedirs(destination_dir, exist_ok=True)
        stem = str(uuid.uuid4())
        destination = os.path.join(destination_dir, stem + "." + book_format.lower())
        with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(destination, "w") as target:
            target.comment = source.comment
            for info in source.infolist():
                target.writestr(
                    info,
                    replacement if info.filename == member_name else source.read(info.filename),
                )
        with zipfile.ZipFile(destination, "r") as check:
            if check.testzip() is not None:
                raise ValueError("personal-cover delivery copy failed CRC validation")
        return destination_dir, stem
    except Exception as error:
        log.warning("Could not embed personal cover for book %s: %s", book_id, error)
        return None
