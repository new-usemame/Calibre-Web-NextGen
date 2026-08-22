# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""One owner for `annotation_type`, and every writer routed through it.

F-9de049: six constructors write `ub.Annotation` and only two set this column,
one of them conditionally. That is the shape `annotation_colors` was created to
undo for `highlight_color`, where three vocabularies accumulated in one column
and had to be reconciled afterwards. Doing it now is cheap because nothing
user-visible reads the column yet.

The vocabulary is not invented. `highlight` and `dogear` are the DEVICE's words —
`Bookmark.Type` in KoboReader.sqlite, and what the KOReader plugin writes and
selects on. `note` is web-reader-only, for the unanchored note no Kobo can
represent, and it is declared for the same reason `WEBREADER_RED_HEX` is: a value
only the web reader can produce still round-trips through one table instead of
being a special case at each call site.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestTheVocabularyItself:
    def test_the_device_words_are_the_device_words(self):
        from cps.services.annotation_types import KOBO_NATIVE_TYPES

        assert set(KOBO_NATIVE_TYPES) == {"highlight", "dogear"}

    def test_note_is_declared_web_reader_only(self):
        """It must not be claimed as something a Kobo can store."""
        from cps.services.annotation_types import (
            KOBO_NATIVE_TYPES, WEBREADER_ONLY_TYPES,
        )

        assert "note" in WEBREADER_ONLY_TYPES
        assert "note" not in KOBO_NATIVE_TYPES

    @pytest.mark.parametrize("supplied, expected", [
        ("highlight", "highlight"),
        ("Highlight", "highlight"),
        ("  DOGEAR  ", "dogear"),
        ("dog-ear", "dogear"),
        ("dog_ear", "dogear"),
        ("bookmark", "dogear"),
        ("annotation", "note"),
    ])
    def test_known_spellings_fold_to_one_token(self, supplied, expected):
        from cps.services.annotation_types import to_storage_type

        assert to_storage_type(supplied) == expected

    @pytest.mark.parametrize("supplied", [None, "", "   ", 42, True, object()])
    def test_absence_is_absence_and_never_a_type(self, supplied):
        """Rule 1: never invent a type.

        A default here would make a failed lookup indistinguishable from a real
        highlight — the exact defect that made every greyscale device's
        highlights import as yellow before F-5769c9.
        """
        from cps.services.annotation_types import to_known_type, to_storage_type

        assert to_storage_type(supplied) is None
        assert to_known_type(supplied) is None

    def test_an_unknown_word_survives_storage_but_is_not_claimed_as_known(self):
        """Rule 2: never destroy a type.

        A future firmware's word must round-trip, while anything keying on the
        value must be told this module cannot name it.
        """
        from cps.services.annotation_types import to_known_type, to_storage_type

        assert to_storage_type("  Sticky-Note  ") == "sticky-note"
        assert to_known_type("Sticky-Note") is None

    def test_an_anchored_annotation_is_a_highlight_even_with_a_note(self):
        """Keying on the anchor, not on note text, is the whole point.

        A Kobo marks a highlight-with-note by populating Bookmark.Annotation, not
        by changing Bookmark.Type. Keying on note text would reclassify a
        highlight the moment somebody typed into it.
        """
        from cps.services.annotation_types import type_for_webreader_annotation

        assert type_for_webreader_annotation(has_anchor=True) == "highlight"
        assert type_for_webreader_annotation(has_anchor=False) == "note"


class TestEveryWriterIsRoutedThroughIt:
    """The point of an owner is that nothing bypasses it."""

    def test_no_annotation_constructor_leaves_the_type_unset(self):
        """Executed against the source of the constructors, because the failure
        mode is a constructor that simply omits the keyword — there is no runtime
        signal for "this writer forgot", only a NULL that looks like every other
        NULL."""
        import inspect
        import re

        from cps import annotations
        from cps.services import annotation_portable
        from cps.services import annotation_sync as sync_module

        offenders = []
        for module in (annotations, annotation_portable, sync_module):
            source = inspect.getsource(module)
            for match in re.finditer(r"ub\.Annotation\((.*?)\n\s*\)", source, re.S):
                # `annotation_type=` with the equals, not the bare word: the
                # comments in these constructors mention the annotation_types
                # MODULE, and a substring search matched that instead — so
                # deleting the real keyword left this gate green. Caught by
                # mutation, not by reading it.
                if not re.search(r"\bannotation_type\s*=", match.group(1)):
                    head = match.group(1).strip().splitlines()[0][:60]
                    offenders.append(f"{module.__name__}: ub.Annotation({head}...")
        assert offenders == [], (
            "these Annotation constructors do not set annotation_type, so the "
            "column silently stays NULL for everything they create:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_scan_finds_the_constructors_it_claims_to(self):
        """Vacuity guard for the test above — a regex that matched nothing would
        report every writer compliant."""
        import inspect
        import re

        from cps import annotations

        found = re.findall(r"ub\.Annotation\((.*?)\n\s*\)", inspect.getsource(annotations), re.S)
        assert len(found) >= 4, f"only {len(found)} constructors found in cps.annotations"


class TestThePortableRoundTrip:
    def test_the_type_survives_export(self):
        from types import SimpleNamespace

        from cps.services.annotation_portable import to_portable

        row = SimpleNamespace(
            annotation_id="a-1", highlighted_text="p", note_text=None,
            highlight_color=None, content_id=None, start_container_path=None,
            start_offset=None, end_container_path=None, end_offset=None,
            context_string=None, chapter_progress=None, position_type=None,
            start_xpointer=None, end_xpointer=None, source="koreader",
            hidden=False, device_origin_id=None, last_synced=None,
            annotation_type="highlight",
        )
        assert to_portable(row)["type"] == "highlight"

    def test_a_sender_that_omits_the_type_is_not_assigned_one(self):
        """Preserve, never choose — the receiving side must not invent."""
        from cps.services.annotation_types import to_storage_type

        assert to_storage_type({}.get("type")) is None
