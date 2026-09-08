# SPDX-License-Identifier: GPL-3.0-or-later
"""A shared shelf is a usable book entry point without granting membership."""
import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask, Response
from flask_babel import Babel
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import NotFound

from cps import constants, db, ub

pytestmark = pytest.mark.unit


@pytest.fixture
def shared_books(monkeypatch, tmp_path):
    from cps import helper, opds, shelf, web
    from cps.api import books, shelves, reader

    engine = create_engine('sqlite://')
    event.listen(engine, 'connect', lambda conn, _: conn.execute(
        "ATTACH DATABASE ':memory:' AS calibre"))
    event.listen(engine, 'connect', db._register_sqlite_udfs)
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    viewer = ub.User(name='viewer', email='viewer@example.invalid', password='',
                     role=constants.ROLE_DOWNLOAD | constants.ROLE_VIEWER,
                     default_language='all', has_own_library=True,
                     user_library_seeded=True)
    owner = ub.User(name='owner', email='owner@example.invalid', password='')
    session.add_all([viewer, owner]); session.flush()
    public = ub.Shelf(name='Shared', user_id=owner.id, is_public=1)
    private = ub.Shelf(name='Private', user_id=owner.id, is_public=0)
    session.add_all([public, private]); session.flush()
    for book_id, target in ((1, public), (2, private), (3, None)):
        now = datetime(2026, 1, 1)
        book = db.Books('Book %s' % book_id, 'Book', 'Author', now, now,
                        '1.0', now, 'book-%s' % book_id, 1, [], [])
        book.id = book_id
        book.uuid = 'book-%s' % book_id
        book.tags.append(db.Tags('tag-%s' % book_id))
        if book_id < 3:
            book.languages.append(db.Languages('eng' if book_id == 1 else 'deu'))
        book.data.append(db.Data(book_id, 'EPUB', 12, 'book'))
        session.add(book)
        if target:
            link = ub.BookShelf(shelf=target.id, book_id=book_id, order=1)
            link.ub_shelf = target
            session.add(link)
    session.commit()
    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_restricted_column=0,
        config_books_per_page=24, config_random_books=0)
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(db, 'current_user', viewer)
    for module in (books, shelves, helper, shelf, web):
        monkeypatch.setattr(module, 'current_user', viewer)
        monkeypatch.setattr(module, 'calibre_db', cdb)
    monkeypatch.setattr(reader, 'current_user', viewer)
    monkeypatch.setattr(opds, 'calibre_db', cdb)
    monkeypatch.setattr(opds.auth, 'current_user', lambda: viewer)
    for module in (books, shelves):
        monkeypatch.setattr(module, 'config', SimpleNamespace(config_read_column=0, config_books_per_page=24))
    monkeypatch.setattr(helper, 'config', SimpleNamespace(config_unicode_filename=False))
    monkeypatch.setattr(web, 'config', SimpleNamespace(config_use_google_drive=False, get_book_path=lambda: str(tmp_path)))
    book_dir = tmp_path / 'book-1'
    book_dir.mkdir()
    (book_dir / 'book.pdf').write_bytes(b'%PDF-shared-reader-bytes')
    session.add(db.Data(1, 'PDF', 24, 'book'))
    session.commit()
    monkeypatch.setattr(books, '_detail_custom_columns', lambda: [])
    monkeypatch.setattr(books, 'get_convert_options', lambda _: ([], []))
    monkeypatch.setattr(helper, 'get_book_cover_internal', lambda book, resolution=None:
        Response(b'cover-bytes' if book else b'missing', status=200 if book else 404))
    monkeypatch.setattr(helper, 'do_download_file', lambda *a, **kw: Response(b'epub-bytes'))
    monkeypatch.setattr(ub, 'update_download', lambda *_: None)
    monkeypatch.setattr(helper, 'CWA_DB', lambda: SimpleNamespace(log_activity=lambda **_: None))
    app = Flask(__name__)
    Babel(app)
    app.secret_key = 'isolated-test'
    app.add_url_rule('/api/v1/shelves/<int:shelf_id>', 'shelf_detail', inspect.unwrap(shelves.shelf_detail))
    app.add_url_rule('/api/v1/books/<int:book_id>', 'book_detail', inspect.unwrap(books.book_detail))
    app.add_url_rule('/cover/<int:book_id>/<resolution>', 'cover',
        lambda book_id, resolution: helper.get_book_cover(book_id))
    app.add_url_rule('/download/<int:book_id>/<book_format>', 'download',
        lambda book_id, book_format: web.download_required(inspect.unwrap(web.download_link))(book_id, book_format, None))
    app.add_url_rule('/opds/download/<book_id>/<book_format>/', 'opds_download', inspect.unwrap(opds.opds_download_link))
    app.add_url_rule('/show/<int:book_id>/<book_format>', 'show',
        lambda book_id, book_format: web.viewer_required(inspect.unwrap(web.serve_book))(book_id, book_format, None))
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'get_bookmark', inspect.unwrap(reader.get_bookmark), methods=['GET'])
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark', 'save_bookmark', inspect.unwrap(reader.save_bookmark), methods=['POST'])
    yield SimpleNamespace(session=session, viewer=viewer, owner=owner, public=public,
        private=private, client=app.test_client(), app=app, cdb=cdb, opds=opds)
    session.close(); engine.dispose()


@pytest.mark.parametrize('selected', [False, True])
def test_shared_shelf_entry_opens_cover_detail_and_acquisition_without_membership(shared_books, selected):
    """F-36b518: follow real API list URLs through filtered HTTP handlers."""
    env = shared_books
    env.viewer.opds_only_shelves_sync = selected
    env.session.add(ub.OpdsShelfExposure(user_id=env.viewer.id, shelf_id=env.public.id))
    env.session.commit()
    listing = env.client.get('/api/v1/shelves/%s' % env.public.id)
    assert listing.status_code == 200
    item, = listing.json['items']
    detail = env.client.get('/api/v1/books/%s' % item['id'])
    assert detail.status_code == 200, detail.data
    assert detail.json['in_my_library'] is False
    assert detail.json['accessible_via_public_shelf'] is True
    assert env.client.get(item['cover_url']).data == b'cover-bytes'
    assert env.client.get('/download/1/epub').data == b'epub-bytes'
    assert env.client.get('/opds/download/1/epub/').data == b'epub-bytes'
    assert env.client.get('/show/1/pdf').data == b'%PDF-shared-reader-bytes'
    saved = env.client.post('/api/v1/books/1/bookmark', json={'bookmark': 'epubcfi(/6/8)', 'format':'epub'})
    assert saved.status_code == 204
    assert env.client.get('/api/v1/books/1/bookmark').json['bookmark'] == 'epubcfi(/6/8)'
    assert env.session.query(ub.Bookmark).one().user_id == env.viewer.id
    assert env.session.query(ub.UserLibraryBook).count() == 0
    # Human sharing must not expand the native device entitlement policy.
    with env.app.test_request_context('/'):
        assert env.cdb.get_filtered_book(1) is None
        assert env.cdb.get_book_by_uuid_for_kobo('book-1', enforce_policy=True) is None
        from cps import helper
        with pytest.raises(NotFound):
            helper.get_download_link(1, 'epub', 'kobo')


@pytest.mark.parametrize('restriction', ['private', 'unshared', 'denied_tag', 'language', 'restricted_column_missing', 'unexposed_opds'])
def test_shared_access_never_bypasses_private_content_or_opds_exposure(shared_books, restriction):
    env = shared_books
    book_id = 1
    if restriction == 'private': book_id = 2
    elif restriction == 'unshared': book_id = 3
    elif restriction == 'denied_tag': env.viewer.denied_tags = 'tag-1'
    elif restriction == 'language': env.viewer.default_language = 'deu'
    elif restriction == 'restricted_column_missing':
        env.cdb.config.config_restricted_column = 999
        env.viewer.allowed_column_value = 'permitted'
    else: env.viewer.opds_only_shelves_sync = True
    env.session.commit()
    if restriction != 'unexposed_opds':
        assert env.client.get('/api/v1/books/%s' % book_id).status_code == 404
        assert env.client.get('/cover/%s/sm' % book_id).status_code == 404
        assert env.client.get('/download/%s/epub' % book_id).status_code == 404
    assert env.client.get('/opds/download/%s/epub/' % book_id).status_code == 404
    assert env.session.query(ub.UserLibraryBook).count() == 0


def test_public_to_private_revokes_continuation_immediately(shared_books):
    env = shared_books
    assert env.client.get('/download/1/epub').status_code == 200
    env.public.is_public = 0
    env.session.commit()
    assert env.client.get('/api/v1/books/1').status_code == 404
    assert env.client.get('/cover/1/sm').status_code == 404
    assert env.client.get('/download/1/epub').status_code == 404


def test_warm_smart_shelf_tracks_membership_mode_and_policy_without_replaying_noops(shared_books, monkeypatch):
    """F-7d3396: real rule/cache results follow writes across fresh requests."""
    from cps import calibre_db, magic_shelf, user_library
    env = shared_books
    viewer = env.viewer
    viewer.role |= constants.ROLE_BROWSE_GLOBAL
    magic = ub.MagicShelf(name='All books', user_id=viewer.id,
        rules={'condition':'AND', 'rules':[{'id':'title','operator':'contains','value':'Book'}]})
    env.session.add_all([magic, ub.UserLibraryBook(user_id=viewer.id, book_id=1)])
    env.session.commit()
    monkeypatch.setattr(magic_shelf, 'current_user', viewer)
    monkeypatch.setattr(calibre_db, '_desktop_compat', False)
    monkeypatch.setattr(db, 'CalibreDB', lambda *a, **kw: env.cdb)

    def visible():
        with env.app.test_request_context('/'):
            books, total = magic_shelf.get_books_for_magic_shelf(magic.id, raise_on_error=True)
            ids = sorted(book.id for book in books)
            assert total == len(ids)
            return ids

    def generation():
        return env.session.query(ub.MagicShelfCache).filter_by(shelf_id=magic.id).one().created_at

    assert visible() == [1]
    first_generation = generation()
    assert visible() == [1]
    assert generation() == first_generation
    user_library.remove_book(viewer, 1, app_session=env.session)
    assert visible() == []
    user_library.add_book(viewer, 1, app_session=env.session, cdb=env.cdb)
    assert visible() == [1]
    restored_generation = generation()
    assert restored_generation > first_generation
    user_library.add_book(viewer, 1, app_session=env.session, cdb=env.cdb)
    assert visible() == [1]
    assert generation() == restored_generation
    user_library.set_library_mode(viewer, constants.LIBRARY_MODE_MONOLIBRARY,
        app_session=env.session, cdb=env.cdb)
    assert visible() == [1, 2, 3]
    user_library.set_library_mode(viewer, constants.LIBRARY_MODE_PERSONAL,
        app_session=env.session, cdb=env.cdb)
    assert visible() == [1]
    viewer.denied_tags = 'tag-1'
    env.session.commit()
    assert visible() == []
    viewer.denied_tags = ''
    env.session.commit()
    assert visible() == [1]
    env.session.add(ub.UserHiddenBook(user_id=viewer.id, book_id=1))
    env.session.commit()
    assert visible() == []


def test_public_shelf_does_not_grant_reader_or_download_roles(shared_books):
    env = shared_books
    env.viewer.role = 0
    env.session.commit()
    assert env.client.get('/api/v1/books/1').status_code == 200
    assert env.client.get('/download/1/epub').status_code == 403
    assert env.client.get('/opds/download/1/epub/').status_code == 401
    assert env.client.get('/show/1/pdf').status_code == 403
