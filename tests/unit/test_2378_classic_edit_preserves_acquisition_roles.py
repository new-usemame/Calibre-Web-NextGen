# SPDX-License-Identifier: GPL-3.0-or-later
"""A classic admin edit must not revoke grants its form cannot draw.

The acquisition grants (bits 11/12) are administered on the Book sources page.
`user_edit.html` has no checkbox for them, so a form that posts only the boxes
it renders used to rebuild the whole mask and silently clear them -- on an edit
as innocuous as a change of email address.
"""
import inspect
from types import SimpleNamespace

import pytest

from cps import constants


ACCESS = constants.ROLE_ACQUISITION_ACCESS
APPROVE = constants.ROLE_ACQUISITION_AUTO_APPROVE


@pytest.mark.unit
def test_classic_form_without_the_checkbox_keeps_a_grant_it_cannot_express():
    # What user_edit.html actually posts for a reader with download rights.
    posted = {'email': 'reader@example.invalid', 'download_role': 'on'}
    current = constants.ROLE_DOWNLOAD | ACCESS | APPROVE
    result = constants.selected_roles(posted) | constants.preserved_roles(posted, current)
    assert result & ACCESS and result & APPROVE
    assert result & constants.ROLE_DOWNLOAD


@pytest.mark.unit
def test_a_grant_the_form_never_held_is_not_invented():
    posted = {'email': 'reader@example.invalid'}
    assert constants.preserved_roles(posted, constants.ROLE_DOWNLOAD) == 0
    assert constants.preserved_roles(posted, None) == 0


@pytest.mark.unit
@pytest.mark.parametrize('key,bit', [
    ('acquisition_access_role', ACCESS),
    ('acquisition_auto_approve_role', APPROVE),
])
def test_a_form_that_does_carry_the_key_stays_authoritative(key, bit):
    """Adding the checkbox to the template later must start working on its own.

    Once the form carries the key, unticking it has to revoke -- otherwise the
    new checkbox would be one-way.
    """
    assert constants.selected_roles({key: 'on'}) & bit == bit
    assert constants.preserved_roles({key: 'on'}, 0) & bit == 0
    # An unticked box that still posts its key must not be re-granted.
    assert constants.preserved_roles({key: ''}, bit) & bit == 0


@pytest.mark.unit
def test_classic_edit_of_only_an_email_does_not_revoke_book_source_access(monkeypatch):
    """The reviewer's actual scenario, driven through the real handler."""
    import cps.admin as admin
    from cps import app

    class QueryResult:
        def filter(self, *_a, **_k): return self
        def all(self): return []
        def count(self): return 1
        def delete(self): return 0

    class SessionStub:
        def query(self, *_e): return QueryResult()
        def add(self, _row): return None
        def delete(self, _row): return None
        def commit(self): return None
        def rollback(self): return None

    user = SimpleNamespace(
        id=7, name='reader', email='reader@example.invalid', password='x',
        kindle_mail='', kindle_mail_subject='', default_language='all',
        locale='en', random_books=0, sidebar_view=0,
        role=constants.ROLE_DOWNLOAD | ACCESS | APPROVE,
        kobo_only_shelves_sync=0, opds_only_shelves_sync=0,
        kobo_two_way_annotation_sync=0, hardcover_token=None,
        auto_send_enabled=False, auto_metadata_fetch=False,
        allow_additional_ereader_emails=False, view_settings={},
        is_anonymous=False,
        role_passwd=lambda: False, role_admin=lambda: False,
        role_anonymous=lambda: False,
        check_visibility=lambda _v: False,
    )

    monkeypatch.setattr(admin.ub, 'session', SessionStub())
    monkeypatch.setattr(admin.ub, 'session_commit', lambda: None)
    monkeypatch.setattr(admin, 'get_sidebar_config', lambda: ([], None))
    monkeypatch.setattr(admin, 'valid_email', lambda value: value)
    monkeypatch.setattr(admin, 'check_email', lambda value, _user_id=None: value)
    monkeypatch.setattr(admin, 'flag_modified', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, 'flash', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, '_', lambda value, **kw: value % kw if kw else value)
    monkeypatch.setattr(admin.kobo_sync_status, 'update_on_sync_shelfs',
                        lambda *_a, **_k: None)

    with app.test_request_context('/admin/user/7', method='POST'):
        admin._handle_edit_user(
            {'email': 'new@example.invalid', 'kindle_mail': '',
             'kindle_mail_subject': '', 'download_role': 'on'},
            user, [], [], False)

    assert user.email == 'new@example.invalid', 'the edit itself must still apply'
    assert user.role & ACCESS, 'book-source access was revoked by an unrelated edit'
    assert user.role & APPROVE, 'auto-approve was revoked by an unrelated edit'


@pytest.mark.unit
def test_classic_default_role_edit_keeps_book_source_grants(monkeypatch):
    """The classic site-default form cannot draw the migrated grant bits either."""
    import cps.admin as admin
    from cps import app

    saved = []
    settings = SimpleNamespace(
        config_default_role=constants.ROLE_DOWNLOAD | ACCESS | APPROVE,
        config_default_show=0,
        save=lambda: saved.append(True),
    )
    monkeypatch.setattr(admin, 'config', settings)
    monkeypatch.setattr(admin, '_config_string', lambda *_a, **_k: False)
    monkeypatch.setattr(admin, '_config_int', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, 'persist_configured_columns', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, 'load_eligible_columns', lambda: ())
    monkeypatch.setattr(admin, 'check_valid_read_column', lambda *_a: True)
    monkeypatch.setattr(admin, 'check_valid_restricted_column', lambda *_a: True)
    monkeypatch.setattr(admin, 'flash', lambda *_a, **_k: None)
    monkeypatch.setattr(admin, '_', lambda value, **kw: value % kw if kw else value)
    monkeypatch.setattr(admin, 'before_request', lambda: None)
    monkeypatch.setattr(admin, 'view_configuration', lambda: 'saved')

    handler = inspect.unwrap(admin.update_view_configuration)
    with app.test_request_context('/admin/viewconfig', method='POST', data={
            'config_read_column': '0', 'config_restricted_column': '0',
            'download_role': 'on'}):
        assert handler() == 'saved'

    assert saved == [True]
    assert settings.config_default_role & constants.ROLE_DOWNLOAD
    assert settings.config_default_role & ACCESS
    assert settings.config_default_role & APPROVE
