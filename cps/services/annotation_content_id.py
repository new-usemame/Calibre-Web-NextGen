# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation and conservative normalization for annotation content ids."""

from __future__ import annotations

import posixpath
import re
import uuid
from pathlib import PurePosixPath

MAX_CONTENT_ID_LENGTH = 2048
_UUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_CANONICAL = re.compile(rf"^({_UUID})!!(.+)$")
_LEGACY_FILE = re.compile(
    r"^file:///mnt/(?:onboard|sd|sdcard)/([^?#]+)#\([0-9]{1,4}\)(.+)$"
)


class ContentIdError(ValueError):
    """A client supplied content id is outside the accepted grammar."""


def _normal_uuid(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_UUID, value):
        raise ContentIdError("content_id book identifier must be a UUID")
    return str(uuid.UUID(value))


def _canonical_uuid_or_none(value):
    """Normalize a SERVER-supplied book uuid, or return None if it isn't one.

    Deliberately non-raising, unlike ``_normal_uuid``: client input is rejected,
    our own data is merely unusable as a comparator.
    """
    if value is None:
        return None
    try:
        return _normal_uuid(value)
    except ContentIdError:
        return None


def _chapter(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1536:
        raise ContentIdError("content_id chapter path is missing or too long")
    if value.startswith("/") or "\\" in value or "!!" in value:
        raise ContentIdError("content_id chapter path is not relative")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContentIdError("content_id chapter path contains control characters")
    # Preserve the existing rejection of empty path segments before normpath
    # can erase them. Besides repeated separators, this rejects URL authority
    # syntax such as "http://example.test/chapter.xhtml".
    if "" in value.split("/"):
        raise ContentIdError("content_id chapter path contains an unsafe segment")
    # Real clients emit CONTAINED traversals. A Kobo whose OPF references a file
    # outside the OPF directory (an EPUB3 nav at the zip root declared
    # href="../nav.xhtml") joins paths without normalizing, so it sends e.g.
    # "OPS/../OPS/chapter-017.xml" -- unambiguously "OPS/chapter-017.xml".
    # This guard exists to stop a path ESCAPING the book container, not to reject
    # every dot segment; rejecting the contained case cost real annotations their
    # content_id and left them unresolvable in the web reader.
    normalized = posixpath.normpath(value)
    if (normalized.startswith("../") or normalized.startswith("/")
            or normalized in (".", "..")):
        raise ContentIdError("content_id chapter path escapes the book container")
    return normalized


def normalize_content_id(value, *, book_uuid=None, allow_legacy_file_uri=False):
    """Return canonical ``uuid!!chapter`` or reject; ``None`` remains ``None``."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_CONTENT_ID_LENGTH:
        raise ContentIdError("content_id must be a non-empty bounded string")
    # The book's own uuid is OUR data, not the client's, so a non-canonical one
    # is not a client error and must not raise ContentIdError. It only means we
    # cannot bind this content_id to a specific book; the grammar check below
    # still rejects anything malformed. Raising here made a legitimate KOReader
    # push fail because the *server's* book record had a non-UUID uuid.
    expected = _canonical_uuid_or_none(book_uuid)
    match = _CANONICAL.fullmatch(value)
    if match:
        actual = _normal_uuid(match.group(1))
        if expected and actual != expected:
            raise ContentIdError("content_id does not belong to this book")
        return f"{actual}!!{_chapter(match.group(2))}"
    if allow_legacy_file_uri:
        match = _LEGACY_FILE.fullmatch(value)
        if match and expected:
            _chapter(match.group(1))
            return f"{expected}!!{_chapter(match.group(2))}"
    # A book whose Calibre uuid is not a canonical UUID (legacy or imported
    # rows) forces its clients to build a content_id we would otherwise call
    # malformed — and dropping it would lose a real annotation over the shape
    # of OUR identifier. Accept only on an exact match against the book's own
    # record: that is direct proof of ownership, which is what the UUID grammar
    # was standing in for. An arbitrary client value still cannot get through,
    # because it has to equal the uuid we already hold.
    if isinstance(book_uuid, str) and book_uuid:
        prefix = f"{book_uuid}!!"
        if value.startswith(prefix) and len(value) > len(prefix):
            return f"{book_uuid}!!{_chapter(value[len(prefix):])}"
    raise ContentIdError("content_id has an unsupported shape")


def normalize_content_id_for_backfill(value, *, book_uuid):
    """Normalize only when the filename UUID matches the row's Calibre book UUID.

    ``book_uuid`` must come from the authoritative Calibre ``books`` row joined
    through this annotation's own ``book_id``. A UUID-looking filename is only
    corroborating evidence; it is never allowed to choose the target book.
    """
    if value is None:
        return None
    try:
        expected = _normal_uuid(book_uuid)
    except ContentIdError:
        return value
    try:
        return normalize_content_id(value, book_uuid=expected)
    except ContentIdError:
        pass
    if not isinstance(value, str) or len(value) > MAX_CONTENT_ID_LENGTH:
        return value
    match = _LEGACY_FILE.fullmatch(value)
    if not match:
        return value
    filename = PurePosixPath(match.group(1)).name
    stem = filename[:-11] if filename.lower().endswith(".kepub.epub") else (
        filename[:-5] if filename.lower().endswith(".epub") else filename
    )
    try:
        if _normal_uuid(stem) != expected:
            return value
        return normalize_content_id(
            value, book_uuid=expected, allow_legacy_file_uri=True,
        )
    except ContentIdError:
        return value
