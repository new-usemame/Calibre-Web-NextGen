# SPDX-License-Identifier: GPL-3.0-or-later
"""The all-account restore point survives interrupted and partial enables."""
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub, user_library
from tests.unit.test_my_library_admin_intro import _book, _cdb, _user

pytestmark = pytest.mark.unit


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    path = tmp_path / 'app.db'
    engine = create_engine('sqlite:///' + str(path))
    ub.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    metadata_engine = create_engine('sqlite:///:memory:', execution_options={'schema_translate_map': {'calibre': None}})
    db.Base.metadata.create_all(metadata_engine)
    metadata = sessionmaker(bind=metadata_engine)()
    metadata.add_all([_book(1, 'One'), _book(2, 'Two')])
    metadata.commit()
    monkeypatch.setattr(ub, 'session', session)
    yield SimpleNamespace(path=path, engine=engine, factory=factory, session=session, cdb=_cdb(metadata))
    session.close()
    engine.dispose()
    metadata.close()
    metadata_engine.dispose()


def test_reopened_database_resumes_interrupted_enable_and_undo_restores_original(recovery, monkeypatch):
    """Kill at a real committed-account boundary, then open a new DB connection."""
    import sqlite3
    session = recovery.session
    user = _user(session, 'original')
    user_id = user.id

    def crash_after_account_commit(_session):
        with sqlite3.connect(recovery.path) as connection:
            own = connection.execute('SELECT has_own_library FROM user WHERE id=?', (user_id,)).fetchone()[0]
        if own:
            raise KeyboardInterrupt('process exit after durable account write')

    event.listen(session, 'after_commit', crash_after_account_commit)
    with pytest.raises(KeyboardInterrupt):
        user_library.enable_my_library_for_all(app_session=session, cdb=recovery.cdb)
    event.remove(session, 'after_commit', crash_after_account_commit)
    session.close()
    recovery.engine.dispose()

    reopened = recovery.factory()
    monkeypatch.setattr(ub, 'session', reopened)
    try:
        state, _ = user_library.enable_my_library_for_all(app_session=reopened, cdb=recovery.cdb)
        assert state['status'] == 'enabled'
        user_library.undo_my_library_for_all(app_session=reopened)
        restored = reopened.get(ub.User, user_id)
        assert (restored.role_browse_global(), restored.has_own_library) == (False, False)
        assert restored.user_library_seeded
        assert user_library.membership_count(user_id, reopened) == 2
    finally:
        reopened.close()


@pytest.mark.parametrize('after_seed_commit', [False, True])
def test_partial_enable_retries_failed_accounts_without_reseeding_completed_selection(recovery, monkeypatch, after_seed_commit):
    first = _user(recovery.session, 'finished')
    second = _user(recovery.session, 'retry-me')
    guest = _user(recovery.session, 'Guest', role=constants.ROLE_ANONYMOUS)
    original = user_library.prepare_user_library_seed
    attempts = []

    def temporary_failure(user, **kwargs):
        attempts.append(user.id)
        if user.id == second.id:
            if after_seed_commit:
                original(user, **kwargs)
            raise RuntimeError('temporary metadata read failure')
        return original(user, **kwargs)

    monkeypatch.setattr(user_library, 'prepare_user_library_seed', temporary_failure)
    state, results = user_library.enable_my_library_for_all(app_session=recovery.session, cdb=recovery.cdb)
    assert state['status'] == 'incomplete'
    assert state['pending_accounts'] == 1
    assert state['failed_accounts'] == [{'user_id': second.id, 'name': second.name, 'error': 'temporary metadata read failure'}]
    user_library.remove_book(first, 1, app_session=recovery.session)
    user_library.remove_book(first, 2, app_session=recovery.session)
    monkeypatch.setattr(user_library, 'prepare_user_library_seed', original)
    state, results = user_library.enable_my_library_for_all(app_session=recovery.session, cdb=recovery.cdb)
    assert state['status'] == 'enabled'
    assert [r['user_id'] for r in results] == [second.id]
    assert user_library.membership_count(first.id, recovery.session) == 0
    assert user_library.membership_count(second.id, recovery.session) == 2
    assert guest.has_own_library is False and guest.role == constants.ROLE_ANONYMOUS
    user_library.undo_my_library_for_all(app_session=recovery.session)
    assert not first.has_own_library and not second.has_own_library
    assert not first.role_browse_global() and not second.role_browse_global()


def test_partial_enable_can_be_undone_without_retry(recovery, monkeypatch):
    first = _user(recovery.session, 'finished')
    second = _user(recovery.session, 'retry-me')
    original = user_library.prepare_user_library_seed
    def fail_second(user, **kwargs):
        if user.id == second.id:
            raise RuntimeError('unavailable')
        return original(user, **kwargs)
    monkeypatch.setattr(user_library, 'prepare_user_library_seed', fail_second)
    state, _ = user_library.enable_my_library_for_all(app_session=recovery.session, cdb=recovery.cdb)
    assert state['status'] == 'incomplete'
    state, restored = user_library.undo_my_library_for_all(app_session=recovery.session)
    assert state['status'] == 'not_enabled' and restored == 2
    assert not first.has_own_library and not first.role_browse_global()
    assert not second.has_own_library and not second.role_browse_global()


def test_overlapping_administrators_cannot_undo_or_replace_running_enable(recovery, monkeypatch):
    user_id = _user(recovery.session, 'concurrent').id
    recovery.session.close()
    entered = threading.Event()
    release = threading.Event()
    errors = []
    # Avoid sharing the Calibre connection across threads; this worker is
    # already seeded, and its pause is at the actual mode-transition seam.
    original_mode = user_library.set_library_mode
    def paused_mode(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_mode(*args, **kwargs)
    monkeypatch.setattr(user_library, 'set_library_mode', paused_mode)
    with recovery.factory() as setup:
        user = setup.get(ub.User, user_id)
        user.user_library_seeded = True
        setup.add(ub.UserLibraryBook(user_id=user_id, book_id=1))
        setup.commit()
    def worker():
        with recovery.factory() as session:
            try:
                user_library.enable_my_library_for_all(app_session=session, cdb=recovery.cdb)
            except BaseException as error:
                errors.append(error)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        with recovery.factory() as other:
            with pytest.raises(user_library.UserLibraryError, match='already running'):
                user_library.enable_my_library_for_all(app_session=other, cdb=recovery.cdb)
            with pytest.raises(user_library.UserLibraryError, match='already running'):
                user_library.undo_my_library_for_all(app_session=other)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    with recovery.factory() as session:
        user_library.undo_my_library_for_all(app_session=session)
        user = session.get(ub.User, user_id)
        assert not user.has_own_library and not user.role_browse_global()


@pytest.mark.parametrize('action', ['enable', 'undo', 'dismiss'])
def test_busy_intro_endpoint_reports_retryable_conflict(recovery, monkeypatch, action):
    from tests.unit.test_my_library_admin_intro import _call
    from cps.api import admin as api_admin
    admin = _user(recovery.session, 'admin', role=constants.ROLE_ADMIN)
    monkeypatch.setattr(api_admin, 'current_user', admin)
    endpoint = getattr(api_admin, 'admin_my_library_intro_' + action)
    with user_library._intro_operation(recovery.session):
        response, status = _call(endpoint, '/api/v1/admin/my-library/intro/' + action, 'POST', {})
    assert status == 409
    assert response.get_json()['error']['code'] == 'intro_busy'
    assert 'already running' in response.get_json()['error']['message']


def test_legacy_completed_snapshot_remains_undoable(recovery):
    import json
    user = _user(recovery.session, 'legacy-enabled', role=constants.ROLE_DOWNLOAD | constants.ROLE_BROWSE_GLOBAL, own_library=True)
    recovery.session.add(ub.MyLibraryAdminIntro(id=1, status='enabled', snapshot_json=json.dumps({str(user.id): {'browse_global': False, 'has_own_library': False}})))
    recovery.session.commit()
    state, results = user_library.enable_my_library_for_all(app_session=recovery.session, cdb=recovery.cdb)
    assert state['status'] == 'enabled' and results == []
    state, restored = user_library.undo_my_library_for_all(app_session=recovery.session)
    assert restored == 1 and state['status'] == 'not_enabled'
    assert not user.has_own_library and not user.role_browse_global()
