# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Match the files found on a USB-connected Kindle back to books in the library.

The browser lists the Kindle over WebUSB and only knows file names. Books put there by this app
(or by Calibre) are named "<title> - <authors>.<ext>", so a name carries enough to find the book
again. Names are compared with punctuation, case and diacritics removed, because the device
filename is a sanitized form of the title (`:` and `/` are replaced, for example).
"""
import os
import re

import unidecode

# What a Kindle can list as a book; anything else on the device (thumbnails, .sdr folders) is not one.
BOOK_EXTENSIONS = frozenset({"azw", "azw3", "kfx", "mobi", "prc", "pdf", "txt", "epub"})
MAX_FILES = 2000


def name_key(value):
    """Comparable form of a title or author: ASCII, lower case, letters and digits only."""
    return re.sub(r"[\W_]+", "", unidecode.unidecode(value or "").casefold())


def candidate_keys(filename):
    """(title_key, author_key or None) guesses for a device filename, most specific first."""
    stem = os.path.splitext(filename or "")[0]
    stem = re.sub(r"\s*\(\d+\)$", "", stem)  # "Title (1)" — a duplicate suffix
    guesses = []
    if " - " in stem:
        title, _, authors = stem.rpartition(" - ")
        guesses.append((name_key(title), name_key(authors)))
    guesses.append((name_key(stem), None))  # the whole stem, for titles that contain " - "
    return [g for g in guesses if g[0]]


def match_book(filename, title_index, author_keys_for):
    """Library book id for a device filename, or None.

    `title_index` maps a title key to the ids carrying it. `author_keys_for(book_id)` returns the
    author keys for that book (the joined list and each author). A title is accepted when the
    author part of the name agrees; a bare title is accepted only when it is unambiguous.
    """
    for title_key, author_key in candidate_keys(filename):
        ids = title_index.get(title_key) or []
        if not ids:
            continue
        if author_key:
            for book_id in ids:
                if author_key in author_keys_for(book_id):
                    return book_id
        elif len(ids) == 1:
            return ids[0]
    return None


def book_author_keys(author_names):
    """Author keys a device filename may use: "A & B" and each name on its own."""
    names = [n.replace("|", ",") for n in author_names]
    keys = {name_key(" & ".join(names)), name_key(" ".join(names))}
    keys.update(name_key(n) for n in names)
    keys.discard("")
    return keys
