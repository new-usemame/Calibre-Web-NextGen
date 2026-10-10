# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate completed ebook bytes and publish an attributable ingest input.

Network filenames never select a filesystem path. Publication retains the
original private source until the durable import receipt has been acknowledged.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from .contracts import DIRECT_FORMATS, MOBI_MEDIA_TYPE
from .mobi import MobiError, validate_mobi


class StagingError(ValueError):
    pass


NAME_PREFIX = 'cwng-acquisition-'
BOOK_EXTENSIONS = tuple(extension for _label, extension in DIRECT_FORMATS.values())
# Written by cwa-ingest-service when the processor ends terminally on an
# acquisition file (exit 3 retains the book; the safety timeout, 124, removes
# it). A name the watcher and the processor already skip. It carries no
# identity: the staging key in the file name binds it to a job, exactly as the
# publication sidecar does.
FAILURE_SUFFIX = '.cwa.failed.json'


def publication_paths(ingest_dir, staging_key):
    """Every path publication may have created for one staging identity.

    Supported extensions are considered because the caller reconciling a lost book
    can no longer read the offer that chose it, and because a safety timeout
    removes the book while leaving its sidecar behind.
    """
    if not isinstance(staging_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', staging_key):
        raise StagingError('invalid_staging_identity')
    directory = Path(ingest_dir)
    books = [directory / (NAME_PREFIX + staging_key + '.' + extension) for extension in BOOK_EXTENSIONS]
    return (books, [Path(str(book) + '.cwa.json') for book in books],
            [Path(str(book) + FAILURE_SUFFIX) for book in books])


def _present(paths):
    return next((path for path in paths if path.is_file() and not path.is_symlink()), None)


def publication_state(ingest_dir, staging_key):
    """What the ingest service has left for this publication, right now."""
    books, sidecars, markers = publication_paths(ingest_dir, staging_key)
    return _present(books), _present(sidecars), _present(markers)


def discard_publication(ingest_dir, staging_key):
    """Remove an abandoned publication so nothing re-imports or re-detects it.

    Only ever called after app.db has dropped the capability, so a processor
    still holding the file cannot acknowledge it afterwards.
    """
    removed = False
    for group in publication_paths(ingest_dir, staging_key):
        for path in group:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
                removed = True
    return removed


def _regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise StagingError('source_not_regular')
    return path


def digest(path):
    value = hashlib.sha256()
    with _regular(path).open('rb') as source:
        for chunk in iter(lambda: source.read(256 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def validate_book(path, media_type, *, max_bytes=100 * 1024 * 1024):
    """Reject HTML/error bodies and unsafe ZIP structure before ingest.

    This is package validation, not a claim that all EPUB/PDF content renders.
    Existing Calibre conversion/repair remains the authoritative processing path.
    """
    path = _regular(path)
    size = path.stat().st_size
    if not 0 < size <= max_bytes:
        raise StagingError('invalid_book_size')
    if media_type == MOBI_MEDIA_TYPE:
        try:
            return validate_mobi(path, max_bytes=max_bytes)
        except MobiError as error:
            raise StagingError(str(error)) from None
    if media_type == 'application/pdf':
        with path.open('rb') as stream:
            header = stream.read(16)
            stream.seek(max(0, size - 1024))
            tail = stream.read()
        if not re.match(rb'%PDF-[12]\.[0-9]', header) or b'%%EOF' not in tail:
            raise StagingError('invalid_pdf')
        return 'pdf'
    if media_type != 'application/epub+zip':
        raise StagingError('unsupported_book_format')
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 10000 or sum(x.file_size for x in entries) > 200 * 1024 * 1024:
                raise StagingError('epub_expansion_limit')
            names = set()
            for entry in entries:
                name = entry.filename
                parts = name.rstrip("/").split("/")
                if (name in names or not name or '\\' in name or name.startswith('/')
                        or any(part in ('..', '.', '') for part in parts)
                        or ':' in parts[0] or '\x00' in name
                        or stat.S_ISLNK(entry.external_attr >> 16)
                        or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or entry.file_size > 50 * 1024 * 1024
                        or entry.file_size > max(1, entry.compress_size) * 1000):
                    raise StagingError('unsafe_epub_package')
                names.add(name)
            if archive.read('mimetype') != b'application/epub+zip':
                raise StagingError('invalid_epub_mimetype')
            info = archive.getinfo('META-INF/container.xml')
            if info.file_size > 2 * 1024 * 1024:
                raise StagingError('invalid_epub_container')
            raw = archive.read(info)
            # Container XML has no legitimate DTD/entity requirement. Handle
            # UTF16 declarations too, before stdlib XML entity processing.
            check = raw.replace(b'\x00', b'').upper()
            if b'<!DOCTYPE' in check or b'<!ENTITY' in check:
                raise StagingError('invalid_epub_container')
            root = ET.fromstring(raw)
            packages = [node.get('full-path') for node in root.iter()
                        if node.tag == '{urn:oasis:names:tc:opendocument:xmlns:container}rootfile'
                        and node.get('media-type') == 'application/oebps-package+xml']
            if not packages or any(name not in names for name in packages):
                raise StagingError('invalid_epub_container')
            if archive.testzip() is not None:
                raise StagingError('invalid_epub_crc')
    except StagingError:
        raise
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError, RuntimeError):
        raise StagingError('invalid_epub') from None
    return 'epub'


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def persist_capability(path, token):
    """Non-replacing token publication, before DB capability issuance."""
    path = Path(path)
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
        raise StagingError('invalid_publication_token')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', prefix='.publication-token-', dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _regular(path).stat().st_size > 128 or path.read_text() != token:
                raise StagingError('publication_token_conflict') from None
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(source, ingest_dir, permit, extension, *, checkpoint=lambda: None):
    """Sidecar first, complete ebook last, with exclusive atomic publication.

    DB permit must already have been issued. Retries verify the same source and
    sidecar; a conflicting file is never overwritten or assigned this request.
    The caller reconciles an existing receipt before calling this method again.
    """
    source, directory = _regular(source), Path(ingest_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise StagingError('ingest_directory_unavailable')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', permit.staging_key) or extension not in BOOK_EXTENSIONS:
        raise StagingError('invalid_staging_identity')
    if digest(source) != permit.source_sha256:
        raise StagingError('source_changed')
    destination = directory / (NAME_PREFIX + permit.staging_key + '.' + extension)
    sidecar = Path(str(destination) + '.cwa.json')
    payload = {'action': 'acquisition_import', 'job_id': permit.job_id,
               'publication_token': permit.token, 'staging_key': permit.staging_key}
    def check_sidecar():
        if _regular(sidecar).stat().st_size > 16384:
            raise StagingError('publication_conflict')
        try:
            if json.loads(sidecar.read_text()) != payload:
                raise StagingError('publication_conflict')
        except (ValueError, UnicodeError):
            raise StagingError('publication_conflict') from None
    if destination.exists() or destination.is_symlink():
        check_sidecar()
        if digest(destination) != permit.source_sha256:
            raise StagingError('publication_conflict')
        return destination
    checkpoint()
    with tempfile.TemporaryDirectory(prefix='.cwng-publication-', dir=directory) as temporary:
        temporary = Path(temporary)
        book, manifest = temporary / 'book.part', temporary / 'manifest.part'
        with source.open('rb') as incoming, book.open('xb') as outgoing:
            os.fchmod(outgoing.fileno(), 0o600)
            shutil.copyfileobj(incoming, outgoing, 256 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest(book) != permit.source_sha256:
            raise StagingError('source_changed')
        with manifest.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint()
        try:
            os.link(manifest, sidecar)
        except FileExistsError:
            check_sidecar()
        _sync_directory(directory)
        checkpoint()
        try:
            os.link(book, destination)
        except FileExistsError:
            if digest(destination) != permit.source_sha256:
                raise StagingError('publication_conflict') from None
        _sync_directory(directory)
    return destination


def cleanup_completed(repository, staging_dir, job_id):
    """A committed receipt, independent of enablement, authorizes source GC."""
    root = Path(staging_dir)
    if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or root.is_symlink():
        return False
    if repository.completed_job(job_id) is None:
        return False
    directory = root / job_id
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory)
        return True
    return False


def cleanup_settled(repository, staging_dir, job_id):
    """Release private staging for any terminal job, not only an imported one.

    Failed and cancelled requests used to keep their downloaded source forever:
    cleanup ran only behind a receipt, so anything that did not finish left its
    bytes in `acquisition-staging`. The repository decides what is terminal, and
    deliberately still protects a failure that can resume its publication.
    """
    root = Path(staging_dir)
    if not re.fullmatch(r'[0-9a-f-]{36}', job_id) or root.is_symlink():
        return False
    if repository.settled_job(job_id) is None:
        return False
    directory = root / job_id
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory)
        return True
    return False
