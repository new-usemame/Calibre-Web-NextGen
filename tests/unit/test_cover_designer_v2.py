# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioural cover for "full control over the generated cover".

The first designer offered five colour schemes, two letterings and three
arrangements, and the complaint that produced this file was precise: a stock
Calibre cover "is basically not available to be made with this designer", and a
preset list you cannot add to or delete from is somebody else's list.

So the promise under test here is a wider one. Every arrangement Calibre's own
generator can draw is offered; every colour on the cover can be set by hand and
the slot you set is the part of the cover that changes; the lettering, its size,
its alignment and the text itself are the reader's to choose; and the designs a
reader saves are theirs to name, rename, reorder, delete, and — for the ones
they cannot delete — hide and bring back.

The other half of the promise is that none of that hands a reader a way to run
code. Calibre's template language has a ``python:`` mode; a font is a file path
in every desktop tool. Neither is reachable from here, and the tests that say so
are the ones worth keeping red.

Nothing here needs Calibre installed: the renderer is pinned to the built-in
Pillow path, which must accept every design the Calibre path accepts.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import cover_generator as cg


pytestmark = pytest.mark.unit


# Deliberately built from the fields v1 already had, so a tree without this
# feature can still collect this file and say which promises it fails rather
# than failing to import and saying nothing.
META = cg.BookCoverMeta(
    title="The Long Way to a Small Angry Planet",
    authors=["Becky Chambers"],
    series="Wayfarers",
    series_index=1,
)


def _rich_book():
    """The same book with the fields the wider designer can put on a cover."""
    return cg.BookCoverMeta(
        title="The Long Way to a Small Angry Planet",
        authors=["Becky Chambers"],
        series="Wayfarers",
        series_index=1,
        publisher="Hodder",
        year=2015,
        tags=["science fiction", "found family"],
        language="eng",
        rating=4.5,
    )

SMALL = {"width": 320, "height": 480}


@pytest.fixture(autouse=True)
def pinned_renderer(monkeypatch):
    """What the suite measures must not depend on whether this box has Calibre."""
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "pil")
    monkeypatch.delenv("CWNG_CALIBRE_DEBUG_PATH", raising=False)


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    """A real app.db with the preset tables, built by the shipped migration."""
    engine = create_engine("sqlite:///{}".format(tmp_path / "app.db"), future=True)
    ub.User.__table__.create(engine)
    ub.migrate_cover_design_preset_tables(engine, None)
    session = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", session)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_image_cache(monkeypatch, tmp_path):
    """Catalogue imagery must not land in the developer's real cache directory."""
    from cps.services import cover_preview_cache

    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(cover_preview_cache, "CACHE_ROOT", root)
    yield root


def _pixels(data: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(data)).convert("RGB")


def _colour_band(data: bytes, target: str, tolerance: int = 40):
    """``(count, first row, last row)`` for pixels close to ``#rrggbb``.

    JPEG moves colours around, so an exact match would measure the encoder
    rather than the design; a wide tolerance answers the two questions that
    matter — is that colour on the cover, and *where* — which is how "the title
    colour paints the title" becomes checkable instead of "some text somewhere
    is red".
    """
    image = _pixels(data)
    want = tuple(int(target.lstrip("#")[index:index + 2], 16) for index in (0, 2, 4))
    pixels = image.load()
    count = 0
    rows = []
    for row in range(image.height):
        hit = False
        for column in range(image.width):
            colour = pixels[column, row]
            if all(abs(colour[index] - want[index]) <= tolerance for index in range(3)):
                count += 1
                hit = True
        if hit:
            rows.append(row)
    return count, (rows[0] if rows else None), (rows[-1] if rows else None)


def _plain(markup: str) -> str:
    """The drawn text without the slot's own bold/italic, for comparing words."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", markup or "", flags=re.IGNORECASE)
    text = re.sub(r"</?(?:b|i|em|strong)>", "", text, flags=re.IGNORECASE)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _render(design: dict, meta: cg.BookCoverMeta = META) -> bytes:
    full = dict(design)
    full.setdefault("size", SMALL)
    return cg.render(meta, cg.resolve_design(full)).data


def _stub_calibre_debug(tmp_path, body: str) -> str:
    path = tmp_path / "calibre-debug"
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


# ---------------------------------------------------------------------------
# Everything Calibre can draw is on offer
# ---------------------------------------------------------------------------

def test_every_arrangement_calibre_can_draw_is_offered_and_draws_differently():
    """Five styles, five different pictures.

    The original designer offered three of Calibre's five arrangements, which is
    how a stock Calibre cover became unreachable. A catalogue that listed all
    five but drew the same thing for some of them would be the same bug wearing
    a longer list, so the pictures are compared, not the ids.
    """
    catalogue = cg.catalogue()
    offered = [entry["id"] for entry in catalogue["styles"]]
    assert set(offered) == set(cg.STYLES)
    assert len(offered) >= 5

    drawn = {}
    for style in offered:
        drawn[style] = _render({"style": style, "scheme": "water"})
    assert len(set(drawn.values())) == len(offered), \
        "two arrangements rendered identically: %s" % sorted(drawn)


def test_each_colour_slot_paints_the_part_of_the_cover_it_names():
    """The four colours are the reader's, in every arrangement.

    Calibre paints the title with ``contrast_color1`` in Blocks and with
    ``contrast_color2`` in the other four. Exposing those raw would mean the
    "title colour" control changed the authors' colour in four arrangements out
    of five. Each style therefore declares which of its parts each slot paints,
    and this test reads that declaration back off the pixels.
    """
    for style, entry in cg.STYLES.items():
        data = _render({
            "style": style, "scheme": None,
            "colors": {"background": "#ffffff", "band": "#ffffff",
                       "title": "#ff0000", "author": "#0000ff"},
        })
        titles, title_top, title_bottom = _colour_band(data, "#ff0000")
        authors, author_top, _author_bottom = _colour_band(data, "#0000ff")
        assert titles > 50, "%s: the title colour never reached the cover" % style

        if "Unused" in entry["color_roles"]["author"]:
            assert authors == 0, \
                "%s says the author colour is unused but painted with it" % style
            continue

        assert authors > 50, "%s: the author colour never reached the cover" % style
        if "ornament" in entry["color_roles"]["author"].lower():
            # This arrangement paints its decoration in the author colour as
            # well, by Calibre's own design, so that colour is all over the
            # cover and the rows cannot tell the two blocks apart. The
            # declaration says so, and the panel shows the reader the same
            # sentence next to the swatch.
            continue
        # The rest put the authors below the title, so this is what says the two
        # colours went to the right blocks rather than merely both appearing
        # somewhere: swap them and the bands cross over.
        assert title_bottom < author_top, (
            "%s: the title colour is drawn at rows %s-%s and the author colour "
            "from row %s — they are on each other's text"
            % (style, title_top, title_bottom, author_top))


def test_a_named_scheme_hands_calibre_its_own_four_colours():
    """A theme by name must still be that theme, byte for byte.

    The reproducible-stock-cover requirement lives here: the slot names the
    panel speaks are a rotation of Calibre's ``color1``/``color2``/
    ``contrast_color1``/``contrast_color2``, and the rotation has to be exactly
    invertible or "The Cross in Water" would stop being Calibre's cover.
    """
    for scheme, theme in cg.COLOR_SCHEMES.items():
        for style in cg.STYLES:
            spec = cg.resolve_design({"style": style, "scheme": scheme})
            assert spec.calibre_colors == {
                "color1": theme["color1"], "color2": theme["color2"],
                "contrast_color1": theme["contrast_color1"],
                "contrast_color2": theme["contrast_color2"],
            }, "%s in %s no longer round-trips" % (scheme, style)


def test_calibres_own_colour_themes_are_all_offered():
    """Calibre ships four themes; a designer without them cannot copy its covers."""
    offered = {entry["id"] for entry in cg.catalogue()["schemes"]}
    for calibre_theme in ("earth", "grass", "water", "silver"):
        assert calibre_theme in offered


def test_changing_only_the_lettering_size_changes_the_cover():
    base = {"style": "blocks", "scheme": "ink"}
    small = _render(dict(base, fonts={"title": {"size": 40}}))
    large = _render(dict(base, fonts={"title": {"size": 140}}))
    assert small != large


def test_changing_only_the_alignment_changes_the_cover():
    base = {"style": "half", "scheme": "ink"}
    left = _render(dict(base, align={"title": "left"}))
    right = _render(dict(base, align={"title": "right"}))
    assert left != right


def test_the_reader_chooses_what_text_goes_on_the_cover():
    """Templates are the point of "everything available for manual control"."""
    spec = cg.resolve_design({"text": {"title": "{title}",
                                       "subtitle": "{publisher}, {year}",
                                       "author": "{author} — {stars}"}})
    text = cg.cover_text(spec, _rich_book())
    assert META.title in text["title"]
    assert "Hodder, 2015" in text["subtitle"]
    assert "Becky Chambers" in text["author"]
    assert "★" in text["author"]


def test_a_template_whose_fields_are_all_empty_prints_nothing():
    """A book with no series must not get a cover that says ", ." """
    spec = cg.resolve_design({"text": {"subtitle": "{series} {series_index}"}})
    bare = cg.BookCoverMeta(title="Solo", authors=["A N Other"])
    assert cg.cover_text(spec, bare)["subtitle"] == ""


@pytest.mark.parametrize("template", ["{publisher}, {year}", "{publisher} ({year})",
                                      "[{year}] {publisher}", "{publisher} - {year}"])
def test_a_template_takes_the_punctuation_with_the_field_that_is_missing(template):
    """A book with no publication year must not get a cover that says
    "Hodder," or "Hodder ()"."""
    spec = cg.resolve_design({"text": {"subtitle": template}})
    no_year = cg.BookCoverMeta(title="Solo", authors=["A N Other"], publisher="Hodder")
    drawn = _plain(cg.cover_text(spec, no_year)["subtitle"])
    assert drawn == "Hodder", "%r left punctuation behind: %r" % (template, drawn)

    has_year = cg.BookCoverMeta(title="Solo", authors=["A N Other"],
                                publisher="Hodder", year=2015)
    kept = _plain(cg.cover_text(spec, has_year)["subtitle"])
    assert "Hodder" in kept and "2015" in kept


def test_punctuation_inside_a_books_own_text_is_left_alone():
    """The punctuation rule is about the template, not about the book."""
    spec = cg.resolve_design({"text": {"title": "{title}"}})
    odd = cg.BookCoverMeta(title="Erased ()", authors=["A N Other"])
    assert _plain(cg.cover_text(spec, odd)["title"]) == "Erased ()"


def test_the_limits_the_panel_is_told_are_the_limits_the_server_enforces():
    """The panel disables its own controls off these numbers. If they drifted
    apart, a reader would type a value the form accepted and the server refused.
    (They also anchor the over-long strings the tests below type.)"""
    limits = cg.catalogue()["limits"]
    assert limits["max_template_length"] == cg.MAX_TEMPLATE_LENGTH < 500
    assert limits["max_name_length"] == cg.MAX_PRESET_NAME_LENGTH < 200
    assert limits["max_presets"] == cg.MAX_PRESETS_PER_USER
    assert limits["font_size_min"] < limits["font_size_max"]
    assert limits["min_width"] < limits["max_width"]


def test_the_cover_is_the_size_the_reader_asked_for_within_limits():
    data = _render({"style": "blocks", "scheme": "ink", "size": {"width": 500, "height": 750}})
    assert _pixels(data).size == (500, 750)

    clamped = cg.resolve_design({"size": {"width": 99999, "height": 1}})
    limits = cg.catalogue()["limits"]
    assert clamped.width == limits["max_width"]
    assert clamped.height == limits["min_height"]


def test_a_custom_palette_reports_itself_as_custom():
    """The panel shows "Custom" off ``scheme is None``; a scheme id whose colours
    are not the ones on the cover would make it lie."""
    tweaked = cg.resolve_design({"scheme": "water", "colors": {"title": "#ff00ff"}})
    assert tweaked.scheme is None
    assert tweaked.colors["title"] == "#ff00ff"

    untouched = cg.resolve_design({"scheme": "water"})
    assert untouched.scheme == "water"


def test_the_resolved_design_comes_back_complete():
    """The client sends a fragment and must be able to render the panel from the
    answer, so every field is filled in and echoed."""
    resolved = cg.resolve_design({"style": "cross"}).to_dict()
    assert set(resolved) >= {"style", "scheme", "colors", "fonts", "align", "text", "size"}
    assert set(resolved["colors"]) == set(cg.COLOR_SLOTS)
    assert set(resolved["fonts"]) == set(cg.TEXT_SLOTS)
    for slot in cg.TEXT_SLOTS:
        assert resolved["fonts"][slot]["family"]
        assert resolved["fonts"][slot]["size"] > 0
        assert resolved["align"][slot] in cg.ALIGNMENTS
        assert resolved["text"][slot] is not None
    assert resolved["size"] == {"width": cg.DEFAULT_WIDTH, "height": cg.DEFAULT_HEIGHT}


# ---------------------------------------------------------------------------
# The renderer must survive everything the catalogue offers
# ---------------------------------------------------------------------------

def test_the_builtin_renderer_draws_every_design_the_catalogue_offers():
    """Pillow is the fallback on an installation without Calibre, and the
    renderer CI uses. Anything the catalogue offers, it must draw."""
    catalogue = cg.catalogue()
    for style in (entry["id"] for entry in catalogue["styles"]):
        for scheme in (entry["id"] for entry in catalogue["schemes"]):
            data = _render({"style": style, "scheme": scheme,
                            "size": {"width": 160, "height": 240}})
            assert data[:2] == b"\xff\xd8", "%s/%s did not render" % (style, scheme)

    for font in (entry["id"] for entry in catalogue["fonts"]):
        for style in cg.STYLES:
            data = _render({"style": style,
                            "fonts": {slot: {"family": font} for slot in cg.TEXT_SLOTS},
                            "size": {"width": 160, "height": 240}})
            assert data[:2] == b"\xff\xd8", "%s in %s did not render" % (font, style)

    for preset in catalogue["presets"]:
        data = cg.render(META, cg.resolve_design(preset["design"]).scaled(160, 240)).data
        assert data[:2] == b"\xff\xd8", "preset %s did not render" % preset["id"]


def test_a_book_with_no_metadata_at_all_still_gets_a_cover():
    empty = cg.BookCoverMeta(title="", authors=[])
    for style in cg.STYLES:
        data = _render({"style": style}, meta=empty)
        assert data[:2] == b"\xff\xd8"


def test_extreme_but_legal_settings_do_not_crash_the_renderer():
    limits = cg.catalogue()["limits"]
    for size in (limits["font_size_min"], limits["font_size_max"]):
        data = _render({"style": "ornamental",
                        "fonts": {slot: {"size": size} for slot in cg.TEXT_SLOTS}})
        assert data[:2] == b"\xff\xd8"
    data = _render({"style": "cross",
                    "size": {"width": limits["min_width"], "height": limits["min_height"]}})
    assert data[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# Nothing a reader types becomes code, a path, or someone else's problem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("template", [
    "{python: __import__('os').system('id')}",
    "{title}{{authors}}",
    "{field:'title'}",
    "{nonexistent}",
    "{title}\n{authors}",
    "<script>alert(1)</script>{title}",
    "<img src=x onerror=alert(1)>",
    "x" * 500,          # past any sane template length; see the limits test
])
def test_a_template_cannot_smuggle_anything_past_the_boundary(template):
    """Calibre's template language has a ``python:`` mode — arbitrary code.

    Nothing a reader types is ever handed to it: what is accepted is a closed
    set of placeholders this module expands itself, plus the four inline tags
    Calibre's formatting parser understands. Everything else is refused at the
    edge rather than silently dropped, because a reader who typed something that
    does nothing deserves to be told.
    """
    with pytest.raises(cg.CoverGenerationError) as raised:
        cg.resolve_design({"text": {"title": template}})
    assert raised.value.code == "invalid_text"


def test_a_template_that_looks_like_calibre_code_is_drawn_as_text():
    """No template engine runs, here or downstream.

    Calibre's own covers are configured with its template language, so a reader
    who knows it will type some. It is not evaluated and it is not an error: it
    is the text they asked to have drawn, and the preview shows them that.
    """
    literal = "program: python_eval('1')"
    spec = cg.resolve_design({"text": {"title": literal}})
    assert _plain(cg.cover_text(spec, META)["title"]) == literal


def test_the_string_the_renderer_receives_carries_no_template_syntax():
    """Belt and braces: even the accepted templates expand to plain text."""
    spec = cg.resolve_design({"text": {"title": "{title}", "subtitle": "{series}",
                                       "author": "{authors}"}})
    for value in cg.cover_text(spec, META).values():
        assert "{" not in value and "}" not in value


def test_a_books_own_text_can_never_become_markup():
    """A title that literally contains <b> must print, not embolden."""
    tricky = cg.BookCoverMeta(title="<b>Bold</b> & <i>italic</i>", authors=["A & B"])
    spec = cg.resolve_design({"text": {"title": "{title}"}})
    drawn = cg.cover_text(spec, tricky)["title"]
    assert "&lt;b&gt;" in drawn
    assert "<b>Bold</b>" not in drawn


@pytest.mark.parametrize("font_id", [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "../../../../etc/passwd",
    "DejaVu Sans",
    "serif; rm -rf /",
])
def test_a_font_is_chosen_by_id_and_can_never_be_a_path(font_id):
    """A font id is looked up in a closed catalogue. It is not a file name.

    Every desktop tool takes a font path here; this one must not, because the id
    arrives from the network and would otherwise be an argument to a subprocess
    and an open() on the server.
    """
    with pytest.raises(cg.CoverGenerationError) as raised:
        cg.resolve_design({"fonts": {"title": {"family": font_id}}})
    assert raised.value.code == "unknown_font"
    assert cg.font_entry(font_id) is None


def test_the_family_name_handed_to_calibre_comes_from_the_catalogue(tmp_path, monkeypatch):
    """What crosses the process boundary is a family this server resolved."""
    seen = tmp_path / "request.json"
    binary = _stub_calibre_debug(tmp_path, """
        cat > "%s"
        printf 'CWNG_COVER_RESULT={"ok": false, "error": "stop here"}\\n'
    """ % seen)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "calibre")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)

    spec = cg.resolve_design({"fonts": {slot: {"family": "mono"} for slot in cg.TEXT_SLOTS}})
    with pytest.raises(cg.CoverGenerationError):
        cg.render(META, spec)

    sent = json.loads(seen.read_text())["spec"]
    catalogue_families = {entry.family for entry in cg.font_catalogue().values()}
    for slot in ("title", "subtitle", "footer"):
        assert sent["fonts"][slot]["family"] in catalogue_families
        assert "/" not in (sent["fonts"][slot]["family"] or "")


@pytest.mark.parametrize("colour", ["red", "#12", "#gggggg", "rgb(1,2,3)", "#12345g"])
def test_a_colour_has_to_be_a_colour(colour):
    with pytest.raises(cg.CoverGenerationError) as raised:
        cg.resolve_design({"colors": {"background": colour}})
    assert raised.value.code == "invalid_color"


def test_an_unknown_id_is_refused_on_a_request_but_tolerated_in_a_saved_design():
    """A request carries ids the client was just handed; a stored preset carries
    ids that were valid when it was saved. Those deserve different answers."""
    for field, value, code in (("style", "spiral", "unknown_style"),
                               ("scheme", "chartreuse", "unknown_scheme")):
        with pytest.raises(cg.CoverGenerationError) as raised:
            cg.resolve_design({field: value})
        assert raised.value.code == code

    lenient = cg.resolve_design({"style": "spiral", "scheme": "chartreuse"}, strict=False)
    assert lenient.style in cg.STYLES
    assert lenient.scheme in cg.COLOR_SCHEMES


def test_an_error_the_reader_sees_never_carries_the_renderers_output():
    """``CoverGenerationError.message`` quotes helper stderr, which carries
    server paths. The route maps it to a sentence this module wrote."""
    from cps import cover_picker

    leaky = cg.CoverGenerationError(
        "render_failed",
        "calibre: /srv/library/Book (7)/cover.jpg qt.qpa.plugin could not load "
        "/usr/lib/x86_64-linux-gnu/qt6/plugins/platforms")
    app = flask.Flask(__name__)
    with app.test_request_context():
        with patch.object(cover_picker, "_", side_effect=lambda text, **kw: text):
            response = cover_picker._designer_json_error(leaky)
    body = json.dumps(response.get_json())
    assert response.status_code == 502
    for secret in ("/srv/library", "/usr/lib/x86_64-linux-gnu", "qt.qpa"):
        assert secret not in body


def test_the_error_body_names_a_code_and_a_safe_sentence():
    """The contract both legs are written against is {"error", "message"}."""
    from cps import cover_picker

    app = flask.Flask(__name__)
    with app.test_request_context():
        with patch.object(cover_picker, "_", side_effect=lambda text, **kw: text):
            response = cover_picker._designer_json_error(
                cg.CoverGenerationError("unknown_style", "Unknown style: spiral"))
    body = response.get_json()
    assert response.status_code == 400
    assert body["error"] == "unknown_style"
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Presets a reader can actually add to and remove from
# ---------------------------------------------------------------------------

def _saved(session, user_id=7, name="Mine", design=None):
    from cps.services import cover_design_presets as presets_mod

    return presets_mod.create_preset(user_id, False, name,
                                     design or {"style": "cross", "scheme": "water"})


def test_a_reader_can_save_a_design_and_find_it_again(app_db):
    from cps.services import cover_design_presets as presets_mod

    saved = _saved(app_db, name="Deep water")
    assert saved["name"] == "Deep water"
    assert saved["design"]["style"] == "cross"
    assert saved["builtin"] is False

    listed = presets_mod.list_presets(7)["presets"]
    assert saved["id"] in [entry["id"] for entry in listed]
    # Shipped designs first, the reader's own after them.
    assert listed[0]["builtin"] is True
    assert listed[-1]["id"] == saved["id"]


def test_a_reader_can_rename_and_redesign_what_they_saved(app_db):
    from cps.services import cover_design_presets as presets_mod

    saved = _saved(app_db)
    updated = presets_mod.update_preset(7, False, saved["id"], name="Renamed",
                                        design={"style": "banner", "scheme": "ember"})
    assert updated["name"] == "Renamed"
    assert updated["design"]["style"] == "banner"
    assert presets_mod.list_presets(7)["presets"][-1]["name"] == "Renamed"


def test_a_reader_can_delete_their_own_design(app_db):
    from cps.services import cover_design_presets as presets_mod

    saved = _saved(app_db)
    assert presets_mod.delete_preset(7, False, saved["id"]) == "deleted"
    assert saved["id"] not in [e["id"] for e in presets_mod.list_presets(7)["presets"]]


def test_deleting_a_shipped_design_hides_it_for_that_reader_alone(app_db):
    """"The presets are 100% useless unless the user can rm and add them" — but
    one reader's tidying must not empty everybody else's list."""
    from cps.services import cover_design_presets as presets_mod

    builtin = cg.DEFAULT_PRESET
    assert presets_mod.delete_preset(7, False, builtin) == "hidden"

    mine = [entry["id"] for entry in presets_mod.list_presets(7)["presets"]]
    theirs = [entry["id"] for entry in presets_mod.list_presets(8)["presets"]]
    assert builtin not in mine
    assert builtin in theirs


def test_a_hidden_design_comes_back_when_asked(app_db):
    from cps.services import cover_design_presets as presets_mod

    builtin = cg.DEFAULT_PRESET
    presets_mod.delete_preset(7, False, builtin)
    restored = presets_mod.restore_preset(7, builtin)
    assert restored["id"] == builtin
    assert builtin in [entry["id"] for entry in presets_mod.list_presets(7)["presets"]]
    # Asking twice is not an error; the panel can retry.
    presets_mod.restore_preset(7, builtin)


def test_one_readers_saved_designs_are_invisible_to_another(app_db):
    from cps.services import cover_design_presets as presets_mod

    saved = _saved(app_db, user_id=7, name="Private")
    assert saved["id"] not in [e["id"] for e in presets_mod.list_presets(8)["presets"]]
    for call in (lambda: presets_mod.update_preset(8, False, saved["id"], name="Stolen"),
                 lambda: presets_mod.delete_preset(8, False, saved["id"])):
        with pytest.raises(cg.CoverGenerationError) as raised:
            call()
        assert raised.value.code in ("unknown_preset", "forbidden")
    assert presets_mod.list_presets(7)["presets"][-1]["name"] == "Private"


def test_only_an_administrator_can_publish_a_design_to_the_whole_library(app_db):
    from cps.services import cover_design_presets as presets_mod

    with pytest.raises(cg.CoverGenerationError) as raised:
        presets_mod.create_preset(7, False, "House style", {"style": "half"}, scope="library")
    assert raised.value.code == "forbidden"

    published = presets_mod.create_preset(9, True, "House style", {"style": "half"},
                                          scope="library")
    assert published["scope"] == "library"
    assert published["id"] in [e["id"] for e in presets_mod.list_presets(7)["presets"]]


def test_a_library_design_can_be_hidden_by_someone_who_cannot_delete_it(app_db):
    from cps.services import cover_design_presets as presets_mod

    published = presets_mod.create_preset(9, True, "House style", {"style": "half"},
                                          scope="library")
    assert presets_mod.delete_preset(7, False, published["id"]) == "hidden"
    assert published["id"] not in [e["id"] for e in presets_mod.list_presets(7)["presets"]]
    # Still there for everybody else, and still deletable by an admin.
    assert published["id"] in [e["id"] for e in presets_mod.list_presets(8)["presets"]]
    assert presets_mod.delete_preset(9, True, published["id"]) == "deleted"


def test_saving_designs_is_capped_so_one_account_cannot_fill_the_table(app_db):
    from cps.services import cover_design_presets as presets_mod

    for index in range(cg.MAX_PRESETS_PER_USER):
        presets_mod.create_preset(7, False, "Design %s" % index, {"style": "blocks"})
    with pytest.raises(cg.CoverGenerationError) as raised:
        presets_mod.create_preset(7, False, "One too many", {"style": "blocks"})
    assert raised.value.code == "too_many_presets"


def test_two_of_a_readers_designs_cannot_share_a_name(app_db):
    from cps.services import cover_design_presets as presets_mod

    _saved(app_db, name="Same")
    with pytest.raises(cg.CoverGenerationError) as raised:
        _saved(app_db, name="Same")
    assert raised.value.code == "duplicate_name"
    # Somebody else may still use that name.
    presets_mod.create_preset(8, False, "Same", {"style": "blocks"})


@pytest.mark.parametrize("name", ["", "   ", "x" * 200,
                                  "two\nlines", "bell\x07"])
def test_a_design_name_has_to_be_a_name(app_db, name):
    from cps.services import cover_design_presets as presets_mod

    with pytest.raises(cg.CoverGenerationError) as raised:
        presets_mod.create_preset(7, False, name, {"style": "blocks"})
    assert raised.value.code == "invalid_name"


def test_a_design_is_validated_before_it_is_stored(app_db):
    """The same validator guards preview, apply and save; a preset is not a way
    to smuggle a design past it and have it rendered later."""
    from cps.services import cover_design_presets as presets_mod

    with pytest.raises(cg.CoverGenerationError):
        presets_mod.create_preset(7, False, "Bad", {"text": {"title": "{python: 1}"}})
    with pytest.raises(cg.CoverGenerationError):
        presets_mod.create_preset(7, False, "Bad", {"fonts": {"title": {"family": "/etc/passwd"}}})
    stored = presets_mod.list_presets(7)["presets"]
    assert [entry["id"] for entry in stored] == [entry["id"] for entry in cg.builtin_presets()], (
        "a rejected design was stored anyway")


def test_a_saved_design_still_opens_after_the_font_it_names_is_gone(app_db):
    """A preset saved months ago must not take the whole catalogue down."""
    from cps.services import cover_design_presets as presets_mod

    saved = _saved(app_db, name="Typographic")
    row = app_db.query(ub.CoverDesignPreset).filter(
        ub.CoverDesignPreset.id == int(saved["id"].split("-")[1])).one()
    stored = json.loads(row.design)
    stored["fonts"]["title"]["family"] = "a-font-this-server-no-longer-has"
    row.design = json.dumps(stored)
    app_db.commit()

    listed = presets_mod.list_presets(7)["presets"]
    reopened = [entry for entry in listed if entry["id"] == saved["id"]][0]
    assert reopened["design"]["fonts"]["title"]["family"] in cg.font_catalogue()
    assert cg.render(META, cg.resolve_design(reopened["design"]).scaled(160, 240)).data[:2] == b"\xff\xd8"


def test_the_designs_a_reader_reorders_stay_in_that_order(app_db):
    from cps.services import cover_design_presets as presets_mod

    first = presets_mod.create_preset(7, False, "First", {"style": "blocks"})
    second = presets_mod.create_preset(7, False, "Second", {"style": "cross"})
    presets_mod.reorder_presets(7, [second["id"], first["id"]])
    mine = [entry["id"] for entry in presets_mod.list_presets(7)["presets"]
            if not entry["builtin"]]
    assert mine == [second["id"], first["id"]]


def test_the_panel_still_opens_when_the_preset_table_cannot_be_read(monkeypatch):
    """A database that has not been migrated must cost the saved designs, not
    the designer."""
    from cps import cover_picker
    from cps.services import cover_design_presets as presets_mod

    monkeypatch.setattr(presets_mod, "list_presets",
                        MagicMock(side_effect=RuntimeError("no such table")))
    app = flask.Flask(__name__)
    with app.test_request_context():
        with patch.object(cover_picker, "current_user", MagicMock(id=7)), \
             patch.object(cover_picker, "config",
                          MagicMock(config_binariesdir="",
                                    config_cover_generator_default_preset="classic")):
            state = cover_picker.designer_state()
    assert state["available"] is True
    assert [entry["id"] for entry in state["presets"]] == list(cg.PRESETS)


# ---------------------------------------------------------------------------
# Catalogue imagery: rendered once, not once per reader
# ---------------------------------------------------------------------------

def test_the_catalogue_shows_a_picture_of_every_arrangement_and_lettering():
    """"Show little previews … so that it is obvious what each one is before the
    user clicks it" — the pictures have to exist and have to differ."""
    catalogue = cg.catalogue()
    for entry in catalogue["styles"]:
        assert entry["thumbnail_url"]
    for entry in catalogue["fonts"]:
        assert entry["sample_url"]
        assert entry["css_stack"]

    thumbs = {entry["id"]: cg.style_thumbnail(entry["id"]).data for entry in catalogue["styles"]}
    assert all(data[:2] == b"\xff\xd8" for data in thumbs.values())
    assert len(set(thumbs.values())) == len(thumbs)

    sample = cg.font_sample(catalogue["fonts"][0]["id"])
    assert sample.data[:2] == b"\xff\xd8"


def test_a_catalogue_picture_is_drawn_once_and_then_read_from_disk():
    from cps.services import cover_designer_cache

    calls = []

    def draw():
        calls.append(1)
        return cg.style_thumbnail("blocks").data

    first, cached_first = cover_designer_cache.cached("style", "blocks", 266, 400, "pil", draw)
    second, cached_second = cover_designer_cache.cached("style", "blocks", 266, 400, "pil", draw)
    assert first == second
    assert (cached_first, cached_second) == (False, True)
    assert len(calls) == 1


# The designer asks for every thumbnail the moment the panel opens, so the
# interesting case is not one request — it is several requests for the same
# uncached picture arriving together. That has to be measured the way the server
# actually runs it: gevent, one OS thread carrying every request as a greenlet,
# and a render dispatched to a threadpool that yields while it waits. A test that
# used ordinary threads would pass whatever this module does, because blocking
# one thread out of several costs nothing. So the scenario runs in a child
# process with a hard wall-clock bound around it, and the bound is the
# assertion: a server that stops answering does not fail this test slowly.
_CONCURRENT_MISS_PROBE = textwrap.dedent('''
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])

    import gevent

    from cps.services import cover_designer_cache, cover_preview, cover_preview_cache

    cover_preview_cache.CACHE_ROOT = Path(sys.argv[2])

    renders = []
    beats = []
    answers = {}


    def draw():
        """A catalogue render: real work on the pool, which yields to the hub."""
        renders.append(1)
        cover_preview._run_in_pool(time.sleep, 0.4)
        return b"\\xff\\xd8" + b"catalogue-image" * 4


    def ask(name):
        answers[name] = cover_designer_cache.cached(
            "style", "banner", 120, 180, "pil", draw)


    def heartbeat():
        """Everything else the server would be doing while that render runs."""
        while True:
            gevent.sleep(0.02)
            beats.append(1)


    pulse = gevent.spawn(heartbeat)
    started = time.time()
    asking = [gevent.spawn(ask, "first"), gevent.spawn(ask, "second")]
    gevent.joinall(asking, timeout=15)
    pulse.kill()
    for greenlet in asking:
        if greenlet.exception is not None:
            print("greenlet failed: %r" % (greenlet.exception,), file=sys.stderr)
    print("PROBE answers=%d renders=%d beats=%d seconds=%.2f"
          % (len(answers), len(renders), len(beats), time.time() - started))
''')


def test_two_readers_opening_the_designer_at_once_both_get_their_picture(tmp_path):
    """Both requests answer, one render happens, and the server stays awake.

    Miss the same key twice at once and the second request must not be able to
    stop the first one from finishing. Holding an ordinary lock across the render
    does exactly that here: the waiting request blocks the one thread the hub
    runs on, the finished render can never be handed back, and the whole server
    stops — not just the designer.
    """
    pytest.importorskip("gevent")
    probe = tmp_path / "concurrent_miss_probe.py"
    probe.write_text(_CONCURRENT_MISS_PROBE)
    cache_root = tmp_path / "designer-cache"
    cache_root.mkdir()
    tree = pathlib.Path(__file__).resolve().parents[2]

    try:
        finished = subprocess.run(
            [sys.executable, str(probe), str(tree), str(cache_root)],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "two readers opened the designer at once and neither got a picture: "
            "the second request for an uncached image wedged the process the "
            "first one's render had to come back through")

    report = [line for line in finished.stdout.splitlines() if line.startswith("PROBE ")]
    assert report, "probe produced no result\nstdout: %s\nstderr: %s" % (
        finished.stdout, finished.stderr)
    measured = dict(field.split("=", 1) for field in report[0].split()[1:])
    detail = "%s\nstderr: %s" % (report[0], finished.stderr)

    # Both readers got their picture ...
    assert int(measured["answers"]) == 2, detail
    # ... from a single render, which is the point of the cache ...
    assert int(measured["renders"]) == 1, detail
    # ... without the waiting request stopping everything else the server does ...
    assert int(measured["beats"]) >= 3, detail
    # ... and in about the length of one render, not in a timeout.
    assert float(measured["seconds"]) < 10, detail


def test_a_picture_cached_without_calibre_is_not_served_once_calibre_is_installed():
    """The two renderers draw the same design differently; a thumbnail is a
    picture of one of them, so which one is part of its identity."""
    from cps.services import cover_designer_cache

    pil_key = cover_designer_cache.cache_key("style", "blocks", 266, 400, "pil")
    calibre_key = cover_designer_cache.cache_key("style", "blocks", 266, 400, "calibre")
    assert pil_key != calibre_key

    cover_designer_cache.store(pil_key, b"\xff\xd8pretend-pillow-drew-this")
    assert cover_designer_cache.load(calibre_key) is None


def test_an_unreasonably_large_picture_is_served_but_not_stored():
    """One runaway render must not eat the shared cache budget."""
    from cps.services import cover_designer_cache

    key = cover_designer_cache.cache_key("style", "huge", 266, 400, "pil")
    cover_designer_cache.store(key, b"\xff\xd8" + b"x" * cover_designer_cache.MAX_IMAGE_BYTES)
    assert cover_designer_cache.load(key) is None


def test_a_picture_of_something_we_do_not_offer_is_refused():
    for call, code in ((lambda: cg.style_thumbnail("../../etc/passwd"), "unknown_style"),
                       (lambda: cg.font_sample("/usr/share/fonts/x.ttf"), "unknown_font")):
        with pytest.raises(cg.CoverGenerationError) as raised:
            call()
        assert raised.value.code == code


# ---------------------------------------------------------------------------
# The v1 designer keeps working
# ---------------------------------------------------------------------------

def test_an_older_client_still_gets_the_cover_it_asked_for():
    """v1 bodies name a preset and three ids. Those still mean something, or
    every automatic cover and every admin default breaks on upgrade."""
    old = cg.resolve_spec(preset="classic", scheme="ember", font="serif", layout="banner",
                          width=320, height=480)
    assert old.style == "banner"
    assert old.scheme == "ember"
    assert all(entry["family"] == "serif" for entry in old.fonts.values())
    assert cg.render(META, old).data[:2] == b"\xff\xd8"


def test_a_preset_still_names_the_three_ids_the_old_panel_draws_from():
    """The pre-v2 panel holds a scheme, a font and a layout, and renders nothing
    at all until it has all three of them — it reads them off the preset it
    opens on. A catalogue that described its presets only as v2 designs would
    leave that panel open, populated and permanently blank."""
    catalogue = cg.catalogue("")
    schemes = {entry["id"] for entry in catalogue["schemes"]}
    fonts = {entry["id"] for entry in catalogue["fonts"]}
    layouts = {entry["id"] for entry in catalogue["layouts"]}
    for entry in catalogue["presets"]:
        assert entry["scheme"] in schemes, entry["id"]
        assert entry["font"] in fonts, entry["id"]
        assert entry["layout"] in layouts, entry["id"]
        # The three are a view of the design, not a second opinion about it.
        assert entry["layout"] == entry["design"]["style"], entry["id"]
        assert entry["scheme"] == entry["design"]["scheme"], entry["id"]
        assert entry["font"] == entry["design"]["fonts"]["title"]["family"], entry["id"]


def test_the_old_panel_opens_on_a_preset_rather_than_on_nothing():
    """It lights the chip whose three ids equal the ones it is holding, and it
    starts out holding the default preset's. Matching nothing is the state it
    shows for a custom design, so opening in it would be a lie."""
    catalogue = cg.catalogue("")
    opening = next(entry for entry in catalogue["presets"]
                   if entry["id"] == catalogue["default_preset"])
    lit = [entry["id"] for entry in catalogue["presets"]
           if (entry["scheme"], entry["font"], entry["layout"])
           == (opening["scheme"], opening["font"], opening["layout"])]
    assert lit[0] == catalogue["default_preset"]


def test_every_shipped_preset_still_names_a_design_we_can_draw():
    for key, entry in cg.PRESETS.items():
        spec = cg.resolve_design(entry["design"], strict=True)
        assert spec.style in cg.STYLES, key
        assert cg.render(META, spec.scaled(160, 240)).data[:2] == b"\xff\xd8"


def test_the_library_default_preset_survives_being_removed_in_a_later_release():
    """It is persisted as an id in settings; an unknown one must fall back, not
    take every automatic cover down."""
    spec = cg.resolve_spec(preset="a-preset-that-was-retired", width=160, height=240)
    assert spec.style in cg.STYLES
    assert cg.render(META, spec).data[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# The lettering list is a list of typefaces
# ---------------------------------------------------------------------------

def _font_directory(tmp_path, *names) -> str:
    """A font directory holding copies of fonts this repository already ships."""
    repository = pathlib.Path(cg.__file__).resolve().parents[2]
    directory = tmp_path / ("fonts-%d" % len(os.listdir(tmp_path)))
    directory.mkdir()
    for name in names:
        source = repository / name
        shutil.copy(source, directory / source.name)
    return str(directory)


def _offered_lettering(binaries_dir="") -> set:
    cg.reset_font_cache()
    try:
        return {entry["label"] for entry in cg.catalogue(binaries_dir)["fonts"]}
    finally:
        cg.reset_font_cache()


def test_an_icon_font_is_not_offered_as_lettering(tmp_path, monkeypatch):
    """Font directories hold icon sheets as well as typefaces, and a title set
    in one comes out as a row of little pictures or of empty boxes. Offering it
    is offering the reader a broken cover."""
    monkeypatch.setattr(cg, "_calibre_font_dirs", lambda *args, **kwargs: ())
    monkeypatch.setattr(cg, "_FONT_SEARCH_DIRS", (_font_directory(
        tmp_path,
        "cps/static/standard_fonts/LiberationSans-Regular.ttf",
        "cps/static/css/fonts/fontello.ttf",
        "cps/static/css/fonts/glyphicons-halflings-regular.ttf"),))

    offered = _offered_lettering()
    assert "Liberation Sans" in offered
    assert not [label for label in offered
                if "fontello" in label.lower() or "glyphicon" in label.lower()]


def test_a_font_that_declares_itself_a_symbol_set_is_not_offered(tmp_path, monkeypatch):
    """Dingbats and symbol faces map the alphabet to pictures, so nothing about
    their outlines gives them away — but they say what they are in their own
    OS/2 table. The same file, that one declaration changed, must drop out."""
    monkeypatch.setattr(cg, "_calibre_font_dirs", lambda *args, **kwargs: ())
    plain = _font_directory(tmp_path, "cps/static/standard_fonts/LiberationSans-Regular.ttf")

    monkeypatch.setattr(cg, "_FONT_SEARCH_DIRS", (plain,))
    assert "Liberation Sans" in _offered_lettering()

    face = next(pathlib.Path(plain).iterdir())
    with face.open("rb") as handle:
        tables = cg._sfnt_tables(handle)
    raw = bytearray(face.read_bytes())
    at = tables["OS/2"][0] + 30
    raw[at:at + 2] = (cg._SYMBOLIC_FAMILY_CLASS << 8).to_bytes(2, "big")
    face.write_bytes(bytes(raw))

    assert "Liberation Sans" not in _offered_lettering()


def test_a_font_this_server_cannot_read_is_still_offered(tmp_path, monkeypatch):
    """The rule errs towards offering a font: a face whose tables we cannot
    parse is a face somebody installed on purpose, not a reason to hide it."""
    monkeypatch.setattr(cg, "_calibre_font_dirs", lambda *args, **kwargs: ())
    directory = _font_directory(tmp_path, "cps/static/standard_fonts/LiberationSans-Regular.ttf")
    face = next(pathlib.Path(directory).iterdir())
    raw = bytearray(face.read_bytes())
    with face.open("rb") as handle:
        tables = cg._sfnt_tables(handle)
    # Truncate the character map so nothing can be counted from it.
    raw[tables["cmap"][0]:tables["cmap"][0] + 4] = b"\x00\x00\x00\x00"
    face.write_bytes(bytes(raw))

    monkeypatch.setattr(cg, "_FONT_SEARCH_DIRS", (directory,))
    assert "Liberation Sans" in _offered_lettering()


# ---------------------------------------------------------------------------
# What the panel opens on, and what it can put back
# ---------------------------------------------------------------------------

def test_the_panel_opens_on_the_design_the_library_chose(monkeypatch):
    """The panel decides which preset is selected by matching the design it
    opens on against each one, so a ``defaults`` that matched nothing would
    open every reader on "Custom"."""
    for preset in ("classic", "calibre-blue", "midnight"):
        offered = cg.catalogue(default_preset=preset)
        assert offered["default_preset"] == preset
        chosen = next(entry for entry in offered["presets"] if entry["id"] == preset)
        assert offered["defaults"] == chosen["design"], preset

    retired = cg.catalogue(default_preset="a-preset-that-was-retired")
    assert retired["default_preset"] == cg.DEFAULT_PRESET


def test_the_library_default_reaches_the_panel(monkeypatch):
    """Settings hold an id; the panel is handed the design it stands for."""
    from cps import cover_picker

    app = flask.Flask(__name__)
    with app.test_request_context():
        with patch.object(cover_picker, "current_user", MagicMock(id=7)), \
             patch.object(cover_picker, "config",
                          MagicMock(config_binariesdir="",
                                    config_cover_generator_default_preset="calibre-blue")), \
             patch.object(cover_picker, "_presets_for_current_user",
                          MagicMock(return_value={"presets": cg.builtin_presets(),
                                                  "hidden": []})):
            state = cover_picker.designer_state()
    assert state["default_preset"] == "calibre-blue"
    chosen = next(entry for entry in state["presets"] if entry["id"] == "calibre-blue")
    assert state["defaults"] == chosen["design"]


def test_a_hidden_shipped_design_is_still_listed_for_the_reader_who_hid_it(app_db):
    """Hiding one is not deleting it, and the only way back is the list that
    manages them: a reader cannot restore something they can no longer see."""
    from cps.services import cover_design_presets as presets_mod

    builtin = cg.DEFAULT_PRESET
    presets_mod.delete_preset(7, False, builtin)

    offered = presets_mod.list_presets(7)["presets"]
    assert builtin not in [entry["id"] for entry in offered]
    assert all(entry["hidden"] is False for entry in offered)

    manage = presets_mod.list_presets(7, include_hidden=True)["presets"]
    assert [entry["id"] for entry in manage if entry["hidden"]] == [builtin]
    assert builtin in [entry["id"] for entry in manage]


def test_a_reader_without_edit_rights_can_still_design_their_own_cover():
    """The personal-cover picker sends ``?scope=personal``: that reader may set
    a cover only they see, and has no edit role for the library's own. The
    designer has to let them draw one, and refuse them without it."""
    from werkzeug.exceptions import Forbidden

    from cps import cover_picker

    # Only the login check is skipped; the scope guard and the view are the
    # subject of the test.
    view = cover_picker.cover_picker_design_preview.__wrapped__
    reader = MagicMock(id=7)
    reader.role_edit.return_value = False
    reader.role_admin.return_value = False
    reader.role_browse_global.return_value = False
    app = flask.Flask(__name__)

    with app.test_request_context("/book/1/cover/design-preview?scope=personal",
                                  json={"design": {"style": "banner", **SMALL}}):
        with patch.object(cover_picker, "current_user", reader), \
             patch.object(cover_picker, "_load_book", MagicMock(return_value=object())), \
             patch.object(cover_picker, "_book_cover_meta",
                          MagicMock(return_value=_rich_book())), \
             patch.object(cover_picker, "config", MagicMock(config_binariesdir="")):
            body = view(1).get_json()
    assert body["data_url"].startswith("data:image/")

    with app.test_request_context("/book/1/cover/design-preview",
                                  json={"design": {"style": "banner"}}):
        with patch.object(cover_picker, "current_user", reader):
            with pytest.raises(Forbidden):
                view(1)
