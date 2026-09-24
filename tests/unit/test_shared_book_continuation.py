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


@pytest.mark.parametrize('access', ['global_only', 'public_shelf', 'member'])
def test_detail_personal_state_requires_membership_or_current_sharing(shared_books, access):
    """A curator's metadata access must not resurrect removed-book state;
    active shared readers and members still see their own saved progress.
    """
    env = shared_books
    env.viewer.role |= constants.ROLE_BROWSE_GLOBAL
    env.public.is_public = int(access == 'public_shelf')
    if access == 'member':
        env.session.add(ub.UserLibraryBook(user_id=env.viewer.id, book_id=1))
    env.session.add(ub.ReadBook(user_id=env.viewer.id, book_id=1,
                               read_status=ub.ReadBook.STATUS_FINISHED))
    env.session.add(ub.FavoriteBook(user_id=env.viewer.id, book_id=1))
    state = ub.KoboReadingState(user_id=env.viewer.id, book_id=1)
    state.current_bookmark = ub.KoboBookmark(progress_percent=75,
        created_at=datetime(2026, 9, 1), last_modified=datetime(2026, 9, 2))
    env.session.add(state)
    env.session.commit()

    response = env.client.get('/api/v1/books/1')
    assert response.status_code == 200, response.data
    allowed = access != 'global_only'
    assert response.json['read'] is allowed
    assert response.json['favorited'] is allowed
    assert response.json['kosync_progress'] == (75 if allowed else None)
    assert bool(response.json['kosync_progress_timestamp']) is allowed
    assert response.json['in_my_library'] is (access == 'member')
    # Hiding the DTO is not destructive; re-adding/sharing recovers stored state.
    assert env.session.query(ub.ReadBook).one().read_status == ub.ReadBook.STATUS_FINISHED
    assert env.session.query(ub.KoboBookmark).one().progress_percent == 75


def _allow_public_shelf_edits(env, monkeypatch):
    """Give the viewer "edit public shelves" and mount both single-add transports.

    Both register before the first request, as Flask requires.
    """
    from cps import shelf as shelf_module
    from cps.api import shelves
    monkeypatch.setattr(shelf_module, 'queue_hardcover_sync', lambda *a, **k: None)
    monkeypatch.setattr(shelf_module, '_log_shelf_activity', lambda *a, **k: None)
    env.viewer.role |= constants.ROLE_EDIT_SHELFS
    env.session.commit()
    env.app.add_url_rule('/api/v1/shelves/<int:shelf_id>/books/<int:book_id>', 'api_shelf_add',
        inspect.unwrap(shelves.add_book_to_shelf_api), methods=['POST'])
    env.app.add_url_rule('/shelf/add/<int:shelf_id>/<int:book_id>', 'classic_shelf_add',
        inspect.unwrap(shelf_module.add_to_shelf), methods=['POST'])


def _add_to_shelf(env, transport, shelf_id, book_id):
    if transport == 'api':
        return env.client.post('/api/v1/shelves/%s/books/%s' % (shelf_id, book_id))
    return env.client.post('/shelf/add/%s/%s' % (shelf_id, book_id),
                           headers={'X-Requested-With': 'XMLHttpRequest'})


def _shelved(env, book_id):
    return env.session.query(ub.BookShelf).filter_by(shelf=env.public.id, book_id=book_id).count()


@pytest.mark.parametrize('shelf_owner', ['another_account', 'nobody'])
@pytest.mark.parametrize('transport', ['api', 'classic'])
def test_managed_editor_cannot_share_itself_a_book_through_a_public_shelf(
        shared_books, monkeypatch, transport, shelf_owner):
    """#2284 review F2. The viewer's library is managed (personal, no global
    browse), and it may edit public shelves. A public shelf lets every account
    read its books. Placing book 3, which is outside the viewer's library, on
    another account's public shelf would therefore let the viewer read and
    download a book that nobody gave it.
    """
    from cps import shelf as shelf_module
    env = shared_books
    _allow_public_shelf_edits(env, monkeypatch)
    if shelf_owner == 'nobody':
        env.public.user_id = None
        env.session.commit()
    assert env.client.get('/download/3/epub').status_code == 404

    added = _add_to_shelf(env, transport, env.public.id, 3)

    # (add, on the shelf, download, detail): the head this was found on answered
    # (200 or 204, 1, 200, 200).
    assert (added.status_code, _shelved(env, 3),
            env.client.get('/download/3/epub').status_code,
            env.client.get('/api/v1/books/3').status_code) == (403, 0, 404, 404), added.data
    if transport == 'api':
        assert added.json['error'] == {
            'code': 'library_membership_rejected',
            'message': shelf_module.SHELF_MANAGED_MEMBERSHIP_REFUSAL,
        }
    assert env.session.query(ub.UserLibraryBook).count() == 0


@pytest.mark.parametrize('reach', ['own_library', 'global_library'])
def test_editor_still_shares_a_book_it_can_open(shared_books, monkeypatch, reach):
    """The F2 limit is the editor's own reach, not a ban on sharing."""
    env = shared_books
    _allow_public_shelf_edits(env, monkeypatch)
    if reach == 'own_library':
        env.session.add(ub.UserLibraryBook(user_id=env.viewer.id, book_id=3))
    else:
        env.viewer.role |= constants.ROLE_BROWSE_GLOBAL
    env.session.commit()

    added = _add_to_shelf(env, 'api', env.public.id, 3)

    assert added.status_code == 200, added.data
    assert _shelved(env, 3) == 1


@pytest.mark.parametrize('library', ['monolibrary', 'personal_with_global_browse'])
def test_unmanaged_editor_still_hears_not_found_for_a_book_it_cannot_see(
        shared_books, monkeypatch, library):
    """Only a managed library gets the "ask an administrator" refusal. An editor
    whose own rules hide the book keeps the plain not-found answer.
    """
    env = shared_books
    _allow_public_shelf_edits(env, monkeypatch)
    if library == 'monolibrary':
        env.viewer.has_own_library = False
    else:
        env.viewer.role |= constants.ROLE_BROWSE_GLOBAL
    env.viewer.denied_tags = 'tag-3'
    env.session.commit()

    added = _add_to_shelf(env, 'api', env.public.id, 3)

    assert added.status_code == 404, added.data
    assert added.json['error']['code'] == 'not_found'
    assert _shelved(env, 3) == 0


def test_refused_add_never_puts_the_book_in_the_shelf_owners_library(shared_books, monkeypatch):
    """A shelf add first gives a personal-library owner who can browse the
    global library the book it is receiving. For an add that will be refused,
    that grant must not happen at all. A grant committed and then reverted is
    still visible to the owner's Kobo sync in between.
    """
    from sqlalchemy import text
    from cps import user_library
    env = shared_books
    _allow_public_shelf_edits(env, monkeypatch)
    monkeypatch.setattr(user_library, 'calibre_db', env.cdb)
    env.owner.has_own_library = True
    env.owner.user_library_seeded = True
    env.owner.role = (env.owner.role or 0) | constants.ROLE_BROWSE_GLOBAL
    env.session.execute(text('CREATE TABLE membership_writes (user_id INTEGER, book_id INTEGER)'))
    env.session.execute(text(
        'CREATE TRIGGER record_membership_write AFTER INSERT ON user_library_book '
        'BEGIN INSERT INTO membership_writes VALUES (new.user_id, new.book_id); END'))
    env.session.commit()

    added = _add_to_shelf(env, 'api', env.public.id, 3)

    assert added.status_code == 403, added.data
    assert env.session.execute(text('SELECT user_id, book_id FROM membership_writes')).all() == []
    assert _shelved(env, 3) == 0


def test_public_shelf_reader_gets_its_own_reading_places(shared_books, monkeypatch):
    """#2284 review F4. The reader opened through a public shelf asks for this
    book's reading places. It gets its own saved places, as a member would, and
    nothing for books that no public shelf shares with it.
    """
    from cps import web
    from cps.api import reader
    env = shared_books
    monkeypatch.setattr(reader, 'calibre_db', env.cdb)
    monkeypatch.setattr(reader, 'config', web.config)
    env.app.add_url_rule('/api/v1/books/<int:book_id>/reading-sources', 'reading_sources',
        inspect.unwrap(reader.get_reading_sources))
    for user, percent in ((env.viewer, 40.0), (env.owner, 90.0)):
        device = ub.Device(user_id=user.id, kind='webreader', display_name='Browser')
        env.session.add(device)
        env.session.flush()
        env.session.add(ub.DeviceReadingPosition(
            device_id=device.id, book_id=1, progress_percent=percent,
            server_modified_at=datetime(2026, 9, 1)))
    env.session.commit()

    places = env.client.get('/api/v1/books/1/reading-sources')

    assert places.status_code == 200, places.data
    # Only the reader's own place; the shelf owner's reading stays private.
    assert [source['progress_percent'] for source in places.json['sources']] == [40.0]
    assert env.client.get('/api/v1/books/2/reading-sources').status_code == 404
    assert env.client.get('/api/v1/books/3/reading-sources').status_code == 404
    env.public.is_public = 0
    env.session.commit()
    assert env.client.get('/api/v1/books/1/reading-sources').status_code == 404


def test_classic_book_page_offers_sending_only_where_sending_works(shared_books, monkeypatch):
    """Found with F4. The classic book page opens a book shared through a public
    shelf, and it offered "Send to eReader" there. Sending follows the reader's
    own library, as the SPA's hidden send action does, so that button could
    only answer "Book not found".
    """
    from cps import cwa_db_loader, helper, web
    env = shared_books
    env.viewer.kindle_mail = 'viewer@kindle.example'
    env.session.commit()
    pages, queued = [], []
    monkeypatch.setattr(web, 'config', SimpleNamespace(
        config_read_column=0, config_use_google_drive=False,
        get_book_path=web.config.get_book_path, get_mail_server_configured=lambda: True))
    monkeypatch.setattr(helper, 'config', SimpleNamespace(
        config_unicode_filename=False, mail_size=10 ** 9, config_converterpath='',
        get_mail_settings=lambda: {}))
    monkeypatch.setattr(web, 'render_title_template',
        lambda _template, **context: pages.append(context) or '')
    monkeypatch.setattr(web, 'CWA_DB', lambda: SimpleNamespace(cwa_settings={}))
    monkeypatch.setattr(web, 'get_kosync_progress_display', lambda *_: (None, None, None))
    monkeypatch.setattr(env.cdb, 'get_cc_columns', lambda *a, **k: [])
    monkeypatch.setattr(env.cdb, 'get_hierarchical_column_ids', lambda *a, **k: set())
    monkeypatch.setattr(helper, 'get_email_body_text', lambda: '')
    monkeypatch.setattr(helper, 'TaskEmail', lambda *args, **kwargs: args[2])
    monkeypatch.setattr(helper.WorkerThread, 'add',
        staticmethod(lambda _user, task, *a, **k: queued.append(task)))
    monkeypatch.setattr(cwa_db_loader, 'load_cwa_db', lambda: SimpleNamespace(
        CWA_DB=lambda: SimpleNamespace(log_activity=lambda **_: None)))
    env.app.add_url_rule('/book/<int:book_id>', 'show_book', inspect.unwrap(web.show_book))
    env.app.add_url_rule('/send/<int:book_id>/<book_format>/<int:convert>', 'send_to_ereader',
        inspect.unwrap(web.send_to_ereader), methods=['POST'])

    def offered_and_sent():
        assert env.client.get('/book/1').status_code == 200
        offered = [option['format'] for option in pages.pop()['entry'].email_share_list]
        sent = env.client.post('/send/1/epub/0').json[0]['type'] == 'success'
        return offered, sent

    # Shared through the public shelf only: the page must not offer what fails.
    assert offered_and_sent() == ([], False)
    env.session.add(ub.UserLibraryBook(user_id=env.viewer.id, book_id=1))
    env.session.commit()
    assert offered_and_sent() == (['Epub', 'Pdf'], True)
    assert queued == ['book.epub']
