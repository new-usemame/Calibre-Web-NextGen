# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one place that decides what ``annotation.annotation_type`` may contain.

WHY THIS MODULE EXISTS
----------------------
``annotation_type`` arrived with #1685 as "Stage 0 two-way-sync metadata" and no
writer was made responsible for it. Six constructors write ``ub.Annotation`` and,
before this module, two set the column — one of them only when the incoming
payload happened to carry a ``type`` key (finding ``F-9de049``):

* ``cps/annotations.py`` — the ``KoboReader.sqlite`` importer: sets it;
* ``cps/annotations.py`` — three web-reader create paths: did not;
* ``cps/services/annotation_portable.py`` — the KOReader import: did not;
* ``cps/services/annotation_sync/__init__.py`` — the live Kobo PATCH:
  conditionally.

That is the same shape ``annotation_colors`` was created to undo for
``highlight_color``, where three vocabularies accumulated in one column and had
to be reconciled after the fact. The column now crosses the portable annotation
boundary in ``cps/services/annotation_portable.py`` and is also named by the
field list in ``cps/services/kobo_annotation_stage0.py``; no frontend code reads
it directly.

THE VOCABULARY, AND WHERE EACH WORD COMES FROM
----------------------------------------------
``highlight`` and ``dogear`` are **the device's own words**, not this project's.
``KoboReader.sqlite`` stores them in ``Bookmark.Type``; the KOReader plugin both
writes ``Type = "highlight"`` and selects ``WHERE Type = 'highlight'``
(``koreader/plugins/cwngsync.koplugin/kobo_sqlite_provider.lua``); and the Kobo
sync payload uses ``"highlight"`` for the same thing. So a web-reader highlight
being called ``highlight`` is not an invention — it is the same word for the same
object.

``note`` is **web-reader-only**, for the unanchored note the reader can create and
no Kobo can represent. It is declared here rather than left implicit for exactly
the reason ``annotation_colors.WEBREADER_RED_HEX`` exists: the web reader has long
offered a value the device cannot produce, and giving it a canonical token means
it round-trips through the same table as everything else instead of being a
special case at each call site.

⚠️ A Kobo distinguishes a highlight WITH a note from one without by populating
``Bookmark.Annotation``, not by changing ``Bookmark.Type``. So an anchored
highlight that carries note text is still ``highlight`` here. ``note`` means
"there is no highlighted passage, only a note", which is a different object.

TWO RULES, INHERITED FROM THE COLOUR TABLE BECAUSE THEY EARNED THEIR PLACE
-------------------------------------------------------------------------
1. **Never invent a type.** An unrecognised value resolves to ``None``
   ("unknown"), never to ``highlight``. A default there would make a failed
   lookup indistinguishable downstream from a real highlight — the exact defect
   that made every greyscale device's highlights import as yellow before
   ``F-5769c9``.
2. **Never destroy a type.** A token this table does not know — a future
   firmware's word, another reader's vocabulary — survives normalisation rather
   than being nulled, so routing a value through here never loses it.
"""

from __future__ import annotations

from typing import Optional

#: Words a Kobo itself uses in ``KoboReader.sqlite``'s ``Bookmark.Type``.
KOBO_NATIVE_TYPES = ("highlight", "dogear")

#: Words only the web reader can produce, because the object does not exist on a
#: device. Kept in the same table so they normalise through one path.
WEBREADER_ONLY_TYPES = ("note",)

#: Everything this module can name.
KNOWN_TYPES = tuple(KOBO_NATIVE_TYPES) + tuple(WEBREADER_ONLY_TYPES)

#: Spellings that mean a known token. Separate from the canonical set so
#: :func:`to_storage_type` always answers with one spelling.
_ALIASES = {
    "dog-ear": "dogear",
    "dog_ear": "dogear",
    "bookmark": "dogear",
    "annotation": "note",
}


def _token(value) -> Optional[str]:
    """Reduce any incoming value to a comparable lower-case token.

    ``None`` for ``None``, a blank string, and anything that is not a string —
    i.e. "there is no type here", never a guess.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token or None


def to_storage_type(value) -> Optional[str]:
    """Normalise any accepted type token to the canonical stored form.

    * a known word, in any case or a known alias -> its canonical spelling;
    * ``None`` / blank / a non-string -> ``None``;
    * anything else -> the token, **preserved** after trimming and lower-casing,
      so a vocabulary this module has not been taught is kept rather than lost.
    """
    token = _token(value)
    if token is None:
        return None
    return _ALIASES.get(token, token)


def to_known_type(value) -> Optional[str]:
    """Like :func:`to_storage_type`, but ``None`` for anything unrecognised.

    Use this wherever the answer keys something — a UI label, a protocol enum, a
    branch — so an unknown word cannot be mistaken for a type the caller handles.
    :func:`to_storage_type` is for storage and export, which want the honest
    value even when this module cannot name it.
    """
    token = to_storage_type(value)
    if token is None or token not in KNOWN_TYPES:
        return None
    return token


def type_for_webreader_annotation(*, has_anchor: bool) -> str:
    """What the web reader is creating.

    The reader has exactly two shapes: an anchored passage (with or without note
    text attached) and an unanchored note. The first is a ``highlight`` — the
    device's own word for the same object — and the second is a ``note``, which
    only the web reader can make.

    Takes the anchor rather than the note text on purpose: a highlight that
    carries a note is still a highlight, and keying on ``note_text`` would
    reclassify it the moment someone typed into it.
    """
    return "highlight" if has_anchor else "note"


def _is_derivable_legacy_position_type(position_type) -> bool:
    """Whether a legacy row retains a complete web-reader branch decision.

    Only the direct web-reader constructors assign either token. Portable
    updates may later rewrite ``source``, but they cannot create these position
    types, so provenance is deliberately not part of this predicate.
    """
    return position_type in ("unanchored", "cfi")


def derive_legacy_annotation_type(*, position_type) -> Optional[str]:
    """Reproduce today's type only from a legacy row's recorded writer input.

    The direct web-reader constructors persist the exact discriminator passed
    to :func:`type_for_webreader_annotation`: ``unanchored`` means no anchor,
    while ``cfi`` means an anchor. Those two cases are derivations, not
    classifications inferred from content. ``source`` is not consulted because
    portable updates can rewrite it without changing ``position_type``.

    Every other shape remains unknown. In particular, a legacy KoboSpan row
    has no ``position_type`` value, and the same stored selector shape can
    arrive through portable import. Inspecting its text or selector would
    therefore guess which writer and input produced it, violating the rule
    that an unrecognised type resolves to ``None``.
    """
    if not _is_derivable_legacy_position_type(position_type):
        return None
    return type_for_webreader_annotation(has_anchor=position_type == "cfi")
