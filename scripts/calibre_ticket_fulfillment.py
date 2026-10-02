#!/usr/bin/env python3
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run ACSM FileTypePlugin import hooks without creating a library row.

Executed by calibre-debug so the hooks use the same installed Calibre runtime
and opt-in configuration as ingest. Only a materialized EPUB/PDF leaves the
plugin temporary directory. The original ticket is never given to a hook.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile
from xml.etree import ElementTree

RESULT_PREFIX = "CWNG_FULFILLMENT_RESULT="


def validate_book(path):
    path = Path(path)
    extension = path.suffix.lower()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Fulfillment did not produce a nonempty book")
    if extension == ".epub":
        try:
            with zipfile.ZipFile(path) as archive:
                if (archive.getinfo("mimetype").file_size > 64
                        or archive.read("mimetype") != b"application/epub+zip"):
                    raise ValueError("Fulfillment returned an invalid EPUB media type")
                container = archive.getinfo("META-INF/container.xml")
                if container.file_size == 0 or container.file_size > 1024 * 1024:
                    raise ValueError("Fulfillment returned an invalid EPUB container")
                tree = ElementTree.fromstring(archive.read(container))
                rootfiles = tree.findall("{urn:oasis:names:tc:opendocument:xmlns:container}rootfiles/"
                                          "{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                packages = [archive.getinfo(node.get("full-path", "")) for node in rootfiles]
                if not packages or any(info.file_size == 0 for info in packages):
                    raise ValueError("Fulfillment returned an EPUB without a package document")
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
            raise ValueError("Fulfillment did not return an EPUB package") from error
    elif extension == ".pdf":
        with path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise ValueError("Fulfillment did not return a PDF document")
            stream.seek(max(0, path.stat().st_size - 1024))
            if b"%%EOF" not in stream.read():
                raise ValueError("Fulfillment returned an incomplete PDF document")
    else:
        raise ValueError("ACSM import hooks must return an EPUB or PDF, not the ticket")
    return extension.lstrip(".")


def fulfill_ticket(source, destination):
    # Imports remain local: ordinary Python can validate the persisted result,
    # while only calibre-debug initializes and executes the plugin registry.
    from calibre.db.adding import run_import_plugins, run_import_plugins_before_metadata

    source, destination = Path(source), Path(destination)
    if source.suffix.lower() != ".acsm":
        raise ValueError("Only ACSM tickets use this fulfillment path")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cwng-acsm-") as temp_dir:
        staged = Path(temp_dir) / "source.acsm"
        shutil.copy2(source, staged)
        with run_import_plugins_before_metadata(temp_dir):
            outputs = run_import_plugins([str(staged)])
            if len(outputs) != 1:
                raise ValueError("ACSM import hooks returned an invalid book list")
            output = Path(outputs[0])
            extension = validate_book(output)
            target = destination / ("fulfilled." + extension)
            temporary = destination / ("fulfilled." + extension + ".part")
            try:
                with output.open("rb") as incoming, temporary.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                validate_book(output)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return {"path": str(target), "format": extension}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(RESULT_PREFIX + json.dumps(fulfill_ticket(args.source, args.destination)))
