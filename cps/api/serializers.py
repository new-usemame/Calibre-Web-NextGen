# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure (context-free) JSON serializers for the /api/v1 surface."""

from .. import constants
from ..clean_html import clean_string


# Fork #585 (@Glennza1962 et al.): map the SPA sidebar's nav entries to the
# classic sidebar-visibility bits (constants.SIDEBAR_*). The classic UI hides
# entries an admin/user disabled via ``user.check_visibility(bit)`` on the
# ``sidebar_view`` bitmask; the new UI must honour the same config. Keys are
# stable, UI-agnostic identifiers the SPA filters its nav list by.
SIDEBAR_VISIBILITY_BITS = {
    "author": constants.SIDEBAR_AUTHOR,
    "series": constants.SIDEBAR_SERIES,
    "category": constants.SIDEBAR_CATEGORY,
    "publisher": constants.SIDEBAR_PUBLISHER,
    "language": constants.SIDEBAR_LANGUAGE,
    "rating": constants.SIDEBAR_RATING,
    "format": constants.SIDEBAR_FORMAT,
    "hot": constants.SIDEBAR_HOT,
    "random": constants.SIDEBAR_RANDOM,
    "best_rated": constants.SIDEBAR_BEST_RATED,
    "read_and_unread": constants.SIDEBAR_READ_AND_UNREAD,
    "archived": constants.SIDEBAR_ARCHIVED,
    "favorites": constants.SIDEBAR_FAVORITES,
    "download": constants.SIDEBAR_DOWNLOAD,
    "list": constants.SIDEBAR_LIST,
    "duplicates": constants.SIDEBAR_DUPLICATES,
}

# Fork #585 v2: the entries the SPA lets a user reorder in the Customize panel —
# the browse-by + discovery nav items (each backed by a visibility bit) plus the
# ``shelves`` block (always visible, only movable). Library / Upload / Admin /
# Table / Duplicates / Smart-shelves / Tasks / About keep fixed structural
# positions and are intentionally NOT reorderable. Order values POSTed to
# ``/account/sidebar`` are validated against this set.
ORDERABLE_SIDEBAR_KEYS = [
    "author", "series", "category", "publisher", "language", "rating", "format",
    "favorites", "hot", "random", "best_rated", "archived",
    "shelves",
]


def serialize_sidebar_visibility(user):
    """Return {key: bool} for each configurable sidebar entry, using the same
    ``check_visibility`` the classic UI + OPDS use. Degrades to all-visible when
    the object has no ``check_visibility`` (keeps the serializer pure/testable
    and never over-hides on an unexpected shape)."""
    check = getattr(user, "check_visibility", None)
    if not callable(check):
        return {key: True for key in SIDEBAR_VISIBILITY_BITS}
    return {key: bool(check(bit)) for key, bit in SIDEBAR_VISIBILITY_BITS.items()}


def serialize_sidebar_order(user):
    """Return the user's saved sidebar order (list of keys), or [] when unset.
    Reads ``view_settings['sidebar']['order']`` via ``get_view_property``; stays
    tolerant of objects without the helper (returns [])."""
    getter = getattr(user, "get_view_property", None)
    if not callable(getter):
        return []
    try:
        order = getter("sidebar", "order")
    except Exception:
        # view_settings not yet a usable dict (fresh/unmigrated row) → default
        # order. The serializer must never 500 on a read.
        return []
    return order if isinstance(order, list) else []


def serialize_user(user):
    return {
        "id": user.id,
        "name": user.name,
        "locale": user.locale,
        "theme": user.theme,
        "ui_font_body": user.ui_font_body or "",
        "ui_font_display": user.ui_font_display or "",
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
        # Fork #585: which sidebar entries the admin/user has enabled.
        "sidebar": serialize_sidebar_visibility(user),
        # Fork #585 v2: the user's saved sidebar order ([] = SPA default order).
        "sidebar_order": serialize_sidebar_order(user),
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


def serialize_book_detail(book, read=False, archived=False, favorited=False, hidden=False,
                          in_progress=False):
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
        try:
            label = i.format_type() if hasattr(i, "format_type") else i.type
        except Exception:
            label = i.type
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
        "in_progress": bool(in_progress),
    }
