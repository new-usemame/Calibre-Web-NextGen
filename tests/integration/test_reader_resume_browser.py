"""Local browser contract; requires frontend npm ci and Playwright Chromium/Chrome."""
import inspect
import time
from types import SimpleNamespace
import flask
import pytest
from cps import ub
from tests.unit.test_reader_resume import store

def test_koreader_http_to_real_spa_epub_resume(store, monkeypatch):
    """Replay KOReader HTTP into SQLite, then drive the real Reader with Chromium.

    Authentication and Calibre checksum lookup are fixture boundaries; the sync
    handler, carrier arbitration, bookmark API, React Reader, and epub.js run
    unchanged. No running library or external device is touched.
    """
    import os
    import socket
    import subprocess
    import sys
    import threading
    from pathlib import Path
    import requests
    from werkzeug.serving import make_server
    from cps import calibre_db
    from cps.api import reader
    import cps.progress_syncing.protocols.kosync
    kosync = sys.modules['cps.progress_syncing.protocols.kosync']
    from cps.services import device_registry
    engine, session = store
    root = Path(__file__).resolve().parents[2]
    user = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False, view_settings={})
    monkeypatch.setattr(reader, 'current_user', user)
    monkeypatch.setattr(kosync, 'authenticate_user', lambda: user)
    monkeypatch.setattr(kosync, '_require_kosync_enabled', lambda: None)
    monkeypatch.setattr(kosync, 'enrich_response_with_book_info', lambda response, document: (response, 42, 'EPUB', 'A Christmas Carol', None))
    monkeypatch.setattr(kosync, 'get_book_checksums', lambda *a, **kw: [])
    monkeypatch.setattr(calibre_db, 'get_book', lambda *a: None)
    monkeypatch.setattr(kosync, 'push_reading_state_to_hardcover', lambda *a: None)
    monkeypatch.setattr(kosync.config, 'config_read_column', 0, raising=False)
    monkeypatch.setattr(device_registry, 'ensure_webreader_device_best_effort', lambda **kw: None)
    monkeypatch.setattr(ub, 'session_flush', lambda: session.flush() is None)
    monkeypatch.setattr(ub, 'session_commit', lambda *args: session.commit() is None)
    app = flask.Flask(__name__)
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'get', inspect.unwrap(reader.get_bookmark))
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'save', inspect.unwrap(reader.save_bookmark), methods=['POST'])
    app.add_url_rule('/kosync/syncs/progress', 'sync', kosync.update_progress, methods=['PUT'])
    app.add_url_rule('/api/v1/auth/csrf', 'csrf', lambda: flask.jsonify(csrf_token='fixture'))
    app.add_url_rule('/api/v1/reader/settings', 'settings', inspect.unwrap(reader.get_reader_settings))
    app.add_url_rule('/api/v1/books/42', 'book', lambda: flask.jsonify(id=42,title='A Christmas Carol',authors=[],formats=[{'format':'EPUB','content_url':'/fixture.epub'}]))
    app.add_url_rule('/fixture.epub', 'epub', lambda: flask.send_file(root / 'tests/fixtures/sample_books/christmas_carol.epub'))
    app.add_url_rule('/annotations/42/data.json', 'annotations', lambda: flask.jsonify(annotations=[]))
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        web_port = port_probe.getsockname()[1]
    env = dict(os.environ, RESUME_API_URL=f'http://127.0.0.1:{server.server_port}', RESUME_WEB_PORT=str(web_port))
    log = open('/tmp/324-vite.log', 'w')
    vite = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--config', 'e2e/reader-resume/vite.config.ts'], cwd=root/'frontend', env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if requests.get(f'http://127.0.0.1:{web_port}/e2e/reader-resume/index.html', timeout=1).ok:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                pytest.fail(Path('/tmp/324-vite.log').read_text())
            time.sleep(.1)
        result = subprocess.run(['node', 'e2e/reader-resume/run.mjs'], cwd=root/'frontend', text=True, capture_output=True, timeout=120, env=env)
        print(result.stdout)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        vite.terminate()
        vite.wait(timeout=10)
        log.close()
        server.shutdown()
        thread.join(timeout=5)
