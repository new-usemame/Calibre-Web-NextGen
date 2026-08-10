# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation and conservative normalization for annotation content ids."""

from __future__ import annotations

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


def _chapter(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1536:
        raise ContentIdError("content_id chapter path is missing or too long")
    if value.startswith("/") or "\\" in value or "!!" in value:
        raise ContentIdError("content_id chapter path is not relative")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContentIdError("content_id chapter path contains control characters")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ContentIdError("content_id chapter path contains an unsafe segment")
    return value


def normalize_content_id(value, *, book_uuid=None, allow_legacy_file_uri=False):
    """Return canonical ``uuid!!chapter`` or reject; ``None`` remains ``None``."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_CONTENT_ID_LENGTH:
        raise ContentIdError("content_id must be a non-empty bounded string")
    expected = _normal_uuid(book_uuid) if book_uuid is not None else None
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
