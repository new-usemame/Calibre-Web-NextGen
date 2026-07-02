# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #615 — "Mark as read" translated with the *unread* sense.

French (and 16 other locales) translated the lowercase msgid "Mark as read"
identically to "Mark as unread" (fr: both "Marquer comme non lu"). The new UI
uses exactly this msgid pair (BookDetail action button / aria-label), so an
unread book offered "Marquer comme non lu". The classic detail page uses the
title-case pair "Mark As Read"/"Mark As Unread", which is translated correctly
everywhere — that pair is the donor for the fix.

Second symptom on the same page: the classic detail read CTA reused the
read-*status* msgid "Read" (fr "Lu", a past participle) as a verb button.
It now uses the verb msgid "Read now" introduced for the SPA by #577, so the
status translations ("Lu", "Gelezen", …) stay untouched where they are correct
(cover badges, listenmp3 checkbox label).
"""
import pathlib

import pytest
from babel.messages.pofile import read_po

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TRANSLATIONS = _ROOT / "cps" / "translations"


def _catalog(po_path):
    with open(po_path, "rb") as fh:
        return read_po(fh)


def _msgstr(cat, msgid):
    msg = cat.get(msgid)
    if msg is None:
        return None
    return msg.string if isinstance(msg.string, str) else None


@pytest.mark.unit
def test_no_locale_translates_mark_as_read_as_unread():
    """The bug class: any locale where both strings are translated must keep
    them different — identical strings mean the "read" side carries the
    "unread" sense (that is how all 17 broken locales were broken)."""
    checked = 0
    offenders = []
    for po in sorted(_TRANSLATIONS.glob("*/LC_MESSAGES/messages.po")):
        cat = _catalog(po)
        mar = _msgstr(cat, "Mark as read")
        mau = _msgstr(cat, "Mark as unread")
        if mar and mau:
            checked += 1
            if mar == mau:
                offenders.append(po.parts[-3])
    assert checked >= 20, "expected the msgid pair in most shipped locales"
    assert offenders == [], (
        "locales translating 'Mark as read' identically to 'Mark as unread': %s"
        % offenders
    )


@pytest.mark.unit
def test_fr_mark_as_read_matches_reporter_expectation():
    """#615's exact symptom: fr must say 'Marquer comme lu' for the read side."""
    cat = _catalog(_TRANSLATIONS / "fr" / "LC_MESSAGES" / "messages.po")
    assert _msgstr(cat, "Mark as read") == "Marquer comme lu"
    assert _msgstr(cat, "Mark as unread") == "Marquer comme non lu"


@pytest.mark.unit
def test_fr_spa_catalog_serves_correct_labels():
    """What the new UI actually receives: the api catalog is derived from the
    same .po, so the fixed pair must come through, and the read-status word
    must stay the past participle (naive fixes flip 'Read' → 'Lire' and break
    the cover badge / status label)."""
    from cps.api.i18n import _load_catalog

    cat = _load_catalog("fr")
    assert cat.get("Mark as read") == "Marquer comme lu"
    assert cat.get("Mark as unread") == "Marquer comme non lu"
    assert cat.get("Read") == "Lu"


@pytest.mark.unit
def test_fr_read_now_translated_and_not_fuzzy():
    """The classic UI reads the compiled .mo, and msgfmt DROPS fuzzy entries —
    a fuzzy 'Read now' means French users silently get the English button
    (that is how the first live verify of this fix failed). Pin the flag off
    and the reporter's requested wording."""
    cat = _catalog(_TRANSLATIONS / "fr" / "LC_MESSAGES" / "messages.po")
    msg = cat.get("Read now")
    assert msg is not None
    assert msg.string == "Lire"
    assert not msg.fuzzy


@pytest.mark.unit
def test_detail_template_read_cta_uses_verb_msgid():
    """The classic detail CTA opens the reader — a verb — so it must use the
    'Read now' msgid, not the status msgid 'Read' (fr 'Lu' on the big button
    was the first half of #615)."""
    src = (_ROOT / "cps" / "templates" / "detail.html").read_text()
    cta_at = src.find('class="book-read-cta"')
    assert cta_at != -1, "read CTA anchor missing from detail.html"
    cta_block = src[cta_at : src.index("</a>", cta_at)]
    assert "_('Read now')" in cta_block
    assert "_('Read')" not in cta_block


@pytest.mark.unit
def test_status_contexts_keep_status_msgid():
    """The past-participle contexts must keep msgid 'Read': the cover badge
    (image.html) and the listenmp3 read checkbox label."""
    image = (_ROOT / "cps" / "templates" / "image.html").read_text()
    assert "_('Read')" in image
    listen = (_ROOT / "cps" / "templates" / "listenmp3.html").read_text()
    assert "_('Read')" in listen
