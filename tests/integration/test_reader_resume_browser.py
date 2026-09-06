"""Local browser contract; requires frontend npm ci and Playwright Chromium/Chrome."""
import inspect
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
import flask
import pytest
from cps import ub
from tests.unit.test_reader_resume import store, seed
from tests.unit.test_kobo_resume_point import epub, _add_selection_difference


def _frontend_dependencies_available(frontend):
    return all((frontend / path).is_file() for path in (
        'node_modules/vite/bin/vite.js',
        'node_modules/@playwright/test/package.json',
    ))


pytestmark = [
    pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for the local reader-resume browser test; install Node.js before running it'),
    pytest.mark.skipif(
        not _frontend_dependencies_available(Path(__file__).resolve().parents[2] / 'frontend'),
        reason='Local reader-resume browser test requires frontend Vite and @playwright/test; install with `npm ci --prefix frontend` and Chrome with `cd frontend && npx playwright install chrome`, then run `python -m pytest tests/integration/test_reader_resume_browser.py -rs`',
    ),
]


@pytest.mark.parametrize('carrier', ['koreader', 'kobo', 'kobo-local-name', 'kobo-unresolved'])
def test_koreader_http_to_real_spa_epub_resume(store, monkeypatch, tmp_path, epub, carrier):
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
    archive_path = root / 'tests/fixtures/sample_books/christmas_carol.epub'
    if carrier.startswith('kobo'):
        import sqlite3
        import zipfile
        from datetime import datetime, timezone
        from sqlalchemy import text
        from cps import config
        from tests.fixtures.kepub_fixture import _kobo_chapter_html
        archive_path = epub
        with zipfile.ZipFile(epub) as source:
            members = {name: source.read(name) for name in source.namelist()}
        members['OEBPS/chapter.xhtml'] = _kobo_chapter_html([
            (f'kobo.1.{i}', f'Paragraph {i}. ' + 'A precise reading position survives the trip from the device. ' * 30)
            for i in range(1, 101)
        ]).encode()
        with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        if carrier == 'kobo-local-name':
            _add_selection_difference(epub, carrier.removeprefix('kobo-'),
                members['OEBPS/chapter.xhtml'].replace(b'<body>', b'<body><p>Leading paragraph.</p>'))
        with sqlite3.connect(tmp_path / 'metadata.db') as metadata:
            metadata.executescript('CREATE TABLE books (id INTEGER, path TEXT); '
                                  'CREATE TABLE data (book INTEGER, format TEXT, name TEXT); '
                                  "INSERT INTO books VALUES (42, ''); "
                                  "INSERT INTO data VALUES (42, 'EPUB', 'reader');")
        monkeypatch.setattr(config, 'config_calibre_dir', str(tmp_path), raising=False)
        monkeypatch.setattr(config, 'get_book_path', lambda: str(tmp_path))
        monkeypatch.setattr(config, 'config_use_google_drive', False, raising=False)
        seed(session, remote=95)
        def sync_kobo(value='kobo.1.50'):
            session.execute(text("UPDATE kobo_bookmark SET location_source='OEBPS/chapter.xhtml', "
                                 "location_type='KoboSpan', location_value=:value, last_modified=:clock"),
                            {'value': value, 'clock': datetime.now(timezone.utc).replace(tzinfo=None)})
            session.commit()
            return flask.jsonify(ok=True)
        # Recorded Kobo progress Location shape, at the persisted carrier seam.
        with flask.Flask(__name__).app_context():
            sync_kobo()
    app = flask.Flask(__name__)
    if carrier.startswith('kobo'):
        app.add_url_rule('/test-state/kobo', 'fixture_sync',
                         lambda: sync_kobo(flask.request.json['value']), methods=['POST'])
    if carrier == 'kobo-unresolved':
        @app.after_request
        def unresolved_exact_hint(response):
            # Keep the real API/hash/carrier, but simulate any server CFI that
            # cannot resolve in epub.js. This independently exercises Reader's
            # fallback even after the archive-selection defect is fixed.
            if flask.request.method == 'GET' and flask.request.path.endswith('/bookmark'):
                payload = response.get_json()
                if payload and payload.get('resume'):
                    payload['resume']['cfi'] = 'epubcfi(/6/2!/4/2/202[kobo.1.101]/1:0)'
                    response.set_data(app.json.dumps(payload))
            return response
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'get', inspect.unwrap(reader.get_bookmark))
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'save', inspect.unwrap(reader.save_bookmark), methods=['POST'])
    app.add_url_rule('/kosync/syncs/progress', 'sync', kosync.update_progress, methods=['PUT'])
    app.add_url_rule('/api/v1/auth/csrf', 'csrf', lambda: flask.jsonify(csrf_token='fixture'))
    app.add_url_rule('/api/v1/reader/settings', 'settings', inspect.unwrap(reader.get_reader_settings))
    app.add_url_rule('/api/v1/books/42', 'book', lambda: flask.jsonify(id=42,title='A Christmas Carol',authors=[],formats=[{'format':'EPUB','content_url':'/fixture.epub'}]))
    app.add_url_rule('/fixture.epub', 'epub', lambda: flask.send_file(archive_path))
    app.add_url_rule('/annotations/42/data.json', 'annotations', lambda: flask.jsonify(annotations=[]))
    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        web_port = port_probe.getsockname()[1]
    env = dict(os.environ, RESUME_API_URL=f'http://127.0.0.1:{server.server_port}', RESUME_WEB_PORT=str(web_port))
    log_path = tmp_path / 'vite.log'
    log = log_path.open('w')
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
                pytest.fail(log_path.read_text())
            time.sleep(.1)
        runner = 'run-fallback.mjs' if carrier.startswith('kobo-') else ('run-kobo.mjs' if carrier == 'kobo' else 'run.mjs')
        result = subprocess.run(['node', 'e2e/reader-resume/' + runner], cwd=root/'frontend', text=True, capture_output=True, timeout=120, env=env)
        print(result.stdout)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        vite.terminate()
        vite.wait(timeout=10)
        log.close()
        server.shutdown()
        thread.join(timeout=5)
