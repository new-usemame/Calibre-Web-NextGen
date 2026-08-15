# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A path that stays inside the container should normalize, not be rejected.

Real Kobo hardware emits `chapterFilename` values like `OPS/../OPS/chapter-017.xml`
when the book's OPF references a file outside the OPF directory (an EPUB3 nav at
the zip root declared `href="../nav.xhtml"`). Nickel joins without normalizing, so
the `..` reaches us verbatim. That path is unambiguously `OPS/chapter-017.xml`.

The grammar's job is to stop traversal ESCAPING the container, not to reject every
dot segment. Rejecting the contained case cost real annotations their content_id
and left them unresolvable in the web reader.
"""

from __future__ import annotations

import pytest

from cps.services.annotation_content_id import normalize_content_id, ContentIdError

UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"


@pytest.mark.parametrize("raw,expected", [
    # the exact shape measured on the operator's Kobo, 2026-08-15
    ("OPS/../OPS/chapter-017.xml", "OPS/chapter-017.xml"),
    ("OEBPS/../OEBPS/chapter001.html", "OEBPS/chapter001.html"),
    ("./OPS/chapter-006.xml", "OPS/chapter-006.xml"),
    ("OPS//chapter-006.xml", "OPS/chapter-006.xml"),
    ("OPS/sub/../chapter-006.xml", "OPS/chapter-006.xml"),
    # already clean -> unchanged
    ("OPS/chapter-006.xml", "OPS/chapter-006.xml"),
])
def test_contained_traversal_normalizes(raw, expected):
    assert normalize_content_id(f"{UUID}!!{raw}", book_uuid=UUID) == f"{UUID}!!{expected}"


@pytest.mark.parametrize("raw", [
    "../outside.xml",              # escapes the container
    "OPS/../../outside.xml",       # escapes after descending
    "/OPS/chapter-006.xml",        # absolute
    "OPS\\chapter-006.xml",        # backslash
    "OPS/chapter-006.xml\n",       # control character
    "..",                          # bare traversal
])
def test_escaping_or_malformed_still_rejected(raw):
    with pytest.raises(ContentIdError):
        normalize_content_id(f"{UUID}!!{raw}", book_uuid=UUID)


def test_normalization_is_idempotent():
    once = normalize_content_id(f"{UUID}!!OPS/../OPS/chapter-017.xml", book_uuid=UUID)
    twice = normalize_content_id(once, book_uuid=UUID)
    assert once == twice
