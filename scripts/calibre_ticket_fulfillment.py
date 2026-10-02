#!/usr/bin/env python3
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run ACSM FileTypePlugin import hooks without creating a library row.

Executed by calibre-debug so the hooks use the same installed Calibre runtime
and opt-in configuration as ingest. Only a materialized EPUB/PDF leaves the
plugin temporary directory. The original ticket is never given to a hook.
"""
import argparse
import hashlib
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



def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def persist_result(destination, source_digest, book):
    """Publish a ticket-bound, byte-validated result before acknowledging it."""
    destination, book = Path(destination), Path(book)
    if book.resolve().parent != destination.resolve():
        raise ValueError('Fulfillment result escaped its recovery directory')
    result = {'source_sha256': source_digest, 'file': book.name,
              'format': validate_book(book), 'book_sha256': file_digest(book)}
    temporary = destination / 'result.json.part'
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(result, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination / 'result.json')
    _sync_directory(destination)
    return {'path': str(book), 'format': result['format']}


def load_result(destination, source_digest):
    """An interrupted or damaged entry is never permission to spend it again."""
    destination = Path(destination)
    if not destination.exists():
        return None
    try:
        if destination.is_symlink():
            raise ValueError('Fulfillment recovery directory is a symlink')
        manifest = destination / 'result.json'
        if manifest.stat().st_size > 8192:
            raise ValueError('Invalid fulfillment recovery manifest')
        result = json.loads(manifest.read_text(encoding='utf-8'))
        name = result['file']
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Invalid recovered book filename')
        book = destination / name
        if (book.is_symlink() or book.resolve().parent != destination.resolve()
                or result['source_sha256'] != source_digest
                or validate_book(book) != result['format']
                or file_digest(book) != result['book_sha256']):
            raise ValueError('Fulfillment recovery identity or book bytes changed')
        return {'path': str(book), 'format': result['format']}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('Incomplete or damaged ACSM recovery entry: ' + str(destination)) from error


def fulfill_ticket(source, destination):
    from calibre.db.adding import run_import_plugins, run_import_plugins_before_metadata

    source, destination = Path(source), Path(destination)
    if source.suffix.lower() != '.acsm':
        raise ValueError('Only ACSM tickets use this fulfillment path')
    source_digest = file_digest(source)
    previous = load_result(destination, source_digest)
    if previous:
        return previous
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Reserve before executing any potentially consumptive hook.
    intent = destination / 'intent.json'
    with intent.open('w', encoding='utf-8') as stream:
        json.dump({'source_sha256': source_digest}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(destination)
    _sync_directory(destination.parent)
    try:
        with tempfile.TemporaryDirectory(prefix='cwng-acsm-') as temp_dir:
            staged = Path(temp_dir) / source.name
            shutil.copy2(source, staged)
            with run_import_plugins_before_metadata(temp_dir):
                outputs = run_import_plugins([str(staged)])
                if len(outputs) != 1:
                    raise ValueError('ACSM import hooks returned an invalid book list')
                output = Path(outputs[0])
                extension = validate_book(output)
                target = destination / (source.stem + '.' + extension)
                temporary = destination / (target.name + '.part')
                with output.open('rb') as incoming, temporary.open('wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.replace(temporary, target)
                if file_digest(source) != source_digest:
                    raise ValueError('Original ticket changed during fulfillment')
                return persist_result(destination, source_digest, target)
    except Exception:
        # A controlled plugin rejection with no materialized/partial book can
        # be tried again after configuration is repaired. An interrupted copy
        # or publication retains its reservation and bytes for manual recovery.
        if set(path.name for path in destination.iterdir()) == {'intent.json'}:
            intent.unlink()
            destination.rmdir()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    print(RESULT_PREFIX + json.dumps(fulfill_ticket(args.source, args.destination)))
