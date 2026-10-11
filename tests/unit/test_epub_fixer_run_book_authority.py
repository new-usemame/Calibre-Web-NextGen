"""Manual EPUB rewrites require the service's administrator and CSRF authority."""
from types import SimpleNamespace

import pytest
from flask import Flask, g, jsonify
from flask_wtf.csrf import generate_csrf

pytestmark = pytest.mark.unit


@pytest.fixture
def fixer_client(monkeypatch, tmp_path):
    from cps import cwa_functions as service, usermanagement
    config = SimpleNamespace(config_anonbrowse=1, config_allow_reverse_proxy_header_login=False,
                             config_use_google_drive=False, get_book_path=lambda: str(tmp_path))
    monkeypatch.setattr(service, 'config', config)
    monkeypatch.setattr(usermanagement, 'config', config)
    directory = tmp_path / 'Fixture'
    directory.mkdir()
    (directory / 'Book.epub').write_bytes(b'fixture owned by this test')
    book = SimpleNamespace(path='Fixture')
    data = SimpleNamespace(name='Book', format='EPUB')
    monkeypatch.setattr(service, 'calibre_db', SimpleNamespace(
        ensure_session=lambda: None, get_book=lambda _: book, get_book_format=lambda *args: data))
    log = tmp_path / 'epub-fixer.log'
    log.write_text('existing history')
    monkeypatch.setattr(service, '_service_log_path', lambda _: str(log))
    started = []

    class Thread:
        def __init__(self, target, args):
            self.target = target
        def start(self):
            started.append(self.target.__name__)

    monkeypatch.setattr(service, 'Thread', Thread)
    monkeypatch.setattr(service, '_', lambda message: message)
    app = Flask(__name__)
    app.config.update(SECRET_KEY='test-only-session-key', TESTING=True, WTF_CSRF_ENABLED=True)
    identity = {'administrator': False, 'authenticated': True}

    @app.before_request
    def identify():
        g._login_user = SimpleNamespace(is_authenticated=identity['authenticated'],
                                       role_admin=lambda: identity['administrator'])

    service.csrf.init_app(app)
    app.add_url_rule('/csrf', view_func=lambda: jsonify(token=generate_csrf()))
    app.register_blueprint(service.epub_fixer)
    client = app.test_client()
    token = client.get('/csrf').json['token']
    return client, token, identity, started, log


@pytest.mark.parametrize('authenticated', [False, True])
def test_non_admin_cannot_start_or_clear_a_repair(fixer_client, authenticated):
    client, token, identity, started, log = fixer_client
    identity['authenticated'] = authenticated
    result = client.post('/cwa-epub-fixer/run-book', json={'book_id': 1},
                         headers={'X-CSRFToken': token})
    assert result.status_code == 403
    assert started == []
    assert log.read_text() == 'existing history'


def test_admin_without_csrf_cannot_start_or_clear_a_repair(fixer_client):
    client, _, identity, started, log = fixer_client
    identity['administrator'] = True
    result = client.post('/cwa-epub-fixer/run-book', json={'book_id': 1})
    assert result.status_code == 400
    assert started == []
    assert log.read_text() == 'existing history'


def test_admin_with_csrf_can_use_the_real_single_book_route(fixer_client):
    client, token, identity, started, log = fixer_client
    identity['administrator'] = True
    result = client.post('/cwa-epub-fixer/run-book', json={'book_id': 1},
                         headers={'X-CSRFToken': token})
    assert result.status_code == 200
    assert result.json['success'] is True
    assert started == ['epub_fixer_start', 'kill_epub_fixer']
    assert log.read_text() == ''
