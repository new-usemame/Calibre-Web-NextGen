# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure (context-free) JSON serializers for the /api/v1 surface."""

from datetime import date, datetime

from .. import constants
from ..clean_html import clean_string
from ..cover_version import COVER_VERSION_ARG, cover_version_token
from ..ui_themes import theme_slug
from ..user_preferences import serialize_named_preferences


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
    payload = {
        "id": user.id,
        "name": user.name,
        "locale": user.locale,
        "theme": theme_slug(user.theme),
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
            "browse_global": bool(
                getattr(user, "role_browse_global", lambda: False)()
            ),
            "passwd": user.role_passwd(),
            "anonymous": user.role_anonymous(),
        },
        # Fork #585: which sidebar entries the admin/user has enabled.
        "sidebar": serialize_sidebar_visibility(user),
        # Fork #585 v2: the user's saved sidebar order ([] = SPA default order).
        "sidebar_order": serialize_sidebar_order(user),
        # Generic per-user UI preferences. ``None`` means never set, which lets
        # the SPA adopt a pre-existing local value exactly once.
        "preferences": serialize_named_preferences(user),
        # Fork #866: marking a shelf "Kobo sync on" is inert until this account
        # setting is on. The shelf page warns about that, so /me carries the
        # flag — otherwise every shelf view would have to fetch the whole
        # account payload (app passwords, locale + language lists) for one bool.
        "kobo_only_shelves_sync": bool(getattr(user, "kobo_only_shelves_sync", False)),
    }
    mode = getattr(user, "library_mode", None)
    payload.update({
        "library_mode": (
            mode() if callable(mode)
            else (constants.LIBRARY_MODE_PERSONAL
                  if bool(getattr(user, "has_own_library", False))
                  else constants.LIBRARY_MODE_MONOLIBRARY)
        ),
        "my_library_seeded": bool(
            getattr(user, "user_library_seeded", False)
        ),
        "can_switch_library_mode": bool(
            getattr(user, "role_browse_global", lambda: False)()
        ),
        "library_mode_managed": not bool(
            getattr(user, "role_browse_global", lambda: False)()
        ),
        "show_my_library_intro": (
            not user.role_anonymous()
            and not bool(getattr(user, "my_library_intro_dismissed", False))
        ),
    })
    return payload


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


def _iso_datetime(value):
    """Serialize optional Calibre timestamps consistently for SPA consumers."""
    return value.isoformat() if isinstance(value, (datetime, date)) else None


def cover_url_for(book, resolution, cover_override=None):
    """Versioned ``/cover/<id>/<resolution>`` URL, or None when there is no cover.

    The SPA used to emit bare cover URLs, which is why cover responses could not
    be cached at all: with nothing in the URL naming a version, the only way a
    replaced cover could ever appear was to revalidate on every render. The
    token comes from ``cps.cover_version`` — the same function the classic UI's
    jinjia filters use and the same one ``helper`` validates against — so the
    two UIs and the server-side cache policy cannot drift apart.

    No token (an unusable ``last_modified``) means an UNVERSIONED URL, which the
    server answers with ``no-cache``. Degraded caching, never a stale image.
    """
    if cover_override is not None:
        from ..services import user_cover
        return user_cover.cover_url(cover_override)
    if not getattr(book, "has_cover", 0):
        return None
    url = f"/cover/{book.id}/{resolution}"
    version = cover_version_token(book)
    return f"{url}?{COVER_VERSION_ARG}={version}" if version else url


def serialize_book_list_item(book, read=False, archived=False, hidden=False,
                             in_progress=False, cover_override=None):
    series = book.series[0].name if getattr(book, "series", None) else None
    return {
        "id": book.id,
        "title": book.title,
        # Calibre escapes commas inside a single author name as '|' in the DB
        # ("William H. Keith, Jr." → "William H. Keith| Jr."); un-escape it so the
        # SPA cards show a comma, not a pipe (#730). Matches web.py / api/browse.py.
        "authors": [a.name.replace("|", ",") for a in book.authors] if getattr(book, "authors", None) else [],
        "series": series,
        "series_index": book.series_index,
        "cover_url": cover_url_for(book, "sm", cover_override),
        "formats": [d.format for d in book.data] if getattr(book, "data", None) else [],
        # Tag names for the table view's Tags column (#725). Flat strings to match
        # the list-item's other flat arrays (authors/formats); the detail
        # serializer keeps {id, name} for linking. fill_indexpage already
        # joinedload's Books.tags, so this adds no query.
        "tags": [t.name for t in book.tags] if getattr(book, "tags", None) else [],
        "date_added": _iso_datetime(getattr(book, "timestamp", None)),
        "last_modified": _iso_datetime(getattr(book, "last_modified", None)),
        "read": bool(read),
        "in_progress": bool(in_progress),
        "archived": bool(archived),
        "hidden": bool(hidden),
    }


def _serialize_custom_columns(book, definitions):
    result = []
    for column in definitions or []:
        values = getattr(book, "custom_column_{}".format(column.id), None) or []
        if not values:
            continue
        serialized_values = []
        for entry in values:
            value = getattr(entry, "value", None)
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
            item = {"value": value, "extra": getattr(entry, "extra", None)}
            if column.datatype == "comments" and isinstance(value, str):
                item["value_html"] = clean_string(value, getattr(book, "id", None))
                item["value"] = None
            serialized_values.append(item)
        result.append({
            "id": column.id,
            "label": column.label,
            "name": column.name,
            "datatype": column.datatype,
            "is_multiple": bool(column.is_multiple),
            "values": serialized_values,
        })
    return result


def serialize_book_detail(book, read=False, archived=False, favorited=False, hidden=False,
                          in_progress=False, custom_column_definitions=None,
                          original_filename=None, annotation_count=0,
                          cover_override=None):
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

    # Rating — Calibre stores 0–10 (half-star granularity: 9 → 4.5 stars), so
    # expose the raw value and let the UI render halves. None when unrated.
    # Mirrors the classic detail page's star block (detail.html:
    # entry.ratings[0].rating). first entry only, matching the model's uniqueness.
    ratings_list = getattr(book, "ratings", None) or []
    rating = ratings_list[0].rating if ratings_list else None

    # Cover. Deliberately NOT ``og``: ``web.get_cover`` maps ``og`` to
    # ``constants.COVER_THUMBNAIL_ORIGINAL``, which is 0 — falsy — so
    # ``helper.get_book_cover_internal``'s ``if resolution:`` branch is skipped
    # entirely and the raw library ``cover.jpg`` is served. On a real library
    # that is a ~1250x2000 JPEG (~280 KB) decoded to fill a 280 CSS-px column.
    #
    # The detail cover's column is a fixed 280px (BookDetail.module.css
    # `.layout`), narrowing to at most 34vw on phones. The thumbnail
    # resolutions ARE density multipliers — constants.COVER_THUMBNAIL_SMALL /
    # _MEDIUM / _LARGE are literally 1 / 2 / 4, and the classic UI's
    # `get_cover_srcset` already spells them as `1x`/`2x`/`4x` — so `sm` suits a
    # 1x screen and `md` a 2x one. The thumbnailer sizes by HEIGHT and keeps each
    # cover's own ratio, so the widths are approximate, not a guaranteed 280/560:
    # a 2:3 cover gives sm≈280 / md≈560, a squarer one more. `src` stays `md` as
    # the no-srcset fallback, since 2x is the common case.
    #
    # `lg` is deliberately NOT offered. Its target height is 4x the base (1680px),
    # which exceeds almost every cover in a real library, and the thumbnail task
    # only downscales when the source is taller — so for most books `lg` is the
    # ORIGINAL dimensions re-encoded, and handing it to a 280px column would undo
    # this fix. Measured on the local library: og 800x1104 JPEG / 104,775 B,
    # lg 800x1104 WebP / 83,102 B, md 608x840 WebP / 58,740 B.
    #
    # `og` remains a working route — this changes only what the page ASKS for.
    library_cover_url = cover_url_for(book, "md")
    cover_url = cover_url_for(book, "md", cover_override)
    cover_srcset = None
    if cover_url and cover_override is None:
        cover_1x = cover_url_for(book, "sm")
        cover_srcset = f"{cover_1x} 1x, {cover_url} 2x" if cover_1x else None

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
            "content_url": f"/show/{bid}/{fmt.lower()}",
        })

    return {
        "id": bid,
        "title": book.title,
        # Un-escape the Calibre '|' comma in author names (#730), same as the
        # list serializer above and every classic display path.
        "authors": [{"id": a.id, "name": a.name.replace("|", ",")}
                    for a in (getattr(book, "authors", None) or [])],
        "series": series,
        "series_index": book.series_index,
        "rating": rating,
        "cover_url": cover_url,
        "cover_srcset": cover_srcset,
        "using_my_cover": cover_override is not None,
        "library_cover_url": library_cover_url,
        "pubdate": pubdate_str,
        "date_added": _iso_datetime(getattr(book, "timestamp", None)),
        "last_modified": _iso_datetime(getattr(book, "last_modified", None)),
        "description_html": description_html,
        "tags": tags,
        "languages": languages,
        "publishers": publishers,
        "identifiers": identifiers,
        "custom_columns": _serialize_custom_columns(book, custom_column_definitions),
        "formats": formats,
        "original_filename": original_filename,
        "read": bool(read),
        "archived": bool(archived),
        "favorited": bool(favorited),
        "hidden": bool(hidden),
        "in_progress": bool(in_progress),
        "annotation_count": int(annotation_count or 0),
    }
