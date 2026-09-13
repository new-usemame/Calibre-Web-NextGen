#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render one designed book cover with Calibre's own cover generator.

Executed as ``calibre-debug -e calibre_generate_cover.py`` so it runs inside the
Calibre interpreter shipped with the image; the application's own Python has no
``calibre`` package and no Qt.  The request arrives as one JSON object on stdin
and the answer leaves as a single ``CWNG_COVER_RESULT=<json>`` line on stdout,
matching the marker-line protocol ``calibre_ingest_transaction.py`` already uses.
Everything else Calibre prints (font warnings, Qt chatter) is therefore ignorable
noise rather than a parse hazard.

Why this does not call ``generate_cover()``
-------------------------------------------
``calibre.ebooks.covers.generate_cover`` does three things we must not let it do:
it picks the colour theme and the style with ``random.choice``, it runs the three
text fields through Calibre's *template language*, and it offers no way to set a
style's text alignment.  The template language includes general-program and
``python:`` modes — arbitrary code — so feeding it a string a reader typed into a
web form would be a remote-code-execution hole, and a randomly chosen style means
the cover applied is not the cover previewed.

So this script performs the same twelve lines ``generate_cover`` performs, with
the theme and style pinned by name, the alignment taken from the request, and the
three strings used **verbatim** as the caller already expanded them.  No template
is ever evaluated here.  The substitution is faithful: rendering the same book
both ways produces byte-identical JPEGs (see docs/cover-designer.md).

Request shapes::

    {"op": "render",
     "title": "<b>Book</b>", "subtitle": "", "footer": "<b>Author</b>",
     "spec": {"width": 1200, "height": 1800, "style": "The Cross",
              "colors": {"color1": "d3dcf2", "color2": "829fe4",
                         "contrast_color1": "00448d", "contrast_color2": "00305a"},
              "fonts": {"title":    {"family": "Liberation Serif", "size": 120},
                        "subtitle": {"family": "Liberation Serif", "size": 80},
                        "footer":   {"family": "Liberation Serif", "size": 80}},
              "align": {"title": "center", "subtitle": "center", "footer": "right"}}}

    {"op": "fonts"}      # the font families this Calibre can actually draw with

Response shapes::

    {"ok": true, "format": "jpeg", "width": 1200, "height": 1800, "data": "<base64>"}
    {"ok": true, "families": ["Liberation Serif", ...]}
    {"ok": false, "error": "..."}

The title/subtitle/footer strings may carry the inline markup Calibre's own
``parse_text_formatting`` understands — ``<b>``, ``<i>``, ``<br>`` — and nothing
else; every other ``<...>`` run is dropped by that function.
"""

import base64
import json
import os
import sys

RESULT_MARKER = "CWNG_COVER_RESULT="

DEFAULT_STYLE = "Blocks"
# Calibre's own defaults, restated so a request that omits a font size still
# renders the cover Calibre would have drawn.
DEFAULT_SIZES = {"title": 120, "subtitle": 80, "footer": 80}
# Calibre lays every block out from a fixed top, so only the horizontal part of
# the alignment is ours to choose.
ALIGN_NAMES = ("left", "center", "right")


def _emit(payload):
    sys.stdout.write(RESULT_MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def _horizontal_alignment(name):
    from qt.core import Qt

    flags = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }
    return flags[name if name in flags else "center"] | Qt.AlignmentFlag.AlignTop


def _list_fonts():
    from calibre.ebooks.covers import init_environment
    init_environment()
    from qt.core import QFontDatabase
    families = [str(name) for name in QFontDatabase.families()]
    return {"ok": True, "families": sorted(families)}


def _render(request):
    from calibre.constants import __appname__, __version__
    from calibre.ebooks.covers import (
        Prefs, cprefs, init_environment, layout_text, load_styles, theme_to_colors,
    )
    from calibre.gui2 import pixmap_to_data
    from qt.core import QImage, QPainter, QRect

    init_environment()

    spec = request.get("spec") or {}
    width = max(1, int(spec.get("width") or 1200))
    height = max(1, int(spec.get("height") or 1600))

    # theme_to_colors prepends '#', so a '#'-prefixed value silently degrades to
    # Calibre's greyscale fallback instead of failing. Strip it here.
    colors = {key: str(value).lstrip("#")
              for key, value in (spec.get("colors") or {}).items()}
    fonts = spec.get("fonts") or {}
    align = spec.get("align") or {}

    def font_of(slot):
        entry = fonts.get(slot) or {}
        family = entry.get("family") or None
        try:
            size = int(entry.get("size") or DEFAULT_SIZES[slot])
        except (TypeError, ValueError):
            size = DEFAULT_SIZES[slot]
        return family, max(4, size)

    prefs_values = dict(cprefs.defaults)
    prefs_values["cover_width"] = width
    prefs_values["cover_height"] = height
    for slot in ("title", "subtitle", "footer"):
        family, size = font_of(slot)
        prefs_values[slot + "_font_family"] = family
        prefs_values[slot + "_font_size"] = size
    # Prefs is a namedtuple over cprefs.defaults' keys, so building it from the
    # defaults dict keeps working when a later Calibre adds a preference.
    prefs = Prefs(**{key: prefs_values[key] for key in cprefs.defaults})

    color_theme = theme_to_colors(colors)
    wanted = spec.get("style") or DEFAULT_STYLE
    available = {klass.NAME: klass for klass in load_styles(prefs, respect_disabled=False)}
    style_class = available.get(wanted)
    if style_class is None:
        raise ValueError("unknown style: %s" % wanted)
    style = style_class(color_theme, prefs)
    # Instance attributes shadow the class defaults layout_text reads.
    style.TITLE_ALIGN = _horizontal_alignment(align.get("title"))
    style.SUBTITLE_ALIGN = _horizontal_alignment(align.get("subtitle"))
    style.FOOTER_ALIGN = _horizontal_alignment(align.get("footer"))

    title = request.get("title") or ""
    subtitle = request.get("subtitle") or ""
    footer = request.get("footer") or ""

    image = QImage(prefs.cover_width, prefs.cover_height, QImage.Format.Format_ARGB32)
    title_block, subtitle_block, footer_block = layout_text(
        prefs, image, title, subtitle, footer, image.height() // 3, style)
    painter = QPainter(image)
    rect = QRect(0, 0, image.width(), image.height())
    pen_colors = style(painter, rect, color_theme,
                       title_block, subtitle_block, footer_block)
    for block, pen in zip((title_block, subtitle_block, footer_block), pen_colors):
        painter.setPen(pen)
        block.draw(painter)
    painter.end()
    image.setText('Generated cover', '%s %s' % (__appname__, __version__))

    return {
        "ok": True,
        "format": "jpeg",
        "width": width,
        "height": height,
        "data": base64.b64encode(pixmap_to_data(image)).decode("ascii"),
    }


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError as error:
        _emit({"ok": False, "error": "unreadable request: %s" % error})
        return 2
    try:
        if (request.get("op") or "render") == "fonts":
            _emit(_list_fonts())
        else:
            _emit(_render(request))
    except Exception as error:  # noqa: BLE001 - the parent needs the reason, not a trace
        _emit({"ok": False, "error": "%s: %s" % (type(error).__name__, error)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
