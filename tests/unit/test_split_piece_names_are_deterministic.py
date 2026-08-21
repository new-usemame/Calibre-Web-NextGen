# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Splitting the same package twice must produce the same member names.

This is load-bearing for two separate decisions and had no test.

1. IDEMPOTENCY. `split_multichapter_documents` returns False and leaves the
   archive byte-identical on a second run. That only holds if the names it would
   choose are stable.

2. F-bbd10e. The guard in `upload_book_formats` / `TaskConvert` withholds the
   split from a book that already has annotations, because renaming spine
   documents strands them. Determinism is what decides whether that guard is
   right in every case: for a book BORN split, whose highlights are anchored to
   `X-split-2.xhtml`, re-splitting a replacement upload would reproduce that name
   and PRESERVE the anchor, while withholding the split removes the file the
   anchor names. The guard is still correct for every book that exists today —
   nothing has been born split yet — but the finding records the inversion, and
   this test is the evidence it rests on.

If piece naming ever becomes non-deterministic (a hash of a timestamp, a uuid, a
set iteration order), both of those arguments collapse silently. This fails first.
"""
from __future__ import annotations

import zipfile

import pytest

pytestmark = pytest.mark.unit


def _names(path):
    with zipfile.ZipFile(path) as archive:
        return sorted(archive.namelist())


def _split_fresh(source_bytes, tmp_path, tag):
    from cps.services.kepub_spine_splitter import split_multichapter_documents

    target = tmp_path / f"{tag}.kepub"
    target.write_bytes(source_bytes)
    assert split_multichapter_documents(target) is True, "fixture did not split"
    return _names(target)


def test_two_independent_splits_choose_the_same_member_names(tmp_path):
    from tests.unit.test_1657_spine_splitter import _book

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source = _book(src_dir).read_bytes()
    first = _split_fresh(source, tmp_path, "one")
    second = _split_fresh(source, tmp_path, "two")

    pieces = [n for n in first if "-split-" in n]
    assert pieces, "the fixture produced no split pieces; this test would be vacuous"
    assert first == second, (
        "splitting the same package twice chose different member names, so a "
        "re-split cannot preserve an existing annotation's anchor and "
        "idempotency cannot hold either"
    )


def test_the_whole_archive_is_byte_identical_across_two_fresh_splits(tmp_path):
    """Stronger than names: the same input must produce the same package.

    Names alone could match while content differed, which would still move a
    KoboSpan and strand a highlight inside a chapter rather than between them.
    """
    from tests.unit.test_1657_spine_splitter import _book

    src_dir = tmp_path / "src2"
    src_dir.mkdir()
    source = _book(src_dir).read_bytes()

    from cps.services.kepub_spine_splitter import split_multichapter_documents

    outputs = []
    for tag in ("a", "b"):
        target = tmp_path / f"{tag}.kepub"
        target.write_bytes(source)
        assert split_multichapter_documents(target) is True
        with zipfile.ZipFile(target) as archive:
            outputs.append({n: archive.read(n) for n in sorted(archive.namelist())})

    assert outputs[0] == outputs[1], (
        "two fresh splits of one source produced different bytes"
    )


def test_a_second_split_of_an_already_split_package_changes_nothing(tmp_path):
    """The idempotency claim, stated as the user-visible contract."""
    from tests.unit.test_1657_spine_splitter import _book
    from cps.services.kepub_spine_splitter import split_multichapter_documents

    book = _book(tmp_path)
    assert split_multichapter_documents(book) is True
    after_first = book.read_bytes()
    assert split_multichapter_documents(book) is False
    assert book.read_bytes() == after_first
