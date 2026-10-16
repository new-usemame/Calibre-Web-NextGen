"""Format identifiers are case-insensitive; Calibre filenames use lower extensions."""
from types import SimpleNamespace
import pytest
from flask import Flask
pytestmark = pytest.mark.unit

@pytest.fixture
def download_app(monkeypatch, tmp_path):
    from cps import helper, progress_syncing
    book = SimpleNamespace(id=224, title='Renamed title', path='Author/Renamed title (224)', authors=[])
    data = SimpleNamespace(format='EPUB', name='Renamed title - Author')
    folder = tmp_path / book.path
    folder.mkdir(parents=True)
    payload = b'published reflow artifact, exactly these bytes'
    path = folder / (data.name + '.epub')
    path.write_bytes(payload)
    monkeypatch.setattr(helper.calibre_db, 'get_filtered_book', lambda *a, **k: book)
    monkeypatch.setattr(helper.calibre_db, 'get_book_format', lambda _, fmt: data if fmt == 'EPUB' else None)
    monkeypatch.setattr(helper, 'current_user', SimpleNamespace(is_authenticated=False, role_admin=lambda: False))
    monkeypatch.setattr(helper.config, 'get_book_path', lambda: str(tmp_path))
    monkeypatch.setattr(helper.config, 'config_use_google_drive', False, raising=False)
    monkeypatch.setattr(helper.config, 'config_embed_metadata', False, raising=False)
    monkeypatch.setattr(helper.config, 'config_binariesdir', '', raising=False)
    monkeypatch.setattr(helper, 'get_temp_dir', lambda: str(tmp_path / 'delivery-temp'))
    monkeypatch.setattr(helper, 'get_valid_filename', lambda name, **k: name)
    monkeypatch.setattr(progress_syncing, 'calculate_and_store_checksum', lambda **k: None)
    app = Flask(__name__)
    @app.route('/download/224/<fmt>')
    @app.route('/opds/download/224/<fmt>/')
    def download(fmt):
        return helper.get_download_link(224, fmt, '')
    return app.test_client(), path, payload

@pytest.mark.parametrize('route', ['/download/224/{}', '/opds/download/224/{}/'])
@pytest.mark.parametrize('fmt', ['epub', 'EPUB', 'EpUb'])
def test_format_case_serves_exact_published_bytes(download_app, route, fmt):
    client, path, payload = download_app
    response = client.get(route.format(fmt))
    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == 'application/epub+zip'
    assert path.read_bytes() == payload
    assert '.epub' in response.headers['Content-Disposition']

def test_missing_format_file_is_not_reported_as_a_download(download_app):
    client, path, _ = download_app
    path.unlink()
    assert client.get('/download/224/EPUB').status_code == 404

def test_unknown_format_keeps_database_lookup_boundary(download_app):
    client, _, _ = download_app
    assert client.get('/download/224/NOT-A-FORMAT').status_code == 404
