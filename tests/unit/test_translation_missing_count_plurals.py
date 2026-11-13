# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for the translation-missing notification count.

polib keeps ``msgstr`` empty for every plural entry by design — the
translations live in the ``msgstr_plural`` dict. The notification count
used to test ``entry.msgstr`` directly, so a fully translated plural
entry (e.g. zh_Hans_CN ``"Last %(count)s day"``) still counted as
missing and the banner kept telling users that strings need
translating when none did.

``_count_missing_translations`` uses polib's ``translated()`` instead, the
same definition the README translation table uses.

Pins:

1. Translated singular and translated plural entries do not count.
2. Empty singular entries count.
3. Plural entries with an empty first form count.
4. A plural entry whose ``msgstr_plural`` dict is empty counts.
5. A fuzzy entry counts (msgfmt leaves it out of the .mo, so users see
   English), and so does a plural entry with a later form left empty.
6. Obsolete entries never count.
"""

from __future__ import annotations

import polib
import pytest

from cps.render_template import _count_missing_translations

pytestmark = pytest.mark.unit


def test_translated_entries_do_not_count() -> None:
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Read", msgstr="阅读"))
    po.append(
        polib.POEntry(
            msgid="Last %(count)s day",
            msgid_plural="Last %(count)s days",
            msgstr_plural={0: "最近 %(count)s 天"},
        )
    )
    assert _count_missing_translations(po) == 0


def test_empty_singular_counts() -> None:
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Read", msgstr=""))
    assert _count_missing_translations(po) == 1


def test_empty_first_plural_form_counts() -> None:
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgid="Last %(count)s day",
            msgid_plural="Last %(count)s days",
            msgstr_plural={0: ""},
        )
    )
    assert _count_missing_translations(po) == 1


def test_missing_plural_dict_counts() -> None:
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgid="Last %(count)s day",
            msgid_plural="Last %(count)s days",
        )
    )
    assert _count_missing_translations(po) == 1


def test_fuzzy_and_partly_filled_plural_entries_count() -> None:
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Read", msgstr="阅读", flags=["fuzzy"]))
    po.append(
        polib.POEntry(
            msgid="%(count)s book",
            msgid_plural="%(count)s books",
            msgstr_plural={0: "%(count)s livre", 1: ""},
        )
    )
    assert _count_missing_translations(po) == 2


def test_obsolete_entries_do_not_count() -> None:
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Gone", msgstr="", obsolete=True))
    assert _count_missing_translations(po) == 0
