# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Render a typographic cover from a book's own metadata.

A book with no cover is not a rare edge case — public-domain EPUBs, personal
scans and most converted documents arrive without one, and until now they all
showed the same grey ``static/generic_cover.svg`` placeholder in every grid.
This module turns the book's title, series and authors into a real cover the
reader can tell apart at a glance, and the cover picker offers it as one more
source beside the online providers.

Two renderers, one contract
---------------------------
The preferred renderer is Calibre's own cover engine — the one behind "Generate
cover" in Calibre's metadata editor — driven through ``calibre-debug -e
scripts/calibre_generate_cover.py`` because the application's interpreter has
neither ``calibre`` nor Qt.  Where Calibre is not installed (unit tests, a
stripped image) a Pillow renderer honours the identical design contract, so the
catalogue, the API and the tests never depend on Calibre being present.
``render()`` reports which renderer produced the bytes; the two are deliberately
not pixel-identical and nothing compares them.

The design vocabulary
---------------------
A *design* is everything the reader can decide, and it is everything Calibre's
generator can be told:

* **style** — the arrangement.  All five of Calibre's own: Blocks, Banner,
  Ornamental, The Cross, Half and half.
* **colours** — four slots (``background``, ``band``, ``title``, ``author``),
  either from a named scheme or set individually as ``#rrggbb``.  They map onto
  Calibre's ``color1`` / ``color2`` / ``contrast_color1`` / ``contrast_color2``;
  which text sits on which ground is the style's business and is documented per
  style in the catalogue.
* **fonts** — family, size, bold and italic per text slot, from the font
  families actually installed on this machine.
* **align** — left / centre / right per text slot.
* **text** — a short template per slot over a closed set of ``{placeholders}``.
* **size** — output pixels, clamped.

Font sizes are *design units*: pixels on a 1200px-wide cover.  The renderer
scales them with the output width, so the same design looks identical whether it
is drawn as a 300px preview thumbnail or a 2400px cover.

Presets are named designs.  The builtin ones live here; the ones a reader saves
live in ``app.db`` (see ``cps/services/cover_design_presets.py``).

Safety
------
Client pixels are never trusted: the SPA sends a design, and both the preview and
the apply path re-render server-side from the book's own stored metadata.  Text
templates are **not** Calibre templates — Calibre's template language has
general-program and ``python:`` modes, i.e. arbitrary code — they are a closed
placeholder vocabulary this module expands itself, and the helper script uses the
resulting strings verbatim without ever invoking a template engine.  A font is
chosen by catalogue id, never by path.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .. import logger

log = logger.create()


class CoverGenerationError(Exception):
    """A generated cover could not be produced, with a reason for the caller."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

# 2:3, because that is what every cover frame in the app is: the picker's current
# cover card, the candidate grid and the compare modal all use aspect-ratio 2/3
# with object-fit: contain. Calibre's own default is 3:4, and rendering at it made
# a freshly applied cover letterbox with dark bars the moment it landed in the
# card the user checks it in.
APPLY_WIDTH, APPLY_HEIGHT = 1200, 1800
DEFAULT_WIDTH, DEFAULT_HEIGHT = APPLY_WIDTH, APPLY_HEIGHT
# Previews render smaller so the round trip stays interactive; the design is
# identical, only the pixel count differs.
PREVIEW_WIDTH, PREVIEW_HEIGHT = 600, 900
MIN_DIMENSION, MAX_DIMENSION = 200, 2400

# Font sizes are expressed at this width and scaled with the output, so a design
# is resolution-independent.
REFERENCE_WIDTH = 1200
MIN_FONT_SIZE, MAX_FONT_SIZE = 8, 400

# Catalogue thumbnails: 2:3 like every cover frame, rendered at 2x a nominal
# 200px-tall slot so they stay sharp on a retina display.
THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT = 266, 400

# A generated cover is a few hundred KB of flat colour and text. Anything past
# this is a runaway renderer, not a cover, and is refused rather than stored.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

_DEFAULT_TIMEOUT_SECONDS = 25.0


# ---------------------------------------------------------------------------
# Catalogue: styles
# ---------------------------------------------------------------------------

TEXT_SLOTS = ("title", "subtitle", "author")
# The slot names this app uses, and the ones Calibre's generator uses for the
# same three blocks. "author" is Calibre's footer.
CALIBRE_SLOT = {"title": "title", "subtitle": "subtitle", "author": "footer"}

ALIGNMENTS = ("left", "center", "right")

# Every arrangement Calibre's generator has (calibre.ebooks.covers.all_styles()).
# ``calibre_style`` is the NAME Calibre knows it by; ``align`` is what that style
# uses when nothing is chosen (Calibre puts the Blocks footer hard right);
# ``color_roles`` says where each of the four colours lands, because that is the
# question a reader actually has in front of the colour picker.
STYLES: dict[str, dict] = {
    "blocks": {
        "label": "Blocks",
        "description": "Title at the top, authors on a colour band across the bottom third.",
        "calibre_style": "Blocks",
        "align": {"title": "center", "subtitle": "center", "author": "right"},
        # Blocks draws the title with Calibre's contrast_color1 and the footer
        # with contrast_color2; the other four styles do the opposite. Recording
        # it here is what lets the "title" colour slot mean the title's colour in
        # every arrangement instead of meaning "whichever text Calibre happens to
        # paint with contrast_color1".
        "swap_contrast": False,
        "color_roles": {
            "background": "Upper two thirds",
            "band": "Bottom third",
            "title": "Title and subtitle",
            "author": "Authors, on the band",
        },
    },
    "banner": {
        "label": "Banner",
        "description": "Title on a folded ribbon near the top, authors below on the page.",
        "calibre_style": "Banner",
        "align": {"title": "center", "subtitle": "center", "author": "center"},
        "swap_contrast": True,
        "color_roles": {
            "background": "Page",
            "band": "The ribbon",
            "title": "Title and subtitle, on the ribbon",
            "author": "Authors, on the page",
        },
    },
    "ornamental": {
        "label": "Ornamental",
        "description": "Title inside a decorated frame over a soft radial wash.",
        "calibre_style": "Ornamental",
        "align": {"title": "center", "subtitle": "center", "author": "center"},
        "swap_contrast": True,
        "color_roles": {
            "background": "Centre of the wash",
            "band": "Edge of the wash",
            "title": "Title and subtitle",
            "author": "Authors, and the ornaments",
        },
    },
    "cross": {
        "label": "The Cross",
        "description": "A band down the left edge meeting a rounded title panel.",
        "calibre_style": "The Cross",
        "align": {"title": "center", "subtitle": "center", "author": "center"},
        "swap_contrast": True,
        "color_roles": {
            "background": "Page",
            "band": "Left edge and the title panel",
            "title": "Title and subtitle, on the panel",
            "author": "Authors, on the page",
        },
    },
    "half": {
        "label": "Half and half",
        "description": "One colour washing into another from top to bottom.",
        "calibre_style": "Half and Half",
        "align": {"title": "center", "subtitle": "center", "author": "center"},
        "swap_contrast": False,
        "color_roles": {
            "background": "Top and bottom of the wash",
            "band": "Middle of the wash",
            "title": "All three text blocks",
            "author": "Unused by this arrangement",
        },
    },
}
DEFAULT_STYLE = "blocks"

# v1 called this vocabulary "layouts" and shipped three of them. The name is kept
# so an older client body (``{"layout": "blocks"}``) still resolves.
LAYOUTS = STYLES


# ---------------------------------------------------------------------------
# Catalogue: colours
# ---------------------------------------------------------------------------

# Colours are stored bare (no '#'): Calibre's ``theme_to_colors`` prepends one,
# and a '#'-prefixed value degrades silently to its greyscale fallback.
#   color1          page background
#   color2          the accent block / band
#   contrast_color1 text drawn on color1
#   contrast_color2 text drawn on color2
# The four public slot names map onto them in that order.
COLOR_SLOTS = ("background", "band", "title", "author")
_CALIBRE_COLOR_KEYS = {
    "background": "color1",
    "band": "color2",
    "title": "contrast_color1",
    "author": "contrast_color2",
}

COLOR_SCHEMES: dict[str, dict] = {
    # Calibre-Web-NextGen's own, shipped in v1.
    "ink": {
        "label": "Ink on cream",
        "color1": "f4efe3", "color2": "1f3a5f",
        "contrast_color1": "1f3a5f", "contrast_color2": "f4efe3",
    },
    "meadow": {
        "label": "Meadow green",
        "color1": "eef4e6", "color2": "3f6b3a",
        "contrast_color1": "24451f", "contrast_color2": "f2f7ec",
    },
    "ember": {
        "label": "Ember red",
        "color1": "fff3e6", "color2": "c0392b",
        "contrast_color1": "7a2d12", "contrast_color2": "fff3e6",
    },
    "slate": {
        "label": "Slate grey",
        "color1": "e9ecef", "color2": "343a40",
        "contrast_color1": "212529", "contrast_color2": "f8f9fa",
    },
    "plum": {
        "label": "Plum violet",
        "color1": "f3ecf7", "color2": "5b2c6f",
        "contrast_color1": "3d1e4a", "contrast_color2": "f7f0fa",
    },
    # Calibre's four builtin themes, verbatim from
    # calibre.ebooks.covers.default_color_themes, so a cover generated in Calibre
    # can be reproduced here exactly.
    "earth": {
        "label": "Earth",
        "color1": "e8d9ac", "color2": "c7b07b",
        "contrast_color1": "564628", "contrast_color2": "382d1a",
    },
    "grass": {
        "label": "Grass",
        "color1": "d8edb5", "color2": "abc8a4",
        "contrast_color1": "375d3b", "contrast_color2": "183128",
    },
    "water": {
        "label": "Water",
        "color1": "d3dcf2", "color2": "829fe4",
        "contrast_color1": "00448d", "contrast_color2": "00305a",
    },
    "silver": {
        "label": "Silver",
        "color1": "e6f1f5", "color2": "aab3b6",
        "contrast_color1": "6e7476", "contrast_color2": "3b3e40",
    },
    # A few more, chosen for contrast rather than novelty: a dark cover, a warm
    # paper, and three that read well at thumbnail size.
    "midnight": {
        "label": "Midnight",
        "color1": "11151f", "color2": "2d3b55",
        "contrast_color1": "e8ecf5", "contrast_color2": "f2f5fa",
    },
    "sepia": {
        "label": "Sepia",
        "color1": "f1e3c8", "color2": "8a5a2b",
        "contrast_color1": "3f2a14", "contrast_color2": "fdf6e8",
    },
    "rose": {
        "label": "Rose",
        "color1": "fdeef2", "color2": "b03a5b",
        "contrast_color1": "7a2038", "contrast_color2": "fff2f6",
    },
    "ocean": {
        "label": "Ocean",
        "color1": "e3f2f7", "color2": "1b6b7a",
        "contrast_color1": "0c3b45", "contrast_color2": "eaf7fa",
    },
    "forest": {
        "label": "Forest",
        "color1": "e8f1e6", "color2": "2f5d3a",
        "contrast_color1": "1b3a23", "contrast_color2": "f0f7ee",
    },
    "noir": {
        "label": "Noir",
        "color1": "f5f5f5", "color2": "161616",
        "contrast_color1": "161616", "contrast_color2": "f5f5f5",
    },
}
DEFAULT_SCHEME = "ink"

# Style and font thumbnails are drawn in this scheme so the arrangement, not the
# palette, is what the reader is comparing.
NEUTRAL_SCHEME = "silver"

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------------------
# Catalogue: text templates
# ---------------------------------------------------------------------------

# The whole template vocabulary. Anything outside it is refused at the API
# boundary; nothing here reaches a template engine.
TEXT_PLACEHOLDERS: dict[str, str] = {
    "title": "Book title",
    "authors": "All authors, separated by commas",
    "author": "First author only",
    "series": "Series name",
    "series_index": "Number within the series",
    "publisher": "Publisher",
    "year": "Year of publication",
    "tags": "Tags, separated by commas",
    "language": "First language",
    "rating": "Rating out of five, as a number",
    "stars": "Rating out of five, as stars",
}

DEFAULT_TEXT = {
    "title": "{title}",
    "subtitle": "{series} {series_index}",
    "author": "{authors}",
}

# Calibre's own defaults, in design units.
DEFAULT_FONT_SIZES = {"title": 120, "subtitle": 80, "author": 80}
# Calibre's stock templates set the title and the authors bold and the series
# italic. Keeping that as a per-slot property rather than markup in the template
# means the reader gets Calibre's look without having to type tags.
DEFAULT_FONT_EMPHASIS = {
    "title": {"bold": True, "italic": False},
    "subtitle": {"bold": False, "italic": True},
    "author": {"bold": True, "italic": False},
}

MAX_TEMPLATE_LENGTH = 200
# The inline markup Calibre's parse_text_formatting understands. Everything else
# between angle brackets is refused rather than silently dropped.
_ALLOWED_TAGS = re.compile(r"</?(?:b|i|em|strong)>|<br\s*/?>", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
# Literal text made only of spacing and punctuation is a separator between two
# fields, not content: it is dropped when either side resolved to nothing.
_TOKEN_SPLIT = re.compile(r"(\{[a-z_]+\}|</?(?:b|i|em|strong)>|<br\s*/?>)", re.IGNORECASE)
_EMPTY_TAG_PAIR = re.compile(r"<(b|i|em|strong)>\s*</\1>", re.IGNORECASE)
_SEPARATOR_ONLY = re.compile(r"^[\s\-\u2013\u2014:;,./|\u00b7]*$")
# Brackets are punctuation too, but paired: "{publisher} ({year})" on a book
# with no year should read "Hodder", and "Hodder (2015)" must keep both of them.
# They are therefore tracked as a run rather than dropped side by side.
_BRACKET_SPLIT = re.compile(r"([()\[\]])")
_BRACKET_PAIRS = {"(": ")", "[": "]"}


# ---------------------------------------------------------------------------
# Catalogue: builtin presets
# ---------------------------------------------------------------------------

# A preset is a partial design; the resolver fills the rest. The five v1 ids keep
# their v1 meaning, because the library default is persisted as one of them.
PRESETS: dict[str, dict] = {
    "classic": {"label": "Classic", "design": {"style": "blocks", "scheme": "ink"}},
    "meadow": {"label": "Meadow", "design": {"style": "banner", "scheme": "meadow"}},
    "ember": {"label": "Ember", "design": {"style": "blocks", "scheme": "ember",
                                           "fonts": {"title": {"family": "sans"},
                                                     "subtitle": {"family": "sans"},
                                                     "author": {"family": "sans"}}}},
    "slate": {"label": "Slate", "design": {"style": "ornamental", "scheme": "slate",
                                           "fonts": {"title": {"family": "sans"},
                                                     "subtitle": {"family": "sans"},
                                                     "author": {"family": "sans"}}}},
    "plum": {"label": "Plum", "design": {"style": "ornamental", "scheme": "plum"}},
    # New in v2: the two Calibre looks people recognise, plus a dark one.
    "calibre-blue": {"label": "Calibre blue", "design": {"style": "cross", "scheme": "water"}},
    "calibre-earth": {"label": "Calibre earth", "design": {"style": "blocks", "scheme": "earth"}},
    "midnight": {"label": "Midnight", "design": {"style": "half", "scheme": "midnight",
                                                 "fonts": {"title": {"family": "sans"},
                                                           "subtitle": {"family": "sans"},
                                                           "author": {"family": "sans"}}}},
    "noir": {"label": "Noir", "design": {"style": "cross", "scheme": "noir",
                                         "fonts": {"title": {"family": "sans"},
                                                   "subtitle": {"family": "sans"},
                                                   "author": {"family": "sans"}}}},
}
DEFAULT_PRESET = "classic"

# A reader naming their own presets, and a cap so one account cannot fill the
# table. Fifty is far past any plausible personal use and small enough that the
# catalogue payload stays a page-load-sized thing.
MAX_PRESET_NAME_LENGTH = 60
MAX_PRESETS_PER_USER = 50

# Every English source string the catalogue hands the SPA. The panel renders them
# through `t(...)`, so the extractor cannot see them at the call site: they are
# anchored by hand in cps/spa_strings.py, the same way the v1 labels are.
CATALOGUE_LABELS = tuple(sorted(
    {entry["label"] for entry in STYLES.values()}
    | {entry["description"] for entry in STYLES.values()}
    | {entry["label"] for entry in COLOR_SCHEMES.values()}
    | {entry["label"] for entry in PRESETS.values()}
    | set(TEXT_PLACEHOLDERS.values())
    | {role for entry in STYLES.values() for role in entry["color_roles"].values()}
))


# ---------------------------------------------------------------------------
# Catalogue: fonts
# ---------------------------------------------------------------------------

# The three ids that always exist, whatever is installed: a design saved on one
# machine still resolves on another. v1 shipped "serif" and "sans" and presets
# still name them.
GENERIC_FONTS: dict[str, dict] = {
    "serif": {
        "label": "Serif",
        "generic": "serif",
        "families": ("Liberation Serif", "DejaVu Serif", "Noto Serif", "Times New Roman",
                     "Georgia", "Nimbus Roman", "FreeSerif", "serif"),
        "css_stack": "Liberation Serif, Georgia, 'Times New Roman', serif",
    },
    "sans": {
        "label": "Sans-serif",
        "generic": "sans",
        "families": ("Liberation Sans", "DejaVu Sans", "Noto Sans", "Arial", "Helvetica",
                     "Nimbus Sans", "FreeSans", "sans-serif"),
        "css_stack": "Liberation Sans, Helvetica, Arial, sans-serif",
    },
    "mono": {
        "label": "Monospace",
        "generic": "mono",
        "families": ("Liberation Mono", "DejaVu Sans Mono", "Noto Sans Mono", "Courier New",
                     "Nimbus Mono PS", "FreeMono", "monospace"),
        "css_stack": "Liberation Mono, 'Courier New', ui-monospace, monospace",
    },
}
DEFAULT_FONT = "serif"

# v1's two-entry font vocabulary. Kept as an alias so an older body resolves and
# so ``FONT_FAMILIES[spec.font]`` in any stale caller still answers.
FONT_FAMILIES = {key: GENERIC_FONTS[key] for key in ("serif", "sans")}

# Families worth listing first when they are installed: the ones Calibre ships,
# then the ones a Linux image usually has, then the desktop classics.
_PREFERRED_FAMILIES = (
    "Liberation Serif", "Liberation Sans", "Liberation Mono",
    "DejaVu Serif", "DejaVu Sans", "DejaVu Sans Mono",
    "Noto Serif", "Noto Sans", "Noto Sans Mono",
    "EB Garamond", "Source Serif 4", "Source Sans 3", "Lato", "Merriweather",
    "Georgia", "Palatino", "Baskerville", "Didot", "Optima", "Futura",
    "Times New Roman", "Arial", "Helvetica", "Verdana", "Courier New",
)

# A machine with a thousand fonts would make the catalogue payload the largest
# thing on the page; the reader does not need all of them in a dropdown.
MAX_FONT_CATALOGUE = 120

_SERIF_HINTS = ("serif", "times", "georgia", "garamond", "roman", "book", "minion",
                "baskerville", "caslon", "didot", "palatino", "charter", "cochin")
_MONO_HINTS = ("mono", "courier", "consol", "code", "terminal", "typewriter")

# Where font files live. The Calibre install tree matters: the container image
# installs no font package, so Calibre's bundled Liberation faces are the only
# real fonts on it.
_FONT_SEARCH_DIRS = (
    "/usr/share/fonts", "/usr/local/share/fonts", "/usr/share/texmf/fonts",
    os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
    os.path.expanduser("~/Library/Fonts"),
)
_CALIBRE_FONT_SUBDIRS = ("resources/fonts", "../resources/fonts",
                         "../Resources/resources/fonts", "resources")

_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")
# A cap on the directory walk so a mounted share full of fonts cannot stall the
# first request that asks for the catalogue.
_MAX_FONT_FILES = 2000

# Not every font file on a machine is a typeface. Icon sheets and dingbat fonts
# sit in the same directories, and a title set in one comes out as a row of
# scissors or of empty boxes, so they are not offered as lettering. Two facts
# out of the file itself decide it, both of them the font's own declaration:
# OpenType family class 12 is "Symbolic", and a font that maps a handful of
# code points is an icon sheet rather than an alphabet.
_SYMBOLIC_FAMILY_CLASS = 12
# Below any real alphabet with its digits and stops. For scale: the narrowest
# text face on the container image maps 190 code points, and Calibre's own icon
# font maps five.
_MIN_TEXT_CODEPOINTS = 32

_font_cache: dict = {}
_font_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Font discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FontEntry:
    """One choosable lettering: what Calibre calls it, what Pillow draws it with."""

    id: str
    label: str
    family: str
    generic: str
    css_stack: str
    # style key ("regular" / "bold" / "italic" / "bold_italic") -> absolute path.
    # Empty for a family Qt knows about but whose file this process never found;
    # Pillow then falls back to the generic, which is why an unfound family is
    # still a perfectly valid design rather than a render failure.
    files: dict = field(default_factory=dict)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "font"


def _classify_family(family: str) -> str:
    lowered = (family or "").lower()
    if any(hint in lowered for hint in _MONO_HINTS):
        return "mono"
    if any(hint in lowered for hint in _SERIF_HINTS):
        return "serif"
    return "sans"


def _style_key(style_name: str) -> str:
    lowered = (style_name or "").lower()
    bold = "bold" in lowered or "heavy" in lowered or "black" in lowered or "semibold" in lowered
    italic = "italic" in lowered or "oblique" in lowered
    if bold and italic:
        return "bold_italic"
    if bold:
        return "bold"
    if italic:
        return "italic"
    return "regular"


def _calibre_font_dirs(binaries_dir: str = "") -> tuple:
    """Calibre's bundled font directory, derived from the binary it ships beside.

    The container image installs no font package at all: Calibre's own Liberation
    faces under its resources tree are the entire font catalogue there, and they
    are the faces ``init_environment()`` registers with Qt. The path comes from
    the resolved ``calibre-debug`` location, never from anything a request says.
    """
    binary = calibre_debug_path(binaries_dir)
    if not binary:
        return ()
    try:
        root = os.path.dirname(os.path.realpath(binary))
    except OSError:  # pragma: no cover - defensive
        return ()
    found = []
    for relative in _CALIBRE_FONT_SUBDIRS:
        candidate = os.path.normpath(os.path.join(root, relative))
        if os.path.isdir(candidate) and candidate not in found:
            found.append(candidate)
    return tuple(found)


def _sfnt_tables(handle) -> dict:
    """``{table tag: (offset, length)}`` for the first face in a font file.

    Enough of the OpenType container to read two declarations out of it. Any
    file that does not parse as one returns nothing, and every caller here
    treats nothing as "no opinion".
    """
    handle.seek(0)
    header = handle.read(12)
    if len(header) < 12:
        return {}
    tag, count = struct.unpack(">IH", header[:6])
    base = 12
    if tag == 0x74746366:  # 'ttcf' - a collection; its first face will do
        handle.seek(12)
        offset = struct.unpack(">I", handle.read(4))[0]
        handle.seek(offset + 4)
        count = struct.unpack(">H", handle.read(2))[0]
        base = offset + 12
    if not 0 < count <= 512:
        return {}
    handle.seek(base)
    blob = handle.read(count * 16)
    tables = {}
    for index in range(count):
        record = blob[index * 16:(index + 1) * 16]
        if len(record) < 16:
            break
        name, _checksum, offset, length = struct.unpack(">4sIII", record)
        tables[name.decode("latin-1", "replace").strip()] = (offset, length)
    return tables


def _os2_family_class(handle, tables: dict) -> int:
    """The OS/2 table's ``sFamilyClass`` high byte, or 0 for "unclassified"."""
    entry = tables.get("OS/2")
    if not entry:
        return 0
    handle.seek(entry[0] + 30)
    raw = handle.read(2)
    if len(raw) < 2:
        return 0
    return struct.unpack(">H", raw)[0] >> 8


# Private-use code points carry no character: an icon set mapped into them is a
# sheet of pictures whatever its glyphs look like.
_PRIVATE_USE_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0x10FFFF))
# Only the Unicode character maps are read. A legacy Mac or symbol map says
# nothing about what the font can spell.
_UNICODE_CMAPS = ((0, None), (3, 1), (3, 10))


def _characters_in(start: int, end: int) -> int:
    """Code points between *start* and *end* that are real characters."""
    total = max(0, end - start + 1)
    for low, high in _PRIVATE_USE_RANGES:
        total -= max(0, min(end, high) - max(start, low) + 1)
    return max(0, total)


def _cmap_codepoints(handle, tables: dict) -> Optional[int]:
    """How many characters the font maps, or None when that cannot be read.

    Counted from the character map's ranges rather than by walking every code
    point, because this runs over every font file on the machine. Ranges can
    only over-count, which is the safe direction: the only thing the number
    decides is whether a font is too small to be an alphabet.
    """
    entry = tables.get("cmap")
    if not entry:
        return None
    base = entry[0]
    handle.seek(base)
    header = handle.read(4)
    if len(header) < 4:
        return None
    count = struct.unpack(">H", header[2:4])[0]
    if not 0 < count <= 64:
        return None
    offsets = []
    for _ in range(count):
        record = handle.read(8)
        if len(record) < 8:
            break
        platform, encoding, offset = struct.unpack(">HHI", record)
        if any(platform == want and (enc is None or encoding == enc)
               for want, enc in _UNICODE_CMAPS):
            offsets.append(base + offset)
    best = None
    for offset in offsets:
        covered = _cmap_subtable_size(handle, offset)
        if covered is not None and (best is None or covered > best):
            best = covered
    return best


def _cmap_subtable_size(handle, offset: int) -> Optional[int]:
    """Characters covered by one Unicode character-map subtable."""
    handle.seek(offset)
    raw = handle.read(2)
    if len(raw) < 2:
        return None
    subtable_format = struct.unpack(">H", raw)[0]
    if subtable_format == 4:
        head = handle.read(6)
        if len(head) < 6:
            return None
        segments = struct.unpack(">H", head[4:6])[0] // 2
        if not 0 < segments <= 20000:
            return None
        handle.read(6)
        ends = struct.unpack(">%dH" % segments, handle.read(segments * 2))
        handle.read(2)
        starts = struct.unpack(">%dH" % segments, handle.read(segments * 2))
        return sum(_characters_in(start, end)
                   for start, end in zip(starts, ends) if start != 0xFFFF)
    if subtable_format == 6:
        head = handle.read(8)
        if len(head) < 8:
            return None
        first, entries = struct.unpack(">HH", head[4:8])
        return _characters_in(first, first + max(0, entries - 1))
    if subtable_format == 12:
        head = handle.read(14)
        if len(head) < 14:
            return None
        groups = struct.unpack(">I", head[10:14])[0]
        if not 0 < groups <= 20000:
            return None
        total = 0
        for _ in range(groups):
            record = handle.read(12)
            if len(record) < 12:
                break
            start, end, _glyph = struct.unpack(">III", record)
            total += _characters_in(start, end)
        return total
    return None


def _font_sets_text(path: str) -> bool:
    """Is this font file a typeface, or a box of pictures?

    Anything unreadable, unparseable or merely unusual is a typeface as far as
    this is concerned: the catalogue errs towards offering a font, never
    towards hiding one it did not understand.
    """
    try:
        with open(path, "rb") as handle:
            tables = _sfnt_tables(handle)
            if not tables:
                return True
            if _os2_family_class(handle, tables) == _SYMBOLIC_FAMILY_CLASS:
                return False
            covered = _cmap_codepoints(handle, tables)
            return covered is None or covered >= _MIN_TEXT_CODEPOINTS
    except (OSError, ValueError, struct.error):  # pragma: no cover - defensive
        return True


def _scan_font_files(extra_dirs: Sequence[str] = ()) -> dict:
    """``{family: {style key: path}}`` for every font file this host can read.

    Reading the family out of each file's own name table is what makes the
    catalogue honest: it is the same family string Qt reports to Calibre, so a
    font offered here is a font both renderers can actually use. The walk is
    bounded because a mounted share full of fonts must not stall the first
    request that opens the designer.
    """
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - Pillow is a hard runtime dependency
        return {}

    families: dict = {}
    seen_paths = set()
    examined = 0
    for root in tuple(extra_dirs) + _FONT_SEARCH_DIRS:
        if examined >= _MAX_FONT_FILES or not root or not os.path.isdir(root):
            continue
        for directory, _subdirs, filenames in os.walk(root):
            for filename in sorted(filenames):
                if examined >= _MAX_FONT_FILES:
                    break
                if not filename.lower().endswith(_FONT_EXTENSIONS):
                    continue
                path = os.path.join(directory, filename)
                try:
                    real = os.path.realpath(path)
                except OSError:  # pragma: no cover - defensive
                    continue
                if real in seen_paths:
                    continue
                seen_paths.add(real)
                examined += 1
                try:
                    family, style_name = ImageFont.truetype(path, 12).getname()
                except (OSError, ValueError, TypeError):
                    continue
                if not family or not _font_sets_text(path):
                    continue
                families.setdefault(str(family), {}).setdefault(_style_key(style_name or ""), path)
            if examined >= _MAX_FONT_FILES:
                break
    if examined >= _MAX_FONT_FILES:
        log.info("cover_generator: stopped scanning fonts at %s files", _MAX_FONT_FILES)
    return families


def _build_font_catalogue(binaries_dir: str = "") -> dict:
    """``{font id: FontEntry}``, generic aliases first, then real families."""
    scanned = _scan_font_files(_calibre_font_dirs(binaries_dir))
    entries: dict = {}

    # The three aliases exist whatever is installed, so a design travels between
    # machines. Each resolves to the first of its preference list that is here.
    for key, generic in GENERIC_FONTS.items():
        family = ""
        for candidate in generic["families"]:
            if candidate in scanned:
                family = candidate
                break
        entries[key] = FontEntry(
            id=key, label=generic["label"], family=family or generic["families"][0],
            generic=generic["generic"], css_stack=generic["css_stack"],
            files=dict(scanned.get(family, {})),
        )

    ordered = [name for name in _PREFERRED_FAMILIES if name in scanned]
    ordered += sorted(name for name in scanned if name not in _PREFERRED_FAMILIES)
    for family in ordered[:MAX_FONT_CATALOGUE]:
        key = _slug(family)
        if key in entries:
            key = key + "-family"
        if key in entries:  # pragma: no cover - two families slugging identically
            continue
        generic = _classify_family(family)
        entries[key] = FontEntry(
            id=key, label=family, family=family, generic=generic,
            css_stack="'%s', %s" % (family.replace("'", ""), GENERIC_FONTS[generic]["css_stack"]),
            files=dict(scanned[family]),
        )
    return entries


def font_catalogue(binaries_dir: str = "") -> dict:
    """The font vocabulary, built once per process.

    Fonts do not appear while the server runs, and rebuilding this means opening
    every font file on the host, so it is cached. ``reset_font_cache()`` exists
    for the tests, which stand up different font situations in one process.
    """
    with _font_cache_lock:
        cached = _font_cache.get(binaries_dir)
        if cached is None:
            cached = _build_font_catalogue(binaries_dir)
            _font_cache[binaries_dir] = cached
        return cached


def reset_font_cache() -> None:
    with _font_cache_lock:
        _font_cache.clear()


def font_entry(font_id: str, binaries_dir: str = "") -> Optional[FontEntry]:
    """The catalogue entry for *font_id*, or None.

    A font is chosen by id from a closed catalogue and never by path: the id is
    the only thing a request supplies, and an id that is not in this mapping
    never reaches a renderer, a file open or a subprocess argument.
    """
    return font_catalogue(binaries_dir).get(font_id)


# ---------------------------------------------------------------------------
# The design object: validation and resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverSpec:
    """A fully resolved rendering request — no ids left to look up."""

    style: str
    scheme: Optional[str]
    colors: dict
    fonts: dict
    align: dict
    text: dict
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT

    # -- v1 compatibility ---------------------------------------------------
    # v1's vocabulary was (scheme, font, layout). Callers that still speak it —
    # the admin default preset, the ingest and enforcer scripts — keep working.
    @property
    def layout(self) -> str:
        return self.style

    @property
    def font(self) -> str:
        return self.fonts["title"]["family"]

    @property
    def calibre_colors(self) -> dict:
        """The four colours under the names Calibre's ``theme_to_colors`` wants.

        Calibre paints the title with ``contrast_color1`` in Blocks and with
        ``contrast_color2`` in every other style. Undoing that here is what makes
        the "title" colour slot mean *the title's colour* in every arrangement,
        so switching arrangement never silently swaps a reader's two text
        colours. Applied to a builtin scheme the swap is exactly the inverse of
        the one :func:`scheme_colors` performs, so a named scheme still produces
        the bytes stock Calibre produces for that theme.
        """
        swap = STYLES[self.style]["swap_contrast"]
        title = self.colors["title"].lstrip("#")
        author = self.colors["author"].lstrip("#")
        return {
            "color1": self.colors["background"].lstrip("#"),
            "color2": self.colors["band"].lstrip("#"),
            "contrast_color1": author if swap else title,
            "contrast_color2": title if swap else author,
        }

    def scaled(self, max_width: int, max_height: int) -> "CoverSpec":
        """The same design at a smaller pixel size, aspect ratio preserved.

        Font sizes are design units on a :data:`REFERENCE_WIDTH`-wide cover, so a
        preview is the same picture with fewer pixels rather than a different
        design: what the reader approves is what gets applied.
        """
        if self.width <= max_width and self.height <= max_height:
            return self
        factor = min(max_width / float(self.width), max_height / float(self.height))
        return dataclasses.replace(
            self,
            width=max(MIN_DIMENSION, int(round(self.width * factor))),
            height=max(MIN_DIMENSION, int(round(self.height * factor))),
        )

    def font_pixels(self, slot: str) -> int:
        """The slot's font size in output pixels at this cover's width."""
        return max(4, int(round(self.fonts[slot]["size"] * self.width / float(REFERENCE_WIDTH))))

    def to_dict(self) -> dict:
        """The resolved design, in the wire shape the client sent."""
        return {
            "style": self.style,
            "scheme": self.scheme,
            "colors": dict(self.colors),
            "fonts": {slot: dict(entry) for slot, entry in self.fonts.items()},
            "align": dict(self.align),
            "text": dict(self.text),
            "size": {"width": self.width, "height": self.height},
        }


@dataclass(frozen=True)
class BookCoverMeta:
    """The book text a generated cover is made of."""

    title: str
    authors: Sequence[str] = ()
    series: Optional[str] = None
    series_index: Optional[float] = None
    publisher: Optional[str] = None
    year: Optional[int] = None
    tags: Sequence[str] = ()
    language: Optional[str] = None
    rating: Optional[float] = None


@dataclass(frozen=True)
class RenderedCover:
    data: bytes
    renderer: str
    spec: CoverSpec


def _clamp(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _clamp_dimension(value, fallback: int) -> int:
    return _clamp(value, MIN_DIMENSION, MAX_DIMENSION, fallback)


def scheme_colors(scheme: str, style: str) -> dict:
    """A named scheme's four colours, in the slot names the client speaks."""
    entry = COLOR_SCHEMES[scheme]
    swap = STYLES[style]["swap_contrast"]
    return {
        "background": "#" + entry["color1"],
        "band": "#" + entry["color2"],
        "title": "#" + entry["contrast_color2" if swap else "contrast_color1"],
        "author": "#" + entry["contrast_color1" if swap else "contrast_color2"],
    }


def _normalise_color(value, slot: str, strict: bool, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text.startswith("#"):
        text = "#" + text
    if not _HEX_COLOR.match(text):
        if strict:
            raise CoverGenerationError(
                "invalid_color", "The %s colour must look like #rrggbb." % slot)
        return fallback
    return text.lower()


def validate_template(template, slot: str) -> str:
    """Check one text template and return it normalised.

    This is deliberately *not* Calibre's template language. Calibre's has
    general-program and ``python:`` modes — arbitrary code — so a string a reader
    typed into a web form must never reach it. What is accepted here is a closed
    set of ``{placeholders}`` this module expands itself plus the four inline
    tags Calibre's ``parse_text_formatting`` understands; anything else is
    refused at the boundary rather than silently dropped, because a reader who
    typed something that does nothing deserves to be told.
    """
    if template is None:
        raise CoverGenerationError("invalid_text", "Missing text for the %s." % slot)
    text = str(template)
    if len(text) > MAX_TEMPLATE_LENGTH:
        raise CoverGenerationError(
            "invalid_text",
            "The %s text is longer than %s characters." % (slot, MAX_TEMPLATE_LENGTH))
    if "\x00" in text or "\n" in text or "\r" in text:
        raise CoverGenerationError(
            "invalid_text", "The %s text cannot contain line breaks; use <br> instead." % slot)
    for name in _PLACEHOLDER.findall(text):
        if name not in TEXT_PLACEHOLDERS:
            raise CoverGenerationError(
                "invalid_text", "There is no {%s} to put in the %s text." % (name, slot))
    stripped = _PLACEHOLDER.sub("", text)
    if "{" in stripped or "}" in stripped:
        raise CoverGenerationError(
            "invalid_text", "The %s text has a { or } that is not part of a placeholder." % slot)
    for match in re.finditer(r"<[^>]*>", text):
        if not _ALLOWED_TAGS.fullmatch(match.group(0)):
            raise CoverGenerationError(
                "invalid_text",
                "The %s text may only use <b>, <i> and <br>." % slot)
    return text


def _resolve_font(raw, slot: str, base: dict, strict: bool, binaries_dir: str) -> dict:
    catalogue_entries = font_catalogue(binaries_dir)
    entry = dict(base)
    if isinstance(raw, str):  # a bare font id is a fair thing to send
        raw = {"family": raw}
    if isinstance(raw, dict):
        family = raw.get("family")
        if family is not None:
            family = str(family)
            if family not in catalogue_entries:
                if strict:
                    raise CoverGenerationError(
                        "unknown_font", "Unknown font: %s" % _safe_id(family))
                log.info("cover_generator: unknown font %r for %s, using %s",
                         family, slot, entry["family"])
            else:
                entry["family"] = family
        if raw.get("size") is not None:
            entry["size"] = _clamp(raw.get("size"), MIN_FONT_SIZE, MAX_FONT_SIZE, entry["size"])
        for flag in ("bold", "italic"):
            if raw.get(flag) is not None:
                entry[flag] = bool(raw.get(flag))
    if entry["family"] not in catalogue_entries:
        entry["family"] = DEFAULT_FONT if DEFAULT_FONT in catalogue_entries else \
            (next(iter(catalogue_entries)) if catalogue_entries else DEFAULT_FONT)
    return entry


def _safe_id(value: str) -> str:
    """An id, trimmed and stripped of anything that is not id-shaped.

    Error messages quote back what was asked for so a client can fix it, and an
    id arrives from the network; this keeps a probe's payload from being echoed
    into a log line or a JSON message.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(value or ""))[:40]


def resolve_design(raw=None, preset: Optional[str] = None, scheme: Optional[str] = None,
                   font: Optional[str] = None, layout: Optional[str] = None,
                   width: Optional[int] = None, height: Optional[int] = None,
                   presets: Optional[dict] = None, strict: bool = True,
                   binaries_dir: str = "") -> CoverSpec:
    """Turn anything a client or the database can say into a complete ``CoverSpec``.

    Every layer is optional and they compose in one direction: the named preset
    is the base, v1's ``scheme``/``font``/``layout`` refine it, and the v2 design
    object wins over both. That ordering is what lets the old request bodies keep
    working unchanged while the designer sends whole designs.

    *strict* is the difference between a request and a memory. A request carries
    ids the client was just handed, so an id outside the catalogue is a bug or a
    probe and earns a 400. A stored preset carries ids that were valid when it
    was saved, and a font uninstalled since must not take the whole catalogue
    down with it, so unknown ids there fall back with a log line.
    """
    known_presets = PRESETS if presets is None else presets
    base_design: dict = {}
    if preset is not None or not isinstance(raw, dict):
        entry = known_presets.get(preset or DEFAULT_PRESET) or known_presets.get(DEFAULT_PRESET)
        if entry is None:
            entry = PRESETS[DEFAULT_PRESET]
        elif preset is not None and preset not in known_presets:
            # The library default is persisted as a preset id: one removed in a
            # later release must not take every automatic cover down with it.
            log.info("cover_generator: unknown preset %r, using %s", preset, DEFAULT_PRESET)
        base_design = dict(entry.get("design") or {})

    design: dict = dict(base_design)
    if layout is not None:
        design["style"] = layout
    if scheme is not None:
        design["scheme"] = scheme
    if font is not None:
        design["fonts"] = {slot: {"family": font} for slot in TEXT_SLOTS}
    if isinstance(raw, dict):
        for key in ("style", "scheme", "colors", "fonts", "align", "text", "size"):
            if key in raw and raw[key] is not None:
                design[key] = raw[key]
        if "scheme" in raw and raw["scheme"] is None:
            design["scheme"] = None
    elif raw is not None:
        raise CoverGenerationError("invalid_design", "The design must be an object.")

    # -- style --------------------------------------------------------------
    style = str(design.get("style") or DEFAULT_STYLE)
    if style not in STYLES:
        if strict:
            raise CoverGenerationError("unknown_style", "Unknown style: %s" % _safe_id(style))
        log.info("cover_generator: unknown style %r, using %s", style, DEFAULT_STYLE)
        style = DEFAULT_STYLE
    style_entry = STYLES[style]

    # -- colours ------------------------------------------------------------
    chosen_scheme = design.get("scheme", DEFAULT_SCHEME)
    if chosen_scheme is not None:
        chosen_scheme = str(chosen_scheme)
        if chosen_scheme not in COLOR_SCHEMES:
            if strict:
                raise CoverGenerationError(
                    "unknown_scheme", "Unknown colour scheme: %s" % _safe_id(chosen_scheme))
            log.info("cover_generator: unknown scheme %r, using %s", chosen_scheme, DEFAULT_SCHEME)
            chosen_scheme = DEFAULT_SCHEME
        colors = scheme_colors(chosen_scheme, style)
    else:
        colors = scheme_colors(DEFAULT_SCHEME, style)

    overrides = design.get("colors")
    if isinstance(overrides, dict):
        for slot in COLOR_SLOTS:
            if overrides.get(slot) is not None:
                colors[slot] = _normalise_color(overrides[slot], slot, strict, colors[slot])
    elif overrides is not None and strict:
        raise CoverGenerationError("invalid_color", "Colours must be given as an object.")

    # A scheme plus overrides that no longer match it is a custom palette; say so
    # rather than echoing back a scheme id whose colours are not on the cover.
    if chosen_scheme is not None and colors != scheme_colors(chosen_scheme, style):
        chosen_scheme = None

    # -- fonts, alignment, text --------------------------------------------
    raw_fonts = design.get("fonts") if isinstance(design.get("fonts"), dict) else {}
    fonts = {}
    for slot in TEXT_SLOTS:
        default_entry = {
            "family": DEFAULT_FONT,
            "size": DEFAULT_FONT_SIZES[slot],
            "bold": DEFAULT_FONT_EMPHASIS[slot]["bold"],
            "italic": DEFAULT_FONT_EMPHASIS[slot]["italic"],
        }
        fonts[slot] = _resolve_font(raw_fonts.get(slot), slot, default_entry, strict, binaries_dir)

    raw_align = design.get("align") if isinstance(design.get("align"), dict) else {}
    align = {}
    for slot in TEXT_SLOTS:
        value = raw_align.get(slot)
        if value is None:
            align[slot] = style_entry["align"][slot]
        elif str(value) in ALIGNMENTS:
            align[slot] = str(value)
        elif strict:
            raise CoverGenerationError(
                "invalid_align", "Alignment must be left, center or right.")
        else:
            align[slot] = style_entry["align"][slot]

    raw_text = design.get("text") if isinstance(design.get("text"), dict) else {}
    text = {}
    for slot in TEXT_SLOTS:
        value = raw_text.get(slot)
        if value is None:
            text[slot] = DEFAULT_TEXT[slot]
            continue
        try:
            text[slot] = validate_template(value, slot)
        except CoverGenerationError:
            if strict:
                raise
            text[slot] = DEFAULT_TEXT[slot]

    # -- size ---------------------------------------------------------------
    size = design.get("size") if isinstance(design.get("size"), dict) else {}
    fallback_width = _clamp_dimension(width, DEFAULT_WIDTH)
    fallback_height = _clamp_dimension(height, DEFAULT_HEIGHT)
    resolved_width = _clamp_dimension(size.get("width"), fallback_width)
    resolved_height = _clamp_dimension(size.get("height"), fallback_height)

    return CoverSpec(style=style, scheme=chosen_scheme, colors=colors, fonts=fonts,
                     align=align, text=text, width=resolved_width, height=resolved_height)


def resolve_spec(preset: Optional[str] = None, scheme: Optional[str] = None,
                 font: Optional[str] = None, layout: Optional[str] = None,
                 width: Optional[int] = None, height: Optional[int] = None,
                 design=None, presets: Optional[dict] = None, strict: bool = True,
                 binaries_dir: str = "") -> CoverSpec:
    """v1's entry point, unchanged in meaning: a preset id plus overrides."""
    return resolve_design(design, preset=preset, scheme=scheme, font=font, layout=layout,
                          width=width, height=height, presets=presets, strict=strict,
                          binaries_dir=binaries_dir)


# ---------------------------------------------------------------------------
# Text: turning a template plus a book into the exact string the renderer draws
# ---------------------------------------------------------------------------

def _format_series_index(value) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else ("%s" % number)


def _escape_formatting(value: str) -> str:
    """Calibre's own escaping, so a book's own text can never become markup.

    ``parse_text_formatting`` treats ``<b>`` in the string as a formatting run.
    A title that literally contains ``<b>`` must print, not embolden, so field
    values are escaped exactly the way Calibre escapes its own template output.
    """
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field_value(name: str, meta: BookCoverMeta) -> str:
    authors = [str(author) for author in (meta.authors or ()) if author]
    if name == "title":
        return str(meta.title or "")
    if name == "authors":
        return ", ".join(authors)
    if name == "author":
        return authors[0] if authors else ""
    if name == "series":
        return str(meta.series or "")
    if name == "series_index":
        return _format_series_index(meta.series_index)
    if name == "publisher":
        return str(meta.publisher or "")
    if name == "year":
        return str(meta.year) if meta.year else ""
    if name == "tags":
        return ", ".join(str(tag) for tag in (meta.tags or ()) if tag)
    if name == "language":
        return str(meta.language or "")
    if name == "rating":
        if not meta.rating:
            return ""
        rating = float(meta.rating)
        return str(int(rating)) if rating.is_integer() else ("%s" % rating)
    if name == "stars":
        if not meta.rating:
            return ""
        filled = max(0, min(5, int(round(float(meta.rating)))))
        return "★" * filled + "☆" * (5 - filled)
    return ""  # pragma: no cover - validate_template refuses unknown names


def _drop_empty_brackets(resolved: list) -> list:
    """Remove bracketed runs that ended up with nothing in them.

    ``{title} ({year})`` is a natural thing to type, and on a book with no
    publication year the brackets have to go with it. They cannot be handled by
    the one-sided separator rule — an opening bracket has text before it and a
    closing one has text after it — so a run is tracked from its opener to its
    matching closer and dropped whole when nothing inside it survived.

    Only the template's own brackets are considered: a run is split out of the
    literal text around the placeholders, never out of a field's value, so a
    book whose title really does contain "()" keeps it.
    """
    expanded = []
    for kind, text in resolved:
        if kind != "literal":
            expanded.append((kind, text))
            continue
        for piece in _BRACKET_SPLIT.split(text):
            if not piece:
                continue
            if piece in _BRACKET_PAIRS:
                expanded.append(("open", piece))
            elif piece in _BRACKET_PAIRS.values():
                expanded.append(("close", piece))
            elif _SEPARATOR_ONLY.match(piece):
                expanded.append(("separator", piece))
            else:
                expanded.append(("literal", piece))

    dropped = set()
    open_runs = []  # (index of the opener, the closer it wants, saw real text)
    for index, (kind, text) in enumerate(expanded):
        if kind == "open":
            open_runs.append([index, _BRACKET_PAIRS[text], False])
        elif kind == "close":
            if open_runs and open_runs[-1][1] == text:
                start, _wanted, saw_text = open_runs.pop()
                if saw_text:
                    if open_runs:
                        open_runs[-1][2] = True
                else:
                    dropped.update(range(start, index + 1))
            elif open_runs:
                open_runs[-1][2] = True  # a stray closer is just text
        elif kind in ("field", "literal") and text and open_runs:
            open_runs[-1][2] = True

    # An opener with no closer is text like any other; nothing to drop.
    return [("literal", text) if kind in ("open", "close") else (kind, text)
            for index, (kind, text) in enumerate(expanded) if index not in dropped]


def expand_text(template: str, meta: BookCoverMeta, bold: bool = False,
                italic: bool = False) -> str:
    """Expand one validated template against one book.

    No template engine runs here and none runs downstream: the helper script
    hands these strings to Calibre's layout verbatim. Field values are escaped,
    so the only markup in the result is markup the template itself carried or
    the slot's own bold/italic.

    Empty fields take their punctuation with them. The default subtitle is
    ``{series} {series_index}``, and a book with no series must get no subtitle
    rather than a stray space; by the same rule ``{series} - {series_index}``
    on a series whose books are unnumbered reads "Wayfarers", not "Wayfarers -".
    """
    parts = [part for part in _TOKEN_SPLIT.split(template or "") if part]
    resolved = []
    has_placeholder = False
    for part in parts:
        match = _PLACEHOLDER.fullmatch(part)
        if match:
            has_placeholder = True
            value = _field_value(match.group(1), meta)
            resolved.append(("field", _escape_formatting(value) if value else ""))
        elif _ALLOWED_TAGS.fullmatch(part):
            resolved.append(("tag", part))
        elif _SEPARATOR_ONLY.match(part):
            resolved.append(("separator", part))
        else:
            resolved.append(("literal", part))

    if has_placeholder and not any(text for kind, text in resolved if kind == "field"):
        return ""

    resolved = _drop_empty_brackets(resolved)

    # A separator only earns its place when there is real text on both sides of
    # it; markup does not count as text, or an empty field would keep its comma
    # and leave a pair of tags wrapped around nothing.
    kept = []
    for index, (kind, text) in enumerate(resolved):
        if kind == "separator":
            before = any(other for other_kind, other in resolved[:index]
                         if other_kind in ("field", "literal"))
            after = any(other for other_kind, other in resolved[index + 1:]
                        if other_kind in ("field", "literal"))
            if not (before and after):
                continue
        kept.append(text)

    result = re.sub(r"<br\s*/?>", "<br>", "".join(kept), flags=re.IGNORECASE)
    for _ in range(3):  # <b><i></i></b> takes one pass per nesting level
        collapsed = _EMPTY_TAG_PAIR.sub("", result)
        if collapsed == result:
            break
        result = collapsed
    segments = []
    for segment in result.split("<br>"):
        segment = re.sub(r"\s+", " ", segment).strip()
        if not segment:
            continue
        if italic:
            segment = "<i>%s</i>" % segment
        if bold:
            segment = "<b>%s</b>" % segment
        segments.append(segment)
    return "<br>".join(segments)


def cover_text(spec: CoverSpec, meta: BookCoverMeta) -> dict:
    """The three final strings, keyed by slot."""
    return {
        slot: expand_text(spec.text[slot], meta,
                          bold=spec.fonts[slot]["bold"], italic=spec.fonts[slot]["italic"])
        for slot in TEXT_SLOTS
    }


def _plain_text(markup: str) -> str:
    """The same string with the inline markup taken back out, for Pillow."""
    text = re.sub(r"<br\s*/?>", "\n", markup or "", flags=re.IGNORECASE)
    text = re.sub(r"</?(?:b|i|em|strong)>", "", text, flags=re.IGNORECASE)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def _style_thumbnail_url(style_id: str) -> str:
    return "/cover-designer/style-thumb/" + style_id


def _font_sample_url(font_id: str) -> str:
    return "/cover-designer/font-sample/" + font_id


def builtin_presets(binaries_dir: str = "") -> list:
    """The shipped presets, each as a fully resolved design."""
    entries = []
    for key, value in PRESETS.items():
        design = resolve_design(value["design"], strict=False,
                                binaries_dir=binaries_dir).to_dict()
        entries.append({"id": key, "name": value["label"], "label": value["label"],
                        "design": design, "builtin": True, "scope": "builtin"})
    return entries


def catalogue(binaries_dir: str = "", extra_presets: Sequence[dict] = (),
              hidden_builtins: Sequence[str] = (), thumb_url=None, sample_url=None,
              default_preset: str = "") -> dict:
    """The design vocabulary, shaped for the SPA's designer panel.

    Labels are English source strings: the SPA translates them through the same
    msgid catalogue as the rest of its chrome (see ``cps/spa_strings.py``).

    ``defaults`` is the design the panel opens on, and it is the chosen default
    preset's own design rather than a fourth set of values: the panel decides
    which preset is selected by matching it, so a design that matched nothing
    would open the panel on "Custom" every time.
    """
    thumb_url = thumb_url or _style_thumbnail_url
    sample_url = sample_url or _font_sample_url
    hidden = set(hidden_builtins or ())
    presets = [entry for entry in builtin_presets(binaries_dir) if entry["id"] not in hidden]
    presets.extend(extra_presets or ())

    chosen = default_preset if default_preset in PRESETS else DEFAULT_PRESET
    fonts = font_catalogue(binaries_dir)
    defaults = resolve_design(PRESETS[chosen]["design"], strict=False,
                              binaries_dir=binaries_dir).to_dict()

    return {
        "styles": [
            {"id": key, "label": value["label"], "description": value["description"],
             "thumbnail_url": thumb_url(key),
             "color_roles": dict(value["color_roles"]), "align": dict(value["align"])}
            for key, value in STYLES.items()
        ],
        "schemes": [
            {"id": key, "label": value["label"], "builtin": True,
             "colors": {"background": "#" + value["color1"], "band": "#" + value["color2"],
                        "title": "#" + value["contrast_color1"],
                        "author": "#" + value["contrast_color2"]},
             "swatch": ["#" + value["color1"], "#" + value["color2"]]}
            for key, value in COLOR_SCHEMES.items()
        ],
        "fonts": [
            {"id": entry.id, "label": entry.label, "css_stack": entry.css_stack,
             "sample_url": sample_url(entry.id), "generic": entry.generic}
            for entry in fonts.values()
        ],
        "presets": presets,
        "defaults": defaults,
        "limits": {
            "min_width": MIN_DIMENSION, "max_width": MAX_DIMENSION,
            "min_height": MIN_DIMENSION, "max_height": MAX_DIMENSION,
            "font_size_min": MIN_FONT_SIZE, "font_size_max": MAX_FONT_SIZE,
            "max_template_length": MAX_TEMPLATE_LENGTH,
            "max_name_length": MAX_PRESET_NAME_LENGTH,
            "max_presets": MAX_PRESETS_PER_USER,
        },
        "placeholders": [{"id": key, "label": value} for key, value in TEXT_PLACEHOLDERS.items()],
        "alignments": list(ALIGNMENTS),
        "text_slots": list(TEXT_SLOTS),
        "color_slots": list(COLOR_SLOTS),
        # v1 keys, still read by the admin settings page and older clients.
        "default_preset": chosen,
        "layouts": [{"id": key, "label": value["label"]} for key, value in STYLES.items()],
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def calibre_debug_path(binaries_dir: str = "") -> str:
    """Absolute path of a usable ``calibre-debug``, or "" when there is none.

    The configured binaries directory wins over ``PATH`` for the same reason the
    converter path does: an admin who points CWNG at one Calibre install must not
    get a different one here.
    """
    if binaries_dir:
        candidate = os.path.join(binaries_dir, "calibre-debug")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    override = os.environ.get("CWNG_CALIBRE_DEBUG_PATH", "")
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else ""
    return shutil.which("calibre-debug") or ""


def _helper_script_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "calibre_generate_cover.py",
    )


def _timeout_seconds() -> float:
    raw = os.environ.get("CWNG_COVER_GENERATOR_TIMEOUT_SECONDS", "")
    try:
        value = float(raw)
        return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS


def _preferred_renderer() -> str:
    """"calibre", "pil" or "auto" — the env knob exists so tests can pin one."""
    value = (os.environ.get("CWNG_COVER_GENERATOR_RENDERER") or "auto").strip().lower()
    return value if value in ("calibre", "pil", "auto") else "auto"


def _pil_available() -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError:  # pragma: no cover - Pillow is a hard runtime dependency
        return False
    return True


def renderer_availability(binaries_dir: str = "") -> dict:
    """Which renderers this installation can actually use, right now."""
    preferred = _preferred_renderer()
    calibre_ok = bool(calibre_debug_path(binaries_dir)) and preferred != "pil"
    pil_ok = _pil_available() and preferred != "calibre"
    return {
        "calibre": calibre_ok,
        "pil": pil_ok,
        "available": bool(calibre_ok or pil_ok),
        "renderer": "calibre" if calibre_ok else ("pil" if pil_ok else None),
    }


_COMPLEX_SCRIPT_WARNED = [False]


def _warn_once_if_no_complex_shaping(text: str) -> None:
    """Say so when Pillow cannot shape the script this title is written in.

    Arabic, Hebrew, Devanagari and the Indic scripts need HarfBuzz (Pillow's
    optional ``raqm`` layout engine) to join and reorder their glyphs; without it
    Pillow draws them unjoined and left-to-right, which is unreadable rather than
    merely plain. Calibre's renderer shapes them correctly, so this only bites a
    Calibre-less installation — and it is a log line, not a refusal: a wrong-
    looking cover the admin has been told about beats no cover with no reason.
    """
    if _COMPLEX_SCRIPT_WARNED[0] or not text:
        return
    if not any("\u0590" <= character <= "\u1cff" or "\ufb00" <= character <= "\ufdff"
               for character in text):
        return
    try:
        from PIL import features
        if features.check("raqm"):
            return
    except ImportError:  # pragma: no cover - defensive
        return
    _COMPLEX_SCRIPT_WARNED[0] = True
    log.warning(
        "cover_generator: this Pillow build has no raqm/HarfBuzz support, so generated "
        "covers for titles in Arabic, Hebrew or an Indic script will render unshaped. "
        "Install Calibre (its renderer shapes them correctly) or a Pillow built with raqm."
    )


def render(meta: BookCoverMeta, spec: CoverSpec, binaries_dir: str = "") -> RenderedCover:
    """Render ``meta`` as ``spec`` and return the JPEG bytes.

    Calibre first when it is installed; a Calibre failure (missing Qt, a timeout,
    a crash) falls through to Pillow rather than failing the request, because a
    plainer cover is a better answer than none. When neither renderer can run,
    ``CoverGenerationError('unavailable')`` says so honestly instead of writing a
    placeholder the caller would mistake for a cover.
    """
    preferred = _preferred_renderer()
    errors = []

    if preferred in ("auto", "calibre"):
        binary = calibre_debug_path(binaries_dir)
        if binary:
            try:
                return RenderedCover(
                    _render_with_calibre(binary, meta, spec, binaries_dir), "calibre", spec)
            except CoverGenerationError as error:
                errors.append("calibre: %s" % error.message)
                log.warning("cover_generator: calibre renderer failed (%s); falling back",
                            error.message)
        else:
            errors.append("calibre: calibre-debug not found")
        if preferred == "calibre":
            raise CoverGenerationError("unavailable", "; ".join(errors))

    if preferred in ("auto", "pil"):
        if _pil_available():
            return RenderedCover(_render_with_pil(meta, spec, binaries_dir), "pil", spec)
        errors.append("pil: Pillow not importable")

    raise CoverGenerationError("unavailable", "; ".join(errors) or "no renderer available")


def render_data_url(meta: BookCoverMeta, spec: CoverSpec, binaries_dir: str = "") -> tuple:
    """``(data_url, renderer)`` for a preview the browser can drop into an <img>."""
    rendered = render(meta, spec, binaries_dir=binaries_dir)
    encoded = base64.b64encode(rendered.data).decode("ascii")
    return "data:image/jpeg;base64," + encoded, rendered.renderer


# ---- Calibre ---------------------------------------------------------------

def _render_with_calibre(binary: str, meta: BookCoverMeta, spec: CoverSpec,
                         binaries_dir: str = "") -> bytes:
    script = _helper_script_path()
    if not os.path.isfile(script):  # pragma: no cover - packaging error
        raise CoverGenerationError("helper_missing", "helper script not found: %s" % script)

    texts = cover_text(spec, meta)
    fonts = font_catalogue(binaries_dir)
    request = json.dumps({
        "op": "render",
        "title": texts["title"],
        "subtitle": texts["subtitle"],
        "footer": texts["author"],
        "spec": {
            "width": spec.width,
            "height": spec.height,
            "style": STYLES[spec.style]["calibre_style"],
            "colors": spec.calibre_colors,
            "fonts": {
                CALIBRE_SLOT[slot]: {
                    # The family name, resolved from the id here: a renderer
                    # argument is never a string the client chose.
                    "family": (fonts[spec.fonts[slot]["family"]].family
                               if spec.fonts[slot]["family"] in fonts else None),
                    "size": spec.font_pixels(slot),
                }
                for slot in TEXT_SLOTS
            },
            "align": {CALIBRE_SLOT[slot]: spec.align[slot] for slot in TEXT_SLOTS},
        },
    })

    env = os.environ.copy()
    # No display in a container, and none on a headless test host either.
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        completed = subprocess.run(
            [binary, "-e", script],
            input=request,
            capture_output=True,
            text=True,
            timeout=_timeout_seconds(),
            env=env,
            # Its own session so a wedged Qt child tree dies with the parent kill.
            start_new_session=(os.name != "nt"),
        )
    except subprocess.TimeoutExpired:
        raise CoverGenerationError("timeout", "calibre-debug timed out after %ss" % _timeout_seconds())
    except OSError as error:
        raise CoverGenerationError("spawn_failed", str(error))

    payload = None
    for line in reversed((completed.stdout or "").splitlines()):
        if line.startswith("CWNG_COVER_RESULT="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except ValueError as error:
                raise CoverGenerationError("bad_result", "unparseable result line: %s" % error)
            break
    if payload is None:
        raise CoverGenerationError(
            "no_result",
            "helper produced no result (exit %s): %s" % (
                completed.returncode, (completed.stderr or "").strip()[:400]),
        )
    if not payload.get("ok"):
        raise CoverGenerationError("render_failed", str(payload.get("error") or "unknown error"))

    try:
        data = base64.b64decode(payload.get("data") or "", validate=True)
    except (ValueError, TypeError) as error:
        raise CoverGenerationError("bad_result", "result payload is not base64: %s" % error)
    if not data:
        raise CoverGenerationError("bad_result", "result payload is empty")
    if len(data) > MAX_OUTPUT_BYTES:
        raise CoverGenerationError(
            "too_large", "generated cover is %s bytes, over the %s byte cap" % (
                len(data), MAX_OUTPUT_BYTES))
    return data


def calibre_font_families(binaries_dir: str = "") -> list:
    """The families Calibre's Qt can actually draw with, asked of Calibre itself.

    Only used by the documentation/diagnostic path: the catalogue is built from
    the font files on disk, which is the same set without a 2-second subprocess
    on the request that opens the designer.
    """
    binary = calibre_debug_path(binaries_dir)
    if not binary:
        return []
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        completed = subprocess.run(
            [binary, "-e", _helper_script_path()], input=json.dumps({"op": "fonts"}),
            capture_output=True, text=True, timeout=_timeout_seconds(), env=env,
            start_new_session=(os.name != "nt"))
    except (subprocess.TimeoutExpired, OSError) as error:
        log.info("cover_generator: could not list calibre fonts: %s", error)
        return []
    for line in reversed((completed.stdout or "").splitlines()):
        if line.startswith("CWNG_COVER_RESULT="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except ValueError:
                return []
            return list(payload.get("families") or []) if payload.get("ok") else []
    return []


# ---- Pillow ----------------------------------------------------------------
#
# The Pillow path exists so an installation without Calibre still gets a real
# cover, and so the tests have a renderer that needs no Qt. It accepts exactly
# the designs the Calibre path accepts and draws each arrangement from the same
# geometry Calibre uses — the margins, the band heights and the banner's curves
# are ported, not eyeballed — but it is a second implementation, not a pixel
# clone: Qt's text layout, its gradients and Ornamental's engraved corner
# flourishes are not reproducible with Pillow's primitives. Nothing compares the
# two outputs; ``render()`` reports which one drew the bytes.

def _hex_to_rgb(value: str) -> tuple:
    value = str(value).lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _mix(first: tuple, second: tuple, position: float) -> tuple:
    return tuple(int(round(a + (b - a) * position)) for a, b in zip(first, second))


def _gradient_color(stops: Sequence[tuple], position: float) -> tuple:
    position = max(0.0, min(1.0, position))
    previous = stops[0]
    for stop in stops:
        if position <= stop[0]:
            if stop[0] == previous[0]:
                return stop[1]
            span = (position - previous[0]) / (stop[0] - previous[0])
            return _mix(previous[1], stop[1], span)
        previous = stop
    return stops[-1][1]


def _linear_gradient(size: tuple, stops: Sequence[tuple]):
    """A vertical gradient, built one row wide and stretched."""
    from PIL import Image

    width, height = size
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    for y in range(height):
        pixels[0, y] = _gradient_color(stops, y / float(max(1, height - 1)))
    return column.resize((width, height))


def _radial_gradient(size: tuple, inner: tuple, outer: tuple):
    """Calibre's Ornamental wash: *inner* at the centre, *outer* one width out."""
    from PIL import Image

    width, height = size
    small_w, small_h = max(2, width // 8), max(2, height // 8)
    small = Image.new("RGB", (small_w, small_h))
    pixels = small.load()
    centre_x, centre_y = (small_w - 1) / 2.0, (small_h - 1) / 2.0
    radius = float(small_w)
    for y in range(small_h):
        for x in range(small_w):
            distance = ((x - centre_x) ** 2 + ((y - centre_y) * small_w / float(small_h)) ** 2) ** 0.5
            pixels[x, y] = _mix(inner, outer, min(1.0, distance / radius))
    return small.resize((width, height), Image.BILINEAR)


def _pil_font(entry: Optional[FontEntry], size: int, bold: bool, italic: bool, fonts: dict):
    """``(font, synthetic_bold)`` for one slot.

    A family installed in one weight only still honours a bold slot: Pillow has
    no synthetic bold, so the caller strokes the glyphs instead, which is what
    Qt does for the same case.
    """
    from PIL import ImageFont

    wanted = ("bold_italic" if bold and italic else
              "bold" if bold else "italic" if italic else "regular")
    order = {
        "bold_italic": ("bold_italic", "bold", "italic", "regular"),
        "bold": ("bold", "bold_italic", "regular", "italic"),
        "italic": ("italic", "bold_italic", "regular", "bold"),
        "regular": ("regular", "italic", "bold", "bold_italic"),
    }[wanted]

    sources = [entry] if entry is not None else []
    if entry is not None and entry.generic in fonts and fonts[entry.generic] is not entry:
        sources.append(fonts[entry.generic])
    for source in sources:
        for key in order:
            path = source.files.get(key)
            if not path:
                continue
            try:
                loaded = ImageFont.truetype(path, size)
            except (OSError, ValueError):  # pragma: no cover - unreadable font file
                continue
            return loaded, bold and key in ("regular", "italic")
    try:
        # Pillow >= 10.1 scales its built-in face; older ones ignore the size and
        # still render legible (if small) text rather than failing the render.
        return ImageFont.load_default(size=size), False
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default(), False


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list:
    """Greedy word wrap measured with the real font, truncated with an ellipsis."""
    if not text:
        return []
    lines: list = []
    paragraphs = text.split("\n")
    for index, paragraph in enumerate(paragraphs):
        words = paragraph.split()
        if not words:
            continue
        remaining = max_lines - len(lines)
        if remaining <= 0:
            break
        # The last paragraph may use every line left; earlier ones keep at least
        # one line back for each paragraph still to come.
        budget = remaining if index == len(paragraphs) - 1 else max(
            1, remaining - (len(paragraphs) - 1 - index))
        current = words[0]
        used: list = []
        overflowed = False
        for word in words[1:]:
            trial = current + " " + word
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                used.append(current)
                current = word
                if len(used) == budget:
                    overflowed = True
                    break
        if not overflowed and len(used) < budget:
            used.append(current)
        if overflowed:
            last = used[-1]
            while last and draw.textlength(last + "…", font=font) > max_width:
                last = last[:-1].rstrip()
            used[-1] = (last + "…") if last else "…"
        lines.extend(used)
    return lines


def _draw_lines(draw, lines, font, fill, left: int, top: int, width: int, align: str,
                line_spacing: int, stroke: int = 0) -> None:
    y = top
    for line in lines:
        text_width = draw.textlength(line, font=font)
        if align == "left":
            x = left
        elif align == "right":
            x = left + width - text_width
        else:
            x = left + (width - text_width) / 2.0
        try:
            draw.text((x, y), line, font=font, fill=fill, anchor="la",
                      stroke_width=stroke, stroke_fill=fill)
        except (ValueError, TypeError):  # pragma: no cover - Pillow's bitmap fallback
            draw.text((x, y), line, font=font, fill=fill)
        y += line_spacing


def _rotate_vector(angle: float, x: float, y: float) -> tuple:
    return x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)


class _Path:
    """A tiny polyline builder with Calibre's own curved-edge maths.

    Banner's ribbon and its folds are cubic Béziers whose control points are
    expressed along and across the edge; porting that here (rather than guessing
    a trapezoid) is what makes the Pillow banner the same shape as Calibre's.
    """

    def __init__(self, start: tuple):
        self.points = [start]

    @property
    def current(self) -> tuple:
        return self.points[-1]

    def line_to(self, x: float, y: float) -> None:
        self.points.append((x, y))

    def curve(self, dx: float, dy: float, c1_frac: float, c1_amp: float,
              c2_frac: float, c2_amp: float, steps: int = 24) -> None:
        length = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        origin = self.current
        c1 = _rotate_vector(angle, c1_frac * length, c1_amp * length)
        c2 = _rotate_vector(angle, c2_frac * length, c2_amp * length)
        p0 = origin
        p1 = (origin[0] + c1[0], origin[1] + c1[1])
        p2 = (origin[0] + c2[0], origin[1] + c2[1])
        p3 = (origin[0] + dx, origin[1] + dy)
        for step in range(1, steps + 1):
            t = step / float(steps)
            inverse = 1 - t
            x = (inverse ** 3 * p0[0] + 3 * inverse ** 2 * t * p1[0]
                 + 3 * inverse * t ** 2 * p2[0] + t ** 3 * p3[0])
            y = (inverse ** 3 * p0[1] + 3 * inverse ** 2 * t * p1[1]
                 + 3 * inverse * t ** 2 * p2[1] + t ** 3 * p3[1])
            self.points.append((x, y))


def _style_margins(style: str, width: int, height: int) -> tuple:
    """Calibre's own margin arithmetic, per style."""
    if style == "banner":
        return int(0.15 * width), int((50 / 800.0) * height)
    if style == "ornamental":
        return int((51 / 400.0) * width), int((83 / 500.0) * height)
    return int((50 / 600.0) * width), int((50 / 800.0) * height)


def _draw_ornaments(draw, width: int, height: int, pen: tuple) -> None:
    """Ornamental's engraved rules and corner brackets.

    Calibre draws a carved SVG flourish in each corner; Pillow has no path
    engine to fill it with, so the corners become double brackets. The eight
    centre rules are at Calibre's own coordinates, scaled from its 400x500
    design viewport.
    """
    sx, sy = width / 400.0, height / 500.0
    thin = max(1, int(round(sx)))
    thick = max(2, int(round(1.8 * sx)))
    for y, line_width in ((28.4, thin), (471.7, thin), (23.8, thick), (476.7, thick)):
        draw.line([(160 * sx, y * sy), (240 * sx, y * sy)], fill=pen, width=line_width)
    for x, line_width in ((31.3, thin), (368.7, thin), (26.3, thick), (373.7, thick)):
        draw.line([(x * sx, 155 * sy), (x * sx, 345 * sy)], fill=pen, width=line_width)

    for corner_x, step_x in ((20.0, 1), (380.0, -1)):
        for corner_y, step_y in ((20.0, 1), (480.0, -1)):
            for inset in (0.0, 7.0):
                x = (corner_x + step_x * inset) * sx
                y = (corner_y + step_y * inset) * sy
                draw.line([(x, (corner_y + step_y * (inset + 90)) * sy), (x, y),
                           ((corner_x + step_x * (inset + 90)) * sx, y)],
                          fill=pen, width=thin, joint="curve")


def _render_with_pil(meta: BookCoverMeta, spec: CoverSpec, binaries_dir: str = "") -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    width, height = spec.width, spec.height
    fonts = font_catalogue(binaries_dir)
    colors = {slot: _hex_to_rgb(value) for slot, value in spec.colors.items()}
    # Half and half paints every block with one colour, and the styles that swap
    # Calibre's two contrast colours draw their ornaments with the other one.
    text_pen = colors["title"]
    author_pen = colors["title"] if spec.style == "half" else colors["author"]
    ornament_pen = colors["author"] if STYLES[spec.style]["swap_contrast"] else colors["title"]

    markup = cover_text(spec, meta)
    texts = {slot: _plain_text(markup[slot]) for slot in TEXT_SLOTS}
    _warn_once_if_no_complex_shaping(texts["title"])

    hmargin, vmargin = _style_margins(spec.style, width, height)
    text_width = max(10, width - 2 * hmargin)

    image = Image.new("RGB", (width, height), colors["background"])
    draw = ImageDraw.Draw(image)

    def build(slot: str, max_height: int) -> dict:
        size_px = spec.font_pixels(slot)
        entry = fonts.get(spec.fonts[slot]["family"])
        font, synthetic_bold = _pil_font(entry, size_px, spec.fonts[slot]["bold"],
                                         spec.fonts[slot]["italic"], fonts)
        spacing = max(1, int(round(size_px * 1.18)))
        lines = _wrap(draw, texts[slot], font, text_width, max(1, int(max_height // spacing)))
        return {"font": font, "lines": lines, "spacing": spacing,
                "height": spacing * len(lines), "size": size_px,
                "stroke": max(1, size_px // 26) if synthetic_bold else 0}

    # Calibre gives the title and the footer a third of the cover each, and the
    # subtitle whatever the title left behind.
    third = height // 3
    blocks = {"title": build("title", third)}
    gap = max(2, int(round(blocks["title"]["size"] * 0.08)))
    blocks["subtitle"] = build("subtitle", max(1, third - blocks["title"]["height"] - gap))
    blocks["author"] = build("author", third)

    title_top = vmargin
    subtitle_top = title_top + blocks["title"]["height"] + (gap if blocks["subtitle"]["lines"] else 0)
    author_top = max(0, height - vmargin - blocks["author"]["height"])

    if spec.style == "blocks":
        draw.rectangle([0, height - height // 3, width, height], fill=colors["band"])

    elif spec.style == "half":
        image.paste(_linear_gradient((width, height), [
            (0.0, colors["background"]), (0.7, colors["band"]), (1.0, colors["background"])]), (0, 0))
        draw = ImageDraw.Draw(image)

    elif spec.style == "cross":
        if blocks["subtitle"]["lines"]:
            band_bottom = subtitle_top + blocks["subtitle"]["height"] + blocks["subtitle"]["spacing"] // 2
        else:
            band_bottom = title_top + blocks["title"]["height"] + gap
        # Calibre's rounded rect uses a relative radius that works out at 5% of
        # the cover width in both directions.
        draw.rounded_rectangle([0, title_top, width, max(title_top + 4, band_bottom)],
                               radius=max(4, int(0.05 * width)), fill=colors["band"])
        draw.rectangle([0, 0, hmargin, height], fill=colors["band"])

    elif spec.style == "banner":
        top = title_top + 2
        extra = (blocks["subtitle"]["spacing"] // 2 if blocks["subtitle"]["lines"]
                 else blocks["title"]["spacing"] // 3)
        band_height = blocks["title"]["height"] + blocks["subtitle"]["height"] + extra
        right = width - hmargin
        band_width = right - hmargin
        deltax = 0.07 * band_height
        fold_width = int(0.1 * width)
        width23 = int(0.67 * fold_width)

        main = _Path((hmargin, top))
        main.curve(width - 2 * hmargin, 0, 0.1, -0.1, 0.9, -0.1)
        main.line_to(right + deltax, top + band_height)
        right_corner = main.current
        main.curve(-band_width - 2 * deltax, 0, 0.1, 0.05, 0.9, 0.05)
        left_corner = main.current

        def fold(x: float, direction: float, corner: tuple) -> tuple:
            path = _Path((x, top + band_height * 0.1))
            path.curve(fold_width * direction, 0, 0.1, 0.1 * direction, 0.5, -0.2 * direction)
            fold_upper = path.current
            path.line_to(path.current[0] - deltax * direction, path.current[1] + band_height)
            fold_corner = path.current
            path.curve(-fold_width * direction, 0, 0.2, -0.1 * direction, 0.8, -0.1 * direction)
            path.curve(deltax * direction, -band_height, 0.2, 0.1 * direction, 0.8, 0.1 * direction)
            inner = _Path(corner)
            inner.curve(fold_corner[0] - corner[0], fold_corner[1] - corner[1], 0.5, 0.3 * direction, 1, 0)
            inner.line_to(*fold_upper)
            return path.points, inner.points

        left_fold, left_inner = fold(hmargin - width23, 1, left_corner)
        right_fold, right_inner = fold(right + width23, -1, right_corner)
        shadow = tuple(value // 2 for value in colors["band"])
        outline_width = max(1, int(round(3 * width / float(REFERENCE_WIDTH))))
        for points, fill in ((left_fold, colors["band"]), (right_fold, colors["band"]),
                             (left_inner, shadow), (right_inner, shadow),
                             (main.points, colors["band"])):
            draw.polygon(points, fill=fill)
            draw.line(list(points) + [points[0]], fill=text_pen, width=outline_width, joint="curve")

    else:  # ornamental
        image.paste(_radial_gradient((width, height), colors["background"], colors["band"]), (0, 0))
        draw = ImageDraw.Draw(image)
        _draw_ornaments(draw, width, height, ornament_pen)

    for slot, top in (("title", title_top), ("subtitle", subtitle_top), ("author", author_top)):
        block = blocks[slot]
        if not block["lines"]:
            continue
        _draw_lines(draw, block["lines"], block["font"],
                    text_pen if slot != "author" else author_pen,
                    hmargin, top, text_width, spec.align[slot], block["spacing"], block["stroke"])

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    data = buffer.getvalue()
    if len(data) > MAX_OUTPUT_BYTES:  # pragma: no cover - flat colour never gets here
        raise CoverGenerationError("too_large", "generated cover exceeds the size cap")
    return data


# ---------------------------------------------------------------------------
# Catalogue imagery
# ---------------------------------------------------------------------------

# A stand-in book for the style thumbnails. It is deliberately ordinary: the
# picture is there to show where the title sits and what the band does, not to
# be read. (The strings are drawn into a JPEG, so they are not translatable;
# the translated style label goes under the thumbnail in the panel.)
SAMPLE_BOOK = BookCoverMeta(title="The Book Title", authors=("Author Name",),
                            series="Series", series_index=1)


def style_thumbnail(style_id: str, binaries_dir: str = "",
                    width: int = THUMBNAIL_WIDTH, height: int = THUMBNAIL_HEIGHT) -> RenderedCover:
    """A small neutral-coloured sample of one arrangement."""
    if style_id not in STYLES:
        raise CoverGenerationError("unknown_style", "Unknown style: %s" % _safe_id(style_id))
    spec = resolve_design({"style": style_id, "scheme": NEUTRAL_SCHEME,
                           "size": {"width": width, "height": height}},
                          strict=False, binaries_dir=binaries_dir)
    return render(SAMPLE_BOOK, spec, binaries_dir=binaries_dir)


def font_sample(font_id: str, binaries_dir: str = "",
                width: int = THUMBNAIL_WIDTH, height: int = THUMBNAIL_HEIGHT) -> RenderedCover:
    """A small neutral-coloured sample of one lettering, set in that lettering."""
    entry = font_entry(font_id, binaries_dir)
    if entry is None:
        raise CoverGenerationError("unknown_font", "Unknown font: %s" % _safe_id(font_id))
    spec = resolve_design({
        "style": "blocks", "scheme": NEUTRAL_SCHEME,
        "fonts": {slot: {"family": entry.id} for slot in TEXT_SLOTS},
        "text": {"title": "{title}", "subtitle": "{series}", "author": "{authors}"},
        "size": {"width": width, "height": height},
    }, strict=False, binaries_dir=binaries_dir)
    sample = BookCoverMeta(title=entry.label, authors=("0123456789",), series="AaBbCc")
    return render(sample, spec, binaries_dir=binaries_dir)


# ---------------------------------------------------------------------------
# Settings, shared by the app and the standalone ingest/enforcer scripts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratorSettings:
    auto_enabled: bool
    default_preset: str


def settings_from_app_db(app_db_path: str) -> GeneratorSettings:
    """Read the two library settings straight out of ``app.db``.

    ``ingest_processor.py`` and ``cover_enforcer.py`` run as standalone
    processes under s6 with no Flask application, and both already read this
    table with sqlite3. A missing column (an installation that has not migrated
    yet) reads as "feature off" rather than raising.
    """
    try:
        connection = sqlite3.connect(app_db_path, timeout=30)
    except sqlite3.Error as error:  # pragma: no cover - defensive
        log.warning("cover_generator: could not open %s: %s", app_db_path, error)
        return GeneratorSettings(False, DEFAULT_PRESET)
    try:
        row = connection.execute(
            "SELECT config_cover_generator_auto_enabled, config_cover_generator_default_preset "
            "FROM settings"
        ).fetchone()
    except sqlite3.Error:
        return GeneratorSettings(False, DEFAULT_PRESET)
    finally:
        connection.close()
    if not row:
        return GeneratorSettings(False, DEFAULT_PRESET)
    return GeneratorSettings(bool(row[0]), str(row[1] or DEFAULT_PRESET))


def generate_cover_file(destination: str, meta: BookCoverMeta,
                        preset: Optional[str] = None,
                        binaries_dir: str = "",
                        design=None) -> bool:
    """Write a generated ``cover.jpg`` at *destination*. False if it exists.

    Used by the ingest and enforcement paths, which own the ``has_cover`` flag
    themselves. Refusing to overwrite an existing file is what keeps "generate a
    cover for books that arrive with none" from ever touching a book that has
    one — including on a re-run over the same library.
    """
    if os.path.exists(destination):
        return False
    # An automatic cover must never fail on a design someone saved months ago:
    # resolve it leniently, the way every stored design is resolved.
    spec = resolve_design(design, preset=preset, strict=False, binaries_dir=binaries_dir)
    rendered = render(meta, spec, binaries_dir=binaries_dir)
    directory = os.path.dirname(destination)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    # Write a sibling and rename so a reader never sees a half-written cover.
    staging = destination + ".cwng-generating"
    with open(staging, "wb") as handle:
        handle.write(rendered.data)
    os.replace(staging, destination)
    log.info("cover_generator: wrote generated cover (%s renderer) to %s",
             rendered.renderer, destination)
    return True
