"""Inbound #324: positions are read-only hints ordered against actual CFI saves."""
import inspect
import sqlite3
import time
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from cps import ub
from cps.services import reading_position
from tests.unit.test_kobo_resume_point import epub


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'))
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', session)
    yield engine, session
    session.close()
    engine.dispose()


def seed(session, *, local=None, local_time=None, remote=37.5, remote_time=None, user=7):
    if local:
        session.add(ub.Bookmark(user_id=user, book_id=42, format='epub',
                                bookmark_key=local, updated_at=local_time))
    state = ub.KoboReadingState(user_id=user, book_id=42)
    state.current_bookmark = ub.KoboBookmark(progress_percent=remote,
        last_modified=remote_time or datetime(2026, 9, 1))
    read_book = ub.ReadBook(user_id=user, book_id=42, read_status=ub.ReadBook.STATUS_IN_PROGRESS)
    read_book.kobo_reading_state = state
    session.add(read_book)
    session.commit()


def read(engine, user=7):
    return reading_position.read_resume_position(engine, user, 42)


def test_remote_resume_is_scoped_read_only_and_freshness_ordered(store):
    engine, session = store
    now = datetime(2026, 9, 1)
    seed(session)
    assert read(engine)['resume'] == {'percentage': 37.5,
        'synced_at': now.replace(tzinfo=timezone.utc).isoformat(), 'mode': 'automatic'}
    assert read(engine, 8) == {'bookmark': None, 'resume': None}
    assert session.query(ub.Bookmark).count() == 0
    bookmark = ub.Bookmark(user_id=7, book_id=42, format='epub',
                           bookmark_key='local-cfi', updated_at=now - timedelta(seconds=1))
    session.add(bookmark)
    session.commit()
    assert read(engine)['resume']['mode'] == 'offer'
    assert read(engine)['bookmark'] == 'local-cfi'
    for clock in (now, now + timedelta(seconds=1)):
        bookmark.updated_at = clock
        session.commit()
        assert read(engine) == {'bookmark': 'local-cfi', 'resume': None}
    session.refresh(bookmark)
    assert bookmark.bookmark_key == 'local-cfi'


def test_unusable_or_unavailable_progress_retains_cfi_with_bounded_lock_wait(store, monkeypatch):
    engine, session = store
    seed(session, local='local-cfi', local_time=datetime(2026, 8, 1))
    for invalid in (-1, 101, float('inf'), 'junk', None):
        session.execute(text('UPDATE kobo_bookmark SET progress_percent=:p'), {'p': invalid})
        session.commit()
        assert read(engine) == {'bookmark': 'local-cfi', 'resume': None}
    session.execute(text('DROP TABLE kobo_bookmark'))
    session.commit()
    assert read(engine) == {'bookmark': 'local-cfi', 'resume': None}
    ub.KoboBookmark.__table__.create(engine)
    session.add(ub.KoboBookmark(
        kobo_reading_state_id=session.query(ub.KoboReadingState).one().id,
        progress_percent=37.5, last_modified=datetime(2026, 9, 1)))
    session.commit()
    # Contention begins after the mandatory local lookup. The optional reader
    # must return the already-read CFI while this writer still holds the lock.
    connect = sqlite3.connect
    lock = connect(engine.url.database)
    def locked_optional_connect(*args, **kwargs):
        lock.execute('BEGIN EXCLUSIVE')
        return connect(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', locked_optional_connect)
        try:
            started = time.monotonic()
            assert read(engine) == {'bookmark': 'local-cfi', 'resume': None}
            assert time.monotonic() - started < .5
            assert lock.in_transaction
        finally:
            lock.rollback()
            lock.close()
    missing = create_engine('sqlite:///' + engine.url.database + '-absent')
    assert read(missing) == {'bookmark': 'local-cfi', 'resume': None}
    from pathlib import Path
    assert not Path(missing.url.database).exists()
    missing.dispose()


def test_local_cfi_waits_out_transient_app_database_writer(store):
    engine, session = store
    seed(session, local='local-cfi', local_time=datetime(2026, 9, 2))
    locked = threading.Event()
    released = threading.Event()
    def writer():
        with sqlite3.connect(engine.url.database) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            locked.set()
            time.sleep(.2)
            connection.rollback()
            released.set()
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert locked.wait(2)
        assert read(engine) == {'bookmark': 'local-cfi', 'resume': None}
        assert released.wait(2)
    finally:
        thread.join(2)
    session.expire_all()
    assert session.query(ub.Bookmark).one().bookmark_key == 'local-cfi'


def test_unknown_local_clock_offers_remote_without_changing_saved_cfi(store):
    engine, session = store
    seed(session, local='pre-migration-cfi')
    assert read(engine) == {'bookmark': 'pre-migration-cfi', 'resume': {
        'percentage': 37.5, 'synced_at': '2026-09-01T00:00:00+00:00', 'mode': 'offer'}}
    session.expire_all()
    saved = session.query(ub.Bookmark).one()
    assert (saved.bookmark_key, saved.updated_at) == ('pre-migration-cfi', None)


def test_migration_preserves_unknown_old_cfi_and_is_repeatable(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.db'))
    with engine.begin() as c:
        c.execute(text('CREATE TABLE bookmark (id INTEGER PRIMARY KEY, bookmark_key TEXT)'))
        c.execute(text("INSERT INTO bookmark VALUES (1, 'old-cfi')"))
    for _ in range(2):
        ub.migrate_bookmark_updated_at(engine, None)
        with engine.connect() as c:
            assert c.execute(text('SELECT bookmark_key, updated_at FROM bookmark')).all() == [('old-cfi', None)]
    engine.dispose()


def test_both_browser_writes_win_over_their_own_mirror_even_without_percentage(store, monkeypatch):
    from cps.api import reader
    from cps import web
    import sys
    import cps.progress_syncing.protocols.kosync
    monkeypatch.setattr(sys.modules['cps.progress_syncing.protocols.kosync'].config, 'config_read_column', 0, raising=False)
    import cps.services.device_registry as registry
    engine, session = store
    user = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False)
    monkeypatch.setattr(reader, 'current_user', user)
    monkeypatch.setattr(web, 'current_user', user)
    monkeypatch.setattr(registry, 'ensure_webreader_device_best_effort', lambda **kw: None)
    monkeypatch.setattr(ub, 'session_flush', lambda: session.flush() is None)
    monkeypatch.setattr(ub, 'session_commit', lambda *args: session.commit() is None)
    seed(session, remote_time=datetime.now(timezone.utc) - timedelta(days=1))
    app = flask.Flask(__name__)
    for percentage in (None, 60, 80):
        for route, field, cfi in ((reader.save_bookmark, 'json', 'api-cfi'),
                                  (web.set_bookmark, 'data', 'classic-cfi')):
            payload = {'bookmark': cfi}
            if percentage is not None:
                payload['percentage'] = percentage + (1 if route == web.set_bookmark else 0)
            with app.test_request_context('/', method='POST', **{field: payload}):
                result = inspect.unwrap(route)(42, 'epub') if route == web.set_bookmark else inspect.unwrap(route)(42)
            assert result[1] in (201, 204)
            assert read(engine) == {'bookmark': cfi, 'resume': None}
            saved = session.query(ub.Bookmark).one()
            mirror = session.query(ub.KoboBookmark).one()
            assert saved.updated_at is not None
            assert saved.updated_at >= mirror.last_modified


def test_exact_kobo_resume_preserves_offer_and_percentage_fallback(store, tmp_path, monkeypatch, epub):
    """Real SQLite + EPUB lookup: same-book span wins; failed resolution changes no payload."""
    from cps import config
    engine, session = store
    archive = epub
    metadata = sqlite3.connect(tmp_path / 'metadata.db')
    metadata.executescript('CREATE TABLE books (id INTEGER, path TEXT); '
                          'CREATE TABLE data (book INTEGER, format TEXT, name TEXT); '
                          "INSERT INTO books VALUES (42, ''); "
                          "INSERT INTO data VALUES (42, 'EPUB', 'reader');")
    metadata.close()
    monkeypatch.setattr(config, 'config_calibre_dir', str(tmp_path), raising=False)
    monkeypatch.setattr(config, 'get_book_path', lambda: str(tmp_path))
    monkeypatch.setattr(config, 'config_use_google_drive', False, raising=False)
    seed(session)
    original = read(engine)
    session.execute(text("UPDATE kobo_bookmark SET location_source='OEBPS/chapter.xhtml', "
                         "location_type='KoboSpan', location_value='kobo.1.2'"))
    session.commit()
    exact = read(engine)
    assert exact['resume']['cfi'] == 'epubcfi(/6/2!/4/2/4[kobo.1.2]/1:0)'
    assert exact['resume']['percentage'] == original['resume']['percentage']
    assert exact['resume']['mode'] == 'automatic'
    assert read(engine, 8) == {'bookmark': None, 'resume': None}
    session.add(ub.Bookmark(user_id=7, book_id=42, format='epub', bookmark_key='local-cfi'))
    session.commit()
    offered = read(engine)
    assert offered['bookmark'] == 'local-cfi'
    assert offered['resume'] == {**exact['resume'], 'mode': 'offer'}
    fallback = {'bookmark': 'local-cfi', 'resume': {**original['resume'], 'mode': 'offer'}}
    session.execute(text("UPDATE kobo_bookmark SET location_value='kobo.99.99'"))
    session.commit()
    assert read(engine) == fallback
    session.execute(text("UPDATE kobo_bookmark SET location_value='kobo.1.2'"))
    session.commit()
    archive.unlink()
    # Completed conversions remain usable until expiry; the reader checks the
    # fingerprint against downloaded bytes. After expiry, missing files fall back.
    assert read(engine) == offered
    from cps.services import kobo_resume
    clock = time.monotonic
    monkeypatch.setattr(kobo_resume, 'time', SimpleNamespace(
        monotonic=lambda: clock() + kobo_resume._CACHE_TTL_SECONDS + 1))
    assert read(engine) == fallback
    assert reading_position.read_resume_position(engine, 7, 42, 'pdf') == {'bookmark': None, 'resume': None}
    session.expire_all()
    assert session.query(ub.Bookmark).one().bookmark_key == 'local-cfi'


def test_stalled_conversion_keeps_percentage_response_and_bounded_worker_admission(store, monkeypatch):
    """Slow filesystem work cannot hold the endpoint or accumulate queued jobs."""
    from cps.services import kobo_resume
    from cps.services.parallel import cooperative_sleep
    engine, session = store
    seed(session)
    original = read(engine)
    session.execute(text("UPDATE kobo_bookmark SET location_source='chapter.xhtml', "
                         "location_type='KoboSpan', location_value='kobo.1.2'"))
    session.commit()
    release = threading.Event()
    calls = []
    finished = []
    def stalled(*args):
        calls.append(args)
        release.wait(.8)
        finished.append(True)
        return None
    monkeypatch.setattr(kobo_resume, '_resolve', stalled)
    try:
        for _ in range(4):
            started = time.monotonic()
            assert read(engine) == original
            assert time.monotonic() - started < .25
        assert len(calls) == 2
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while len(finished) < len(calls) and time.monotonic() < deadline:
            cooperative_sleep(.001)
    assert len(finished) == 2


def test_completed_conversion_survives_request_deadline(store, monkeypatch):
    """A completed late conversion must supply the next read without parsing again."""
    from cps.services import kobo_resume
    engine, session = store
    seed(session)
    fallback = read(engine)
    session.execute(text("UPDATE kobo_bookmark SET location_source='late.xhtml', "
                         "location_type='KoboSpan', location_value='kobo.1.2'"))
    session.commit()
    exact = {'cfi': 'epubcfi(/6/2!/4/2/4[kobo.1.2]/1:0)', 'epub_sha256': 'a' * 64}
    release = threading.Event()
    threads = []
    calls = []
    real_thread = threading.Thread

    def tracked_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    def slow_resolve(*args):
        calls.append(args)
        assert release.wait(5), 'test did not release conversion'
        return exact.copy()

    monkeypatch.setattr(kobo_resume.threading, 'Thread', tracked_thread)
    monkeypatch.setattr(kobo_resume, '_resolve', slow_resolve)
    try:
        assert read(engine) == fallback
        assert threads[0].is_alive(), 'first read must expire before conversion completes'
        release.set()
        threads[0].join(2)
        assert not threads[0].is_alive()
        release.clear()  # A fresh parse would miss the budget again, as on slow storage.
        resumed = read(engine)
        assert resumed['resume'] == {**fallback['resume'], **exact}
        assert len(calls) == 1
    finally:
        release.set()
        for thread in threads:
            thread.join(2)


def test_conversion_cache_expires_evicts_and_scopes_positions(monkeypatch):
    """Hot entries expire, older completions are evicted, and locations/libraries differ."""
    from collections import OrderedDict
    from cps import config
    from cps.services import kobo_resume
    now = [100.0]
    calls = []
    monkeypatch.setattr(kobo_resume, '_CACHE', OrderedDict())
    monkeypatch.setattr(kobo_resume, '_CACHE_MAX_ENTRIES', 2)
    monkeypatch.setattr(kobo_resume, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(kobo_resume.threading, 'Thread',
                        lambda *, target, **kw: SimpleNamespace(start=target))

    def resolve(*args):
        calls.append(args)
        return {'cfi': str(args), 'epub_sha256': str(len(calls))}

    monkeypatch.setattr(kobo_resume, '_resolve', resolve)
    def read_span(book=42, source='chapter.xhtml', value='kobo.1.2'):
        return kobo_resume.exact_resume(book, source, 'KoboSpan', value)

    first = read_span()
    first['cfi'] = 'caller mutation'
    now[0] += kobo_resume._CACHE_TTL_SECONDS - 1
    assert read_span()['epub_sha256'] == '1'
    assert read_span()['cfi'] != first['cfi']
    now[0] += 2
    assert read_span()['epub_sha256'] == '2'  # Access did not extend expiry.
    assert read_span(value='kobo.1.3')['epub_sha256'] == '3'
    assert read_span(source='other.xhtml')['epub_sha256'] == '4'
    assert read_span()['epub_sha256'] == '5'  # Two-entry capacity evicted it.
    assert read_span(book=43)['epub_sha256'] == '6'
    monkeypatch.setattr(config, 'config_calibre_dir', '/different-library', raising=False)
    assert read_span(book=43)['epub_sha256'] == '7'
    assert read_span(book=43)['epub_sha256'] == '7'


@pytest.mark.parametrize('failure', [None, {}, RuntimeError('unavailable')])
def test_failed_conversion_is_retried(monkeypatch, failure):
    from collections import OrderedDict
    from cps.services import kobo_resume
    monkeypatch.setattr(kobo_resume, '_CACHE', OrderedDict())
    monkeypatch.setattr(kobo_resume.threading, 'Thread',
                        lambda *, target, **kw: SimpleNamespace(start=target))
    exact = {'cfi': 'exact', 'epub_sha256': 'fingerprint'}
    outcomes = [failure, exact]

    def resolve(*args):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(kobo_resume, '_resolve', resolve)
    assert not kobo_resume.exact_resume(42, 'chapter', 'KoboSpan', 'kobo.1.2')
    assert kobo_resume.exact_resume(42, 'chapter', 'KoboSpan', 'kobo.1.2') == exact
    assert not outcomes


@pytest.mark.parametrize('raw, expected', [
    (None, .05), ('0.2', .2), ('0', .05), ('-1', .05),
    ('nan', .05), ('inf', .05), ('invalid', .05),
])
def test_resume_budget_configuration(monkeypatch, raw, expected):
    from cps.services import kobo_resume
    if raw is None:
        monkeypatch.delenv('CWA_KOBO_RESUME_TIMEOUT_SECONDS', raising=False)
    else:
        monkeypatch.setenv('CWA_KOBO_RESUME_TIMEOUT_SECONDS', raw)
    assert kobo_resume._resume_timeout() == expected
