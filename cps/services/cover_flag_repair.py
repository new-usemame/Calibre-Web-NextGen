# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Find books whose ``cover.jpg`` is on disk but whose ``has_cover`` flag is 0.

A cover is only served when ``books.has_cover`` is 1, so a library rebuilt
from files, or one whose flags were reset, can hold a perfectly good
``cover.jpg`` that the app never shows (issue #2678).

The flag alone cannot say *why* it is 0. A cover removed in the editor also
leaves ``has_cover = 0`` and its file behind, and nothing records that choice,
so the scan only proposes candidates and the admin confirms each one. Nothing
here runs on its own.

The books directory is resolved by the caller (``config.get_book_path()``), so
a split library (``metadata.db`` in one place, book folders in another) is
looked up where covers are actually served from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Sequence

COVER_FILENAME = "cover.jpg"


@dataclass(frozen=True)
class CoverFlagCandidate:
    book_id: int
    title: str


@dataclass(frozen=True)
class CoverFlagRepairResult:
    fixed: tuple[int, ...]
    skipped: int


def _has_usable_cover(books_root: str, book_path: str) -> bool:
    cover = os.path.join(books_root, book_path, COVER_FILENAME)
    try:
        return os.path.isfile(cover) and os.path.getsize(cover) > 0
    except OSError:
        return False


def find_candidates(books, books_root: str) -> list[CoverFlagCandidate]:
    """Books with ``has_cover = 0`` and a non-empty ``cover.jpg`` in their folder.

    ``books`` is an iterable of ``(id, title, path, has_cover)`` rows.
    """
    return [
        CoverFlagCandidate(book_id=book_id, title=title)
        for book_id, title, path, has_cover in books
        if not has_cover and path and _has_usable_cover(books_root, path)
    ]


def restore_flags(
    books_by_id: dict,
    selected_ids: Sequence[int],
    books_root: str,
) -> CoverFlagRepairResult:
    """Set ``has_cover = 1`` on each selected book that still qualifies.

    The selection comes from a preview that may be minutes old, so every book
    is checked again against the live row and the files: one that gained a
    cover in the meantime, was deleted, or lost its file is skipped, not
    written. ``books_by_id`` maps id to a Books-like object; the caller owns
    the commit.
    """
    fixed: list[int] = []
    skipped = 0
    for book_id in dict.fromkeys(selected_ids):
        book = books_by_id.get(book_id)
        if (
            book is None
            or book.has_cover
            or not book.path
            or not _has_usable_cover(books_root, book.path)
        ):
            skipped += 1
            continue
        book.has_cover = 1
        fixed.append(book_id)
    return CoverFlagRepairResult(fixed=tuple(fixed), skipped=skipped)


def book_rows(session, books_model) -> Iterable[tuple]:
    """``(id, title, path, has_cover)`` for every book not flagged as having a cover."""
    return (
        session.query(
            books_model.id, books_model.title, books_model.path, books_model.has_cover
        )
        .filter((books_model.has_cover == 0) | (books_model.has_cover.is_(None)))
        .order_by(books_model.sort)
        .all()
    )
