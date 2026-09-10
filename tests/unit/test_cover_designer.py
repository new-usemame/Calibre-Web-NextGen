# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioural cover for "Design a cover".

The feature's promise is narrow and checkable: the reader picks a design, sees a
preview, applies it, and gets *that* cover — rendered on the server from the
book's own metadata, on an installation that may or may not have Calibre.  These
tests drive that promise at the two seams where it can actually break: the
renderer's contract (determinism, honouring the chosen design, refusing a design
it does not have, surviving a broken Calibre) and the apply route (the stored
bytes come from the server's renderer, never from the request body).

Nothing here requires Calibre to be installed; the Calibre path is exercised
through a stub ``calibre-debug`` so its failure modes — a crash, a wedge, a
runaway payload — are reproducible rather than hoped about.
"""

from __future__ import annotations

import base64
import io
import json
import os
import stat
import textwrap
import time
from unittest.mock import MagicMock, patch

import flask
import pytest

from cps.services import cover_generator as cg


pytestmark = pytest.mark.unit


META = cg.BookCoverMeta(
    title="The Long Way to a Small Angry Planet",
    authors=["Becky Chambers"],
    series="Wayfarers",
    series_index=1,
)


@pytest.fixture(autouse=True)
def pinned_renderer(monkeypatch):
    """Pin the built-in renderer unless a test asks for the Calibre path.

    Whether the machine running the suite happens to have Calibre installed must
    not change what these tests measure.
    """
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "pil")
    monkeypatch.delenv("CWNG_CALIBRE_DEBUG_PATH", raising=False)


def _dominant_colours(data: bytes, count: int = 3):
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    colours = image.getcolors(maxcolors=image.width * image.height)
    return [colour for _n, colour in sorted(colours, reverse=True)[:count]]


def _stub_calibre_debug(tmp_path, body: str) -> str:
    """A fake ``calibre-debug`` whose behaviour the test dictates."""
    path = tmp_path / "calibre-debug"
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


# ---------------------------------------------------------------------------
# The design the reader chose is the design that gets drawn
# ---------------------------------------------------------------------------

def test_the_same_design_renders_the_same_cover_every_time():
    """Preview then apply must agree, so the render cannot be random.

    Calibre's own generator picks a random theme and style when it is not told
    which to use. If this feature ever stops pinning both, a reader would apply
    a cover they never saw — and this goes red.
    """
    spec = cg.resolve_spec(preset="classic", width=300, height=400)
    first = cg.render(META, spec).data
    second = cg.render(META, spec).data
    assert first == second


def test_the_chosen_colour_scheme_reaches_the_pixels():
    """Two schemes must not produce the same picture.

    A renderer that accepted the scheme id and ignored it would pass every
    structural check while giving the reader one cover for five choices.
    """
    ink = cg.render(META, cg.resolve_spec(scheme="ink", font="serif", layout="blocks",
                                          width=300, height=400)).data
    ember = cg.render(META, cg.resolve_spec(scheme="ember", font="serif", layout="blocks",
                                            width=300, height=400)).data
    assert ink != ember
    assert _dominant_colours(ink)[0] != _dominant_colours(ember)[0]


def test_the_chosen_arrangement_reaches_the_pixels():
    for left, right in (("blocks", "banner"), ("banner", "ornamental")):
        one = cg.render(META, cg.resolve_spec(scheme="ink", font="serif", layout=left,
                                              width=300, height=400)).data
        other = cg.render(META, cg.resolve_spec(scheme="ink", font="serif", layout=right,
                                                width=300, height=400)).data
        assert one != other, f"{left} and {right} rendered identically"


def test_the_book_is_what_gets_drawn_not_a_template():
    """A different book must give a different cover."""
    spec = cg.resolve_spec(preset="classic", width=300, height=400)
    other = cg.BookCoverMeta(title="A Psalm for the Wild-Built", authors=["Becky Chambers"])
    assert cg.render(META, spec).data != cg.render(other, spec).data


def test_a_design_we_do_not_offer_is_refused_rather_than_substituted():
    """An unknown option is a bug or a probe, not stale state — say no.

    Quietly falling back would let a client apply a cover in a design nobody
    chose, and the reader would have no way to tell.
    """
    for kwargs in ({"scheme": "chartreuse"}, {"font": "comic"}, {"layout": "spiral"}):
        with pytest.raises(cg.CoverGenerationError) as raised:
            cg.resolve_spec(preset="classic", **kwargs)
        assert raised.value.code.startswith("unknown_")


def test_a_preset_that_no_longer_exists_still_renders():
    """The library default is a stored preset id; a release that drops a preset
    must not take every automatic cover down with it."""
    spec = cg.resolve_spec(preset="a-preset-from-an-older-release", width=300, height=400)
    assert spec.scheme == cg.PRESETS[cg.DEFAULT_PRESET]["scheme"]
    assert cg.render(META, spec).data


def test_an_absurd_size_request_is_clamped_not_honoured():
    """Sizes arrive from a request; a 40000px cover is a memory attack."""
    spec = cg.resolve_spec(preset="classic", width=99999, height=-5)
    assert spec.width == cg.MAX_DIMENSION
    assert spec.height == cg.MIN_DIMENSION


def test_every_catalogued_design_actually_renders():
    """The panel offers what the catalogue lists, so everything listed must work."""
    catalogue = cg.catalogue()
    for scheme in catalogue["schemes"]:
        for layout in catalogue["layouts"]:
            spec = cg.resolve_spec(scheme=scheme["id"], font="sans", layout=layout["id"],
                                   width=240, height=320)
            assert cg.render(META, spec).data[:2] == b"\xff\xd8", (scheme["id"], layout["id"])


# ---------------------------------------------------------------------------
# The Calibre renderer and its failure modes
# ---------------------------------------------------------------------------

def test_calibre_output_is_used_when_calibre_is_installed(tmp_path, monkeypatch):
    payload = base64.b64encode(b"\xff\xd8\xff-calibre-drew-this").decode()
    binary = _stub_calibre_debug(tmp_path, f"""
        cat > /dev/null
        echo 'some calibre chatter on stdout'
        echo 'CWNG_COVER_RESULT={{"ok": true, "data": "{payload}"}}'
    """)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "auto")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)

    rendered = cg.render(META, cg.resolve_spec(preset="classic", width=300, height=400))
    assert rendered.renderer == "calibre"
    assert rendered.data == b"\xff\xd8\xff-calibre-drew-this"


def test_the_book_reaches_calibre_as_its_own_metadata(tmp_path, monkeypatch):
    """The helper is handed the book's title, authors and series — not a template.

    The request is echoed back through the result line so the test observes what
    the subprocess was actually given.
    """
    binary = _stub_calibre_debug(tmp_path, """
        REQUEST=$(cat)
        printf 'CWNG_COVER_REQUEST=%s\\n' "$REQUEST"
        echo 'CWNG_COVER_RESULT={"ok": true, "data": "/9j/"}'
    """)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "calibre")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)

    seen = {}
    real_run = cg.subprocess.run

    def capture(command, **kwargs):
        completed = real_run(command, **kwargs)
        for line in completed.stdout.splitlines():
            if line.startswith("CWNG_COVER_REQUEST="):
                seen.update(json.loads(line.split("=", 1)[1]))
        return completed

    with patch.object(cg.subprocess, "run", side_effect=capture):
        cg.render(META, cg.resolve_spec(preset="meadow", width=300, height=400))

    assert seen["title"] == META.title
    assert seen["authors"] == ["Becky Chambers"]
    assert seen["series"] == "Wayfarers"
    # Colours travel without '#': calibre's theme_to_colors prepends one, and a
    # '#'-prefixed value silently renders greyscale instead of the chosen scheme.
    assert all(not value.startswith("#") for value in seen["spec"]["colors"].values())
    assert seen["spec"]["style"] == cg.LAYOUTS["banner"]["calibre_style"]


def test_a_broken_calibre_still_gives_the_reader_a_cover(tmp_path, monkeypatch):
    """A plainer cover beats an error page when Calibre cannot run."""
    binary = _stub_calibre_debug(tmp_path, """
        cat > /dev/null
        echo 'qt.qpa.plugin: could not load the Qt platform plugin' >&2
        exit 1
    """)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "auto")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)

    rendered = cg.render(META, cg.resolve_spec(preset="classic", width=300, height=400))
    assert rendered.renderer == "pil"
    assert rendered.data[:2] == b"\xff\xd8"


def test_a_wedged_calibre_is_killed_instead_of_hanging_the_request(tmp_path, monkeypatch):
    """A cover preview is an interactive request; a stuck Qt must not hold it.

    The stub sleeps far longer than the timeout, so a missing timeout shows up
    as a test that takes 30 seconds instead of one that fails an assertion.
    """
    binary = _stub_calibre_debug(tmp_path, """
        cat > /dev/null
        sleep 30
    """)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "auto")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_TIMEOUT_SECONDS", "1")

    started = time.monotonic()
    rendered = cg.render(META, cg.resolve_spec(preset="classic", width=300, height=400))
    elapsed = time.monotonic() - started

    assert rendered.renderer == "pil"
    assert elapsed < 15, f"the wedged renderer held the request for {elapsed:.1f}s"


def test_a_runaway_render_is_refused_before_it_reaches_a_book_folder(tmp_path, monkeypatch):
    """The subprocess output is untrusted input; a cover is not 9 MB of anything."""
    oversized = base64.b64encode(b"x" * (cg.MAX_OUTPUT_BYTES + 1024)).decode()
    binary = _stub_calibre_debug(tmp_path, f"""
        cat > /dev/null
        echo 'CWNG_COVER_RESULT={{"ok": true, "data": "{oversized}"}}'
    """)
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "calibre")
    monkeypatch.setenv("CWNG_CALIBRE_DEBUG_PATH", binary)

    with pytest.raises(cg.CoverGenerationError) as raised:
        cg.render(META, cg.resolve_spec(preset="classic", width=300, height=400))
    assert raised.value.code == "unavailable"
    assert "too_large" in raised.value.message or "over the" in raised.value.message


def test_an_installation_with_no_renderer_says_so(monkeypatch):
    """Silence would look like a cover that failed to load; say it plainly."""
    monkeypatch.setenv("CWNG_COVER_GENERATOR_RENDERER", "auto")
    monkeypatch.setattr(cg, "calibre_debug_path", lambda *a, **k: "")
    monkeypatch.setattr(cg, "_pil_available", lambda: False)

    assert cg.renderer_availability()["available"] is False
    with pytest.raises(cg.CoverGenerationError) as raised:
        cg.render(META, cg.resolve_spec(preset="classic"))
    assert raised.value.code == "unavailable"


# ---------------------------------------------------------------------------
# Writing a cover for a book that has none
# ---------------------------------------------------------------------------

def test_an_existing_cover_is_never_overwritten(tmp_path):
    """"Books that already have a cover are never touched" is the whole safety
    story for automatic generation, including on a re-run over the library."""
    destination = tmp_path / "Author" / "Title (7)" / "cover.jpg"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"the reader's own cover")

    assert cg.generate_cover_file(str(destination), META) is False
    assert destination.read_bytes() == b"the reader's own cover"


def test_a_generated_cover_is_only_visible_once_it_is_complete(tmp_path):
    """No half-written cover.jpg: the file appears by rename or not at all."""
    destination = tmp_path / "Author" / "Title (7)" / "cover.jpg"

    assert cg.generate_cover_file(str(destination), META) is True
    assert destination.read_bytes()[:2] == b"\xff\xd8"
    leftovers = [name for name in os.listdir(destination.parent) if name != "cover.jpg"]
    assert leftovers == []


def test_automatic_generation_is_off_until_an_admin_turns_it_on(tmp_path):
    """It writes into book folders, so an unmigrated or silent database is off."""
    import sqlite3

    unmigrated = tmp_path / "app.db"
    with sqlite3.connect(unmigrated) as connection:
        connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO settings (id) VALUES (1)")
    assert cg.settings_from_app_db(str(unmigrated)).auto_enabled is False

    migrated = tmp_path / "app2.db"
    with sqlite3.connect(migrated) as connection:
        connection.execute(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY, "
            "config_cover_generator_auto_enabled BOOLEAN, "
            "config_cover_generator_default_preset TEXT)")
        connection.execute(
            "INSERT INTO settings VALUES (1, 1, 'meadow')")
    settings = cg.settings_from_app_db(str(migrated))
    assert settings.auto_enabled is True
    assert settings.default_preset == "meadow"


# ---------------------------------------------------------------------------
# The apply route: the server renders, the client does not
# ---------------------------------------------------------------------------

def _apply_generated(body):
    """Drive cover_picker_apply's generated branch and return what got staged."""
    from cps import cover_picker

    book = MagicMock()
    book.id = 42
    book.title = META.title
    book.has_cover = 0
    book.path = "Becky Chambers/The Long Way (42)"
    author = MagicMock()
    author.name = "Becky Chambers"
    book.authors = [author]
    series = MagicMock()
    series.name = META.series
    book.series = [series]
    book.series_index = META.series_index

    staged = {}

    def record_bytes(_book, raw, extension):
        staged["data"] = raw
        staged["extension"] = extension
        return MagicMock(), None

    # The route's auth decorators are @wraps-based, so the view underneath them
    # is reachable directly. Authorisation is not what these tests are about;
    # what gets written to disk is.
    view = cover_picker.cover_picker_apply
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__

    app = flask.Flask(__name__)
    with app.test_request_context(json=body, method="POST"):
        with patch.object(cover_picker, "_", side_effect=lambda text, **kw: text), \
             patch.object(cover_picker, "config", MagicMock(config_binariesdir="")), \
             patch.object(cover_picker, "_load_book", return_value=book), \
             patch.object(cover_picker, "_get_lock_state", return_value=False), \
             patch.object(cover_picker, "_apply_bytes", side_effect=record_bytes), \
             patch.object(cover_picker, "_apply_response",
                          side_effect=lambda staged_cover, message, book_: flask.jsonify({"ok": True})):
            response = view(42)
    return response, staged


def test_the_applied_cover_comes_from_the_server_not_the_request():
    """A generated apply carries design ids; any pixels in the body are ignored.

    This is the property that makes the feature safe to expose: without it, the
    apply route would be an "upload arbitrary bytes as this book's cover" path
    that skips the upload validation entirely.
    """
    forged = base64.b64encode(b"\x89PNG\r\n\x1a\n-forged-by-the-client").decode()
    response, staged = _apply_generated({
        "kind": "generated", "preset": "classic",
        "data_url": "data:image/png;base64," + forged,
        "image": forged,
    })

    assert response.json["ok"] is True
    assert staged["data"][:2] == b"\xff\xd8"       # the server's JPEG
    assert b"forged-by-the-client" not in staged["data"]
    expected = cg.render(META, cg.resolve_spec(preset="classic",
                                               width=cg.APPLY_WIDTH, height=cg.APPLY_HEIGHT)).data
    assert staged["data"] == expected


def test_applying_a_design_we_do_not_offer_is_a_client_error():
    response, staged = _apply_generated({"kind": "generated", "scheme": "chartreuse"})
    assert response.status_code == 400
    assert staged == {}


def test_the_designer_panel_is_hidden_when_nothing_can_render(monkeypatch):
    """The SPA shows the panel on `designer.available`; an installation that
    cannot render must not advertise a button that can only fail."""
    from cps import cover_picker

    monkeypatch.setattr(cover_picker.cover_generator, "calibre_debug_path", lambda *a, **k: "")
    monkeypatch.setattr(cover_picker.cover_generator, "_pil_available", lambda: False)
    with patch.object(cover_picker, "config", MagicMock(config_binariesdir="",
                                                        config_cover_generator_default_preset="classic")):
        assert cover_picker.designer_state()["available"] is False
