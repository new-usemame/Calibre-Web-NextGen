# SPDX-License-Identifier: GPL-3.0-or-later
"""Concurrent downloads must own their artifacts and survive embed failure."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import threading
import time

import pytest
from flask import Flask
from werkzeug.datastructures import Headers

pytestmark = pytest.mark.unit


@pytest.fixture
def export_env(monkeypatch, tmp_path):
    from cps import embed_helper
    monkeypatch.setattr(embed_helper, 'config', SimpleNamespace(
        config_calibre_split=False, config_binariesdir='/calibre',
        get_book_path=lambda: str(tmp_path / 'library')))
    monkeypatch.setattr(embed_helper, 'get_temp_dir', lambda: str(tmp_path))
    monkeypatch.setenv('CWA_METADATA_LOCK_DIR', str(tmp_path))
    monkeypatch.setenv('CWA_EMBED_TIMEOUT', '2')
    from cps.services import calibre_user_plugins
    monkeypatch.setattr(calibre_user_plugins, 'apply_to_env', lambda _: None)
    return embed_helper


def test_simultaneous_exports_coordinate_the_calibre_library_lock(export_env, monkeypatch):
    """The fake Calibre rejects overlapping processes, like the real CLI."""
    active = 0
    maximum = 0
    guard = threading.Lock()
    start = threading.Barrier(2)

    class Process:
        def __init__(self, command, *args):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
                self.returncode = int(active > 1)
            self.destination = Path(command[command.index('--to-dir') + 1])
            self.name = command[command.index('--template') + 1]

        def communicate(self, timeout=None):
            nonlocal active
            time.sleep(0.05)
            if self.returncode == 0:
                (self.destination / (self.name + '.epub')).write_bytes(b'embedded')
            with guard:
                active -= 1
            return b'', b'Calibre library is locked' if self.returncode else b''

    monkeypatch.setattr(export_env, 'process_open', Process)
    def export():
        start.wait(timeout=5)
        return export_env._do_calibre_export_blocking(1, 'epub')
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(export) for _ in range(2)]
        results = [job.result(timeout=5) for job in jobs]
    assert maximum == 1, 'calibredb processes must never overlap on one library'
    assert len(set(results)) == 2
    for directory, name in results:
        assert (Path(directory) / (name + '.epub')).read_bytes() == b'embedded'


@pytest.mark.parametrize('returncode', [0, 1])
def test_export_never_advertises_a_missing_artifact(export_env, monkeypatch, returncode):
    """Both lock errors and success-without-output must trigger raw fallback."""
    monkeypatch.setattr(export_env, 'process_open', lambda *a, **kw: SimpleNamespace(
        returncode=returncode, communicate=lambda **_: (b'', b'no exported file')))
    assert export_env._do_calibre_export_blocking(1, 'epub') == (None, None)


@pytest.fixture
def download_env(monkeypatch, tmp_path):
    from cps import helper
    from cps import progress_syncing
    from cps.progress_syncing import settings
    library = tmp_path / 'library'
    book_dir = library / 'book'
    book_dir.mkdir(parents=True)
    (book_dir / 'same-title.epub').write_bytes(b'original')
    staged = tmp_path / 'staged'
    staged.mkdir()
    monkeypatch.setattr(helper, 'config', SimpleNamespace(
        config_use_google_drive=False, config_embed_metadata=True,
        config_binariesdir='/calibre', config_kepubifypath='',
        get_book_path=lambda: str(library)))
    monkeypatch.setattr(helper, 'get_temp_dir', lambda: str(staged))
    monkeypatch.setattr(helper, 'current_user', SimpleNamespace(is_authenticated=False))
    monkeypatch.setattr(settings, 'is_koreader_sync_enabled', lambda: True)
    checksums = []
    monkeypatch.setattr(progress_syncing, 'calculate_and_store_checksum',
        lambda **kw: checksums.append(kw))
    app = Flask(__name__)
    @app.get('/download')
    def download():
        return helper.do_download_file(
            SimpleNamespace(id=1, path='book'), 'epub', 'web',
            SimpleNamespace(name='same-title'),
            Headers({'Content-Disposition': "attachment; filename*=UTF-8''Reader%20Title.epub"}))
    return SimpleNamespace(helper=helper, app=app, staged=staged,
        checksums=checksums, book_dir=book_dir)


def test_simultaneous_downloads_keep_distinct_staged_bytes_and_client_filename(download_env, monkeypatch):
    """Two responses must never rename exports onto the same temp basename."""
    env = download_env
    local = threading.local()
    at_send = threading.Barrier(2)
    def export(*_):
        name = str(uuid4())
        (env.staged / (name + '.epub')).write_bytes(local.expected)
        return str(env.staged), name
    monkeypatch.setattr(env.helper, 'do_calibre_export', export)
    real_send = env.helper.send_from_directory
    def send(directory, name):
        at_send.wait(timeout=5)
        return real_send(directory, name)
    monkeypatch.setattr(env.helper, 'send_from_directory', send)
    def download(expected):
        local.expected = expected
        with env.app.test_client() as client:
            response = client.get('/download')
            actual = response.data
            response.close()
            return response.status_code, actual
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(download, content) for content in (b'first-user', b'second-user')]
        assert [job.result(timeout=5) for job in jobs] == [
            (200, b'first-user'), (200, b'second-user')]
    assert {row['filename_for_matching'] for row in env.checksums} == {'Reader Title.epub'}
    assert len({row['file_path'] for row in env.checksums}) == 2
    assert list(env.staged.iterdir()) == []
    assert (env.book_dir / 'same-title.epub').read_bytes() == b'original'


def test_embed_failure_delivers_original_without_cleaning_library_file(download_env, monkeypatch):
    env = download_env
    monkeypatch.setattr(env.helper, 'do_calibre_export', lambda *_: (None, None))
    response = env.app.test_client().get('/download')
    assert response.status_code == 200
    assert response.data == b'original'
    response.close()
    assert (env.book_dir / 'same-title.epub').read_bytes() == b'original'
