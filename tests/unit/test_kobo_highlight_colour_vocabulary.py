# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for finding F-5769c9 — the Kobo highlight-colour
vocabulary.

THE DEFECT, measured on the operator's own Kobo Clara BW (4.45.23792) and
deployed server on 2026-08-18:

* ``Bookmark.Color`` really means ``0 yellow / 1 pink / 2 blue / 3 green /
  4 grey``. The importer's table had **no entry for 4**, had **2 and 3
  swapped**, and called 1 **red** — a colour Kobo does not have.
* A greyscale device writes ``Color=4`` for every organic highlight, so on the
  operator's device **all 17 highlights** imported as yellow via a
  ``.get(..., "yellow")`` default.
* That default is the aggravating half: it invents a specific colour for a
  failed lookup, so nothing downstream can tell a real yellow from a miss.
* ``annotation.highlight_color`` already held three vocabularies at once —
  wire hex from the live Kobo PATCH path (612 of 616 device rows), names from
  this importer, and web-reader names (4 ``red`` rows).

THE FIX: store the wire hex, normalise on read, in one shared table
(``cps/services/annotation_colors``). These tests assert the MEASURED mapping
and the full int -> stored -> displayed round trip, not the lookup table
restating itself — which is exactly what the test this replaces did.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.fixtures.kobo_reader_sqlite import build_kobo_db_with_colors

pytestmark = pytest.mark.unit


BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
BOOK_ID = 348

# The measured device mapping. Every assertion below traces back to this table
# and nothing else; it is the only place the numbers appear in this file.
MEASURED = (
    (0, "#F6F3B3", "yellow"),
    (1, "#E8AFCF", "pink"),
    (2, "#B2E1E8", "blue"),
    (3, "#C6E09E", "green"),
    (4, "#A0A0A0", "grey"),
)


@pytest.fixture
def memory_db(tmp_path, monkeypatch):
    from cps import ub, constants
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    yield session
    session.close()
    annotation_backup.reset_for_tests()


def _book_lookup(uuid):
    return SimpleNamespace(id=BOOK_ID) if uuid == BOOK_UUID else None


def _ingest(session, tmp_path, colors):
    """Import one highlight per ``Bookmark.Color`` in ``colors``; return the
    inserted rows keyed by ``clr-<index>``."""
    from cps import ub
    from cps.annotations import ingest_bookmarks

    db = build_kobo_db_with_colors(tmp_path / "kr.sqlite", colors, book_uuid=BOOK_UUID)
    ingest_bookmarks(db, user_id=7, session=session,
                     book_lookup=_book_lookup, commit=session.commit)
    return {r.annotation_id: r for r in
            session.query(ub.Annotation).filter_by(user_id=7).all()}


# ---------------------------------------------------------------------------
# The measured mapping, and the round trip through storage to display
# ---------------------------------------------------------------------------


class TestMeasuredMapping:
    @pytest.mark.parametrize("code,wire_hex,name", MEASURED)
    def test_bookmark_color_int_maps_to_the_measured_wire_hex(self, code, wire_hex, name):
        from cps.services.annotation_colors import hex_for_bookmark_color

        assert hex_for_bookmark_color(code) == wire_hex

    @pytest.mark.parametrize("code,wire_hex,name", MEASURED)
    def test_int_to_stored_to_displayed_round_trips(
        self, memory_db, tmp_path, code, wire_hex, name,
    ):
        """The real round trip: a device integer, through the import, out of the
        column, to the token the reader renders.

        The test this replaces asserted the importer's own lookup table back at
        itself, so it stayed green while every highlight on a real device came
        out the wrong colour.
        """
        from cps.annotations import _data_json_row

        rows = _ingest(memory_db, tmp_path, [code])
        stored = rows["clr-0"]
        assert stored.highlight_color == wire_hex
        assert _data_json_row(stored, None, None)["highlight_color"] == name


class TestGreyscaleDeviceRegression:
    """``Color=4`` is the whole user-visible defect: it is what a greyscale
    Clara BW writes for EVERY organic highlight, and it had no table entry."""

    def test_color_four_imports_as_grey_and_not_as_yellow(self, memory_db, tmp_path):
        from cps.annotations import _data_json_row

        rows = _ingest(memory_db, tmp_path, [4])
        stored = rows["clr-0"]
        assert stored.highlight_color == "#A0A0A0", (
            "Color=4 is grey; storing anything else means every highlight on a "
            "greyscale device is recorded as a colour the user never chose"
        )
        displayed = _data_json_row(stored, None, None)["highlight_color"]
        assert displayed == "grey"
        assert displayed != "yellow"

    def test_a_whole_greyscale_library_does_not_come_back_yellow(self, memory_db, tmp_path):
        """The reporter's actual shape: every highlight on the device is 4."""
        from cps.annotations import _data_json_row

        rows = _ingest(memory_db, tmp_path, [4] * 17)
        assert len(rows) == 17
        displayed = {_data_json_row(r, None, None)["highlight_color"] for r in rows.values()}
        assert displayed == {"grey"}


class TestUnknownIsNeverInvented:
    """An unrecognised colour must read as unknown, never as a real one."""

    @pytest.mark.parametrize("code", [5, 9, 99, -1, None, "0", True])
    def test_unrecognised_bookmark_color_is_unknown(self, code):
        from cps.services.annotation_colors import hex_for_bookmark_color

        assert hex_for_bookmark_color(code) is None

    def test_unrecognised_int_imports_as_unknown_not_yellow(self, memory_db, tmp_path):
        from cps.annotations import _data_json_row

        rows = _ingest(memory_db, tmp_path, [9, None])
        for key in ("clr-0", "clr-1"):
            stored = rows[key]
            assert stored.highlight_color is None, (
                "a colour code we cannot resolve must be stored as unknown; "
                "inventing one makes a failed lookup indistinguishable from a "
                "real highlight of that colour"
            )
            assert _data_json_row(stored, None, None)["highlight_color"] is None

    def test_data_json_no_longer_asserts_yellow_for_a_colourless_row(self):
        """``_data_json_row`` used to emit ``r.highlight_color or "yellow"``.

        That handed the reader a real-looking colour both for a standalone note
        (which has none by design) and for a row whose colour we failed to
        resolve. The client palettes carry their own visual fallback; the
        server does not get to assert a colour it does not have.
        """
        from cps.annotations import _data_json_row

        row = SimpleNamespace(
            annotation_id="a", content_id=None, start_container_path=None,
            start_offset=None, end_container_path=None, end_offset=None,
            highlighted_text="t", highlight_color=None, note_text=None,
            chapter_progress=None, source="kobo", position_type=None,
            pdf_page=None, comic_page=None, origin_device_id=None,
            assigned_device_id=None,
        )
        assert _data_json_row(row, None, None)["highlight_color"] is None


# ---------------------------------------------------------------------------
# The normaliser tolerates every shape already in the column
# ---------------------------------------------------------------------------


class TestLegacyColumnShapes:
    """No migration is shipped, so the column keeps holding all three
    vocabularies at once. Every one of them must read back without raising."""

    @pytest.mark.parametrize("stored,expected", [
        # wire hex — 612 of 616 device rows on the deployed server
        ("#F6F3B3", "yellow"),
        ("#E8AFCF", "pink"),
        ("#B2E1E8", "blue"),
        ("#C6E09E", "green"),
        ("#A0A0A0", "grey"),
        ("#a0a0a0", "grey"),          # case-insensitive
        ("  #A0A0A0  ", "grey"),      # whitespace-tolerant
        # legacy names — what the old importer and the web reader wrote
        ("yellow", "yellow"),
        ("green", "green"),
        ("blue", "blue"),
        ("red", "red"),               # the 4 web-reader rows, unmigrated
        ("gray", "grey"),             # alternate spelling folds to one token
        # NULL, and the shapes a malformed row can carry
        (None, None),
        ("", None),
        ("   ", None),
        (0, None),
        ([], None),
        # a vocabulary this table has not been taught is preserved, not dropped
        ("olive", "olive"),
        ("#123456", "#123456"),
    ])
    def test_display_normaliser_handles_every_legacy_shape(self, stored, expected):
        from cps.services.annotation_colors import to_display_name

        assert to_display_name(stored) == expected

    @pytest.mark.parametrize("supplied,expected", [
        ("yellow", "#F6F3B3"),
        ("pink", "#E8AFCF"),
        ("blue", "#B2E1E8"),
        ("green", "#C6E09E"),
        ("grey", "#A0A0A0"),
        ("gray", "#A0A0A0"),
        ("red", "#D9534F"),
        ("#a0a0a0", "#A0A0A0"),
        ("#A0A0A0", "#A0A0A0"),
        (None, None),
        ("", None),
        (0, None),
        ("olive", "olive"),
    ])
    def test_storage_normaliser_handles_every_legacy_shape(self, supplied, expected):
        from cps.services.annotation_colors import to_storage_color

        assert to_storage_color(supplied) == expected

    def test_normalising_is_idempotent_in_both_directions(self):
        from cps.services.annotation_colors import to_display_name, to_storage_color

        for probe in ("yellow", "#A0A0A0", "red", "gray", "olive", None, ""):
            stored = to_storage_color(probe)
            assert to_storage_color(stored) == stored
            name = to_display_name(stored)
            assert to_display_name(name) == name
            # A display name still resolves back to the same stored value.
            assert to_storage_color(name) == stored


# ---------------------------------------------------------------------------
# The web reader keeps its input contract while the column speaks hex
# ---------------------------------------------------------------------------


class TestWebReaderContract:
    def _book(self):
        return SimpleNamespace(id=BOOK_ID, uuid=BOOK_UUID)

    def _create(self, session, color):
        from cps.annotations import create_annotation

        return create_annotation(
            {"highlight_color": color, "highlighted_text": "a passage",
             "cfi_range": "epubcfi(/6/4!/4/2,/1:0,/1:9)",
             "chapter_filename": "chapter1.html"},
            user_id=7, book=self._book(), session=session, commit=session.commit,
        )

    @pytest.mark.parametrize("name,wire_hex", [
        ("yellow", "#F6F3B3"),
        ("green", "#C6E09E"),
        ("blue", "#B2E1E8"),
        ("red", "#D9534F"),
    ])
    def test_reader_still_sends_names_and_the_column_stores_hex(
        self, memory_db, name, wire_hex,
    ):
        from cps.annotations import _data_json_row

        row = self._create(memory_db, name)
        assert row.highlight_color == wire_hex
        # …and the name the reader sent is the name it gets back.
        assert _data_json_row(row, None, None)["highlight_color"] == name

    def test_edit_accepts_a_name_and_stores_hex(self, memory_db):
        from cps.annotations import edit_annotation

        row = self._create(memory_db, "yellow")
        edited = edit_annotation(
            row.annotation_id, user_id=7, book_id=BOOK_ID, session=memory_db,
            commit=memory_db.commit, color="blue",
        )
        assert edited.highlight_color == "#B2E1E8"

    def test_edit_still_rejects_a_colour_the_palette_does_not_offer(self, memory_db):
        from cps.annotations import edit_annotation

        row = self._create(memory_db, "yellow")
        with pytest.raises(ValueError):
            edit_annotation(
                row.annotation_id, user_id=7, book_id=BOOK_ID, session=memory_db,
                commit=memory_db.commit, color="chartreuse",
            )


class TestRedSurvives:
    """Kobo has no red, but the web reader has always offered it and 4 rows on
    the deployed server already hold the bare name. Neither is dropped."""

    def test_a_legacy_red_row_still_reads_as_red_without_a_migration(self):
        from cps.annotations import _data_json_row
        from cps.services.annotation_colors import to_display_name

        assert to_display_name("red") == "red"
        row = SimpleNamespace(
            annotation_id="a", content_id=None, start_container_path=None,
            start_offset=None, end_container_path=None, end_offset=None,
            highlighted_text="t", highlight_color="red", note_text=None,
            chapter_progress=None, source="webreader", position_type=None,
            pdf_page=None, comic_page=None, origin_device_id=None,
            assigned_device_id=None,
        )
        assert _data_json_row(row, None, None)["highlight_color"] == "red"

    def test_red_is_not_silently_folded_into_a_kobo_colour(self):
        from cps.services.annotation_colors import (
            KOBO_BOOKMARK_COLOR_HEX, to_display_name, to_storage_color,
        )

        red_hex = to_storage_color("red")
        assert red_hex not in KOBO_BOOKMARK_COLOR_HEX.values(), (
            "red must not collide with a Kobo palette colour, or a web-reader "
            "red would read back as pink"
        )
        assert to_display_name(red_hex) == "red"


# ---------------------------------------------------------------------------
# Every read path speaks the display vocabulary
# ---------------------------------------------------------------------------


class TestEveryReadPathNormalises:
    def _row(self, color="#A0A0A0"):
        return SimpleNamespace(
            annotation_id="a-1", book_id=BOOK_ID,
            highlighted_text="a passage", highlight_color=color,
            note_text=None, content_id=None, chapter_progress=0.25,
            context_string=None, cfi_range=None, source="kobo",
            created_at=None, last_synced=None,
            start_container_path=None, start_offset=None,
            end_container_path=None, end_offset=None,
            position_type=None, pdf_page=None, comic_page=None,
            origin_device_id=None, assigned_device_id=None,
            start_xpointer=None, end_xpointer=None, hidden=False,
            device_origin_id=None,
            # to_portable exports this since F-9de049; a real Annotation row
            # always carries it, and the stub models a real row.
            annotation_type=None,
        )

    def test_markdown_export(self):
        from cps.annotations import render_markdown

        out = render_markdown("A Book", [self._row()])
        assert "color: **grey**" in out
        assert "#A0A0A0" not in out

    def test_json_export(self):
        import json
        from cps.annotations import render_json

        payload = json.loads(render_json("A Book", BOOK_ID, 7, [self._row()]))
        assert payload["annotations"][0]["highlight_color"] == "grey"

    def test_csv_export(self):
        from cps.annotations import render_csv

        out = render_csv([self._row()])
        assert "grey" in out
        assert "#A0A0A0" not in out

    def test_portable_wire(self):
        from cps.services.annotation_portable import to_portable

        assert to_portable(self._row())["color"] == "grey"

    def test_jinja_filter_for_the_classic_view(self):
        from cps.jinjia import annotation_color_filter

        # The classic view builds a CSS class out of this value, and
        # `cwa-annotation-#A0A0A0` is not a valid selector.
        assert annotation_color_filter("#A0A0A0") == "grey"
        assert annotation_color_filter(None) is None

    def test_no_read_path_emits_a_known_kobo_wire_hex(self):
        """One sweep over every projection, so a new one added beside these
        cannot quietly skip the normaliser.

        Scoped to the five MEASURED hexes on purpose. An UNKNOWN hex is
        deliberately passed through by these projections (see
        ``TestUnknownTokensArePreservedButNeverKeyAPalette``) — preserving a
        colour we cannot name is the point, and asserting "no hex ever" here
        would contradict it.
        """
        import json
        from cps.annotations import _data_json_row, render_csv, render_json, render_markdown
        from cps.services.annotation_portable import to_portable

        for wire_hex, _name in [(h, n) for _c, h, n in MEASURED]:
            row = self._row(wire_hex)
            emitted = [
                _data_json_row(row, None, None)["highlight_color"],
                json.loads(render_json("B", BOOK_ID, 7, [row]))["annotations"][0]["highlight_color"],
                to_portable(row)["color"],
            ]
            assert wire_hex.lower() not in [str(v).lower() for v in emitted], emitted
            assert wire_hex not in render_markdown("B", [row])
            assert wire_hex not in render_csv([row])


class TestLiveKoboPatchPathNormalises:
    """The live PATCH path already stored the wire hex; it must keep doing so
    through the shared normaliser, and a no-op PATCH must stay a no-op."""

    def test_wire_hex_from_a_device_is_stored_canonically(self):
        from cps.services.annotation_colors import to_storage_color

        assert to_storage_color("#a0a0a0") == "#A0A0A0"

    def test_a_patch_that_changes_nothing_is_not_counted_as_a_change(self):
        """A legacy row holding the NAME and a device sending the equivalent
        HEX are the same colour. Comparing the raw strings would report a
        content change on every sync forever."""
        from cps.services.annotation_sync import _kobo_payload_matches_row

        annotation = SimpleNamespace(
            highlighted_text="t", note_text=None, highlight_color="yellow",
            chapter_progress=0.5, content_id="c", start_container_path="s",
            end_container_path="e", start_offset=0, end_offset=1,
            context_string=None, hidden=False,
        )
        payload = {"highlightColor": "#F6F3B3"}
        span = {}
        assert _kobo_payload_matches_row(annotation, payload, span, None) is True


class TestClassicViewTemplateWiring:
    """The classic per-book view renders the colour through a Jinja filter, and
    a filter that is defined but never registered fails at RENDER time — a 500
    on the page, which no direct call to the function would catch."""

    def test_the_filter_is_registered_and_reachable_from_a_template(self):
        import flask
        from cps.jinjia import jinjia

        app = flask.Flask(__name__)
        app.register_blueprint(jinjia)
        with app.app_context():
            rendered = app.jinja_env.from_string(
                "{{ value|annotation_color }}"
            ).render(value="#A0A0A0")
        assert rendered == "grey"

    def test_the_template_pipes_the_colour_through_the_filter(self):
        """Source-pin the wiring itself.

        The template builds a CSS class out of this value. Dropping the filter
        would put `cwa-annotation-#F6F3B3` in a class attribute, which is not a
        valid selector and cannot be styled — and nothing else in the suite
        renders this template.
        """
        from pathlib import Path

        here = Path(__file__).resolve().parents[2]
        source = (here / "cps" / "templates" / "annotations_view.html").read_text()
        assert "ann.highlight_color|annotation_color" in source
        # Nothing may reach the class attribute or the label unfiltered.
        assert "cwa-annotation-{{ ann.highlight_color" not in source
        assert "swatch-{{ ann.highlight_color" not in source


class TestUnknownTokensArePreservedButNeverKeyAPalette:
    """Preservation and validation are different jobs, and one function cannot
    do both.

    ``to_display_name`` preserves a token this app cannot name, because an
    export and a backup want the honest stored value. But a CSS class, a
    human-facing tag and any fixed palette need a token they can actually key
    on — handing those a raw ``#123456`` recreates the broken-selector problem
    the normaliser was introduced to prevent. ``to_known_display_name`` is the
    validating half.
    """

    @pytest.mark.parametrize("stored", ["#123456", "olive", "chartreuse", "  Olive "])
    def test_display_preserves_what_known_display_rejects(self, stored):
        from cps.services.annotation_colors import to_display_name, to_known_display_name

        assert to_display_name(stored) is not None
        assert to_known_display_name(stored) is None

    @pytest.mark.parametrize("stored,expected", [
        ("#A0A0A0", "grey"), ("#a0a0a0", "grey"), ("gray", "grey"),
        ("red", "red"), ("#D9534F", "red"), ("yellow", "yellow"),
        (None, None), ("", None),
    ])
    def test_known_display_answers_for_everything_it_can_name(self, stored, expected):
        from cps.services.annotation_colors import to_known_display_name

        assert to_known_display_name(stored) == expected

    def test_the_hardcover_tag_is_a_colour_we_can_name_or_nothing(self):
        """Hardcover renders this as free text on the user's journal entry."""
        from cps.services.annotation_colors import to_known_display_name

        assert to_known_display_name("#A0A0A0") == "grey"
        assert to_known_display_name("#123456") is None


class TestClassicViewNeverEmitsABrokenOrInventedClass:
    """The classic view styles a row by ``cwa-annotation-<colour>``."""

    def _render(self, stored):
        import flask
        from cps.jinjia import jinjia

        app = flask.Flask(__name__)
        app.register_blueprint(jinjia)
        with app.app_context():
            return app.jinja_env.from_string(
                "{% set c = v|annotation_palette_class %}"
                "cwa-annotation{% if c %} cwa-annotation-{{ c }}{% endif %}"
            ).render(v=stored)

    def test_a_known_colour_gets_its_modifier(self):
        assert self._render("#A0A0A0") == "cwa-annotation cwa-annotation-grey"

    def test_no_colour_gets_no_modifier_rather_than_yellow(self):
        """The template used to append ``or 'yellow'``, which painted the row's
        border yellow for a row that has no colour at all."""
        assert self._render(None) == "cwa-annotation"
        assert self._render("") == "cwa-annotation"

    def test_an_unnameable_colour_gets_no_modifier_rather_than_a_broken_one(self):
        assert self._render("#123456") == "cwa-annotation"
        assert self._render("olive") == "cwa-annotation"

    def test_the_template_uses_the_palette_filter_for_every_class(self):
        """Source-pin, because nothing in the suite renders this template and a
        raw colour interpolated into a class is silently broken, not an error."""
        from pathlib import Path

        here = Path(__file__).resolve().parents[2]
        source = (here / "cps" / "templates" / "annotations_view.html").read_text()
        assert "ann.highlight_color|annotation_palette_class" in source
        assert "cwa-annotation-{{ ann_color" not in source
        assert "swatch-{{ ann_color" not in source
        assert "or 'yellow'" not in source

    def test_every_display_name_this_app_can_produce_has_a_style(self):
        """A name with no CSS rule renders the default neutral border while the
        label beside it says "pink" — which is how pink and grey shipped
        half-supported in the first place."""
        from pathlib import Path
        from cps.services.annotation_colors import to_display_name, KOBO_BOOKMARK_COLOR_HEX

        here = Path(__file__).resolve().parents[2]
        source = (here / "cps" / "templates" / "annotations_view.html").read_text()
        names = {to_display_name(h) for h in KOBO_BOOKMARK_COLOR_HEX.values()} | {"red"}
        for name in names:
            assert f".cwa-annotation-{name} " in source or f".cwa-annotation-{name}  " in source, name
            assert f".cwa-annotation-swatch-{name} " in source, name


class TestLegacyReaderDoesNotResendAnUneditableColour:
    """Adding a NOTE to an imported highlight must not fail on its colour.

    The legacy reader's edit popup sent ``highlight_color`` on every save,
    seeded from the row. The server accepts only the four colours its palette
    offers, so once imported rows arrive as `pink`/`grey` — and before that, as
    a raw hex mangled into a token like `f6f3b3` — saving a note on one was
    rejected outright and the note was lost.
    """

    def _source(self):
        from pathlib import Path

        here = Path(__file__).resolve().parents[2]
        return (here / "cps" / "static" / "js" / "reading" / "annotations.js").read_text()

    def test_the_colour_is_sent_only_when_the_user_picked_a_swatch(self):
        source = self._source()
        assert "if (chosen.picked) { patch.highlight_color = chosen.color; }" in source
        assert "{ highlight_color: chosen.color, note_text: note.value || null }" not in source

    def test_a_colour_the_edit_endpoint_would_reject_is_still_rejected(self, memory_db):
        """The client-side fix is the right one precisely because the server
        contract is unchanged: pink is a colour to RENDER, not one to choose."""
        from cps.annotations import create_annotation, edit_annotation

        row = create_annotation(
            {"highlight_color": "yellow", "highlighted_text": "a passage",
             "cfi_range": "epubcfi(/6/4!/4/2,/1:0,/1:9)"},
            user_id=7, book=SimpleNamespace(id=BOOK_ID, uuid=BOOK_UUID),
            session=memory_db, commit=memory_db.commit,
        )
        with pytest.raises(ValueError):
            edit_annotation(row.annotation_id, user_id=7, book_id=BOOK_ID,
                            session=memory_db, commit=memory_db.commit, color="grey")
        # …and a note-only edit on that same row is untouched by the colour rule.
        edited = edit_annotation(row.annotation_id, user_id=7, book_id=BOOK_ID,
                                 session=memory_db, commit=memory_db.commit,
                                 note="a note")
        assert edited.note_text == "a note"
        assert edited.highlight_color == "#F6F3B3"


class TestNoClientPaintsAnUnknownColourYellow:
    """The server stopped inventing a colour; a client that then paints yellow
    for null puts the same lie back one layer down."""

    def _read(self, *parts):
        from pathlib import Path

        here = Path(__file__).resolve().parents[2]
        return here.joinpath(*parts).read_text()

    def test_legacy_epub_reader(self):
        source = self._read("cps", "static", "js", "reading", "annotations.js")
        assert "if (!rgb) { rgb = UNKNOWN_RGB; }" in source
        assert "rgb = NAMED_RGB.yellow" not in source

    def test_legacy_pdf_reader(self):
        source = self._read("cps", "static", "js", "reading", "annotations_pdf.js")
        assert "|| UNKNOWN_RGBA" in source
        assert "|| COLOR_RGBA.yellow" not in source

    def test_legacy_comic_reader(self):
        source = self._read("cps", "static", "js", "reading", "annotations_comic.js")
        assert "|| UNKNOWN_BG" in source
        assert "|| COLOR_BG.yellow" not in source

    def test_spa_reader(self):
        source = self._read("frontend", "src", "pages", "Reader.tsx")
        assert "UNKNOWN_FILL" in source
        assert "|| HILITE_FILL.yellow" not in source
        assert "highlight_color || 'yellow'" not in source

    def test_spa_highlights_page(self):
        source = self._read("frontend", "src", "pages", "Annotations.tsx")
        assert "COLOR_HEX[row.highlight_color || 'yellow']" not in source
        assert "'var(--border)'" in source
