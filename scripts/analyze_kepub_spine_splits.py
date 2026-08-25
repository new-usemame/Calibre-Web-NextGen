#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure KEPUB TOC identity collapse before and after lexical spine splitting.

The input library is read-only. Each KEPUB is copied to a temporary directory
before ``split_multichapter_documents`` is called.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

from lxml import etree


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from cps.services.kepub_package_normalizer import (  # noqa: E402
    MAX_PACKAGE_DOCUMENT_BYTES,
    MAX_TOC_DOCUMENT_BYTES,
    _XML_PARSER,
    _contained_toc_target,
    _package_document_path,
    _read_bounded_member,
    _toc_documents,
)
from cps.services.kepub_spine_splitter import (  # noqa: E402
    split_multichapter_documents,
)


@dataclass(frozen=True)
class Metrics:
    entries: int
    distinct_documents: int
    collapsed_entries: int


def _all_navigation_references(document, kind):
    if kind == "NCX":
        return document.xpath("//*[local-name()='content'][@src]/@src")
    return document.xpath("//*[local-name()='a'][@href]/@href")


def measure(path):
    identities = []
    with zipfile.ZipFile(path) as archive:
        opf_path = _package_document_path(archive)
        opf_bytes = _read_bounded_member(
            archive, opf_path, MAX_PACKAGE_DOCUMENT_BYTES, "package document")
        seen_tocs = set()
        for toc_path, kind in _toc_documents(opf_path, opf_bytes):
            key = (toc_path, kind)
            if key in seen_tocs:
                continue
            seen_tocs.add(key)
            toc_bytes = _read_bounded_member(
                archive, toc_path, MAX_TOC_DOCUMENT_BYTES,
                "{} TOC document".format(kind))
            document = etree.fromstring(toc_bytes, parser=_XML_PARSER)
            for value in _all_navigation_references(document, kind):
                target = _contained_toc_target(toc_path, value)
                if target is None:
                    continue
                parts, resolved = target
                identities.append(toc_path if not parts.path else resolved)
    distinct = len(set(identities))
    return Metrics(len(identities), distinct, len(identities) - distinct)


def _kepubs(path):
    if path.is_file():
        return [path]
    return sorted(
        candidate for candidate in path.rglob("*")
        if candidate.is_file()
        and candidate.name.lower().endswith((".kepub", ".kepub.epub"))
    )


def _label(path, root):
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        value = path.name
    return value.replace("|", "\\|")


def _summary(rows, side):
    metrics = [
        row[side] for row in rows
        if isinstance(row[side], Metrics) and row[side].entries
    ]
    return Metrics(
        sum(metric.entries for metric in metrics),
        sum(metric.distinct_documents for metric in metrics),
        sum(metric.collapsed_entries for metric in metrics),
    ), sum(metric.collapsed_entries > 0 for metric in metrics), len(metrics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path, help="KEPUB file or library directory")
    args = parser.parse_args()
    library = args.library.expanduser().resolve()
    paths = _kepubs(library)
    if not paths:
        parser.error("no .kepub or .kepub.epub files found")

    rows = []
    with tempfile.TemporaryDirectory(prefix="cwng-kepub-spine-analysis-") as temporary:
        temporary_root = Path(temporary)
        for index, source in enumerate(paths):
            try:
                before = measure(source)
                copy_path = temporary_root / "{:04d}-{}".format(index, source.name)
                shutil.copy2(source, copy_path)
                result = split_multichapter_documents(copy_path)
                after = measure(copy_path)
                after_first = copy_path.read_bytes()
                second_result = split_multichapter_documents(copy_path)
                if second_result is not False or copy_path.read_bytes() != after_first:
                    raise ValueError(
                        "second split was not a byte-identical False no-op")
                rows.append({
                    "label": _label(source, library),
                    "before": before,
                    "after": after,
                    "result": result,
                })
            except Exception as error:  # report one bad book without hiding the other 40
                rows.append({
                    "label": _label(source, library),
                    "before": error,
                    "after": error,
                    "result": "error",
                })

    print("| KEPUB | before entries/docs/collapsed | after entries/docs/collapsed | split |")
    print("|---|---:|---:|:---:|")
    for row in rows:
        if isinstance(row["before"], Metrics):
            before = "{}/{}/{}".format(
                row["before"].entries,
                row["before"].distinct_documents,
                row["before"].collapsed_entries,
            )
            after = "{}/{}/{}".format(
                row["after"].entries,
                row["after"].distinct_documents,
                row["after"].collapsed_entries,
            )
        else:
            before = after = "ERROR: {}".format(row["before"])
        print("| {} | {} | {} | {} |".format(
            row["label"], before, after, row["result"]))

    before, before_affected, before_valid = _summary(rows, "before")
    after, after_affected, after_valid = _summary(rows, "after")
    print()
    print("Before: {}/{}/{} entries/docs/collapsed; {} of {} books affected.".format(
        before.entries, before.distinct_documents, before.collapsed_entries,
        before_affected, before_valid))
    print("After:  {}/{}/{} entries/docs/collapsed; {} of {} books affected.".format(
        after.entries, after.distinct_documents, after.collapsed_entries,
        after_affected, after_valid))
    return 1 if any(row["result"] in (None, "error") for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
