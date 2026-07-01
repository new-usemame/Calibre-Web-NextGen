# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure (context-free) JSON serializers for the /api/v1 surface."""

from ..clean_html import clean_string


def serialize_user(user):
    return {
        "id": user.id,
        "name": user.name,
        "locale": user.locale,
        "theme": user.theme,
        "role": {
            "admin": user.role_admin(),
            "upload": user.role_upload(),
            "edit": user.role_edit(),
            "download": user.role_download(),
            "delete_books": user.role_delete_books(),
            "edit_shelfs": user.role_edit_shelfs(),
            "viewer": user.role_viewer(),
            "passwd": user.role_passwd(),
        },
    }


def serialize_shelf(shelf, count, is_owner):
    """Serialize a Shelf for the list/detail API. ``count`` (archive-aware book
    count) and ``is_owner`` are computed by the caller — the serializer stays
    pure of DB/Flask so it's trivially testable."""
    return {
        "id": shelf.id,
        "name": shelf.name,
        "is_public": bool(shelf.is_public),
        "is_owner": bool(is_owner),
        "kobo_sync": bool(getattr(shelf, "kobo_sync", False)),
        "count": count,
    }


def serialize_book_list_item(book, read=False, archived=False):
    series = book.series[0].name if getattr(book, "series", None) else None
    return {
        "id": book.id,
        "title": book.title,
        "authors": [a.name for a in book.authors] if getattr(book, "authors", None) else [],
        "series": series,
        "series_index": book.series_index,
        "cover_url": f"/cover/{book.id}/sm" if getattr(book, "has_cover", 0) else None,
        "formats": [d.format for d in book.data] if getattr(book, "data", None) else [],
        "read": bool(read),
        "archived": bool(archived),
    }


def serialize_book_detail(book, read=False, archived=False, favorited=False, hidden=False):
    """Full detail serializer — pure, no Flask/DB imports.

    Callers must enrich each language object with a ``.language_name`` attribute
    before calling (``l.language_name = isoLanguages.get_language_name(...)``).
    Falls back to ``l.lang_code`` via ``getattr`` so the function stays testable
    without that enrichment.
    """
    bid = book.id

    # Series (first entry only) — {id, name} so the UI can link to the series view
    series_list = getattr(book, "series", None) or []
    series = ({"id": series_list[0].id, "name": series_list[0].name}
              if series_list else None)

    # Cover
    cover_url = f"/cover/{bid}/og" if getattr(book, "has_cover", 0) else None

    # Pubdate — sentinel year <= 101 → null
    pubdate_raw = getattr(book, "pubdate", None)
    if pubdate_raw is not None and getattr(pubdate_raw, "year", 0) > 101:
        pubdate_str = pubdate_raw.date().isoformat()
    else:
        pubdate_str = None

    # Description — sanitize stored comment HTML with the same allowlist the
    # rest of the app uses (clean_html.clean_string, via bleach/nh3). The
    # comments field is edit-user- and metadata-provider-sourced, NOT trusted,
    # so the API must never emit raw HTML (stored XSS otherwise). Mirrors
    # detail.html's `entry.comments[0].text|clean_string|safe`.
    comments = getattr(book, "comments", None) or []
    description_html = clean_string(comments[0].text, bid) if comments else None

    # Tags — {id, name} for linking
    tags = [{"id": t.id, "name": t.name} for t in (getattr(book, "tags", None) or [])]

    # Languages — {id (lang_code), name (display)}; name enriched by caller,
    # falls back to lang_code so the serializer stays pure/testable
    languages = [
        {"id": l.lang_code, "name": getattr(l, "language_name", None) or l.lang_code}
        for l in (getattr(book, "languages", None) or [])
    ]

    # Publishers — {id, name} for linking
    publishers = [{"id": p.id, "name": p.name} for p in (getattr(book, "publishers", None) or [])]

    # Identifiers — expose a clickable link (Goodreads, StoryGraph, Hardcover,
    # Amazon, ISBN…) and a display label, mirroring the classic detail page (#582).
    # The link is the model's own URL rule (Identifiers.__repr__), but only emitted
    # when it's a real http(s) URL — never a javascript:/data:/raw-value repr — so
    # a crafted identifier can't inject a dangerous href. Non-linkable IDs stay as
    # plain text (url=None).
    identifiers = []
    for i in (getattr(book, "identifiers", None) or []):
        try:
            link = repr(i)
        except Exception:
            link = None
        url = link if (link and (link.startswith("http://") or link.startswith("https://"))) else None
        label = i.format_type() if hasattr(i, "format_type") else i.type
        identifiers.append({"type": i.type, "val": i.val, "url": url, "label": label})

    # Formats
    formats = []
    for d in (getattr(book, "data", None) or []):
        fmt = d.format
        formats.append({
            "format": fmt,
            "size_bytes": d.uncompressed_size,
            "download_url": f"/download/{bid}/{fmt.lower()}/{d.name}",
            "read_url": f"/read/{bid}/{fmt.lower()}",
        })

    return {
        "id": bid,
        "title": book.title,
        "authors": [{"id": a.id, "name": a.name}
                    for a in (getattr(book, "authors", None) or [])],
        "series": series,
        "series_index": book.series_index,
        "cover_url": cover_url,
        "pubdate": pubdate_str,
        "description_html": description_html,
        "tags": tags,
        "languages": languages,
        "publishers": publishers,
        "identifiers": identifiers,
        "formats": formats,
        "read": bool(read),
        "archived": bool(archived),
        "favorited": bool(favorited),
        "hidden": bool(hidden),
    }
