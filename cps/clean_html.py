# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from . import logger
from lxml.etree import ParserError

log = logger.create()

try:
    # at least bleach 6.0 is needed -> incomplatible change from list arguments to set arguments
    from bleach import clean as clean_html
    from bleach.sanitizer import ALLOWED_TAGS
    bleach = True
except ImportError:
    from nh3 import clean as clean_html
    bleach = False


# Structural tags Calibre descriptions rely on that bleach's default allowlist
# does not carry. Hoisted to a module constant so the set a description is
# sanitized against can be inspected rather than re-derived: the New UI's
# description editor (#919) only offers formatting that survives this filter,
# and tests/unit/test_description_editor_allowlist_ssot.py round-trips the
# editor's tags through clean_string to keep the two from drifting apart.
#
# This matters more than a normal allowlist because bleach ESCAPES a disallowed
# tag rather than dropping it: an editor button emitting <u> would not fail
# quietly, it would print "&lt;u&gt;" into the reader's description.
_EXTRA_ALLOWED_TAGS = ("p", "span", "div", "pre", "br", "h1", "h2", "h3", "h4", "h5", "h6")

if bleach:
    DESCRIPTION_ALLOWED_TAGS = frozenset(ALLOWED_TAGS) | frozenset(_EXTRA_ALLOWED_TAGS)
else:
    # nh3 applies its own (broader) built-in allowlist and takes no tag argument
    # on this path, so there is no explicit set to expose.
    DESCRIPTION_ALLOWED_TAGS = None


def clean_string(unsafe_text, book_id=0):
    try:
        if bleach:
            safe_text = clean_html(unsafe_text, tags=set(DESCRIPTION_ALLOWED_TAGS))
        else:
            safe_text = clean_html(unsafe_text)
    except ParserError as e:
        log.error("Comments of book {} are corrupted: {}".format(book_id, e))
        safe_text = ""
    except TypeError as e:
        log.error("Comments can't be parsed, maybe 'lxml' is too new, try installing 'bleach': {}".format(e))
        safe_text = ""
    return safe_text
