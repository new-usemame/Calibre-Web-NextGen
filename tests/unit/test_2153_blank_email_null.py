"""A blank email is stored as NULL so any number of accounts can omit it (#2153)."""
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
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


def test_blank_emails_are_stored_as_null_and_do_not_collide(session):
    for name, blank in (('a', ''), ('b', '   '), ('c', None), ('d', '')):
        session.add(ub.User(name=name, email=blank))
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM user WHERE email IS NULL")).scalar() == 4
    assert session.execute(text("SELECT COUNT(*) FROM user WHERE email = ''")).scalar() == 0


def test_real_duplicate_email_still_conflicts(session):
    session.add(ub.User(name='a', email='same@example.test'))
    session.commit()
    session.add(ub.User(name='b', email='same@example.test'))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_check_email_ignores_blank_but_still_rejects_taken_address(session):
    from cps import helper
    session.add_all([ub.User(name='a', email=None), ub.User(name='b', email='taken@example.test')])
    session.commit()
    assert helper.check_email('') == ''
    assert helper.check_email('   ') == ''
    with pytest.raises(Exception):
        helper.check_email('TAKEN@example.test')


def test_migration_converts_the_legacy_blank_row(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    # What an affected install holds: exactly one account with email = ''.
    s.execute(text("INSERT INTO user (name, email) VALUES ('legacy', '')"))
    s.commit()
    ub.migrate_user_table(engine, s)
    assert s.execute(text("SELECT email FROM user WHERE name = 'legacy'")).scalar() is None
    s.add(ub.User(name='new', email=''))
    s.commit()
    assert s.query(ub.User).filter(ub.User.email.is_(None)).count() == 2
    ub.migrate_user_table(engine, s)  # idempotent
    assert s.query(ub.User).filter(ub.User.email.is_(None)).count() == 2
    assert s.execute(text("SELECT COUNT(*) FROM user WHERE email = ''")).scalar() == 0
    s.close()
    engine.dispose()


def test_conflict_message_names_the_colliding_field():
    from cps.api import admin
    email = SimpleNamespace(orig=Exception("UNIQUE constraint failed: user.email"))
    name = SimpleNamespace(orig=Exception("UNIQUE constraint failed: user.name"))
    assert 'email' in admin._conflict_message(email)
    assert 'username' in admin._conflict_message(name)
    assert 'email or username' in admin._conflict_message(Exception('other'))


def test_new_ui_admin_creates_two_users_without_email(session, monkeypatch):
    from cps.api import admin
    monkeypatch.setattr(admin, 'current_user', SimpleNamespace(
        is_authenticated=True, is_anonymous=False, role_admin=lambda: True))
    cfg = SimpleNamespace(config_default_role=1, config_default_locale='en',
        config_default_language='all', config_allowed_tags='', config_denied_tags='',
        config_allowed_column_value='', config_denied_column_value='',
        config_default_show=1, config_theme=1)
    monkeypatch.setattr(admin, 'config', cfg)
    monkeypatch.setattr(admin, 'valid_password', lambda value: value)
    # The real application object, not a private Flask(...) (fork #2546 ratchet).
    from cps import app
    for name in ('test1', 'test2'):
        with app.test_request_context('/api/v1/admin/users', method='POST', json={
                'name': name, 'password': 'Private-test-2153', 'email': ''}):
            response = inspect.unwrap(admin.admin_create_user)()
        assert response[1] == 201, response[0].get_json()
    assert session.query(ub.User).filter(ub.User.email.is_(None)).count() == 2
