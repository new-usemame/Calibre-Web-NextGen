# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for fork #612 — navigating between shelves in the new UI
mixed both shelves' books: the previous shelf's grid stayed rendered and the
next shelf's books were appended after it.

Root cause: ``useShelf``/``useMagicShelfBooks`` used an unscoped
``placeholderData: (prev) => prev``, so on an id change (wouter reuses the
component for /shelf/A -> /shelf/B) react-query briefly served shelf A's rows
under shelf B's query key. The page accumulators keyed by the ROUTE id at
effect-run time, so they stamped shelf A's rows as "shelf B, seen" and then
appended shelf B's real rows behind them. Catalog/Table/AdvancedSearch already
guard this with ``isPlaceholderData`` — Shelf and MagicShelfView were the two
stragglers.

These pin all three layers of the fix so a refactor can't silently regress it:
placeholder data must not cross shelf ids, accumulators must skip placeholder
data, and paging must reset when the id changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
SHELF_TSX = FRONTEND / "pages" / "Shelf.tsx"
MAGIC_TSX = FRONTEND / "pages" / "MagicShelfView.tsx"
QUERIES_TS = FRONTEND / "lib" / "queries.ts"


@pytest.fixture(scope="module")
def shelf_src() -> str:
    return SHELF_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def magic_src() -> str:
    return MAGIC_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def queries_src() -> str:
    return QUERIES_TS.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    """Extract the source of `export function <name>(...)` up to the next export."""
    match = re.search(
        r"export function %s\b.*?(?=\nexport |\Z)" % re.escape(name), src, re.S
    )
    assert match, f"export function {name} not found"
    return match.group(0)


# --- layer 1: placeholder data must not cross shelf ids -----------------------


def test_use_shelf_placeholder_scoped_to_same_shelf(queries_src):
    """#612: unscoped `placeholderData: (prev) => prev` carried shelf A's rows
    under shelf B's key. The placeholder must check the previous query's id."""
    block = _function_block(queries_src, "useShelf")
    assert not re.search(r"placeholderData:\s*\(\s*prev\s*\)\s*=>\s*prev", block), (
        "useShelf still uses an unscoped placeholderData — previous shelf's rows "
        "leak across an id change"
    )
    assert "prevQuery" in block, "useShelf placeholderData must compare prevQuery's shelf id"


def test_use_magic_shelf_books_placeholder_scoped_to_same_shelf(queries_src):
    block = _function_block(queries_src, "useMagicShelfBooks")
    assert not re.search(r"placeholderData:\s*\(\s*prev\s*\)\s*=>\s*prev", block), (
        "useMagicShelfBooks still uses an unscoped placeholderData"
    )
    assert "prevQuery" in block


# --- layer 2: accumulators must never act on placeholder data -----------------


def test_shelf_accumulator_skips_placeholder_data(shelf_src):
    """Catalog-parity guard: the accumulation effect early-returns on
    isPlaceholderData so stale rows are never stamped as the new shelf's."""
    assert re.search(
        r"if\s*\(\s*!data\s*\|\|\s*isPlaceholderData\s*\)\s*return", shelf_src
    ), "Shelf.tsx accumulator lacks the isPlaceholderData early-return guard"
    assert re.search(r"\bisPlaceholderData\b[^=]*=\s*useShelf\(|useShelf\(", shelf_src)
    assert "isPlaceholderData" in re.search(
        r"const\s*\{[^}]*\}\s*=\s*useShelf\(", shelf_src
    ).group(0), "Shelf.tsx must destructure isPlaceholderData from useShelf"


def test_magic_shelf_accumulator_skips_placeholder_data(magic_src):
    assert re.search(
        r"if\s*\(\s*!data\s*\|\|\s*isPlaceholderData\s*\)\s*return", magic_src
    ), "MagicShelfView.tsx accumulator lacks the isPlaceholderData guard"
    assert "isPlaceholderData" in re.search(
        r"const\s*\{[^}]*\}\s*=\s*useMagicShelfBooks\(", magic_src
    ).group(0), "MagicShelfView.tsx must destructure isPlaceholderData"


# --- layer 3: paging must reset when the shelf id changes ---------------------


def _has_page_reset_on_id(src: str) -> bool:
    """An effect that calls setPage(1) with [id] as its dependency array."""
    return bool(
        re.search(
            r"useEffect\(\s*\(\)\s*=>\s*\{[^}]*setPage\(1\)[^}]*\}\s*,\s*\[\s*id\s*\]\s*\)",
            src,
            re.S,
        )
    )


def test_shelf_resets_page_on_id_change(shelf_src):
    """#612 follow-on: arriving on shelf B while paged to A's page N skipped
    shelf B's first pages entirely."""
    assert _has_page_reset_on_id(shelf_src), (
        "Shelf.tsx must reset page to 1 when the shelf id changes"
    )


def test_magic_shelf_resets_page_on_id_change(magic_src):
    assert _has_page_reset_on_id(magic_src), (
        "MagicShelfView.tsx must reset page to 1 when the shelf id changes"
    )
