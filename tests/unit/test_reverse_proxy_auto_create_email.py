# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Proxy provisioning and account isolation with real validation and SQLite.

Profile email is editable without ownership verification. It must never select
an account for proxy authentication, even when automatic provisioning is on.
"""
from types import SimpleNamespace

import pytest
from flask import request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

from cps import app, ub, usermanagement
from cps.api import account
from cps.helper import valid_email

pytestmark = pytest.mark.unit
IDENTITY_HEADER = 'Cf-Access-Authenticated-User-Email'


@pytest.fixture
def database(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(usermanagement, 'config', SimpleNamespace(
        config_reverse_proxy_login_header_name=IDENTITY_HEADER,
        config_reverse_proxy_auto_create_users=True,
        config_default_role=1, config_default_show=1, config_default_locale='en',
        config_default_language='all', config_theme=1,
    ))
    yield session
    session.close()
    engine.dispose()


def proxy_login(identity, **headers):
    with app.test_request_context('/', headers={IDENTITY_HEADER: identity, **headers}):
        return usermanagement.load_user_from_reverse_proxy_header(request)


@pytest.mark.parametrize('auto_create', [False, True])
def test_unverified_profile_email_does_not_become_someone_elses_proxy_identity(database, monkeypatch, auto_create):
    """A profile edit must not redirect another person's trusted proxy login.

    Reintroducing email-based linking returns the attacker's persisted account
    here. Exercise the actual profile mutation before the proxy resolver.
    """
    attacker = ub.User(name='attacker', email='attacker@example.test',
                       password=generate_password_hash('attacker-local-password'))
    database.add(attacker)
    database.commit()
    monkeypatch.setattr(account, 'current_user', attacker)
    monkeypatch.setattr(account, '_serialize_account', lambda: {'email': attacker.email})
    with app.test_request_context('/api/v1/account/profile', method='POST',
                                  json={'email': 'victim@example.test'}):
        response = account.update_profile()
    assert response.status_code == 200
    database.expire_all()
    assert database.get(ub.User, attacker.id).email == 'victim@example.test'
    assert check_password_hash(attacker.password, 'attacker-local-password')

    usermanagement.config.config_reverse_proxy_auto_create_users = auto_create
    authenticated = proxy_login('victim@example.test')
    assert authenticated is not attacker, 'Unverified profile email selected the attacker account'
    if auto_create:
        assert authenticated is not None
        assert authenticated.name == 'victim@example.test'
        assert authenticated.email == 'victim.example.test@localhost'
        assert valid_email(authenticated.email) == authenticated.email
        assert database.query(ub.User).count() == 2
    else:
        assert authenticated is None
        assert database.query(ub.User).count() == 1


def test_email_username_produces_a_profile_valid_email(database):
    created = proxy_login('reader@example.test')
    assert created is not None
    assert created.email == 'reader@example.test'
    assert valid_email(created.email) == created.email
    database.expire_all()
    assert database.get(ub.User, created.id).email == 'reader@example.test'


@pytest.mark.parametrize('identity, expected', [
    ('reader@example.test', 'reader@example.test'),
    ('reader', 'reader@localhost'),
])
@pytest.mark.parametrize('email_header', ['Remote-Email', 'X-Remote-Email'])
def test_proxy_does_not_store_or_match_untrusted_separate_email_headers(database, identity, expected, email_header):
    """Client-supplied email headers neither select an account nor populate one."""
    owner = ub.User(name='owner', email='someone-else@example.test')
    database.add(owner)
    database.commit()
    created = proxy_login(identity, **{email_header: owner.email})
    assert created is not None
    assert created.id != owner.id
    assert created.name == identity
    assert created.email == expected
    assert database.query(ub.User).count() == 2


def test_existing_username_still_takes_precedence(database):
    existing = ub.User(name='Reader@Example.test', email='other@example.test')
    database.add(existing)
    database.commit()
    authenticated = proxy_login('reader@example.test', **{'Remote-Email': 'ignored@example.test'})
    assert authenticated.id == existing.id
    assert authenticated.email == 'other@example.test'
    assert database.query(ub.User).count() == 1


def test_taken_email_is_not_linked_and_uses_a_valid_placeholder(database):
    existing = ub.User(name='rob', email='Rob@Example.test')
    database.add(existing)
    database.commit()
    created = proxy_login('rob@example.test')
    assert created is not None
    assert created.id != existing.id
    assert created.email == 'rob.example.test@localhost'
    assert valid_email(created.email) == created.email


@pytest.mark.parametrize('offered', ['trusted@example.test', None, '', 'invalid', 'a@b@example.test',
                                    'a@example.test,b@example.test', 'taken@example.test'])
def test_trusted_caller_email_is_validated_and_deduplicated(database, offered):
    """Keep the positional email argument for existing non-proxy callers."""
    database.add(ub.User(name='existing', email='TAKEN@example.test'))
    database.commit()
    created = usermanagement.create_authenticated_user('reader@example.test', offered, 'trusted caller')
    assert created is not None
    expected = offered if offered == 'trusted@example.test' else 'reader@example.test'
    assert created.email == expected
    assert valid_email(created.email) == expected


def test_production_spa_requests_provision_a_separate_account_for_an_email_collision(database, monkeypatch, tmp_path):
    """The real factory and HTTP route must honor username-only proxy identity.

    Startup services are stubbed by the existing factory harness; provisioning,
    account queries and the request authentication hook use the real code/DB.
    """
    from tests.unit.test_1931_spa_reverse_proxy_auth import _factory_app

    create_user = usermanagement.create_authenticated_user
    world = _factory_app(monkeypatch, tmp_path)
    monkeypatch.setattr(ub, 'session', database)
    monkeypatch.setattr(usermanagement, 'create_authenticated_user', create_user)
    world.config.config_reverse_proxy_login_header_name = IDENTITY_HEADER
    world.config.config_reverse_proxy_auto_create_users = True
    database.add(ub.User(name='existing', email='reader@example.test'))
    database.commit()

    client = world.app.test_client()
    for _ in range(2):
        response = client.get('/api/v1/auth/me', headers={IDENTITY_HEADER: 'reader@example.test'})
        assert response.status_code == 200
        assert response.get_json()['name'] == 'reader@example.test'
        assert response.headers['X-Test-Authenticated-As'] == 'reader@example.test'
    assert database.query(ub.User).count() == 2
    assert database.query(ub.User).filter_by(name='reader@example.test').one().email == 'reader.example.test@localhost'
