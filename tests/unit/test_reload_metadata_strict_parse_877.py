# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioural tests for fork issue #877 (@yodatak): reloading embedded
metadata from disk must apply only what the file actually carries.

Background: ``uploader.process`` exists for the UPLOAD path, where
guessing is correct — a brand-new book with no embedded metadata is best
described by its filename, and the user sees the guess in the edit form
before it is saved. RELOAD has the opposite contract: it refreshes an
EXISTING book's curated metadata, so a guess silently overwrites data a
human entered, and a title change additionally renames the book's folder
on disk (``helper.update_dir_structure``).

Every parser fabricates a title the same way — ``title = <parsed> or
original_file_name`` (``pdf`` uploader.py:196-197/211, ``epub`` epub.py:126,
``comic`` comic.py:183/203, ``audio`` audio.py:131) — and ``process`` then
adds a second layer of guessing of its own. Neither layer marks the result
as a guess, so the caller cannot distinguish "the file says this" from
"we made this up".

``strict=True`` is the reload contract: parse errors propagate, and a
field the file does not carry comes back empty instead of guessed.

These are behavioural tests against real files and the real parser — the
pre-existing suite for this route (test_reload_metadata_from_disk_218.py)
only greps the source, which is why this regression passed CI.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

pypdf = pytest.importorskip("pypdf", reason="pypdf is required to build real PDF fixtures")


# The Calibre data-file stem for a book. Calibre names data files
# "<Title> - <Author>", so a fabricated title is not merely wrong, it is
# conspicuously wrong: "Foundation" becomes "Foundation - Isaac Asimov".
CALIBRE_DATA_STEM = "Foundation - Isaac Asimov"
CURATED_TITLE = "Foundation"


@pytest.fixture(scope="module")
def uploader():
    """Import cps.uploader inside an app context (it calls gettext at
    import-adjacent call time)."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("cps.uploader")


@pytest.fixture(scope="module")
def app_ctx():
    from flask import Flask
    from flask_babel import Babel

    app = Flask(__name__)
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(REPO_ROOT / "cps" / "translations")
    Babel(app)
    with app.test_request_context():
        yield app


@pytest.fixture(scope="module")
def pdf_without_title(tmp_path_factory) -> str:
    """A real PDF carrying no /Title — the common case for scanned or
    exported PDFs, and the reporter's format."""
    from pypdf import PdfWriter

    path = tmp_path_factory.mktemp("pdf") / "notitle.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


@pytest.fixture(scope="module")
def pdf_with_title(tmp_path_factory) -> str:
    """A real PDF that does carry /Title and /Author."""
    from pypdf import PdfWriter

    path = tmp_path_factory.mktemp("pdf") / "withtitle.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "The Real Embedded Title", "/Author": "Ursula K. Le Guin"})
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


@pytest.fixture(scope="module")
def corrupt_pdf(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("pdf") / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a parseable pdf at all\n")
    return str(path)


# ---------------------------------------------------------------------------
# The reported defect: a title-less file stomps the curated title.
# ---------------------------------------------------------------------------


def test_strict_parse_does_not_fabricate_a_title_from_the_filename(
    uploader, app_ctx, pdf_without_title
):
    """RED on main: returns 'Foundation - Isaac Asimov'.

    The file carries no title, so reload must be told that — not handed
    the filename dressed up as embedded metadata.
    """
    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    assert meta.title == "", (
        f"strict parse fabricated a title from the filename: {meta.title!r}. "
        "The reload route applies meta.title whenever it differs from the "
        "book's title, so this silently overwrites the curated title and "
        "renames the book's folder on disk."
    )


def test_reload_leaves_the_curated_title_alone_when_the_file_has_none(
    uploader, app_ctx, pdf_without_title
):
    """The user-visible outcome, expressed as the route's own guard
    (editbooks.py:2418) — RED on main, where the guard fires and the
    curated title is replaced by the filename stem."""
    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    would_overwrite = bool(meta.title and meta.title != CURATED_TITLE)
    assert not would_overwrite, (
        f"reload would overwrite the curated title {CURATED_TITLE!r} with "
        f"{meta.title!r}"
    )


def test_strict_parse_still_returns_a_real_embedded_title(
    uploader, app_ctx, pdf_with_title
):
    """The fix must not be 'always return empty' — a real embedded title
    must still reach the book. This is what makes the test above
    meaningful rather than vacuous."""
    meta = uploader.process(
        pdf_with_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    assert meta.title == "The Real Embedded Title"


# ---------------------------------------------------------------------------
# The locale-dependent defect: a translated 'Unknown' defeats the route's
# missing-author sentinel, so non-English users get their authors stomped.
# ---------------------------------------------------------------------------


def test_strict_parse_missing_author_is_empty_not_a_translated_guess(
    uploader, app_ctx, pdf_without_title, monkeypatch
):
    """RED on main under any non-English locale.

    ``process`` replaced a missing author with ``_('Unknown')``, which is
    translated ('Inconnu' in French, 'Unbekannt' in German). The reload
    route guards with ``meta.author != 'Unknown'`` — a literal English
    string — so the guard passes for every non-English user and
    ``handle_author_on_edit`` overwrites their real authors.

    Monkeypatching gettext rather than relying on compiled .mo catalogs
    keeps this deterministic in CI, where the catalogs may not be built.
    """
    monkeypatch.setattr(uploader, "_", lambda s: "Inconnu" if s == "Unknown" else s)

    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    assert meta.author == "", (
        f"strict parse returned a guessed author {meta.author!r}. A "
        "translated guess defeats the route's `!= 'Unknown'` sentinel and "
        "overwrites the book's real authors for non-English users."
    )


def test_strict_parse_missing_author_never_reaches_the_route_guard(
    uploader, app_ctx, pdf_without_title, monkeypatch
):
    """The sentinel comparison itself is the landmine. Once a missing
    author comes back empty, the route needs no magic-string compare at
    all — an empty author is falsy and skipped in every locale."""
    monkeypatch.setattr(uploader, "_", lambda s: "Unbekannt" if s == "Unknown" else s)

    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    author_value = (meta.author or "").strip()
    assert not author_value, "a missing author must be falsy so the route skips it"


def test_strict_parse_returns_a_real_embedded_author(uploader, app_ctx, pdf_with_title):
    """Guards against over-fixing the author path into a constant empty.

    Also covers a second defect this test found: pdf_meta seeded `author`
    with the 'Unknown' sentinel in its no-XMP branch, while the
    DocumentInfo fallback only fills fields that are empty. The PDF's own
    /Author was therefore unreadable for any PDF without an XMP block —
    most of them — so reload could never update authors from a PDF at all.
    """
    meta = uploader.process(
        pdf_with_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    assert "Le Guin" in meta.author


def test_pdf_without_an_author_still_reports_none_rather_than_guessing(
    uploader, app_ctx, pdf_without_title
):
    """The counterpart to the test above: now that DocumentInfo authors
    are actually read, a PDF that carries no author must still come back
    empty so reload leaves the curated authors alone."""
    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True, strict=True,
    )
    assert meta.author == ""


def test_upload_of_a_pdf_now_reads_its_embedded_author(uploader, app_ctx, pdf_with_title):
    """The pdf_meta fix reaches the upload path too, which is the point:
    uploading a PDF used to label it 'Unknown' even when the file said
    who wrote it."""
    meta = uploader.process(
        pdf_with_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None, no_cover=True,
    )
    assert "Le Guin" in meta.author


# ---------------------------------------------------------------------------
# Parse failures must surface, not be silently swallowed into a guess.
# ---------------------------------------------------------------------------


def test_strict_parse_propagates_parse_errors(uploader, app_ctx, corrupt_pdf):
    """RED on main: process swallowed the exception and returned
    default_meta, so the route's error handler (editbooks.py:2398-2403)
    became unreachable and an unparseable file reported success while
    stomping the title.

    This payload has no cross-reference table, so pypdf raises while
    constructing PdfReader — before pdf_meta's own try/except around the
    DocumentInfo read, which swallows metadata-read errors even under
    strict. Propagation therefore covers files that fail to open, not
    every conceivable parse failure. A file that opens but has an
    unreadable DocumentInfo still returns empty fields rather than
    guesses, so the invariant that matters for #877 — never overwrite
    curated data with a guess — holds either way; only the error report
    degrades to a "0 fields updated" success. If a future pypdf defers
    this failure to metadata access, process() returns instead of
    raising and this test fails loudly here rather than passing for the
    wrong reason.
    """
    with pytest.raises(Exception) as excinfo:
        uploader.process(
            corrupt_pdf, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
            no_cover=True, strict=True,
        )
    # Guard against a false green: before the fix this raised TypeError for
    # the unknown `strict` kwarg, which is not the parse error we mean.
    assert not (
        isinstance(excinfo.value, TypeError) and "strict" in str(excinfo.value)
    ), f"raised for the wrong reason: {excinfo.value!r}"
    # Pin that the error is the PDF parse itself. Without this the test
    # would accept any exception — a missing fixture or a refactor typo
    # would keep it green while proving nothing about propagation.
    assert type(excinfo.value).__module__.split(".")[0] == "pypdf", (
        f"expected the pypdf parse error to propagate, got {excinfo.value!r}"
    )


# ---------------------------------------------------------------------------
# The upload contract must not change. Guessing is correct there.
# ---------------------------------------------------------------------------


def test_upload_path_still_guesses_the_title_from_the_filename(
    uploader, app_ctx, pdf_without_title
):
    """Non-strict is the upload contract: a new book with no embedded
    title is best described by its filename, and the user reviews the
    guess in the edit form before saving. This must keep working."""
    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True,
    )
    assert meta.title == CALIBRE_DATA_STEM


def test_upload_path_still_swallows_parse_errors(uploader, app_ctx, corrupt_pdf):
    """Non-strict must still degrade to default_meta so an unparseable
    upload is importable rather than rejected."""
    meta = uploader.process(
        corrupt_pdf, CALIBRE_DATA_STEM, ".pdf", rar_executable=None, no_cover=True,
    )
    assert meta.title == CALIBRE_DATA_STEM


def test_upload_path_still_labels_a_missing_author_unknown(
    uploader, app_ctx, pdf_without_title, monkeypatch
):
    """The upload form shows the user a translated 'Unknown' placeholder.
    Strict mode must not take that away from the upload path."""
    monkeypatch.setattr(uploader, "_", lambda s: "Inconnu" if s == "Unknown" else s)
    meta = uploader.process(
        pdf_without_title, CALIBRE_DATA_STEM, ".pdf", rar_executable=None,
        no_cover=True,
    )
    assert meta.author == "Inconnu"


def test_strict_defaults_to_off_so_existing_callers_are_unchanged(uploader):
    """Signature pin: strict must be opt-in keyword with a False default,
    so the upload path keeps its behaviour without being touched."""
    import inspect

    sig = inspect.signature(uploader.process)
    assert "strict" in sig.parameters, "process() must expose a strict parameter"
    assert sig.parameters["strict"].default is False
