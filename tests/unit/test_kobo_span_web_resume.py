# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo's newer place opens the web reader exactly, through the KEPUB.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md §18 and its
refinements. OBSERVED on the household (book 498): the Clara's newer place,
KoboSpan ``kobo.370.4``, reached a checked Browser only as an offer, because
``kobo_resume._resolve`` looks the span up in the library EPUB and kepubify
puts ``kobo.N.M`` spans only in the KEPUB. Read as a spec:

* A KoboSpan that the library EPUB lacks resolves through the library KEPUB
  (``kepub_alignment.span_to_xpointer`` then ``koreader_xpointer.xpointer_to_cfi``)
  to the CFI of the same word, and the reader opens there (``automatic``).
* Only when the reporting Kobo's recorded download proof
  (``kobo_device_book_download.kepub_file_identity``) equals the library
  KEPUB's current identity. No proof, a different one, a KEPUB replaced since
  the download, a missing KEPUB, or a span the KEPUB does not hold: the place
  stays the percentage offer, as today.
* A span the library EPUB itself carries resolves as today.
* The bridge is bounded by the existing resume worker and timeout, and never
  makes a KEPUB.

Every case drives the real ``GET /api/v1/books/<id>/bookmark`` with the gates
on: a checked Browser whose own exact place is older, and a checked Kobo
reporting a newer place through the real PUT /state handler.
"""

import logging
import os
import shutil
import sqlite3
import threading
import time

import pytest

from cps import db, ub
from tests.unit.kobo_state_world import read_column_class  # noqa: F401  (fixture)
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    KEPUB_FIXTURE, after_now, span_rows, sw,
)
from tests.unit.test_share_place_web_gates import (  # noqa: F401  (fixtures)
    BOOK, browser, cfi_of, file_backed, resume, rows, save, w,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_on(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.fixture
def library(w, tmp_path):
    """The library also lists the book's KEPUB (it is on disk in ``sw``)."""
    data = w.session.query(db.Data).filter_by(book=BOOK, format="KEPUB").one()
    metadata = sqlite3.connect(tmp_path / "metadata.db")
    metadata.execute("INSERT INTO data VALUES (?, 'KEPUB', ?)", (BOOK, data.name))
    metadata.commit()
    metadata.close()
    assert not _in_file(w.epub, b"kobo.")  # the library EPUB has no Kobo spans
    return w


def _in_file(path, needle):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        return any(needle in archive.read(name) for name in archive.namelist()
                   if name.endswith((".xhtml", ".html", ".htm")))


def _clara(w, *, proof):
    """A checked Kobo; ``proof``: "proven" (it completed a download of the
    current library KEPUB), "none" (no download row), "mismatch" (its recorded
    proof names another file)."""
    from cps.services.kobo_post_download_restore import record_download
    kobo = w.kobo_device("Kobo Clara", checked=True)
    if proof in ("proven", "mismatch"):
        record_download(device_id=kobo.id, book_id=BOOK, book_format="kepub",
                        log=logging.getLogger("test"))
        w.session.commit()
        row = w.session.query(ub.KoboDeviceBookDownload).filter_by(
            device_id=kobo.id, book_id=BOOK).one()
        assert row.kepub_file_identity  # the proof record_download froze
        if proof == "mismatch":
            row.kepub_file_identity = "0:0:1:1"
            w.session.commit()
    return kobo


def _newer_kobo_place(w, rows, *, proof, span=None, before_put=None):
    """The Browser saved an exact place at 20%; then the Clara reports a newer
    place at 60%: ``rows['late']``'s word, as a KEPUB span."""
    browser(w, checked=True)
    save(w, 20, cfi_of(w, rows["early"]))
    kobo = _clara(w, proof=proof)
    if before_put is not None:
        before_put()
    late = rows["late"]
    w.put(kobo, late["source"], span or late["span"], 60, after_now(2))
    return kobo


def _offered(w):
    served = resume(w)["resume"]
    assert served is not None, "a newer shared place is always at least offered"
    assert served["percentage"] == pytest.approx(60)
    assert served["mode"] == "offer"
    assert "cfi" not in served
    return served


# == (a) A KEPUB-only span with a matching proof opens exactly ================

def test_a_kepub_only_span_from_a_proven_kobo_opens_the_browser_exactly(library, rows):
    w = library
    _newer_kobo_place(w, rows, proof="proven")

    served = resume(w)["resume"]

    assert served is not None
    assert served["mode"] == "automatic"
    assert served["percentage"] == pytest.approx(60)
    assert served["cfi"] == cfi_of(w, rows["late"])
    assert served.get("epub_sha256")


# == (b) No proof, or a proof of another file: the offer ======================

@pytest.mark.parametrize("proof", ["none", "mismatch"])
def test_without_a_matching_download_proof_the_kobo_place_is_offered(library, rows, proof):
    _newer_kobo_place(library, rows, proof=proof)

    _offered(library)


# == (c) Control: a span the library EPUB carries resolves as today ===========

def test_control_a_span_in_the_library_epub_still_opens_exactly_without_proof(library, rows):
    """A library EPUB that carries Kobo spans (here: the kepubified file) is
    looked up directly, with no download proof involved."""
    from cps.services import kobo_position
    w = library
    shutil.copyfile(KEPUB_FIXTURE, w.epub)
    late = rows["late"]
    expected = kobo_position.compute_cfi_point(w.epub, late["source"], "KoboSpan", late["span"])
    assert expected
    browser(w, checked=True)
    save(w, 20, kobo_position.compute_cfi_point(
        w.epub, rows["early"]["source"], "KoboSpan", rows["early"]["span"]))
    kobo = _clara(w, proof="none")
    w.put(kobo, late["source"], late["span"], 60, after_now(2))

    served = resume(w)["resume"]

    assert served is not None and served["mode"] == "automatic"
    assert served["cfi"] == expected


# == (d) An alignment miss or a missing KEPUB: the offer =====================

def test_a_span_the_kepub_does_not_hold_is_offered(library, rows):
    _newer_kobo_place(library, rows, proof="proven", span="kobo.99999.1")

    _offered(library)


def test_a_missing_library_kepub_is_offered(library, rows):
    w = library
    _newer_kobo_place(w, rows, proof="proven")
    os.unlink(w.kepub)

    _offered(w)


# == (e) The KEPUB replaced after the download: the offer =====================

def test_a_kepub_replaced_after_the_kobos_download_is_offered(library, rows):
    """Same words, another file: the Kobo was proven to hold the old one, so
    its span may name a different spot in the new one."""
    w = library
    _newer_kobo_place(w, rows, proof="proven")
    replacement = w.kepub + ".new"
    shutil.copyfile(KEPUB_FIXTURE, replacement)
    os.replace(replacement, w.kepub)
    stat = os.stat(w.kepub)
    os.utime(w.kepub, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    _offered(w)


# == (f) Bounded: a stalled bridge answers quickly, and no KEPUB is made ======

def test_a_stalled_bridge_answers_quickly_with_the_offer_and_makes_no_kepub(
        library, rows, monkeypatch):
    from cps import helper
    from cps.services import kepub_alignment, kobo_resume, koreader_xpointer
    from cps.tasks import convert
    w = library
    _newer_kobo_place(w, rows, proof="proven")

    stall = 3.0
    assert kobo_resume.RESUME_TIMEOUT_SECONDS < stall / 10
    release = threading.Event()
    stalled = []

    def stalled_bridge(*_a, **_k):
        stalled.append(True)
        release.wait(stall)
        return None

    made = []
    monkeypatch.setattr(kepub_alignment, "span_to_xpointer", stalled_bridge)
    monkeypatch.setattr(koreader_xpointer, "xpointer_to_cfi", stalled_bridge)
    monkeypatch.setattr(helper, "convert_book_format",
                        lambda *a, **k: made.append(("convert_book_format", a)))
    monkeypatch.setattr(convert.TaskConvert, "_convert_kepubify",
                        lambda *a, **k: made.append(("kepubify", a)))
    kepub_before = os.stat(w.kepub).st_mtime_ns
    try:
        started = time.monotonic()
        served = _offered(w)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert served["mode"] == "offer"
    assert elapsed < stall / 3, elapsed
    assert made == []
    assert os.stat(w.kepub).st_mtime_ns == kepub_before
