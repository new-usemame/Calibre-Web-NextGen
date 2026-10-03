# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, non-mutating discovery of owned completed book files."""
import importlib
import importlib.util
from pathlib import Path
import sys

import pytest

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


def config(root):
    return {'remote_path': '/downloads', 'local_path': str(root)}


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
