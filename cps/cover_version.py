# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Single source of truth for a book cover's cache-version token.

Cover responses may be cached for a year, and the only thing that makes that
safe is that the URL — not the stored copy — is what changes when the cover
changes. That requires exactly one definition of "which version of this book's
cover is this", shared by everything that *builds* a cover URL (the jinjia
filters for the classic UI, the ``/api/v1`` serializers for the SPA) and by the
one place that *validates* one (``helper.get_book_cover_internal``). If those
definitions drift, a URL that looks versioned stops matching and either loses
its caching or — worse — pins bytes it does not name.

Deliberately a leaf module: no Flask, no SQLAlchemy, no ``cps`` imports. That is
what lets ``cps/api/serializers.py`` stay context-free and lets the ingest
subprocess use it too.

Resolution is MICROSECONDS, not seconds. ``mark_book_modified`` stamps
``datetime.now(timezone.utc)``, so two cover replacements inside the same wall
clock second are entirely possible (a picker retry, a script). At second
resolution both would produce the same token, and with a year-long lifetime the
first image would stay on screen indefinitely — the exact failure the versioning
exists to prevent.

Resolution alone is not enough, and this module cannot supply the rest:
``datetime.now()`` may return the SAME microsecond on successive calls
(observed on the development machine). Uniqueness for sequential writes is
``mark_book_modified``'s job — it advances past a same-instant collision — and
this module's contract is only that equal timestamps map to equal tokens and
distinct ones to distinct tokens, exactly, at every point in the datetime range.
"""

from datetime import datetime, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The query parameter every cover URL carries its version in.
COVER_VERSION_ARG = "c"


def cover_version_token(book):
    """Return ``book``'s canonical cover-version token, or ``None``.

    ``None`` means "this row cannot be versioned" (no timestamp, or an
    unusable one). Callers must then emit an UNVERSIONED URL, which the server
    answers with ``no-cache`` — degraded caching, never a stale image.

    Naive values are read as UTC: Calibre stores UTC wall time in
    ``books.last_modified``, and letting ``datetime.timestamp()`` apply the
    process's local zone would make the same row produce different tokens on
    two servers, or after a TZ change.
    """
    last_modified = getattr(book, "last_modified", None)
    if last_modified is None:
        return None
    try:
        if last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
        # Integer timedelta arithmetic, not ``int(timestamp() * 1_000_000)``:
        # that multiplication rounds through a float and makes adjacent
        # microseconds collide at the extremes of the datetime range. The token
        # is a cache key for an immutable response, so "close enough" is exactly
        # the wrong property.
        delta = last_modified - _EPOCH
        return str(delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds)
    except (AttributeError, TypeError, ValueError, OSError, OverflowError):
        return None
