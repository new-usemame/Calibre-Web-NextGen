"""New accounts limit Kobo sync, while existing explicit choices survive (#1057)."""
import inspect
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub

pytestmark = pytest.mark.unit


@pytest.fixture
def session(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', s)
    yield s
    s.close()
    engine.dispose()


def test_new_model_accounts_limit_sync_without_changing_existing_choices(session):
    old = ub.User(name='existing', email='existing@example.test', kobo_only_shelves_sync=0)
    new = ub.User(name='new', email='new@example.test')
    session.add(old)
    session.commit()
    session.add(new)
    session.commit()
    session.expire_all()
    assert session.get(ub.User, old.id).kobo_only_shelves_sync == 0
    assert session.get(ub.User, new.id).kobo_only_shelves_sync == 1
    # Explicit whole-library choice still works for future accounts.
    explicit = ub.User(name='whole', email='whole@example.test', kobo_only_shelves_sync=0)
    session.add(explicit)
    session.commit()
    assert explicit.kobo_only_shelves_sync == 0


def test_new_ui_admin_creation_persists_shelf_only_default(session, monkeypatch):
    from cps.api import admin
    monkeypatch.setattr(admin, 'current_user', SimpleNamespace(
        is_authenticated=True, is_anonymous=False, role_admin=lambda: True))
    cfg = SimpleNamespace(config_default_role=1, config_default_locale='en',
        config_default_language='all', config_allowed_tags='', config_denied_tags='',
        config_allowed_column_value='', config_denied_column_value='',
        config_default_show=1, config_theme=1)
    monkeypatch.setattr(admin, 'config', cfg)
    # Name/password policy is outside this persistence test; use valid inputs.
    monkeypatch.setattr(admin, 'check_username', lambda value: value)
    monkeypatch.setattr(admin, 'valid_password', lambda value: value)
    app = Flask(__name__)
    with app.test_request_context('/api/v1/admin/users', method='POST', json={
            'name': 'created', 'password': 'Private-test-1057'}):
        response = inspect.unwrap(admin.admin_create_user)()
    assert response[1] == 201
    assert session.query(ub.User).filter_by(name='created').one().kobo_only_shelves_sync == 1


@pytest.mark.parametrize('path', ['proxy', 'oidc'])
def test_auto_provisioning_limits_new_accounts_but_preserves_existing_whole_library(session, monkeypatch, path):
    from cps import usermanagement, oauth_bb
    cfg = SimpleNamespace(config_default_role=1, config_default_locale='en',
        config_default_language='all', config_default_show=1, config_theme=1,
        config_enable_oauth_group_admin_management=False)
    if path == 'proxy':
        monkeypatch.setattr(usermanagement, 'config', cfg)
        new = usermanagement.create_authenticated_user('provisioned', 'provisioned@example.test', 'proxy')
    else:
        monkeypatch.setattr(oauth_bb, 'config', cfg)
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            'preferred_username': 'provisioned', 'sub': 'subject', 'email': 'provisioned@example.test'})
        provider = {'id': 3, 'blueprint': SimpleNamespace(session=SimpleNamespace(get=lambda *_a, **_k: response)),
                    'oauth_userinfo_url': 'https://idp.example.test/userinfo'}
        monkeypatch.setattr(oauth_bb, 'get_oauth_blueprints', lambda: [None, None, provider])
        monkeypatch.setattr(oauth_bb, 'current_user', None)
        monkeypatch.setattr(oauth_bb, 'bind_oauth_or_register', lambda *_a, **_k: None)
        monkeypatch.setattr(oauth_bb, 'flash', lambda *_a, **_k: None)
        monkeypatch.setattr(oauth_bb, 'url_for', lambda *_a, **_k: '/app')
        app = Flask(__name__)
        app.secret_key = 'private-test-key'
        with app.test_request_context('/'):
            oauth_bb.register_user_from_generic_oauth()
        new = session.query(ub.User).filter_by(name='provisioned').one()
    assert new is not None
    assert new.kobo_only_shelves_sync == 1
    new.kobo_only_shelves_sync = 0
    session.commit()
    if path == 'proxy':
        usermanagement.create_authenticated_user('provisioned', 'provisioned@example.test', 'proxy')
    else:
        with app.test_request_context('/'):
            oauth_bb.register_user_from_generic_oauth()
    session.expire_all()
    assert session.get(ub.User, new.id).kobo_only_shelves_sync == 0


@pytest.mark.parametrize('kobo_enabled, checked, expected', [(False, False, 1), (True, False, 0), (True, True, 1)])
def test_classic_admin_creation_honors_visible_choice_and_hidden_default(session, monkeypatch, kobo_enabled, checked, expected):
    from cps import admin
    cfg = SimpleNamespace(config_theme=1, config_public_reg=False,
        config_allowed_tags='', config_denied_tags='', config_allowed_column_value='', config_denied_column_value='')
    monkeypatch.setattr(admin, 'config', cfg)
    monkeypatch.setattr(admin, 'check_username', lambda value: value)
    monkeypatch.setattr(admin, 'check_email', lambda value: value)
    monkeypatch.setattr(admin.helper, 'valid_password', lambda value: value)
    monkeypatch.setattr(admin, 'flash', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, '_', lambda value, **kwargs: value % kwargs if kwargs else value)
    monkeypatch.setattr(admin, 'url_for', lambda *_a, **_k: '/admin')
    content = ub.User()
    data = {'name': 'classic', 'email': 'classic@example.test', 'password': 'Private-test-1057', 'default_language': 'all', 'locale': 'en'}
    if checked:
        data['kobo_only_shelves_sync'] = 'on'
    with Flask(__name__).test_request_context('/admin/new', method='POST'):
        response = admin._handle_new_user(data, content, [], [], kobo_enabled)
    assert response.status_code == 302
    session.expire_all()
    assert session.query(ub.User).filter_by(name='classic').one().kobo_only_shelves_sync == expected
