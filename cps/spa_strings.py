# -*- coding: utf-8 -*-
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
