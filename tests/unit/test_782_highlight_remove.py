# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #782: in the new UI's epub reader you could
*create* a colored highlight but never *remove* one — tapping an existing
highlight did nothing.

Root cause (verified against this tree): both highlight paint sites
(`Reader.tsx`'s ``paintHighlight`` + the saved-highlights load loop) called
``rendition.annotations.highlight(cfiRange, {}, undefined, '', …)`` — the 3rd
arg (click callback) was ``undefined`` and the 4th (className) was ``''``, so
epub.js never fired anything on tap. The reader also discarded each saved
row's ``annotation_id``, and the create path ignored the id the POST returns,
so even with a callback there was no server id to DELETE.

The backend already had a working ``DELETE /annotations/<book_id>/<ann_id>``
(soft delete, idempotent) and a ``PATCH`` that supports ``highlight_color``;
the fix is entirely client-side. ``api.ts`` gained ``apiDelete`` (mirrors
``apiPost``'s CSRF handling; tolerates empty/204) and ``apiPatch`` (for the
recolor path).

These are source-pins (the SPA is TypeScript, so like ``test_750``'s
``feed.xml`` pin we read the source as text and assert on the call shape):
1. ``api.ts`` exports ``apiDelete`` (method ``DELETE``) + ``apiPatch``.
2. ``Reader.tsx`` no longer paints highlights with an undefined click callback,
   and wires a real handler (``openHighlightEditor``) + ``cwng-hl`` className.
3. ``Reader.tsx`` captures the annotation id at both paint sites (load + create).
4. The remove flow calls ``apiDelete`` on the annotation path and un-paints via
   ``rendition.annotations.remove(…, 'highlight')``.
5. ``spa_strings.py`` anchors the new SPA-only msgid (``Remove highlight``).

Every assertion below fails on ``main`` (no apiDelete, undefined callback, no
id capture, no remove call, no msgid) and passes on the branch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
API_TS = REPO_ROOT / "frontend" / "src" / "lib" / "api.ts"
READER_TSX = REPO_ROOT / "frontend" / "src" / "pages" / "Reader.tsx"
SPA_STRINGS = REPO_ROOT / "cps" / "spa_strings.py"


@pytest.fixture(scope="module")
def api_src() -> str:
    return API_TS.read_text()


@pytest.fixture(scope="module")
def reader_src() -> str:
    return READER_TSX.read_text()


# ---------------------------------------------------------------------------
# 1. api.ts gained apiDelete (method DELETE) + apiPatch (recolor)
# ---------------------------------------------------------------------------

def test_api_ts_defines_delete_helper(api_src):
    assert "export async function apiDelete" in api_src, (
        "api.ts must export an apiDelete helper for the remove flow"
    )
    assert "method: 'DELETE'" in api_src, "apiDelete must issue a DELETE request"
    # DELETE responses are frequently 204/empty — the helper tolerates that
    # rather than throwing on res.json() (mirrors apiPost's 204 branch).
    assert "204" in api_src and "res.text()" in api_src, (
        "apiDelete must tolerate an empty/204 body"
    )


def test_api_ts_defines_patch_helper(api_src):
    # Recolor rides the backend's PATCH /annotations/<book>/<ann> route.
    assert "export async function apiPatch" in api_src, (
        "api.ts must export an apiPatch helper for the recolor flow"
    )
    assert "method: 'PATCH'" in api_src


# ---------------------------------------------------------------------------
# 2. Reader wires a real click callback (not undefined) + className
# ---------------------------------------------------------------------------

def test_reader_does_not_paint_with_undefined_callback(reader_src):
    # Pre-fix both paint sites called `.highlight(cfiRange, {}, undefined, '', …)`.
    # That exact shape must be gone — a tap must now fire a callback.
    assert ", {}, undefined, ''" not in reader_src, (
        "annotations.highlight must not be called with an undefined click callback"
    )


def test_reader_passes_click_callback_and_classname(reader_src):
    assert "openHighlightEditor" in reader_src, (
        "a click handler (openHighlightEditor) must open the edit/remove popover"
    )
    assert "() => openHighlightEditor(cfiRange, annotationId, color)" in reader_src, (
        "the highlight click callback must be a real function, not undefined"
    )
    assert "'cwng-hl'" in reader_src, (
        "highlights must carry a className so epub.js wires the click callback"
    )


# ---------------------------------------------------------------------------
# 3. The annotation id is captured at both paint sites
# ---------------------------------------------------------------------------

def test_reader_captures_id_on_load(reader_src):
    # Saved highlights come from /annotations/<id>/data.json; each row's
    # annotation_id must reach paintHighlight so a tap can target the row.
    #
    # Matches the ARGUMENT, not the whole call. These pins guard an invariant —
    # "the id reaches the paint site" — and pinning the exact literal made them
    # fail on any signature change instead: #1508 appended a `hasNote` argument
    # and broke both, with the id still flowing correctly. A pin that reds on
    # every legitimate refactor teaches people to update it without reading it,
    # which is how the regression it exists to catch eventually walks through.
    assert re.search(
        r"paintHighlight\(\s*a\.cfi_range\s*,[^)]*\ba\.annotation_id\b", reader_src
    ), "the load loop must pass the saved annotation_id into paintHighlight"


def test_reader_captures_id_on_create(reader_src):
    # The create POST returns the new annotation row (incl. its id); capture it
    # so a just-created highlight is immediately removable.
    #
    # Two-step so an indirection through a local (#1508's `newId`) still passes,
    # while dropping the id entirely still fails: find whatever the response id
    # is bound to, then require THAT name at the paint site.
    bound = re.search(r"(?:const|let|var)\s+(\w+)\s*=\s*created\?\.annotation_id", reader_src)
    assert bound, "createHighlight must capture the annotation_id the POST returned"
    assert re.search(
        rf"paintHighlight\([^)]*\b{re.escape(bound.group(1))}\b", reader_src
    ), "the captured annotation_id must be the one painted with"


# ---------------------------------------------------------------------------
# 4. The remove flow DELETEs + un-paints; recolor PATCHes
# ---------------------------------------------------------------------------

def test_reader_remove_flow_calls_delete_and_unpaints(reader_src):
    assert "apiDelete(" in reader_src, "remove flow must call apiDelete"
    # The annotation id is interpolated into the per-annotation DELETE path.
    assert "${hl.id}" in reader_src, "the remove path must target the annotation id"
    assert "remove(hl.cfiRange, 'highlight')" in reader_src, (
        "remove flow must un-paint via rendition.annotations.remove(…, 'highlight')"
    )


def test_reader_recolor_flow_calls_patch(reader_src):
    assert "apiPatch(" in reader_src, "recolor flow must call apiPatch"
    assert "highlight_color: color" in reader_src, (
        "recolor must send highlight_color to the PATCH endpoint"
    )


# ---------------------------------------------------------------------------
# 5. spa_strings.py anchors the new SPA-only msgid
# ---------------------------------------------------------------------------

def test_spa_strings_anchors_remove_highlight():
    src = SPA_STRINGS.read_text()
    assert '_("Remove highlight")' in src, (
        "the new 'Remove highlight' msgid must be anchored in spa_strings.py "
        "so pybabel re-extraction doesn't drop it"
    )
