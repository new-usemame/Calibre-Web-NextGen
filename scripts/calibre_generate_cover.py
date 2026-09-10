#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render one generated book cover with Calibre's own cover generator.

Executed as ``calibre-debug -e calibre_generate_cover.py`` so it runs inside the
Calibre interpreter shipped with the image; the application's own Python has no
``calibre`` package and no Qt.  The request arrives as one JSON object on stdin
and the answer leaves as a single ``CWNG_COVER_RESULT=<json>`` line on stdout,
matching the marker-line protocol ``calibre_ingest_transaction.py`` already uses.
Everything else Calibre prints (font warnings, Qt chatter) is therefore ignorable
noise rather than a parse hazard.

The request carries explicit colours, a style name and font families instead of
naming one of Calibre's built-in themes: built-in theme names are Calibre's to
rename, while a cover the user previewed must render identically when applied.
``calibre.ebooks.covers.load_color_themes`` merges ``prefs['color_themes']`` over
the built-ins and drops everything listed in ``disabled_color_themes``, so
injecting one named theme and disabling the rest makes the choice deterministic.
Colours are bare hex without ``#`` because ``theme_to_colors`` prepends one.

Request shape::

    {"title": "...", "authors": ["..."], "series": "..." | null,
     "series_index": 2.0 | null,
     "spec": {"width": 1200, "height": 1600, "style": "Blocks",
              "font_family": "Liberation Serif",
              "colors": {"color1": "f4efe3", "color2": "1f3a5f",
                         "contrast_color1": "1f3a5f", "contrast_color2": "f4efe3"}}}

Response shape::

    {"ok": true, "format": "jpeg", "width": 1200, "height": 1600,
     "data": "<base64>"}
    {"ok": false, "error": "..."}
"""

import base64
import json
import os
import sys

RESULT_MARKER = "CWNG_COVER_RESULT="

# One injected theme name. It is disabled-by-omission for every other render, so
# it never collides with a user's own Calibre themes.
THEME_NAME = "CWNG Generated"


def _emit(payload):
    sys.stdout.write(RESULT_MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _render(request):
    from calibre.ebooks.covers import cprefs, generate_cover, override_prefs
    from calibre.ebooks.metadata.book.base import Metadata

    spec = request.get("spec") or {}
    width = int(spec.get("width") or 1200)
    height = int(spec.get("height") or 1600)
    colors = dict(spec.get("colors") or {})
    # theme_to_colors prepends '#', so a '#'-prefixed value silently degrades to
    # Calibre's greyscale fallback instead of failing. Strip it here.
    colors = {key: str(value).lstrip("#") for key, value in colors.items()}

    title = request.get("title") or ""
    authors = [a for a in (request.get("authors") or []) if a]
    metadata = Metadata(title, authors or [""])
    if request.get("series"):
        metadata.series = request["series"]
        if request.get("series_index") is not None:
            try:
                metadata.series_index = float(request["series_index"])
            except (TypeError, ValueError):
                pass

    # Calibre's defaults are tuned for a 1200px-wide cover; scale with the
    # requested width so a smaller preview does not clip its own title.
    scale = width / 1200.0
    prefs = override_prefs(
        cprefs,
        color_themes={THEME_NAME: colors},
        override_color_theme=THEME_NAME,
        override_style=spec.get("style") or "Blocks",
        cover_width=width,
        cover_height=height,
        title_font_family=spec.get("font_family") or None,
        subtitle_font_family=spec.get("font_family") or None,
        footer_font_family=spec.get("font_family") or None,
        title_font_size=max(12, int(round(120 * scale))),
        subtitle_font_size=max(10, int(round(80 * scale))),
        footer_font_size=max(10, int(round(80 * scale))),
    )
    data = generate_cover(metadata, prefs=prefs)
    return {
        "ok": True,
        "format": "jpeg",
        "width": width,
        "height": height,
        "data": base64.b64encode(data).decode("ascii"),
    }


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError as error:
        _emit({"ok": False, "error": "unreadable request: %s" % error})
        return 2
    try:
        _emit(_render(request))
    except Exception as error:  # noqa: BLE001 - the parent needs the reason, not a trace
        _emit({"ok": False, "error": "%s: %s" % (type(error).__name__, error)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
