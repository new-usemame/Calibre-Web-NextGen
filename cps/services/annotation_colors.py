# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one place that maps annotation highlight colours between the
stored wire vocabulary and the display vocabulary.

WHY THIS MODULE EXISTS
----------------------
``annotation.highlight_color`` accumulated three vocabularies at once
(finding ``F-5769c9``, measured on a deployed server 2026-08-18):

* the live Kobo ``PATCH`` path stores whatever the device sent, which is a
  **wire hex** such as ``#F6F3B3`` — 612 of 616 device rows;
* the ``KoboReader.sqlite`` importer stored a **name** it derived from a
  lookup table that was wrong in three separate ways;
* the web reader stores a **name** from its own four-colour palette, one of
  which (``red``) no Kobo device can produce.

The decision (operator, 2026-08-18) is: **store the wire hex, normalise on
read.** This module is that normalisation, in both directions, and it is
deliberately tolerant of every shape already in the column — canonical hex,
a legacy name, ``NULL`` — because there is no data migration.

THE MEASURED KOBO MAPPING
-------------------------
``Bookmark.Color`` in ``KoboReader.sqlite`` and the ``highlightColor`` the
device sends and accepts over the wire, measured on the operator's own Kobo
Clara BW (firmware 4.45.23792) by serving each palette hex in an authored
response and reading ``Bookmark.Color`` back:

======  =========  ======
int     hex        name
======  =========  ======
0       #F6F3B3    yellow
1       #E8AFCF    pink
2       #B2E1E8    blue
3       #C6E09E    green
4       #A0A0A0    grey
======  =========  ======

A greyscale device (Clara BW) writes ``Color=4`` for every organic
highlight, which is why the missing entry made **every** highlight on such a
device import as yellow.

Kobo has no red. ``red`` is a CWNG web-reader-only colour and carries its own
canonical hex (:data:`WEBREADER_RED_HEX`) so it round-trips through the same
table as every other colour instead of being a special case at each callsite.

TWO RULES THIS MODULE KEEPS
---------------------------
1. **Never invent a colour.** An unrecognised ``Bookmark.Color`` integer
   resolves to ``None`` ("unknown"), never to a specific colour. The previous
   ``.get(color or 0, "yellow")`` default is what made a failed lookup
   indistinguishable from a real yellow highlight downstream.
2. **Never destroy a colour.** A token this table does not know (a KOReader
   palette name, a hex from a future firmware) survives both directions rather
   than being nulled, so no third-party vocabulary is lost by being routed
   through here. "Survives" means the token itself is preserved after
   whitespace trimming, case folding, and known-alias folding — not that the
   string comes back byte-identical.

PRESERVATION IS NOT VALIDATION, and the two have separate functions here.
:func:`to_display_name` preserves an unknown token because an export and a
backup want the honest stored value. A consumer that needs a token it can *key
a palette on* — a CSS class, a tag, a protocol enum — must use
:func:`to_known_display_name`, which answers ``None`` rather than handing back
something no palette has an entry for.
"""

from __future__ import annotations

from typing import Optional

# Kobo's Bookmark.Color integer -> the wire hex the device sends and accepts.
# MEASURED on a Kobo Clara BW (4.45.23792), 2026-08-18 — finding F-5769c9.
# This supersedes the earlier inferred table, which had no entry for 4, had 2
# and 3 swapped, and called 1 "red".
KOBO_BOOKMARK_COLOR_HEX = {
    0: "#F6F3B3",
    1: "#E8AFCF",
    2: "#B2E1E8",
    3: "#C6E09E",
    4: "#A0A0A0",
}

# The web reader's own red. No Kobo device produces it; it exists because the
# web reader has offered red since the reader shipped and 4 rows on the
# operator's server already hold the legacy name. The value is the exact fill
# the reader already paints for red, so nothing on screen changes.
WEBREADER_RED_HEX = "#D9534F"

# Canonical hex (lower-case key) -> the display token every UI palette keys on.
_HEX_TO_NAME = {
    "#f6f3b3": "yellow",
    "#e8afcf": "pink",
    "#b2e1e8": "blue",
    "#c6e09e": "green",
    "#a0a0a0": "grey",
    WEBREADER_RED_HEX.lower(): "red",
}

# Display token -> canonical stored hex. Built from the table above so the two
# directions cannot drift apart.
_NAME_TO_HEX = {name: hexval.upper() for hexval, name in _HEX_TO_NAME.items()}

# Spellings that mean an existing display token. Kept separate from the
# canonical table so `to_display_name` always answers with one spelling.
_NAME_ALIASES = {
    "gray": "grey",
}

# The colours the web reader's create/edit palette accepts. Unchanged by the
# vocabulary switch on purpose: this is the UI's input contract, and widening
# it is a product decision, not a storage one. Note these are the four the
# reader OFFERS, not "the set a Kobo round-trips" — a Kobo round-trips
# yellow/pink/blue/green/grey and cannot represent red at all.
WEBREADER_COLOR_NAMES = ("yellow", "red", "green", "blue")


def _token(value) -> Optional[str]:
    """Reduce any incoming colour value to a comparable lower-case token.

    Returns ``None`` for ``None``, a blank string, and any non-string that is
    not already a string — i.e. "there is no colour here", never a guess.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token or None


def hex_for_bookmark_color(value) -> Optional[str]:
    """Map a ``KoboReader.sqlite`` ``Bookmark.Color`` integer to its wire hex.

    Returns ``None`` for anything not in the measured table — including
    ``NULL``, a bool, and a colour index a future firmware invents. ``None``
    means *unknown*, and it is stored as ``NULL`` so no read path can mistake
    a failed lookup for a real colour.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return KOBO_BOOKMARK_COLOR_HEX.get(value)


def to_storage_color(value) -> Optional[str]:
    """Normalise any accepted colour token to the canonical stored form.

    * a display name this table knows -> its canonical upper-case hex;
    * a hex this table knows, in any case -> the canonical upper-case hex;
    * ``None`` / blank / a non-string -> ``None``;
    * anything else -> the token, **preserved** after trimming, lower-casing
      and known-alias folding. A vocabulary this module has not been taught (a
      KOReader palette name, a hex from a newer device) is kept rather than
      discarded, so routing a value through here never loses it.
    """
    token = _token(value)
    if token is None:
        return None
    token = _NAME_ALIASES.get(token, token)
    if token in _NAME_TO_HEX:
        return _NAME_TO_HEX[token]
    if token in _HEX_TO_NAME:
        return token.upper()
    return token


def to_display_name(value) -> Optional[str]:
    """Normalise a stored colour to what should be SHOWN for it.

    * a canonical hex -> its name (``#A0A0A0`` -> ``grey``);
    * a legacy name still in the column -> that name (``red`` -> ``red``),
      which is why no migration is needed;
    * ``None`` / blank / a non-string -> ``None``, so "no colour" and
      "unknown colour" stay distinguishable from a real one;
    * anything else -> the token, preserved after trimming, lower-casing and
      known-alias folding.

    ⚠️ The last case means the result is **not guaranteed to be a name any
    palette knows** — it can be a raw hex or a foreign vocabulary's word. That
    is deliberate for exports and backups, which want the honest stored value.
    Anything that keys a palette, builds a CSS class, or fills a protocol enum
    must use :func:`to_known_display_name` instead.
    """
    token = _token(value)
    if token is None:
        return None
    token = _NAME_ALIASES.get(token, token)
    if token in _HEX_TO_NAME:
        return _HEX_TO_NAME[token]
    return token


def to_known_display_name(value) -> Optional[str]:
    """Like :func:`to_display_name`, but answers ``None`` for anything this
    module cannot name.

    For consumers where an unrecognised token is worse than nothing: a CSS
    class (``cwa-annotation-#123456`` is not a selector), a human-facing tag,
    or any surface that indexes a fixed palette. "I don't know" is a state
    those can render; a token with no palette entry is not.
    """
    name = to_display_name(value)
    return name if name in _NAME_TO_HEX else None
