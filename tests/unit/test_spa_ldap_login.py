# SPDX-License-Identifier: GPL-3.0-or-later
"""LDAP sign-in across the real SPA route, provisioning, session and limiter.

Only the external directory and activity-log sink are substituted. Real SQLite
rows and cookie sessions ensure a mocked provisioning symbol cannot hide an
incorrect import; repeated requests discriminate failure-only throttling from
counting legitimate sign-ins.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_babel import Babel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import config, constants, limiter, services, ub
from cps.api import api_v1
from cps.cw_login import LoginManager

pytestmark = pytest.mark.unit


@pytest.fixture
def ldap_login(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', session)
    settings = {
        'config_login_type': constants.LOGIN_LDAP,
        'config_disable_standard_login': False,
        'config_ldap_auto_create_users': True,
        'config_ldap_user_object': '(uid=%s)',
        'config_default_language': 'all', 'config_default_locale': 'en',
        'config_default_role': constants.ROLE_DOWNLOAD,
        'config_default_show': 0, 'config_allowed_tags': '',
        'config_denied_tags': '', 'config_allowed_column_value': '',
        'config_denied_column_value': '', 'config_theme': 1,
    }
    for key, value in settings.items():
        monkeypatch.setattr(config, key, value, raising=False)
    directory = Mock()
    directory.bind_user.side_effect = lambda name, password: (password == 'directory-password', None)
    directory.get_object_details.return_value = {
        'uid': [b'Reader'], 'mail': [b'reader@example.org'],
    }
    monkeypatch.setattr(services, 'ldap', directory)
    monkeypatch.setattr('cps.cw_login.utils.CWA_DB', Mock())
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='ldap-regression',
                      WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=True,
                      RATELIMIT_STORAGE_URI='memory://')
    Babel(app)
    manager = LoginManager(app)
    manager.user_loader(lambda user_id, user_random, session_key: session.get(ub.User, int(user_id)))
    limiter.init_app(app)
    with app.app_context():
        limiter.reset()
    app.register_blueprint(api_v1)
    client = app.test_client()
    def login(password='directory-password'):
        return client.post('/api/v1/auth/login', json={
            'username': '  READER  ', 'password': password, 'remember': True,
        })
    def existing():
        user = ub.User(name='Reader', email='reader@example.org',
                       password=generate_password_hash('local-password'),
                       role=constants.ROLE_DOWNLOAD)
        session.add(user)
        session.commit()
        return user
    yield SimpleNamespace(client=client, login=login, existing=existing,
                          session=session, directory=directory)
    session.close()
    engine.dispose()


@pytest.mark.parametrize('already_exists', [False, True])
def test_ldap_success_provisions_once_authenticates_and_resets_failure_buckets(
        ldap_login, already_exists):
    """A successful bind clears both windows, including after provisioning.

    Two bad attempts before and after the first good login catch a missing
    reset on the new-account exit independently of the existing-account exit.
    More than forty good sign-ins then catch a retained daily failure bucket.
    """
    h = ldap_login
    if already_exists:
        h.existing()
    assert [h.login('wrong').status_code for _ in range(2)] == [401, 401]
    response = h.login()
    assert response.status_code == 200, response.get_json()
    user = h.session.query(ub.User).one()
    assert (user.name, user.email, user.role) == (
        'Reader', 'reader@example.org', constants.ROLE_DOWNLOAD)
    assert response.get_json()['id'] == user.id
    with h.client.session_transaction() as cookie:
        assert int(cookie['_user_id']) == user.id
    assert h.client.get('/api/v1/auth/me').get_json()['id'] == user.id
    assert [h.login('wrong').status_code for _ in range(2)] == [401, 401]
    assert [h.login().status_code for _ in range(42)] == [200] * 42
    assert h.session.query(ub.User).count() == 1
    assert h.directory.get_object_details.call_count == (0 if already_exists else 1)
    assert [h.login('local-password').status_code for _ in range(4)] == [401, 401, 401, 429]


@pytest.mark.parametrize('failure', ['bad_password', 'directory_error', 'missing_details', 'invalid_details', 'email_conflict'])
def test_rejected_directory_provisioning_never_creates_or_authenticates_account(ldap_login, failure):
    h = ldap_login
    password = 'directory-password'
    if failure == 'bad_password':
        password = 'wrong'
    elif failure == 'directory_error':
        h.directory.bind_user.side_effect = RuntimeError('directory unavailable')
    elif failure == 'missing_details':
        h.directory.get_object_details.return_value = None
    elif failure == 'invalid_details':
        h.directory.get_object_details.return_value = {'mail': [b'reader@example.org']}
    else:
        h.session.add(ub.User(name='Other', email='reader@example.org', password='', role=0))
        h.session.commit()
    response = h.login(password)
    assert response.status_code == 401
    assert response.get_json()['error']['code'] == 'invalid_credentials'
    assert h.session.query(ub.User).filter(ub.User.name == 'Reader').count() == 0
    with h.client.session_transaction() as cookie:
        assert '_user_id' not in cookie


@pytest.mark.parametrize('setting', ['auto_create_disabled', 'standard_login_disabled', 'standard_mode'])
def test_directory_login_respects_instance_switches(ldap_login, monkeypatch, setting):
    h = ldap_login
    if setting == 'auto_create_disabled':
        monkeypatch.setattr(config, 'config_ldap_auto_create_users', False)
        assert h.login().status_code == 401
        h.directory.bind_user.assert_not_called()
        h.directory.get_object_details.assert_not_called()
        assert h.session.query(ub.User).count() == 0
        h.existing()
        assert h.login().status_code == 200  # the switch only disables creation
    elif setting == 'standard_login_disabled':
        monkeypatch.setattr(config, 'config_disable_standard_login', True)
        assert h.login().status_code == 403
        h.directory.bind_user.assert_not_called()
        assert h.session.query(ub.User).count() == 0
    else:
        monkeypatch.setattr(config, 'config_login_type', constants.LOGIN_STANDARD)
        h.existing()
        assert h.login('local-password').status_code == 200
        h.directory.bind_user.assert_not_called()
