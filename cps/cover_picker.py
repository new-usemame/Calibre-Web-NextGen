# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Flask blueprint for the focused cover-picker UI.

A user-facing surface dedicated to setting/replacing a book's cover. The
existing metadata-search modal stays untouched; this is a separate path
for users who only want to swap a cover. See notes/COVER-PICKER-DESIGN.md
for the full rationale.

Routes (all gated by edit permission):

    GET  /book/<book_id>/cover                  -> picker page
    POST /book/<book_id>/cover/candidates       -> JSON: gather candidates
    POST /book/<book_id>/cover/preview          -> JSON: validate URL
    POST /book/<book_id>/cover/extract          -> JSON: data-URL of embedded
    POST /book/<book_id>/cover/apply            -> applies chosen cover
    POST /book/<book_id>/cover/lock             -> toggle BookCoverLock

The cover designer's own surface is book-independent and lives beside them:

    GET    /cover-designer/catalogue            -> JSON: every design choice
    GET    /cover-designer/style-thumb/<id>     -> JPEG: sample of one arrangement
    GET    /cover-designer/font-sample/<id>     -> JPEG: sample of one lettering
    GET    /cover-designer/presets              -> JSON: builtin + library + own
    POST   /cover-designer/presets              -> saves a design under a name
    PUT    /cover-designer/presets/<id>         -> renames/replaces a saved design
    DELETE /cover-designer/presets/<id>         -> deletes it, or hides one that
                                                   is not this reader's to delete
    POST   /cover-designer/presets/<id>/restore -> un-hides one they hid

The blueprint is thin — orchestration lives in cps.services.cover_picker
and cps.services.cover_url_validator. Adding a new candidate source
means adding a metadata provider in cps/metadata_provider/, not editing
this file.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlparse
from functools import wraps
from typing import Optional

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, request, url_for
from werkzeug.routing import BuildError
from flask_babel import gettext as _
from flask_babel import get_locale

from . import calibre_db, config, helper, kobo_sync_status, logger, ub
from .cw_login import current_user
from .render_template import render_title_template
from .services import (
    cover_design_presets, cover_designer_cache, cover_extract, cover_generator, cover_preview,
    cover_picker as cover_picker_svc, cover_url_validator,
)
from .usermanagement import user_login_required


log = logger.create()

cover_picker = Blueprint("cover_picker", __name__)


def edit_required(f):
    """Mirrors editbooks.edit_required — admin or edit-role only."""
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_edit() or current_user.role_admin():
            return f(*args, **kwargs)
        abort(403)
    return inner


def cover_source_required(f):
    """Allow personal source browsing without granting global cover writes."""
    @wraps(f)
    def inner(*args, **kwargs):
        if request.args.get("scope") == "personal":
            return f(*args, **kwargs)
        if current_user.role_edit() or current_user.role_admin():
            return f(*args, **kwargs)
        abort(403)
    return inner


def _load_book(book_id: int):
    """Fetch book + 404 if absent. Matches editbooks.py conventions."""
    personal = request.args.get("scope") == "personal"
    global_editor = bool(current_user.role_edit() or current_user.role_admin())
    book = calibre_db.get_filtered_book(
        book_id,
        allow_show_archived=True,
        allow_show_hidden=True,
        allow_show_global=(personal and bool(current_user.role_browse_global())) or global_editor,
    )
    if not book:
        abort(404)
    return book


def _book_query_for_search(book) -> str:
    """Build the metadata-search query the picker fires off behind the scenes.

    Title + first author, never a bare ISBN. Most sources are text catalogues
    or scrapers that cannot resolve an ISBN they do not stock: measured on the
    household instance (2026-09-10, same book, 15 sources) the stored ISBN made
    3 sources answer with 17 candidates, the title and author made 6 answer
    with 61. The ISBNs and ASINs still reach the lookups built for them
    (``book_isbns`` / ``book_asins`` feed the Amazon CDN probe)."""
    title = book.title or ""
    authors = [a.name for a in (book.authors or [])]
    return (title + " " + (authors[0] if authors else "")).strip()


def _book_isbns(book) -> list[str]:
    """Return every stored ISBN identifier for cover-source lookups."""
    return [
        i.val for i in (book.identifiers or [])
        if (i.type or "").lower() in ("isbn", "isbn_10", "isbn_13") and i.val
    ]


def _book_asins(book) -> list[str]:
    """Return every stored Amazon identifier for cover-source lookups.

    A Kindle edition commonly carries an ASIN and no ISBN, so without this the
    book gets no high-resolution Amazon candidate at all (fork #304). Calibre
    writes territory ids as amazon_uk / amazon_de / ..., matched as a family.
    """
    asins: list[str] = []
    for identifier in (book.identifiers or []):
        name = (identifier.type or "").strip().lower()
        if not identifier.val:
            continue
        if name in ("amazon", "asin", "amazon_asin", "mobi-asin") or name.startswith("amazon_"):
            asins.append(identifier.val)
    return asins


def _is_provider_enabled_for_user(provider) -> bool:
    """Honor both per-user and global provider toggles, same as
    cps.search_metadata.metadata_search."""
    try:
        from .search_metadata import _get_global_provider_enabled_map
        global_enabled = _get_global_provider_enabled_map()
    except Exception:
        global_enabled = {}
    user_settings = current_user.view_settings.get("metadata", {}) if current_user else {}
    return bool(global_enabled.get(provider.__id__, True)) and bool(user_settings.get(provider.__id__, True))


@cover_picker.route("/book/<int:book_id>/cover", methods=["GET"])
@user_login_required
@edit_required
def cover_picker_page(book_id):
    """Render the picker page. Candidates are loaded asynchronously so
    the page itself returns instantly; the JS hits the candidates
    endpoint immediately on load.

    The back link returns the user to wherever they opened the picker from:
    the edit-metadata page when they came from there (``?origin=edit``), and
    the book detail page otherwise — which is both the usual entry point (the
    cover on the detail page) and the safe default for a direct/bookmarked hit
    (fork #26)."""
    book = _load_book(book_id)
    locked = _get_lock_state(book_id)
    if request.args.get("origin") == "edit":
        back_url = url_for("edit-book.show_edit_book", book_id=book_id)
        back_label = _(u"Back to edit metadata")
    else:
        back_url = url_for("web.show_book", book_id=book_id)
        back_label = _(u"Back to book")
    return render_title_template(
        "cover_picker.html",
        book=book,
        cover_locked=locked,
        config=config,
        back_url=back_url,
        back_label=back_label,
        title=_(u"Change cover — %(title)s", title=book.title),
        page="coverpicker",
    )


@cover_picker.route("/book/<int:book_id>/cover/state", methods=["GET"])
@user_login_required
@edit_required
def cover_picker_state(book_id):
    """Bootstrap state for the SPA cover picker: the current lock state plus
    whether the e-reader padding preview is available and its admin defaults.
    Title/authors/current-cover come from /api/v1/books/<id>; this supplies the
    picker-specific bits the standard book payload doesn't carry. Read-only."""
    _load_book(book_id)  # 404s if missing/hidden
    return jsonify({
        "locked": _get_lock_state(book_id),
        "ereader_enabled": bool(config.config_kobo_cover_padding_enabled),
        "ereader_defaults": {
            "aspect": config.config_kobo_cover_padding_aspect or "kobo_libra_color",
            "fill_mode": config.config_kobo_cover_padding_fill_mode or "edge_mirror",
            "color": config.config_kobo_cover_padding_color or "",
        },
        "designer": designer_state(),
    })


def _binaries_dir() -> str:
    return getattr(config, "config_binariesdir", "") or ""


def _catalogue_url(endpoint: str, key: str, value: str, path: str) -> str:
    """Where one catalogue image lives.

    ``url_for`` when there is an application that knows this blueprint, because
    an installation mounted under a sub-path needs its script root prepended.
    When there is not — ``designer_state()`` is also read by the personal-cover
    payload and by callers that only want to know whether this box can render at
    all — the literal path the blueprint registers is the honest answer. A
    catalogue is worth more than a perfectly prefixed URL nobody will fetch.
    """
    try:
        return url_for(endpoint, **{key: value})
    except (RuntimeError, BuildError):
        return path + quote(str(value), safe="")


def _style_thumb_url(style_id: str) -> str:
    return _catalogue_url("cover_picker.cover_designer_style_thumb", "style_id", style_id,
                          "/cover-designer/style-thumb/")


def _font_sample_url(font_id: str) -> str:
    return _catalogue_url("cover_picker.cover_designer_font_sample", "font_id", font_id,
                          "/cover-designer/font-sample/")


def designer_state() -> dict:
    """The "Design a cover" vocabulary plus whether this box can render at all.

    Shared with the personal-cover payload in cps/api/actions.py so both pickers
    offer exactly the same designs. ``available`` is false on an installation
    with neither Calibre nor Pillow, and the panel stays hidden rather than
    offering a button that can only fail.

    The preset list is per-user: the shipped designs this reader has not hidden,
    then the library's, then their own.
    """
    binaries = _binaries_dir()
    availability = cover_generator.renderer_availability(binaries)
    saved = _presets_for_current_user(binaries)
    catalogue = cover_generator.catalogue(
        binaries,
        extra_presets=[entry for entry in saved["presets"] if not entry.get("builtin")],
        hidden_builtins=saved["hidden"],
        thumb_url=_style_thumb_url,
        sample_url=_font_sample_url,
    )
    catalogue["default_preset"] = (
        getattr(config, "config_cover_generator_default_preset", None)
        or cover_generator.DEFAULT_PRESET
    )
    if catalogue["default_preset"] not in cover_generator.PRESETS:
        catalogue["default_preset"] = cover_generator.DEFAULT_PRESET
    catalogue["hidden_presets"] = saved["hidden"]
    catalogue["can_share_presets"] = bool(getattr(current_user, "role_admin", lambda: False)())
    catalogue["available"] = availability["available"]
    catalogue["renderer"] = availability["renderer"]
    return catalogue


def _presets_for_current_user(binaries: str) -> dict:
    """Saved presets for whoever is asking, or just the builtins if nobody is.

    A database that has not been migrated yet (or a read that fails) must not
    take the designer down with it: the shipped designs are enough to open the
    panel with, so a failure here degrades to them.
    """
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return {"presets": cover_generator.builtin_presets(binaries), "hidden": []}
    try:
        return cover_design_presets.list_presets(int(user_id), binaries)
    except Exception as error:  # noqa: BLE001 - a preset table is not worth a 500
        log.warning("cover designer: could not read saved presets: %s", error)
        return {"presets": cover_generator.builtin_presets(binaries), "hidden": []}


@cover_picker.route("/book/<int:book_id>/cover/candidates", methods=["POST"])
@user_login_required
@cover_source_required
def cover_picker_candidates(book_id):
    """Run the provider pool + cover_booster; return candidate list +
    per-provider status. Same JSON shape pattern as /metadata/search."""
    book = _load_book(book_id)
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip() or _book_query_for_search(book)

    from .search_metadata import (
        cl as providers,
        _classify_empty_provider,
        _classify_provider_failure,
    )

    static_cover = url_for("static", filename="generic_cover.svg")

    candidates, statuses = cover_picker_svc.gather_cover_candidates(
        providers=providers,
        query=query,
        static_cover=static_cover,
        locale=get_locale(),
        is_provider_enabled=_is_provider_enabled_for_user,
        classify_failure=_classify_provider_failure,
        classify_empty=_classify_empty_provider,
        extract_embedded=lambda: cover_extract.extract_embedded_cover(book),
        book_isbns=_book_isbns(book),
        book_asins=_book_asins(book),
    )

    return jsonify({
        "candidates": [c.to_dict() for c in candidates],
        "providers": [s.to_dict() for s in statuses],
        "query": query,
    })


@cover_picker.route("/book/<int:book_id>/cover/preview", methods=["POST"])
@user_login_required
@cover_source_required
def cover_picker_preview(book_id):
    """Validate a URL the user pasted (in the picker URL panel OR the
    inline cover_url field on the edit page). Same code path; the inline
    field hits /metadata/cover/preview for the global variant."""
    _load_book(book_id)  # 404s if book is missing or hidden
    body = request.get_json(silent=True) or {}
    result = cover_url_validator.validate_cover_url(body.get("url") or "")
    return jsonify(result.to_dict())


@cover_picker.route("/book/<int:book_id>/cover/extract", methods=["POST"])
@user_login_required
@cover_source_required
def cover_picker_extract(book_id):
    """Re-render the embedded-cover candidate as a data URL. Used when
    the picker page wants to refresh just the embedded cover after an
    upload changed the book file."""
    book = _load_book(book_id)
    extracted = cover_extract.extract_embedded_cover(book)
    if extracted is None:
        return jsonify({"available": False})
    data_url = "data:" + extracted.mime_type + ";base64," + base64.b64encode(extracted.data).decode("ascii")
    return jsonify({
        "available": True,
        "cover_url": data_url,
        "source_format": extracted.source_format,
    })


@cover_picker.route("/book/<int:book_id>/cover/apply", methods=["POST"])
@user_login_required
@edit_required
def cover_picker_apply(book_id):
    """Apply a chosen cover. Accepts a JSON payload describing the
    source: a remote URL, a candidate from the grid (just a URL really),
    an uploaded file (multipart), or 'embedded' to apply the embedded
    cover from the book file.

    Returns JSON {ok, error?} for AJAX callers; the picker page swaps
    the preview image without a full reload on success.
    """
    book = _load_book(book_id)
    if _get_lock_state(book_id):
        return _json_error("locked", _(u"This book's cover is locked. Unlock it first."), 409)

    # Multipart upload from the picker's file panel.
    if request.files.get("file"):
        staged_cover, message = _apply_uploaded_file(book, request.files["file"])
        return _apply_response(staged_cover, message, book)

    body = request.get_json(silent=True) or {}
    kind = body.get("kind") or "url"

    if kind == "url":
        url = (body.get("url") or "").strip()
        if not url:
            return _json_error("empty_url", _(u"Provide a cover URL."), 400)
        # A Google Images results link is applied as the image behind it,
        # the same way the preview validated it — API clients that skip
        # the preview get the same unwrapping.
        url = cover_url_validator.resolve_pasted_cover_url(url)
        staged_cover, message = helper.save_cover_from_url(url, book.path)
        return _apply_response(staged_cover, message, book)

    if kind == "embedded":
        extracted = cover_extract.extract_embedded_cover(book)
        if extracted is None:
            return _json_error("no_embedded", _(u"This book doesn't have an embedded cover we can extract."), 400)
        staged_cover, message = _apply_bytes(book, extracted.data, extracted.extension)
        return _apply_response(staged_cover, message, book)

    if kind == "generated":
        # The body carries design *ids* only. The cover is re-rendered here from
        # the book's own stored metadata, so an image the client fabricated (or a
        # preview that has since drifted from the book's title) can never become
        # the stored cover.
        try:
            spec = _spec_from_body(body, cover_generator.APPLY_WIDTH, cover_generator.APPLY_HEIGHT)
            rendered = cover_preview._run_in_pool(
                cover_generator.render, _book_cover_meta(book), spec, _binaries_dir(),
            )
        except cover_generator.CoverGenerationError as error:
            return _designer_json_error(error)
        staged_cover, message = _apply_bytes(book, rendered.data, ".jpg")
        return _apply_response(staged_cover, message, book)

    return _json_error("bad_kind", _(u"Unknown cover source."), 400)


@cover_picker.route("/book/<int:book_id>/cover/design-preview", methods=["POST"])
@user_login_required
@cover_source_required
def cover_picker_design_preview(book_id):
    """Render a designed cover and return it as a data URL.

    Read-only: nothing is written until the user applies. The preview renders at
    half the applied size so the round trip stays interactive; the design is
    identical, only the pixel count differs.

    Body: ``{"design": {...}}`` — the whole design object (style, scheme or four
    explicit colours, per-slot font/size/alignment/template, size), every field
    optional and the defaults supplying the rest. The v1 body
    ``{"preset", "scheme", "font", "layout"}`` still works and is mapped onto a
    design, so an older client keeps its covers.
    """
    book = _load_book(book_id)
    body = request.get_json(silent=True) or {}
    try:
        spec = _spec_from_body(body)
        # The design keeps the size the reader chose; only the picture shrinks,
        # and font sizes scale with it, so the preview is the same cover with
        # fewer pixels rather than a different one.
        data_url, renderer = cover_preview._run_in_pool(
            cover_generator.render_data_url, _book_cover_meta(book),
            spec.scaled(cover_generator.PREVIEW_WIDTH, cover_generator.PREVIEW_HEIGHT),
            _binaries_dir(),
        )
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    resolved = spec.to_dict()
    return jsonify({"ok": True, "data_url": data_url, "renderer": renderer,
                    "design": resolved, "resolved": resolved})


def _spec_from_body(body: dict, width: Optional[int] = None, height: Optional[int] = None):
    """Resolve a request body into a design. Raises CoverGenerationError.

    Both vocabularies are accepted: the designer sends a whole ``design`` object,
    while an older client (and the admin default) sends preset/scheme/font/layout
    ids. The design wins where they overlap, so a client can send both while it
    migrates.
    """
    return cover_generator.resolve_spec(
        design=(body.get("design") if isinstance(body.get("design"), dict) else None),
        preset=(body.get("preset") or None),
        scheme=(body.get("scheme") or None),
        font=(body.get("font") or None),
        layout=(body.get("layout") or None),
        width=width,
        height=height,
        binaries_dir=_binaries_dir(),
    )


def _designer_error(error):
    """Map a renderer failure onto (code, user-facing message, HTTP status).

    The renderer's own message quotes the helper's stderr, which carries server
    paths and Calibre internals. It is logged here and never returned: every
    caller — this blueprint and the personal-cover route in cps/api/actions.py —
    sends the user one of these three sentences instead.
    """
    log.warning("cover designer request failed: %s: %s", error.code, error.message)
    if error.code in ("unknown_scheme", "unknown_font", "unknown_layout", "unknown_style"):
        return error.code, _(u"That cover design is not one we offer."), 400
    # These carry a sentence this module wrote about what the reader asked for —
    # a placeholder that does not exist, a colour that is not a colour — and it
    # is far more use than a generic refusal, so it goes back verbatim. It is
    # written here, never by the renderer: no subprocess output reaches a client.
    # (Source English; these are not in the SPA's msgid catalogue yet.)
    if error.code in ("invalid_design", "invalid_color", "invalid_align", "invalid_text",
                      "invalid_name", "duplicate_name", "too_many_presets", "builtin_preset"):
        return error.code, error.message, 400
    if error.code == "forbidden":
        return error.code, error.message, 403
    if error.code == "unknown_preset":
        return error.code, _(u"That preset no longer exists."), 404
    if error.code == "unavailable":
        return error.code, _(u"This server can't design covers — no renderer is installed."), 503
    if error.code == "storage_failed":
        return error.code, _(u"Could not save that design."), 500
    return error.code, _(u"Could not design a cover for this book."), 502


def _designer_json_error(error):
    """The designer's error body: the contract's shape and the picker's, at once.

    The contract the SPA panel is written against says ``{"error", "message"}``;
    every other route in this blueprint says ``{"error_code", "error_message"}``
    and the existing client reads that. Sending both keys costs a few bytes and
    means neither client has to guess which route it is talking to.
    """
    code, message, status = _designer_error(error)
    return make_response(jsonify({
        "ok": False, "error": code, "message": message,
        "error_code": code, "error_message": message,
    }), status)


def _first_name(book, attribute: str, field: str = "name"):
    """The first related row's *field*, or None. Never raises."""
    try:
        related = getattr(book, attribute, None) or []
        value = getattr(related[0], field, None) if related else None
    except Exception:  # noqa: BLE001 - a malformed row must not fail an apply
        return None
    return str(value) if value else None


def _book_year(book):
    """The publication year, or None for a book that does not claim one."""
    pubdate = getattr(book, "pubdate", None)
    year = getattr(pubdate, "year", None)
    if not isinstance(year, int):
        return None
    # Calibre writes 0101-01-01 for "no publication date"; a cover that
    # announces the year 101 is worse than one that says nothing.
    return year if year > 1400 else None


def _book_rating(book):
    """The rating out of five, or None. Calibre stores 0-10 (half stars)."""
    try:
        ratings = getattr(book, "ratings", None) or []
        raw = getattr(ratings[0], "rating", None) if ratings else None
        return float(raw) / 2.0 if raw is not None else None
    except (TypeError, ValueError, IndexError):
        return None


def _book_cover_meta(book):
    """The book's own text, as the renderer's input.

    Everything here is best-effort: a book with a malformed series index or a
    rating row that is not a number still gets a cover, just without that line.
    A designed cover is never important enough to fail an apply over.
    """
    series = _first_name(book, "series")
    series_index = None
    if series is not None:
        try:
            raw_index = getattr(book, "series_index", None)
            series_index = float(raw_index) if raw_index is not None else None
        except (TypeError, ValueError):
            series_index = None
    try:
        authors = [str(a.name) for a in (getattr(book, "authors", None) or []) if getattr(a, "name", None)]
    except Exception:  # noqa: BLE001
        authors = []
    try:
        tags = [str(t.name) for t in (getattr(book, "tags", None) or []) if getattr(t, "name", None)]
    except Exception:  # noqa: BLE001
        tags = []
    title = getattr(book, "title", None)
    return cover_generator.BookCoverMeta(
        title=str(title) if title else "",
        authors=authors,
        series=series,
        series_index=series_index,
        publisher=_first_name(book, "publishers"),
        year=_book_year(book),
        tags=tags,
        language=_first_name(book, "languages", "lang_code"),
        rating=_book_rating(book),
    )


# ---------------------------------------------------------------------------
# The cover designer's own surface: catalogue, imagery and saved presets.
# These are book-independent, so they live off /cover-designer/ rather than
# under a book, and the panel can open them once instead of per book.
# ---------------------------------------------------------------------------

@cover_picker.route("/cover-designer/catalogue", methods=["GET"])
@user_login_required
def cover_designer_catalogue():
    """Every design choice this installation offers, for this user."""
    return jsonify(designer_state())


def _catalogue_image(kind: str, identifier: str, render):
    """Serve one small catalogue image, cached on disk.

    Every user gets the same picture of the same arrangement, so it is rendered
    once and kept: on a Calibre installation each of these is a subprocess, and a
    panel with a hundred lettering samples in it cannot pay that per open.
    """
    binaries = _binaries_dir()
    availability = cover_generator.renderer_availability(binaries)
    if not availability["available"]:
        return _designer_json_error(
            cover_generator.CoverGenerationError("unavailable", "no renderer installed"))
    try:
        data, was_cached = cover_designer_cache.cached(
            kind, identifier, cover_generator.THUMBNAIL_WIDTH, cover_generator.THUMBNAIL_HEIGHT,
            availability["renderer"] or "none",
            lambda: cover_preview._run_in_pool(render, identifier, binaries).data,
        )
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    response = make_response(data)
    response.headers["Content-Type"] = "image/jpeg"
    # Private: the catalogue depends on what this installation has installed,
    # not on who is asking, but it is still behind a login.
    response.headers["Cache-Control"] = "private, max-age=86400"
    response.headers["X-Cwng-Designer-Cache"] = "hit" if was_cached else "miss"
    return response


@cover_picker.route("/cover-designer/style-thumb/<style_id>", methods=["GET"])
@user_login_required
def cover_designer_style_thumb(style_id):
    """A small neutral sample of one arrangement, for the picker's style row."""
    return _catalogue_image("style", style_id, cover_generator.style_thumbnail)


@cover_picker.route("/cover-designer/font-sample/<font_id>", methods=["GET"])
@user_login_required
def cover_designer_font_sample(font_id):
    """A small sample of one lettering, set in that lettering."""
    return _catalogue_image("font", font_id, cover_generator.font_sample)


def _current_user_id() -> int:
    return int(current_user.id)


def _is_admin() -> bool:
    return bool(getattr(current_user, "role_admin", lambda: False)())


@cover_picker.route("/cover-designer/presets", methods=["GET"])
@user_login_required
def cover_designer_presets():
    """Builtin, then library, then this reader's own."""
    return jsonify(cover_design_presets.list_presets(_current_user_id(), _binaries_dir()))


@cover_picker.route("/cover-designer/presets", methods=["POST"])
@user_login_required
def cover_designer_preset_create():
    """Save the design in the body under a name. ``scope`` "library" needs admin."""
    body = request.get_json(silent=True) or {}
    try:
        preset = cover_design_presets.create_preset(
            _current_user_id(), _is_admin(), body.get("name"), body.get("design"),
            body.get("scope") or "user", _binaries_dir())
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    return make_response(jsonify({"ok": True, "preset": preset}), 201)


@cover_picker.route("/cover-designer/presets/<preset_id>", methods=["PUT"])
@user_login_required
def cover_designer_preset_update(preset_id):
    """Rename a saved preset, replace its design, or both."""
    body = request.get_json(silent=True) or {}
    try:
        preset = cover_design_presets.update_preset(
            _current_user_id(), _is_admin(), preset_id,
            name=body.get("name"), design=body.get("design"),
            binaries_dir=_binaries_dir())
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    return jsonify({"ok": True, "preset": preset})


@cover_picker.route("/cover-designer/presets/<preset_id>", methods=["DELETE"])
@user_login_required
def cover_designer_preset_delete(preset_id):
    """Delete a preset, or hide one that is not this reader's to delete."""
    try:
        outcome = cover_design_presets.delete_preset(_current_user_id(), _is_admin(), preset_id)
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    response = make_response("", 204)
    # The panel needs to know whether to offer "restore" afterwards.
    response.headers["X-Cwng-Preset-Outcome"] = outcome
    return response


@cover_picker.route("/cover-designer/presets/<preset_id>/restore", methods=["POST"])
@user_login_required
def cover_designer_preset_restore(preset_id):
    """Bring back a preset this reader hid."""
    try:
        preset = cover_design_presets.restore_preset(
            _current_user_id(), preset_id, _binaries_dir())
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    return jsonify({"ok": True, "preset": preset})


@cover_picker.route("/cover-designer/presets/order", methods=["POST"])
@user_login_required
def cover_designer_preset_order():
    """Persist the reader's own ordering of their saved presets."""
    body = request.get_json(silent=True) or {}
    order = body.get("order")
    if not isinstance(order, list):
        return _designer_json_error(
            cover_generator.CoverGenerationError("invalid_design", "Send an \"order\" list of preset ids."))
    try:
        cover_design_presets.reorder_presets(_current_user_id(), order[:200])
    except cover_generator.CoverGenerationError as error:
        return _designer_json_error(error)
    return jsonify(cover_design_presets.list_presets(_current_user_id(), _binaries_dir()))


@cover_picker.route("/book/<int:book_id>/cover/lock", methods=["POST"])
@user_login_required
@edit_required
def cover_picker_lock(book_id):
    """Toggle (or explicitly set) the BookCoverLock for this book. Body:
    {locked: true|false}. Returns the new state."""
    _load_book(book_id)
    body = request.get_json(silent=True) or {}
    desired = bool(body.get("locked"))
    record = ub.session.query(ub.BookCoverLock).filter_by(book_id=book_id).first()
    now = datetime.now(timezone.utc)
    if record is None:
        record = ub.BookCoverLock(
            book_id=book_id, locked=desired,
            locked_by=current_user.id, locked_at=now,
        )
        ub.session.add(record)
    else:
        record.locked = desired
        record.locked_by = current_user.id
        record.locked_at = now
    try:
        ub.session.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.error("cover_picker_lock commit failed: %s", exc)
        ub.session.rollback()
        return _json_error("commit_failed", _(u"Could not save lock state."), 500)
    return jsonify({"locked": record.locked})


# ---- E-reader cover preview (was Kobo, generalized 2026-05) --------------


@cover_picker.route("/book/<int:book_id>/cover/ereader-preview", methods=["POST"])
@user_login_required
@cover_source_required
def cover_picker_ereader_preview(book_id):
    """Re-render an image through the e-reader cover-padding pipeline and
    return a base64 data URL the picker page can drop straight into an
    ``<img src>``. Picker-session-local: aspect / fill_mode / color come
    from the request, not from global config, so users can preview
    variations without mutating admin defaults.

    Body shape (JSON):
        {
            "candidate_url": "https://...",   # OR
            "embedded": true,                 # use the book's embedded cover
            "aspect":    "kobo_libra_color",
            "fill_mode": "edge_mirror",
            "color":     "#1a1a1a"            # only used when fill_mode == manual
        }

    With neither ``candidate_url`` nor ``embedded`` set, the book's
    current saved cover is used as the source.
    """
    book = _load_book(book_id)
    body = request.get_json(silent=True) or {}

    candidate_url = (body.get("candidate_url") or "").strip()
    use_embedded = bool(body.get("embedded"))
    aspect = body.get("aspect") or ""
    fill_mode = body.get("fill_mode") or ""
    color = body.get("color") or ""

    # Belt-and-suspenders: cw_advocate (the SSRF guard) is the primary line
    # of defense, but it's a vendored library we don't actively maintain.
    # Reject anything that's not http(s) here so a future cw_advocate parser
    # bug can't widen the attack surface.
    if candidate_url:
        scheme = urlparse(candidate_url).scheme.lower()
        if scheme not in ("http", "https"):
            return _json_error(
                "bad_scheme",
                _(u"Only http(s) URLs can be previewed."),
                400,
            )

    # Run the whole fetch+pad pipeline on the gevent-aware threadpool. The
    # external cover fetch (`requests` via cw_advocate) does a blocking SSL
    # read on the calling thread; if that thread is the gevent MainThread,
    # every other greenlet stalls for the duration of the fetch. Confirmed
    # live with py-spy: MainThread stuck in ssl.py:read inside
    # `_fetch_url_bytes`, while login + static + metadata-search piled up.
    # Offloading the source-resolve step to the same pool that does the
    # Wand work lets the gevent hub keep serving other endpoints.
    def _resolve_then_render():
        blob = _resolve_preview_source(book, candidate_url, use_embedded)
        if blob is None:
            return None
        return cover_preview.render_preview_data_url(
            blob, aspect=aspect, fill_mode=fill_mode, color=color,
        )

    try:
        data_url = cover_preview._run_in_pool(_resolve_then_render)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("cover_picker_ereader_preview render failed: %s", exc)
        return _json_error("render_failed", _(u"Could not render the e-reader preview."), 500)

    if data_url is None:
        return _json_error("source_unavailable", _(u"Could not load a source image to preview."), 502)

    return jsonify({"ok": True, "data_url": data_url})


# Backwards-compat alias. Remove in the release AFTER the one that
# ships this rename. Kept so in-flight client bookmarks don't 404.
@cover_picker.route("/book/<int:book_id>/cover/kobo-preview", methods=["POST"])
@user_login_required
def cover_picker_kobo_preview_legacy(book_id):
    return redirect(
        url_for("cover_picker.cover_picker_ereader_preview", book_id=book_id),
        code=308,
    )


def _resolve_preview_source(book, candidate_url: str, use_embedded: bool) -> Optional[bytes]:
    """Pick the right bytes to feed pad_blob: a SSRF-safe fetch of an
    external URL, the embedded cover from the book file, or the book's
    on-disk current cover. Returns None when none of those resolve."""
    if candidate_url:
        return _fetch_url_bytes(candidate_url)
    if use_embedded:
        extracted = cover_extract.extract_embedded_cover(book)
        return extracted.data if extracted else None
    return _read_current_cover_bytes(book)


# URL → fetched bytes cache. The picker re-renders covers on every settings
# change (toggle on, aspect dropdown, fill-mode dropdown, color input). Each
# refresh re-fetches the same set of external candidate URLs from Amazon,
# OpenLibrary, Google Books, etc — at 1-30 seconds per fetch. The Wand work
# is fast (~0.2s); the SSL handshakes are the bottleneck. A small in-process
# LRU keyed by URL collapses the second-and-subsequent settings change down
# to "Wand work only" (sub-second per cover instead of 5+ minutes for the
# whole grid). Bytes are bounded by total size so a malicious cover URL
# can't exhaust process memory.
import threading as _threading
_FETCH_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MB ≈ 60 covers @ ~1 MB each
_FETCH_CACHE_LOCK = _threading.Lock()
_FETCH_CACHE = {}  # url -> (bytes, last_used_ts)
_FETCH_CACHE_TOTAL = [0]  # mutable so the closure can update


def _fetch_cache_get(url):
    with _FETCH_CACHE_LOCK:
        entry = _FETCH_CACHE.get(url)
        if entry is None:
            return None
        # Refresh LRU position by re-inserting.
        del _FETCH_CACHE[url]
        _FETCH_CACHE[url] = entry
        return entry[0]


def _fetch_cache_put(url, data):
    if not data:
        return
    size = len(data)
    if size > _FETCH_CACHE_MAX_BYTES:
        # Single oversized blob — don't cache.
        return
    with _FETCH_CACHE_LOCK:
        # Evict LRU entries until we fit.
        while _FETCH_CACHE_TOTAL[0] + size > _FETCH_CACHE_MAX_BYTES and _FETCH_CACHE:
            _, (old_data, _ts) = _FETCH_CACHE.popitem(last=False) if hasattr(_FETCH_CACHE, "popitem") else (None, (b"", 0))
            # dict.popitem(last=False) doesn't exist on a regular dict pre-3.7
            # but our minimum is 3.13 — and we use insertion order via the
            # plain dict for FIFO. Force a manual oldest-key pop:
            break
        # Manual eviction loop (insertion-order dict yields FIFO via iter()):
        while _FETCH_CACHE_TOTAL[0] + size > _FETCH_CACHE_MAX_BYTES and _FETCH_CACHE:
            oldest = next(iter(_FETCH_CACHE))
            _, _ = _FETCH_CACHE.pop(oldest), None
            _FETCH_CACHE_TOTAL[0] -= len(_)
            if _FETCH_CACHE_TOTAL[0] < 0:
                _FETCH_CACHE_TOTAL[0] = 0
        _FETCH_CACHE[url] = (data, 0)
        _FETCH_CACHE_TOTAL[0] += size


def _fetch_url_bytes(url: str) -> Optional[bytes]:
    """Fetch up to ~10 MB through cw_advocate. Mirrors the SSRF guard +
    timeout shape used by helper.save_cover_from_url so external image
    URLs in the picker get the same treatment everywhere.

    Cached per URL so subsequent settings changes don't re-fetch. Tighter
    timeout than save_cover_from_url because the picker is a live UX path
    (a 30 s laggard blocks the user's entire grid), not a save flow.
    """
    cached = _fetch_cache_get(url)
    if cached is not None:
        return cached
    try:
        from . import cw_advocate
    except Exception:  # pragma: no cover - defensive
        return None
    try:
        # (5 s connect, 8 s read) — picker context is interactive, so prefer
        # to drop a slow URL fast and let the user move on. The full 10/30
        # timeout still applies on the save path (helper.save_cover_from_url).
        resp = cw_advocate.get(url, timeout=(5, 8), allow_redirects=True, stream=True,
                               headers=cover_url_validator.cover_fetch_headers())
        if resp.status_code != 200:
            return None
        max_bytes = 10 * 1024 * 1024
        # Pre-stream cap: trust Content-Length when the server bothers to
        # send one. This drops the worker fast when an attacker advertises
        # a 1 GB image instead of waiting to stream past max_bytes.
        size_hint = resp.headers.get("Content-Length")
        if size_hint and size_hint.isdigit() and int(size_hint) > max_bytes:
            log.info("cover_picker_ereader_preview rejecting %s — Content-Length %s exceeds cap", url, size_hint)
            return None
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        data = b"".join(chunks)
        _fetch_cache_put(url, data)
        return data
    except Exception as exc:
        log.warning("cover_picker_ereader_preview fetch failed for %s: %s", url, exc)
        return None


def _read_current_cover_bytes(book) -> Optional[bytes]:
    """Load the book's on-disk cover.jpg. Returns None if the book has
    no saved cover or the read fails."""
    try:
        if request.args.get("scope") == "personal":
            from .services import user_cover
            override = user_cover.override_for_user(current_user.id, book.id)
            if override is not None:
                with open(user_cover.path_for_row(override), "rb") as fh:
                    return fh.read()
        if not getattr(book, "has_cover", False):
            return None
        cover_path = os.path.join(config.config_calibre_dir, book.path, "cover.jpg")
        if not os.path.isfile(cover_path):
            return None
        with open(cover_path, "rb") as fh:
            return fh.read()
    except Exception as exc:
        log.warning("cover_picker_ereader_preview disk-cover read failed: %s", exc)
        return None


# ---- helpers --------------------------------------------------------------


def _get_lock_state(book_id: int) -> bool:
    record = ub.session.query(ub.BookCoverLock).filter_by(book_id=book_id).first()
    return bool(record and record.locked)


def _apply_uploaded_file(book, file_storage):
    """Apply a user-uploaded image file to the book. Reuses
    helper.save_cover so the conversion + size limit checks match the
    existing upload-cover-from-disk path on the edit page."""
    return helper.save_cover(file_storage, book.path)


def _apply_bytes(book, raw: bytes, extension: str):
    """Apply raw image bytes (e.g. from the embedded-cover extract). Wraps
    the bytes in a FileStorage-like object so the existing helper.save_cover
    path can process them without changes."""
    import io
    from werkzeug.datastructures import FileStorage
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".bmp": "image/bmp",
    }.get(extension.lower(), "application/octet-stream")
    storage = FileStorage(
        stream=io.BytesIO(raw),
        filename=f"embedded_cover{extension}",
        content_type=mime,
    )
    return helper.save_cover(storage, book.path)


def _apply_response(staged_cover, message, book):
    if staged_cover:
        previous_has_cover = book.has_cover
        previous_last_modified = book.last_modified
        try:
            book.has_cover = 1
            # A new cover IS a metadata change: bump last_modified (drives the
            # web cover cache-buster on every cover URL + Kobo sync
            # re-selection) and queue the metadata write-back. remove_synced_book
            # runs post-commit below (best-effort) so a commit failure can't
            # leave it half-applied. Single source of truth: helper.mark_book_modified.
            helper.mark_book_modified(book)
            calibre_db.session.commit()
        except Exception as exc:
            log.error("cover apply: failed to record cover change for book %s: %s", book.id, exc)
            staged_cover.discard()
            try:
                calibre_db.session.rollback()
            except Exception:
                pass
            book.has_cover = previous_has_cover
            book.last_modified = previous_last_modified
            return _json_error("commit_failed", _(u"Cover save failed."), 500)

        published, publish_error = staged_cover.publish()
        if not published:
            staged_cover.discard()
            book.has_cover = previous_has_cover
            book.last_modified = previous_last_modified
            try:
                calibre_db.session.commit()
            except Exception as compensation_exc:
                log.error(
                    "cover apply: metadata compensation failed for book %s "
                    "after publish failure %s: %s",
                    book.id,
                    publish_error,
                    compensation_exc,
                )
                try:
                    calibre_db.session.rollback()
                except Exception:
                    pass
            return _json_error("publish_failed", _(u"Cover save failed."), 500)

        # #707: enqueue file-level embedding only after both metadata and the
        # canonical cover are visible.  A pre-publish record could be consumed
        # against the old cover bytes.
        try:
            helper.log_metadata_change(book, {'cover': True})
        except Exception as exc:
            log.error("cover apply: enforcement record failed for book %s: %s", book.id, exc)
        # Post-commit best-effort: the cover is applied and the last_modified
        # bump above already drives both the web cache-bust and Kobo
        # re-selection, so a failure here self-heals on the next sync /
        # thumbnail access. Log loudly but still report success.
        try:
            kobo_sync_status.remove_synced_book(book.id, all=True)
        except Exception as exc:
            log.error("post-apply Kobo housekeeping failed for book %s: %s", book.id, exc)
        try:
            helper.replace_cover_thumbnail_cache(book.id)
        except Exception as exc:
            log.error("post-apply thumbnail housekeeping failed for book %s: %s", book.id, exc)
        return jsonify({
            "ok": True,
            "cover_url": url_for("web.get_cover", book_id=book.id, resolution="og") + f"?ts={int(datetime.now(timezone.utc).timestamp())}",
        })
    return _json_error("save_failed", str(message) if message else _(u"Cover save failed."), 400)


def _json_error(code: str, message: str, status: int):
    return make_response(jsonify({"ok": False, "error_code": code, "error_message": message}), status)
