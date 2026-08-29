# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comic page-extraction endpoints for /api/v1 — let the native React comic
reader show CBZ/CBR/CBT pages as plain <img>s without a client-side archive lib.

Pages are extracted server-side (zipfile for CBZ/CBT, rarfile+unrar for CBR) and
served one image at a time, natural-sorted by filename.
"""
import io
import os
import re
import zipfile

from flask import jsonify, send_file, abort

from . import api_v1
from .. import calibre_db, config, logger
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano

log = logger.create()

_IMG_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif')
_MIME = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
         'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp', 'avif': 'image/avif'}
_COMIC_FORMATS = ('cbz', 'cbr', 'cbt')


def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def _comic_file(book_id):
    """(filesystem path, format) of a book's comic archive, or (None, None)."""
    book = calibre_db.get_filtered_book(book_id)
    if not book:
        return None, None
    for d in (getattr(book, "data", None) or []):
        fmt = d.format.lower()
        if fmt in _COMIC_FORMATS:
            path = os.path.join(config.get_book_path(), book.path, d.name + "." + fmt)
            return path, fmt
    return None, None


def _list_pages(path, fmt):
    if fmt in ("cbz", "cbt"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(_IMG_EXT)]
    elif fmt == "cbr":
        import rarfile  # server ships unrar
        with rarfile.RarFile(path) as r:
            names = [n for n in r.namelist() if n.lower().endswith(_IMG_EXT)]
    else:
        return []
    names.sort(key=_natural_key)
    return names


def _read_entry(path, fmt, name):
    if fmt in ("cbz", "cbt"):
        with zipfile.ZipFile(path) as z:
            return z.read(name)
    import rarfile
    with rarfile.RarFile(path) as r:
        return r.read(name)


@api_v1.route("/books/<int:book_id>/comic")
@login_required_if_no_ano
def comic_info(book_id):
    """Page count for the native comic reader."""
    if not current_user.role_viewer():
        abort(403)
    path, fmt = _comic_file(book_id)
    if not path or not os.path.isfile(path):
        return jsonify({"error": {"code": "not_found", "message": "No comic file for this book"}}), 404
    try:
        pages = len(_list_pages(path, fmt))
    except Exception:
        log.error("Could not read comic archive for book %s", book_id, exc_info=True)
        return jsonify({"error": {"code": "unreadable", "message": "Could not read the comic archive"}}), 500
    return jsonify({"pages": pages, "format": fmt})


@api_v1.route("/books/<int:book_id>/comic/<int:page>")
@login_required_if_no_ano
def comic_page(book_id, page):
    """Serve a single comic page as an image."""
    if not current_user.role_viewer():
        abort(403)
    path, fmt = _comic_file(book_id)
    if not path or not os.path.isfile(path):
        abort(404)
    try:
        pages = _list_pages(path, fmt)
    except Exception:
        abort(500)
    if page < 0 or page >= len(pages):
        abort(404)
    name = pages[page]
    try:
        data = _read_entry(path, fmt, name)
    except Exception:
        abort(500)
    ext = name.rsplit(".", 1)[-1].lower()
    resp = send_file(io.BytesIO(data), mimetype=_MIME.get(ext, "application/octet-stream"))
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp
