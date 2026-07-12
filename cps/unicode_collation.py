# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free collation keys for Latin text.

This is intentionally a bounded improvement, not an ICU replacement. Accented
letters fold to their base for ordering and buckets. Spanish ``ñ`` remains a
distinct letter after the N block, while Unicode casefold supplies German
``ß`` -> ``ss`` primary equivalence.
"""

import unicodedata

_ENYE_MARKER = "\uf8ff"


def unicode_sort_key(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    protected = value.replace("ñ", _ENYE_MARKER).replace("Ñ", _ENYE_MARKER)
    decomposed = unicodedata.normalize("NFKD", protected)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    # Sort after the complete N block but before O in normal Unicode order.
    return folded.replace(_ENYE_MARKER.casefold(), "n\uffff")


def unicode_initial(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if not value:
        return ""
    if value[0] in ("ñ", "Ñ"):
        return "Ñ"
    key = unicode_sort_key(value[0])
    return key[0].upper() if key else ""
