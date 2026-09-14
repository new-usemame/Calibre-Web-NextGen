# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The prompt contract, versioned.

``PROMPT_VERSION`` participates in the per-page cache key, so editing a prompt
invalidates exactly the pages that prompt produced and nothing else. Bump it in the
same commit as any edit to the text below — a changed prompt with an unchanged
version silently serves stale pages from the cache.

The wording is the one measured in RESEARCH §2: models that were told "you may
correct obvious OCR errors" corrected things that were not errors, and the
word-preservation gate then failed the page. Telling them to reproduce damage
exactly is what makes the gate pass on a faithful edit.
"""

import os

#: Bump on ANY change to the system prompt or the response contract.
PROMPT_VERSION = "reflow-structure-5"

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    with open(os.path.join(_HERE, name), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def structure_prompt():
    """The system prompt for the page-structure editor."""
    return _load("structure.txt")


def user_prompt(page_text, ladder=(1, 2, 3, 4), hints=None, page_label=None,
                headings=()):
    """The per-page message: the deterministic text, plus what we already know.

    ``headings`` is ``(level, text)`` for the lines this page sets as headings, which
    the deterministic reader measured off the page (``assemble.page_headings``). They
    are given to the model rather than left to it because a model asked to find the
    headings itself finds too many -- MEASURED on page index 102 of the acceptance
    book, three items of a numbered list came back as ``<h2>``, which is three
    chapters of the finished EPUB -- and ``gate.check_structure`` refuses an answer
    that marks anything else.
    """
    levels = ", ".join("<h%d>" % int(level) for level in sorted(set(ladder))) or "<h1>"
    lines = []
    if page_label:
        lines.append("Printed page: %s" % page_label)
    lines.append("Heading levels this book uses: %s. Do not invent a deeper level."
                 % levels)
    if headings:
        lines.append("The headings on this page. Each line below stands in the text "
                     "already: wrap it where it stands, at the level given, and mark "
                     "nothing else as a heading.")
        lines.extend("- <h%d> %s" % (int(level), (text or "").strip())
                     for level, text in headings)
    else:
        lines.append("This page sets no heading. Every block on it is body text.")
    if hints:
        lines.append("What the deterministic pass could not resolve on this page:")
        lines.extend("- %s" % hint for hint in hints)
    lines.append("")
    lines.append("PDF text layer for this page:")
    lines.append("---")
    lines.append(page_text or "")
    lines.append("---")
    return "\n".join(lines)
