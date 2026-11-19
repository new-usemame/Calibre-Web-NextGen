# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""ISBN check digits.

Provider records and book files carry ISBNs that fail their own check digit:
typos, placeholder values such as 9780000000000, an ASIN or internal id
stored under ``isbn``. Two such values can still be equal, which is enough
to pick the wrong edition, and the same value is then written into the
library. ``isbn13`` gives one comparable form for a valid ISBN-10 or ISBN-13
and None for anything else.
"""
import re

_SEPARATORS = re.compile(r"[\s\-‐-―]")
_ISBN10 = re.compile(r"[0-9]{9}[0-9X]")
_ISBN13 = re.compile(r"97[89][0-9]{10}")
ISBN_IDENTIFIER_TYPES = ("isbn", "isbn13", "isbn_13", "isbn10", "isbn_10")


def _clean(value):
    text = _SEPARATORS.sub("", str(value or "")).upper()
    if text.startswith("ISBN"):
        text = text[4:].lstrip(":")
    return text


def _isbn10_valid(text):
    total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(text))
    return total % 11 == 0


def _isbn13_check(first12):
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(first12))
    return str((10 - total % 10) % 10)


def normalize(value):
    """Clean a checksum-valid ISBN, preserving its 10- or 13-digit form.

    Hyphens, spaces and a leading "ISBN" are ignored. Other characters are
    rejected rather than stripped into a different, apparently valid value.
    """
    text = _clean(value)
    if _ISBN13.fullmatch(text):
        return text if _isbn13_check(text[:12]) == text[12] else None
    if _ISBN10.fullmatch(text) and _isbn10_valid(text):
        return text
    return None


def isbn13(value):
    """The ISBN-13 form of a valid ISBN-10 or ISBN-13, else None.

    An ISBN-10 is converted by its 978 prefix, so the two forms of one
    edition compare equal. Formatting follows ``normalize``.
    """
    text = normalize(value)
    if text is None or len(text) == 13:
        return text
    first12 = "978" + text[:9]
    return first12 + _isbn13_check(first12)


def is_valid(value):
    return isbn13(value) is not None
