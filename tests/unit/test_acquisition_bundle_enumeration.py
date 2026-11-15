# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, non-mutating discovery of owned completed book files."""
import importlib
import importlib.util
from pathlib import Path
import sys

import pytest
from tests.unit.test_acquisition_usenet import repo, s as repository_storage, usenet_worker_fixture

_path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_enumeration_tests', _path / '__init__.py',
    submodule_search_locations=[str(_path)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


def sab():
    return importlib.import_module(spec.name + '.sabnzbd')


def clients():
    return importlib.import_module(spec.name + '.clients')


def config(root, allow_mobi=False):
    return {'remote_path': '/downloads', 'local_path': str(root), 'allow_mobi': allow_mobi}


def client_config(root, adapter, *, include_allow_mobi=False, allow_mobi=False):
    c = clients()
    value = {'endpoint': 'https://client.example/', 'category': 'books',
        'remote_path': '/downloads', 'local_path': str(root)}
    if adapter == 'sabnzbd':
        value.update(auth_kind='none', username='', secret='SAB_KEY')
    else:
        value.update(auth_kind='basic', username='admin', secret='PRIVATE')
    if include_allow_mobi:
        value['allow_mobi'] = allow_mobi
    return c.connection_config(adapter, value)


def book(path, content=b'book'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_completed_enumeration_returns_all_sorted_supported_regular_files_and_wrapper_stays_choose_one(tmp_path):
    b = sab(); root = tmp_path / 'complete'; root.mkdir()
    folder = root / 'owned'; folder.mkdir()
    pdf = book(folder / 'z.PDF', b'%PDF')
    epub = book(folder / 'a.epub')
    book(folder / 'notes.txt')

    found = b.completed_books(config(root), '/downloads/owned')

    assert found == ((epub, 'application/epub+zip'), (pdf, 'application/pdf'))
    with pytest.raises(b.ClientError, match='multiple_books'):
        b.completed_book(config(root), '/downloads/owned')


def test_torrent_enumeration_uses_only_reported_paths_and_deduplicates_names(tmp_path):
    c = clients(); root = tmp_path / 'complete'; root.mkdir()
    owned = book(root / 'bundle' / 'inside.pdf', b'%PDF')
    files = [{'name': 'bundle/inside.pdf', 'size': 4},
             {'name': 'bundle/inside.pdf', 'size': 4}]

    found = c.torrent_books(config(root), '/downloads', files)

    assert found == ((owned, 'application/pdf'),)
    assert c.torrent_book(config(root), '/downloads', files) == found[0]


@pytest.mark.parametrize('kind', ['completed', 'torrent'])
@pytest.mark.parametrize('unsafe', ['traversal', 'symlink', 'fifo'])
def test_enumeration_rejects_unsafe_companion_even_after_valid_candidate(tmp_path, kind, unsafe):
    b = sab(); c = clients(); root = tmp_path / 'complete'; root.mkdir()
    folder = root / 'owned'; folder.mkdir()
    good = book(folder / 'book.epub')
    if unsafe == 'traversal':
        reported = [{'name': 'owned/book.epub'}, {'name': '../escape.txt'}]
        outside = None
    elif unsafe == 'symlink':
        outside = tmp_path / 'outside.txt'; outside.write_text('outside')
        (folder / 'companion.txt').symlink_to(outside)
        reported = [{'name': 'owned/book.epub'}, {'name': 'owned/companion.txt'}]
    else:
        import os
        import stat
        fifo = folder / 'companion.dat'
        os.mkfifo(fifo)
        assert stat.S_ISFIFO(fifo.stat().st_mode)
        reported = [{'name': 'owned/book.epub'}, {'name': 'owned/companion.dat'}]

    with pytest.raises((b.ClientError, c.ClientError), match='unsafe_completed_path'):
        if kind == 'completed':
            # The direct owned completion path has a valid book plus a bad sibling.
            if unsafe == 'traversal':
                b.completed_books(config(root), '/downloads/owned/../owned')
            else:
                b.completed_books(config(root), '/downloads/owned')
        else:
            c.torrent_books(config(root), '/downloads', reported)
    assert good.read_bytes() == b'book'


def test_completed_enumeration_rejects_symlinked_ancestor(tmp_path):
    b = sab(); root = tmp_path / 'complete'; root.mkdir()
    actual = root / 'actual'; actual.mkdir()
    book(actual / 'book.epub')
    (root / 'owned').symlink_to(actual, target_is_directory=True)
    with pytest.raises(b.ClientError, match='unsafe_completed_path'):
        b.completed_books(config(root), '/downloads/owned')


@pytest.mark.parametrize('kind', ['completed', 'torrent'])
def test_enumeration_caps_walk_entries_candidates_and_aggregate_actual_size(tmp_path, kind):
    b = sab(); c = clients(); root = tmp_path / 'complete'; root.mkdir()
    folder = root / 'owned'; folder.mkdir()
    for index in range(1001):
        (folder / f'companion-{index:04}.txt').touch()
    with pytest.raises((b.ClientError, c.ClientError), match='completed_files_limit'):
        if kind == 'completed':
            b.completed_books(config(root), '/downloads/owned')
        else:
            c.torrent_books(config(root), '/downloads', [{'name': f'owned/companion-{i:04}.txt'} for i in range(1001)])

    for entry in folder.iterdir(): entry.unlink()
    for index in range(21): book(folder / f'{index:02}.epub')
    with pytest.raises((b.ClientError, c.ClientError), match='completed_books_limit'):
        if kind == 'completed':
            b.completed_books(config(root), '/downloads/owned')
        else:
            c.torrent_books(config(root), '/downloads', [{'name': f'owned/{i:02}.epub', 'size': 4} for i in range(21)])

    for entry in folder.iterdir(): entry.unlink()
    for index in range(6):
        path = folder / f'{index}.pdf'; path.touch(); path.open('r+b').truncate(100 * 1024 * 1024)
    with pytest.raises((b.ClientError, c.ClientError), match='completed_size_limit'):
        if kind == 'completed':
            b.completed_books(config(root), '/downloads/owned')
        else:
            c.torrent_books(config(root), '/downloads', [{'name': f'owned/{i}.pdf', 'size': 100 * 1024 * 1024} for i in range(6)])


def test_torrent_entry_count_is_bounded_before_deduplication(tmp_path):
    c = clients(); root = tmp_path / 'complete'; root.mkdir()
    book(root / 'book.epub')
    with pytest.raises(c.ClientError, match='completed_files_limit'):
        c.torrent_books(config(root), '/downloads', [{'name': 'book.epub', 'size': 4}] * 1001)


@pytest.mark.parametrize('adapter', ['sabnzbd', 'nzbget', 'qbittorrent', 'transmission'])
def test_client_mobi_opt_in_config_is_bool_and_defaults_off(tmp_path, adapter):
    default = client_config(tmp_path, adapter)
    assert default['allow_mobi'] is False
    assert client_config(tmp_path, adapter, include_allow_mobi=True, allow_mobi=False)['allow_mobi'] is False
    assert client_config(tmp_path, adapter, include_allow_mobi=True, allow_mobi=True)['allow_mobi'] is True
    catalog = importlib.import_module(spec.name + '.catalog')
    for invalid in (None, 0, 1, 'true'):
        with pytest.raises(catalog.CatalogError, match='invalid_connection'):
            client_config(tmp_path, adapter, include_allow_mobi=True, allow_mobi=invalid)


def test_mobi_completion_and_torrent_candidates_require_client_opt_in(tmp_path):
    b, c = sab(), clients()
    contracts = importlib.import_module(spec.name + '.contracts')
    mobi_type = contracts.MOBI_MEDIA_TYPE
    fixtures = Path(__file__).resolve().parents[1] / 'fixtures' / 'sample_books'
    root = tmp_path / 'complete'; root.mkdir()
    standalone = book(root / 'Standalone.MOBI', (fixtures / 'test_original_direct.mobi').read_bytes())
    folder = root / 'owned'; folder.mkdir()
    alpha = book(folder / 'Alpha.MOBI', (fixtures / 'test_original_direct_uncompressed.mobi').read_bytes())
    beta = book(folder / 'beta.mobi', (fixtures / 'test_original_direct.mobi').read_bytes())
    epub = book(folder / 'ordinary.epub', (fixtures / 'test_minimal_valid.epub').read_bytes())
    unreported = book(root / 'unreported.pdf', b'%PDF-1.7\nfixture\n%%EOF\n')
    default = config(root)
    enabled = dict(default, allow_mobi=True)

    assert b.completed_books(default, '/downloads/Standalone.MOBI') == ()
    assert b.completed_book(enabled, '/downloads/Standalone.MOBI') == (standalone, mobi_type)
    assert b.completed_books(default, '/downloads/owned') == ((epub, 'application/epub+zip'),)
    assert b.completed_books(enabled, '/downloads/owned') == (
        (alpha, mobi_type), (beta, mobi_type), (epub, 'application/epub+zip'))

    reported = [
        {'name': 'owned/beta.mobi', 'size': beta.stat().st_size},
        {'name': 'owned/ordinary.epub', 'size': epub.stat().st_size},
        {'name': 'owned/Alpha.MOBI', 'size': alpha.stat().st_size},
        {'name': 'owned/beta.mobi', 'size': beta.stat().st_size},
    ]
    assert c.torrent_books(default, '/downloads', reported) == ((epub, 'application/epub+zip'),)
    assert c.torrent_books(enabled, '/downloads', reported) == (
        (alpha, mobi_type), (beta, mobi_type), (epub, 'application/epub+zip'))
    assert all(path != unreported for path, _ in c.torrent_books(enabled, '/downloads', reported))
    conflicting = reported + [{'name': 'owned/beta.mobi', 'size': beta.stat().st_size + 1}]
    with pytest.raises(c.ClientError, match='invalid_client_response'):
        c.torrent_books(enabled, '/downloads', conflicting)


def test_mobi_fingerprint_fallback_uses_the_format_label(tmp_path):
    root = tmp_path / 'complete'; root.mkdir()
    owned = root / 'owned'; owned.mkdir()
    control_named = book(owned / '\x01',
        (Path(__file__).resolve().parents[1] / 'fixtures/sample_books/test_original_direct.mobi').read_bytes())
    secrets = importlib.import_module(spec.name + '.secrets')
    fingerprint = importlib.import_module(spec.name + '.bundle_files').fingerprint_candidates
    manifest = fingerprint(config(root, True),
        ((control_named, importlib.import_module(spec.name + '.contracts').MOBI_MEDIA_TYPE),),
        secrets.SecretBox(b'x' * 32), 'owned-job', lambda: None, max_bytes=100 * 1024 * 1024)

    assert manifest['candidates'][0]['name'] == 'MOBI'


def test_mixed_real_mobi_manifest_is_durable_and_displays_truthful_format(repo, tmp_path):
    repository, _ = repo
    _, _, _, job = usenet_worker_fixture(repo, tmp_path)
    claim = repository.claim()
    repository.advance(job.id, claim.token, 'queued', 'resolving')
    repository.advance(job.id, claim.token, 'resolving', 'downloading')
    repository.begin_submission(job.id, claim.token)
    repository.record_external(job.id, claim.token, 'owned')

    fixtures = Path(__file__).resolve().parents[1] / 'fixtures' / 'sample_books'
    owned = tmp_path / 'owned'; owned.mkdir()
    mobi = book(owned / 'direct.MOBI', (fixtures / 'test_original_direct.mobi').read_bytes())
    epub = book(owned / 'reader.epub', (fixtures / 'test_minimal_valid.epub').read_bytes())
    contracts = importlib.import_module(spec.name + '.contracts')
    fingerprint = importlib.import_module(spec.name + '.bundle_files').fingerprint_candidates
    bundle = importlib.import_module(spec.name + '.bundle_storage')
    storage = importlib.import_module(spec.name + '.storage')
    manifest = fingerprint(config(tmp_path, True),
        ((epub, 'application/epub+zip'), (mobi, contracts.MOBI_MEDIA_TYPE)),
        repository.box, job.id, lambda: None, max_bytes=100 * 1024 * 1024)

    repository.await_choices(job.id, claim.token, manifest)
    visible = repository.bundle_choices(1, job.id)
    by_name = {choice['name']: choice for choice in visible['candidates']}
    assert {name: choice['format'] for name, choice in by_name.items()} == {
        'reader.epub': 'EPUB', 'direct.MOBI': 'MOBI'}
    assert 'relative_path' not in str(visible) and 'sha256' not in str(visible)
    with pytest.raises(repository_storage.NotFound):
        repository.bundle_choices(2, job.id)

    mobi_choice = by_name['direct.MOBI']
    with pytest.raises(repository_storage.Conflict):
        repository.select_book(1, job.id, 'stale-generation', mobi_choice['id'], requires_approval=False)
    selected = repository.select_book(1, job.id, visible['generation'], mobi_choice['id'], requires_approval=False)
    assert selected.id == job.id and selected.title == 'direct.MOBI' and selected.state == 'queued'
    persisted = {choice['name']: choice for choice in repository.bundle_choices(1, job.id)['candidates']}
    assert persisted['direct.MOBI']['format'] == 'MOBI'
    assert persisted['direct.MOBI']['job_id'] == job.id and persisted['direct.MOBI']['state'] == 'queued'

    with pytest.raises(storage.StorageError):
        bundle.BundleChoicesMixin._validate_manifest({**manifest, 'candidates': [
            {**manifest['candidates'][0], 'media_type': 'application/x-unrecognized'},
            manifest['candidates'][1],
        ]})
    with pytest.raises(storage.StorageError):
        bundle.BundleChoicesMixin._validate_manifest({**manifest, 'candidates': [
            manifest['candidates'][0],
            {**manifest['candidates'][1], 'relative_path': manifest['candidates'][0]['relative_path']},
        ]})
