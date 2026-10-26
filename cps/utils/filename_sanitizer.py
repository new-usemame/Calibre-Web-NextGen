# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Shared filename sanitizer to mirror Calibre‑Web's behavior
# SPDX-License-Identifier: GPL-3.0-or-later

import re
from typing import Optional
from urllib.parse import quote

try:
    import unidecode  # type: ignore
except Exception:  # pragma: no cover
    unidecode = None  # will gate usage below

# Same regex as cps/string_helper.strip_whitespaces
_ZW_TRIM_RE = re.compile(r"(^[\s\u200B-\u200D\ufeff]+)|([\s\u200B-\u200D\ufeff]+$)")


def strip_whitespaces(text: str) -> str:
    return _ZW_TRIM_RE.sub("", text)


def get_valid_filename_shared(value: str,
                               replace_whitespace: bool = True,
                               chars: int = 128,
                               unicode_filename: bool = False) -> str:
    """Mirror cps.helper.get_valid_filename but without relying on CPS config.
    - unicode_filename: if True, transliterate using unidecode to ASCII.
    - chars: max length in UTF-8 safe truncation.
    """
    if not isinstance(value, str):
        value = str(value) if value is not None else ""

    if value[-1:] == '.':
        value = value[:-1] + '_'

    value = value.replace("/", "_").replace(":", "_").strip('\0')

    if unicode_filename and unidecode is not None:
        value = unidecode.unidecode(value)

    if replace_whitespace:
        #  *+:\"/<>? are replaced by _
        value = re.sub(r'[*+:\\\"/<>?]+', '_', value, flags=re.U)
        # pipe has to be replaced with comma
        value = re.sub(r'[|]+', ',', value, flags=re.U)

    # utf-8 safe trimming
    value = strip_whitespaces(value.encode('utf-8')[:chars].decode('utf-8', errors='ignore'))

    if not value:
        raise ValueError("Filename cannot be empty")
    return value


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def build_content_disposition(disposition: str, filename: str) -> str:
    """Return an RFC 6266 Content-Disposition value naming ``filename``.

    ``filename*`` carries the real (UTF-8, percent-encoded) name; ``filename``
    is a plain-ASCII fallback for clients that ignore ``filename*``. Control
    characters are dropped so a title can never inject a header line, and the
    fallback never contains a quote or backslash.
    """
    filename = _CONTROL_CHARS_RE.sub("", filename or "")
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    fallback = filename
    if unidecode is not None:
        fallback = unidecode.unidecode(filename)
    fallback = fallback.encode("ascii", "ignore").decode("ascii")
    fallback = fallback.replace("\\", "_").replace('"', "'").strip()
    if not fallback or fallback == "." + ext:
        fallback = "book" + ("." + ext if ext else "")
    return "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
        disposition, fallback, quote(filename, safe=""))
