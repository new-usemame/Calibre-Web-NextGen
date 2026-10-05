# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #509 — KOReader "currently reading" never
surfaces in the web UI.

Reporter @barukh27 (2026-06-22): progress synced from KOReader is stored
in the database but "No webUI presence" — a book you are partway through
on KOReader looks identical to an unread book everywhere in the web UI.

Root cause (verified against source): ``ub.ReadBook.read_status`` is a
tri-state — UNREAD=0, FINISHED=1, IN_PROGRESS=2. The kosync PUT handler
writes all three correctly (``cps/progress_syncing/protocols/kosync.py``:
>=99% FINISHED, >0% IN_PROGRESS), but every browsing surface flattens the
tri-state to a boolean that is true only for FINISHED:

  - ``cps/web.py`` (JSON serializer + detail page): ``read_status == STATUS_FINISHED``
  - ``cps/templates/*`` grid/list cards: the cover_badges macro is called
    with ``is_read=(entry[2] == True)`` — and ``2 == True`` is ``False``.

So ``read_status == 2`` renders identically to ``0`` (unread). The data
model already knows the third state (the smart-shelf filter even exposes
"Currently Reading"), but the rendering throws it away.

These tests pin the fix: the in-progress state must survive to the
rendered UI as a distinct "Currently reading" badge, separate from the
"Read" badge, without breaking the existing read/unread toggle.
"""

import pathlib
import types

import pytest

try:
    import jinja2
except ImportError:  # pragma: no cover
    jinja2 = None


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "cps" / "templates"
WEB_PY = REPO_ROOT / "cps" / "web.py"
DETAIL_HTML = REPO_ROOT / "cps" / "templates" / "detail.html"
IMAGE_HTML = REPO_ROOT / "cps" / "templates" / "image.html"

# The four grid/list templates that render cover badges through the macro.
GRID_TEMPLATES = ["index.html", "shelf.html", "search.html", "author.html"]

# Mirror of cps/ub.py ReadBook constants.
STATUS_UNREAD = 0
STATUS_FINISHED = 1
STATUS_IN_PROGRESS = 2


# ---------------------------------------------------------------------------
# Behavioral: the cover_badges macro must render a DISTINCT in-progress badge
# for read_status == 2, the read badge for == 1, and neither for == 0.
# This is the user-visible output; it fails on main because the macro takes
# a boolean is_read and has no in-progress branch.
# ---------------------------------------------------------------------------


def _render_cover_badges(read_status: int) -> str:
    """Render the cover_badges macro in isolation with a given read_status.

    Calls the macro with the status as the second positional arg, which works
    against both the old signature (``cover_badges(book, is_read=false)``) and
    the new one (``cover_badges(book, read_status=0)``) so the test can run
    RED on main and GREEN on the branch.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    env.globals["_"] = lambda s: s
    env.globals["g"] = types.SimpleNamespace(
        book_shelves_map={}, favorite_book_ids=set()
    )
    env.globals["url_for"] = lambda *a, **k: "#"
    # image.html's book_cover/series macros reference these filters; Jinja
    # resolves filter names at COMPILE time when the module is imported, so
    # they must exist even though cover_badges itself doesn't use them.
    for _filt in ("get_cover_srcset", "get_series_srcset", "last_modified",
                  "cache_timestamp"):
        env.filters[_filt] = lambda value, *a, **k: ""
    tmpl = env.from_string(
        "{% import 'image.html' as image %}"
        "{{ image.cover_badges(book, status) }}"
    )
    return tmpl.render(book=types.SimpleNamespace(id=1), status=read_status)


@pytest.mark.unit
@pytest.mark.skipif(jinja2 is None, reason="jinja2 not installed")
class TestCoverBadgeMacroRendersInProgress:
    def test_in_progress_renders_distinct_badge(self):
        """read_status == IN_PROGRESS must render a 'currently reading'
        badge that is visually DISTINCT from the 'read' badge."""
        out = _render_cover_badges(STATUS_IN_PROGRESS)
        assert "cover-badge-in-progress" in out, (
            "An in-progress book (read_status==2) must render a distinct "
            "'.cover-badge-in-progress' badge. fork #509 @barukh27: KOReader "
            "progress is stored but never surfaced in the web UI. Got:\n" + out
        )
        assert "cover-badge-read" not in out, (
            "An in-progress book must NOT render the green 'Read' badge — "
            "that is the bug (2 == True is False, so it currently renders "
            "as unread, but flipping it to the read badge would be just as "
            "wrong). In-progress is its own state. Got:\n" + out
        )

    def test_finished_still_renders_read_badge(self):
        """No regression: a finished book keeps the existing read badge and
        does not pick up the in-progress badge."""
        out = _render_cover_badges(STATUS_FINISHED)
        assert "cover-badge-read" in out, (
            "A finished book (read_status==1) must still render the read "
            "badge. Got:\n" + out
        )
        assert "cover-badge-in-progress" not in out, (
            "A finished book must not render the in-progress badge. Got:\n" + out
        )

    def test_unread_renders_no_read_badges(self):
        """No regression: an unread book renders neither read nor
        in-progress badge."""
        out = _render_cover_badges(STATUS_UNREAD)
        assert "cover-badge-read" not in out, (
            "An unread book must render no read badge. Got:\n" + out
        )
        assert "cover-badge-in-progress" not in out, (
            "An unread book must render no in-progress badge. Got:\n" + out
        )


# ---------------------------------------------------------------------------
# Source-pin: the Python coercion sites must carry the RAW tri-state, not
# only the FINISHED boolean, so templates can render in-progress.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoercionCarriesRawStatus:
    def test_detail_read_status_derivation_preserves_in_progress(self):
        """The shared detail derivation must distinguish reading from unread/read."""
        from cps.helper import read_statuses_for_books, read_status_code
        user = types.SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False)
        states = read_statuses_for_books([(1, None), (2, 1), (3, 2)], False, user)
        assert states == {1: "unread", 2: "finished", 3: "in_progress"}
        assert read_status_code(states[3]) == 2

    def test_detail_template_branches_on_in_progress(self):
        """detail.html must render a 'currently reading' marker when the raw
        status is IN_PROGRESS, keyed on the raw int (not the read boolean)."""
        src = DETAIL_HTML.read_text(encoding="utf-8")
        assert "read_status_raw" in src, (
            "detail.html must consult the raw tri-state read status "
            "(read_status_raw) to show a 'currently reading' marker for "
            "in-progress books. fork #509 @barukh27."
        )
        assert "cover-badge-in-progress" in src or "currently-reading" in src, (
            "detail.html must render a distinct in-progress marker "
            "(.cover-badge-in-progress / .currently-reading) for a book the "
            "user is partway through. fork #509."
        )

    def test_detail_toggle_removes_currently_reading_pill(self):
        """When the user marks a book read/unread via the detail-page toggle,
        the JS handler must remove the sync-driven "Currently reading" pill —
        otherwise the page shows "Currently reading" next to "Mark As Unread"
        until a reload (Greptile #520 finding, fork #509)."""
        src = DETAIL_HTML.read_text(encoding="utf-8")
        # The toggle handler and the pill removal must both be present, and the
        # removal must target the pill's id.
        assert "#toggle-read-btn" in src, "detail.html toggle handler missing"
        assert '$("#currently-reading-badge").remove()' in src or \
               "$('#currently-reading-badge').remove()" in src, (
            "The #toggle-read-btn success handler must remove "
            "#currently-reading-badge so an in-progress book marked read "
            "doesn't show the stale pill until reload. fork #509 / Greptile #520."
        )

    def test_image_macro_takes_tristate_not_bool(self):
        """The cover_badges macro must accept the tri-state read_status, not
        a flattened is_read boolean, and emit the in-progress badge."""
        src = IMAGE_HTML.read_text(encoding="utf-8")
        assert "cover-badge-in-progress" in src, (
            "image.html cover_badges macro must emit a "
            "'.cover-badge-in-progress' badge for read_status == 2. fork #509."
        )


# ---------------------------------------------------------------------------
# Source-pin: the four grid/list call sites must pass the raw tri-state into
# the macro, not the flattened ``entry[2] == True`` boolean (2 == True → False
# silently drops every in-progress book to "unread").
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("template_name", GRID_TEMPLATES)
class TestGridCallSitesPassRawStatus:
    def test_call_site_does_not_flatten_with_eq_true(self, template_name):
        path = TEMPLATES_DIR / template_name
        if not path.exists():  # author/search may differ across versions
            pytest.skip(f"{template_name} not present")
        src = path.read_text(encoding="utf-8")
        if "cover_badges" not in src:
            pytest.skip(f"{template_name} does not call cover_badges")
        assert "is_read=(entry[2] == True)" not in src, (
            f"{template_name} flattens the tri-state read status with "
            f"'entry[2] == True', which silently drops in-progress (2 == True "
            f"is False) to 'unread'. Pass the raw status to cover_badges "
            f"instead. fork #509 @barukh27."
        )
