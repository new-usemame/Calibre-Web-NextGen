# SPDX-License-Identifier: GPL-3.0-or-later
"""Real filesystem publication fences and untrusted ebook package checks."""
import importlib
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import zipfile
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_acquisition_staging_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec); sys.modules[spec.name] = package; spec.loader.exec_module(package)
s = importlib.import_module(spec.name + '.staging')


def epub(path, extra=None):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OPS/book.opf', '<package xmlns="http://www.idpf.org/2007/opf"><metadata/><manifest/><spine/></package>')
        if extra: z.writestr(*extra)


def test_valid_packages_and_error_bodies_are_distinguished(tmp_path):
    book = tmp_path / 'source.part'; epub(book)
    assert s.validate_book(book, 'application/epub+zip') == 'epub'
    with pytest.raises(s.StagingError): s.validate_book(book, 'application/pdf')
    book.write_bytes(b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n')
    assert s.validate_book(book, 'application/pdf') == 'pdf'
    for content in (b'<html>Login required</html>', b'%PDF-1.7\ntruncated', b''):
        book.write_bytes(content)
        with pytest.raises(s.StagingError): s.validate_book(book, 'application/pdf')
    with pytest.raises(s.StagingError): s.validate_book(book, 'application/epub+zip')


@pytest.mark.parametrize('entry', [('../escape', b'bad'), ('/absolute', b'bad'), ('mimetype', b'duplicate'), ('.', b'bad'), ('OPS/./book.opf', b'alias'), ('OPS//book.opf', b'alias')])
def test_unsafe_epub_members_never_reach_ingest(tmp_path, entry):
    book = tmp_path / 'source.part'; epub(book, entry)
    with pytest.raises(s.StagingError): s.validate_book(book, 'application/epub+zip')
    assert not (tmp_path.parent / 'escape').exists()


def test_token_and_publication_replay_never_overwrite_another_intent(tmp_path):
    source = tmp_path / 'source.part'; epub(source)
    directory = tmp_path / 'ingest'; directory.mkdir()
    token = secrets.token_urlsafe(32); tokenpath = tmp_path / 'token'
    s.persist_capability(tokenpath, token); s.persist_capability(tokenpath, token)
    with pytest.raises(s.StagingError): s.persist_capability(tokenpath, secrets.token_urlsafe(32))
    assert tokenpath.read_text() == token and tokenpath.stat().st_mode & 0o777 == 0o600
    permit = SimpleNamespace(job_id='request', staging_key='owned', token=token, source_sha256=s.digest(source))
    result = s.publish(source, directory, permit, 'epub')
    assert result.read_bytes() == source.read_bytes() and source.exists()
    manifest = Path(str(result) + '.cwa.json')
    assert json.loads(manifest.read_text())['job_id'] == 'request'
    assert result.stat().st_mode & 0o777 == 0o600
    assert s.publish(source, directory, permit, 'epub') == result
    result.write_bytes(b'existing content')
    with pytest.raises(s.StagingError): s.publish(source, directory, permit, 'epub')
    assert result.read_bytes() == b'existing content'


def test_crash_between_manifest_and_book_recovers_same_publication(tmp_path):
    source = tmp_path / 'source.part'; epub(source)
    directory = tmp_path / 'ingest'; directory.mkdir()
    permit = SimpleNamespace(job_id='request', staging_key='owned', token=secrets.token_urlsafe(32), source_sha256=s.digest(source))
    calls = []
    def stop():
        calls.append(1)
        if len(calls) == 3: raise RuntimeError('simulated stop after sidecar')
    with pytest.raises(RuntimeError): s.publish(source, directory, permit, 'epub', checkpoint=stop)
    assert len(list(directory.glob('*.cwa.json'))) == 1
    assert not list(directory.glob('*.epub')) and source.exists()
    result = s.publish(source, directory, permit, 'epub')
    assert result.read_bytes() == source.read_bytes()
    assert len(list(directory.glob('*.epub'))) == 1
    assert not list(directory.glob('.cwng-publication-*'))


def test_substituted_source_and_conflicting_sidecar_are_preserved(tmp_path):
    source = tmp_path / 'source.part'; epub(source)
    directory = tmp_path / 'ingest'; directory.mkdir()
    permit = SimpleNamespace(job_id='request', staging_key='owned', token=secrets.token_urlsafe(32), source_sha256=s.digest(source))
    sidecar = directory / 'cwng-acquisition-owned.epub.cwa.json'; sidecar.write_text('{}')
    with pytest.raises(s.StagingError): s.publish(source, directory, permit, 'epub')
    assert sidecar.read_text() == '{}' and not list(directory.glob('*.epub'))
    source.write_bytes(b'changed')
    with pytest.raises(s.StagingError): s.publish(source, directory, permit, 'epub')
    assert sidecar.read_text() == '{}'
