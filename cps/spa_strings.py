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


# ============================================================================
# Accessibility remediation (WCAG 2.2 AA sweep) — SPA-only strings introduced by
# the a11y pass: skip link, live-region announcements, ARIA labels for icon-only
# controls, reader/annotation color names, form/error messages. Anchored so the
# auto-translation job keeps them (babel does not scan .tsx).
# ============================================================================
_('Add a format')
_('Add rule')
_('Add to shelf')
_('Add your own')
_('App password label')
_('App passwords')
_('Author')
_('Authors (separate with &)')
_('Back to book')
_('Back to sign in')
_('Blue')
_('Book content')
_('Browse')
_('Change password')
_('Close')
_('Close menu')
_('Contents')
_('Convert failed.')
_('Convert to format')
_('Copy')
_('Could not change password.')
_('Could not create.')
_('Could not fetch cover.')
_('Could not load metadata.')
_('Could not load the book file ({status})')
_('Could not load your account.')
_('Could not save.')
_('Cover')
_('Cover image URL')
_('Cover update failed.')
_('Cover updated.')
_('Create smart shelf')
_('Create your account')
_('Dark')
_('Dark theme')
_('Delete')
_('Delete the {fmt} file? The book stays; only this format is removed.')
_('Delete {fmt}')
_('Delete {n} book(s)? This cannot be undone.')
_('Description contains')
_('Deselect {title}')
_('Edit metadata')
_('Edit {title}')
_('Failed to load books.')
_('Failed to open the book.')
_('Generate')
_('Green')
_('Help')
_('Highlight color')
_('Import from Kobo')
_('Interface language')
_('Invalid username or password.')
_('Label (e.g. KOReader on phone)')
_('Languages (comma separated)')
_('Light')
_('Light theme')
_('Login failed.')
_('Mark read')
_('Mark unread')
_('Merge')
_('Metadata applied to {n} book(s).')
_('New password for “{label}” — copy it now, it won’t be shown again:')
_('New passwords do not match.')
_('No books here.')
_('No results for "{q}".')
_('No {filter} books here.')
_('Password changed.')
_('Paste URL')
_('Private shelf')
_('Profile')
_('Profile saved.')
_('Public shelf')
_('Publisher')
_('Publishers (comma separated)')
_('Read')
_('Reading progress')
_('Red')
_('Registration failed.')
_('Remove {name}')
_('Request failed.')
_('Reset')
_('Reset your password')
_('Revoke {label}')
_('Save changes')
_('Save failed.')
_('Save profile')
_('Saved.')
_('Saving…')
_('Search')
_('Search failed.')
_('Search for metadata')
_('Search results')
_('Search the library')
_('Select {title}')
_('Sepia')
_('Sepia theme')
_('Series')
_('Series position {n}')
_('Sign in')
_('Sign in failed. Please try again.')
_('Skip to content')
_('Some fields could not be saved.')
_('Table of contents')
_('Tags (comma separated)')
_('Title')
_('Unread')
_('Update password')
_('Upload')
_('Upload failed.')
_('Upload image')
_('Uploading…')
_('Yellow')
_('e.g. MOBI')
_('{format} reader')
_('{name} API key')
_('{n} book(s) added to the shelf.')
_('{n} book(s) deleted.')
_('{n} marked as read.')
_('{n} marked as unread.')
_('{n} selected')
_('{pct}% read')
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
# #585 v2 — in-SPA "Customize sidebar" editor (SidebarCustomize.tsx + Sidebar.tsx):
# toggle section visibility and reorder entries from the new UI.
_("Customize sidebar")
_("Show or hide sections and drag to reorder them. Use the arrow keys on a handle to move an entry.")
_("Reset to default")
_("Always shown")
_("Saving…")
_("Sidebar saved.")
_("Sidebar reset to default.")
_("{label} moved to position {pos} of {total}")
_("Reorder {label} (position {pos} of {total}). Use arrow keys to move.")
_("Move {label} up")
_("Move {label} down")
_("Hide {label}")
_("Show {label}")
_("Could not save sidebar. Please try again.")
