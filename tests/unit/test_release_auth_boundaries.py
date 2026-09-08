# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent checks at the LDAP and shared-resource authorization seams."""
import pytest
from cps import constants, ub
from tests.unit.test_spa_ldap_login import ldap_login
from tests.unit.test_shared_book_continuation import shared_books

pytestmark = pytest.mark.unit


def test_cross_site_ldap_login_is_rejected_before_binding(ldap_login):
    h = ldap_login
    response = h.client.post('/api/v1/auth/login', json={'username':'Reader','password':'directory-password'}, headers={'Origin':'https://untrusted.example'})
    assert response.status_code == 403
    h.directory.bind_user.assert_not_called()
    assert h.session.query(ub.User).count() == 0


def test_ldap_login_requires_csrf_and_valid_token_still_works(ldap_login):
    from flask_wtf.csrf import CSRFProtect
    h = ldap_login
    app = h.client.application
    app.config['WTF_CSRF_ENABLED'] = True
    CSRFProtect(app)
    payload = {'username':'Reader','password':'directory-password'}
    assert h.client.post('/api/v1/auth/login', json=payload).status_code == 400
    h.directory.bind_user.assert_not_called()
    token = h.client.get('/api/v1/auth/csrf').json['csrf_token']
    response = h.client.post('/api/v1/auth/login', json=payload, headers={'X-CSRFToken':token,'Origin':'http://localhost'})
    assert response.status_code == 200
    assert h.session.query(ub.User).one().role == constants.ROLE_DOWNLOAD


def test_other_users_success_cannot_reset_targets_failed_login_bucket(ldap_login):
    h = ldap_login
    h.existing()
    h.session.add(ub.User(name='Other',email='other@example.invalid',password='',role=0))
    h.session.commit()
    assert [h.login('wrong').status_code for _ in range(3)] == [401]*3
    other = h.client.post('/api/v1/auth/login',json={'username':'Other','password':'directory-password'})
    assert other.status_code == 200
    assert h.login('wrong').status_code == 429


def test_shared_book_never_returns_owners_private_bookmark(shared_books):
    h = shared_books
    h.session.add(ub.Bookmark(user_id=h.owner.id,book_id=1,format='epub',bookmark_key='OWNER-PRIVATE-LOCATION'))
    h.session.commit()
    response = h.client.get('/api/v1/books/1/bookmark')
    assert response.status_code == 200
    assert b'OWNER-PRIVATE-LOCATION' not in response.data
    assert h.client.post('/api/v1/books/1/bookmark',json={'format':'epub','bookmark':'VIEWER-LOCATION'}).status_code == 204
    assert h.session.query(ub.Bookmark).filter_by(user_id=h.owner.id,book_id=1).one().bookmark_key == 'OWNER-PRIVATE-LOCATION'
    assert h.client.get('/api/v1/books/1/bookmark').json['bookmark'] == 'VIEWER-LOCATION'


def test_native_kobo_download_does_not_inherit_public_shelf_permission(shared_books, monkeypatch):
    import inspect
    from cps import kobo, helper
    h = shared_books
    # Invoke the actual native download handler after its authentication/role
    # decorators, so this probe specifically checks its resource-policy seam.
    monkeypatch.setattr(kobo, 'get_download_link', helper.get_download_link)
    endpoint = inspect.unwrap(kobo.download_book)
    h.app.add_url_rule('/native-download/<book_id>/<book_format>', 'native_download', endpoint)
    assert h.client.get('/download/1/epub').data == b'epub-bytes'
    denied = h.client.get('/native-download/1/epub')
    assert denied.status_code == 404
    assert b'epub-bytes' not in denied.data
    assert h.session.query(ub.UserLibraryBook).count() == 0


def test_shared_nonmember_annotation_archive_and_export_remain_private(shared_books, monkeypatch):
    import inspect
    from cps import annotations
    h = shared_books
    monkeypatch.setattr(annotations, 'current_user', h.viewer)
    monkeypatch.setattr(annotations, 'calibre_db', h.cdb)
    for suffix, handler in [('data.json', annotations.annotations_data),
                            ('export.json', annotations.annotations_export_json)]:
        h.app.add_url_rule('/annotations/<int:book_id>/' + suffix, suffix, inspect.unwrap(handler))
    for user, text in [(h.owner, 'OWNER-PRIVATE-HIGHLIGHT'), (h.viewer, 'VIEWER-HIGHLIGHT')]:
        h.session.add(ub.Annotation(user_id=user.id, book_id=1, annotation_id='highlight-' + str(user.id),
            source='webreader', highlighted_text=text, cfi_range='epubcfi(/6/2)', position_type='cfi'))
    h.session.commit()
    for suffix in ['data.json', 'export.json']:
        response = h.client.get('/annotations/1/' + suffix)
        assert response.status_code == 200, response.data
        assert b'VIEWER-HIGHLIGHT' in response.data
        assert b'OWNER-PRIVATE-HIGHLIGHT' not in response.data
    assert h.session.query(ub.UserLibraryBook).count() == 0
    h.viewer.denied_tags = 'tag-1'
    h.session.commit()
    for suffix in ['data.json', 'export.json']:
        assert h.client.get('/annotations/1/' + suffix).status_code == 404
