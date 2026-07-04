# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction anchors for SPA-only translatable strings.

The React SPA's English source strings ARE the gettext msgids (see
cps/api/i18n.py — the per-locale JSON catalog is derived from the same .po files
the classic UI uses). But pybabel only scans Python + Jinja (babel.cfg), so a
string that appears ONLY in the .tsx SPA is dropped from messages.pot on the
next re-extract — and msgmerge then marks its translations obsolete, so the SPA
silently falls back to English (this is exactly how #577's "Read now" → "Nu
lezen" was lost after the auto-translation job ran).

Referencing those SPA-only msgids here, with the gettext marker, keeps them in
the catalog across re-extracts. This module is never imported and does nothing at
runtime — babel reads the *source*, not the call result; ``_`` is a local no-op,
not flask_babel's request-scoped gettext (which can't run at import time).

Add a SPA-only msgid here the moment you introduce it in the frontend.
"""


def _(message):  # noqa: E743 - intentional gettext extraction marker, not the builtin
    return message


# #577 — the new-UI "open the reader" button. A distinct msgid from the "Read"
# read-status label (which is a past participle in many languages, e.g. nl
# "Gelezen") so the verb and the status can be translated separately.
_("Read now")

# #573 — the new-UI series view's series-order sort options (Catalog.tsx). Only
# shown when viewing a single series, so the reader can order by position.
_("Series order")
_("Series order (reverse)")

# #572 — inline tag add/remove on the book detail page (quick-edit, no full
# editor). SPA-only strings, anchored so the auto-translation job keeps them.
_("Add tag")
_("New tag")
_("Remove tag")
_("Remove tag %(name)s")

# "What's new" in-app feature log (SPA /whats-new + the Help menu entry). The
# page CHROME is localized; the per-release entry copy in data/whatsNew.ts is
# English by design (documented on the page). Category names double as the chip
# labels AND the WhatsNewCategory union values — keep them in sync with
# frontend/src/data/whatsNew.ts if that union ever changes.
_("What's new")
_("Help — new updates available")
_("The latest features and fixes in Calibre-Web NextGen — newest first.")
_("{n} update")
_("{n} updates")
_("No release notes yet — check back after the next update.")
_("The interface is translated into your language; these update notes are written in English.")
# Category chips (must match the WhatsNewCategory union)
_("Reading")
_("Library")
_("Sync")
_("Account")
_("Admin")
_("Under the hood")
# "Try it" deep-link button labels (data-authored, but stable enough to anchor
# so a reader's locale can translate the call-to-action).
_("Open your library")
_("Open your shelves")
_("Manage sync & app passwords")
_("Create an app password")
_("Browse series")
_("Open Table view")
_("Open Admin")
_("Browse Discover")
_("Explore your library")
_("Go to Upload")
# Metadata edit — Hardcover editions drill-down (EditBook.tsx)
_("Editions")
_("Back to results")
_("No editions found for this book.")
# Metadata edit — full-record "View all details" overlay (EditBook.tsx)
_("View all details")
_("Result details")
_("Published")
_("Format")
_("Source")
