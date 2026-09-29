# SPDX-License-Identifier: GPL-3.0-or-later
"""OPDS filename templates: metadata, safety, configuration and download scope."""
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.parse import unquote

import pytest
from flask import Flask, Response
from jinja2 import Environment
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from werkzeug.http import parse_options_header

from cps import config_sql, db
from cps.services import opds_filename as names


pytestmark = pytest.mark.unit


@pytest.fixture
def book():
    return NS(
        id=42, title='The Book', sort='Book, The', author_sort='Writer, Ann',
        authors=[NS(name='Ann Writer'), NS(name='Ben Reader')], isbn='9781234567890',
        languages=[NS(lang_code='eng'), NS(lang_code='fra')],
        pubdate=datetime(2020, 5, 6), timestamp=datetime(2024, 1, 2),
        last_modified=datetime(2024, 2, 3), publishers=[NS(name='Press')],
        ratings=[NS(rating=9)], series=[NS(name='The Saga', sort='Saga, The')],
        series_index='2.0', tags=[NS(name='Fiction'), NS(name='Space')],
    )


def test_all_standard_metadata_fields(book):
    values = names._BookValues(book, None, '')
    assert dict(values) == {
        'id': '42', 'title': 'Book, The', 'author_sort': 'Writer, Ann',
        'authors': 'Ann Writer & Ben Reader', 'isbn': '9781234567890',
        'languages': 'eng, fra', 'pubdate': '2020-05-06', 'timestamp': '2024-01-02',
        'last_modified': '2024-02-03', 'publisher': 'Press', 'rating': '4.5',
        'series': 'Saga, The', 'series_index': '2', 'tags': 'Fiction, Space',
    }


def test_sorted_names_fall_back_to_configured_article_rule(book):
    book.sort = book.series[0].sort = None
    assert names.render_filename('{title} - {series}', book, None, r'^(The|A|An)\s+') == 'Book, The - Saga, The'


@pytest.mark.parametrize('template,expected', [
    ('{author_sort[0]} - {series_index:0>3s} - {title}', 'W - 002 - Book, The'),
    ('x{series_index:>3s}x', 'x  2x'),
    ('{series_index}', '2'),
    ('{{title}} {title}', '{title} Book, The'),
    ('{author_sort[999]}', 'book-42'),
])
def test_substitutions_and_padding(book, template, expected):
    assert names.render_filename(template, book, None) == expected


def test_missing_metadata_is_empty_even_with_padding(book):
    book.series = book.ratings = book.publishers = book.languages = book.tags = book.authors = []
    book.author_sort = book.isbn = book.last_modified = book.timestamp = None
    book.pubdate = db.Books.DEFAULT_PUBDATE
    template = 'x{series}{series_index:0>3s}{rating}{publisher}{languages}{tags}{authors}{author_sort[0]}{isbn}{pubdate}{last_modified}{timestamp}x'
    assert names.render_filename(template, book, None) == 'xx'


@pytest.mark.parametrize('template', [
    '{title', '{}', '{unknown}', '{title.__class__}', '{title[__class__]}',
    '{title!r}', '{title:{id}}', '{series_index:999s}', '{title:1000000000s}',
    '{title:.999s}', '{title:uppercase()}', 'x' * 1025, None, 12, ['{title}'],
])
def test_invalid_templates_are_rejected(template):
    with pytest.raises(ValueError):
        names.validate_template(template)


@pytest.mark.parametrize('title', ['../a\\b\r\nInjected: yes\x00', 'CON', '..', '中文' * 100, 'quote";file.epub'])
def test_safe_single_filename(book, title):
    book.title = book.sort = title
    filename = names.render_filename('{title}', book, None)
    assert filename and len(filename.encode('utf-8')) <= 128
    assert not names._UNSAFE.search(filename)
    assert not names._RESERVED.match(filename)
    assert not filename.startswith('.') and not filename.endswith(('.', ' '))


def test_transliteration_cannot_introduce_reserved_names(book):
    book.title = book.sort = 'ＣＯＮ'
    assert names.render_filename('{title}', book, None, unicode_filename=True) == '_CON'


def test_numbers_keep_fractions_and_bound_exponents(book):
    book.series_index = '2.50'
    assert names.render_filename('{series_index}', book, None) == '2.5'
    book.series_index = '1.1e999999999'
    assert names.render_filename('{series_index}', book, None) == 'book-42'


@pytest.fixture
def custom_session():
    engine = create_engine('sqlite://')
    db.CustomColumns.__table__.create(engine)
    with Session(engine) as session:
        for cid, label, kind, normalized, value in [
            (1, 'shelf', 'text', True, 'Favorites'),
            (2, 'count', 'int', False, 0),
            (3, 'read', 'bool', False, 0),
            (4, 'date', 'datetime', False, '2020-05-06 00:00:00'),
            (5, 'saga', 'series', True, 'Custom Saga'),
            (6, 'stars', 'rating', True, '7'),
        ]:
            session.add(db.CustomColumns(id=cid, label=label, datatype=kind, normalized=normalized))
            # Match Calibre's normalized and per-book custom-column layouts.
            book_column = '' if normalized else ', book INTEGER'
            session.execute(text(f'CREATE TABLE custom_column_{cid} (id INTEGER PRIMARY KEY, value{book_column})'))
            if normalized:
                session.execute(text(f'INSERT INTO custom_column_{cid} (id, value) VALUES (1, :value)'), {'value': value})
                session.execute(text(f'CREATE TABLE books_custom_column_{cid}_link (book INTEGER, value INTEGER, extra REAL)'))
                session.execute(text(f'INSERT INTO books_custom_column_{cid}_link VALUES (42, 1, 1.5)'))
            else:
                session.execute(text(f'INSERT INTO custom_column_{cid} VALUES (1, :value, 42)'), {'value': value})
        session.add_all([
            db.CustomColumns(id=7, label='computed', datatype='composite', display='{"composite_template": "{#shelf} {title}"}'),
            db.CustomColumns(id=8, label='cycle', datatype='composite', display='{"composite_template": "{#cycle}"}'),
        ])
        session.commit()
        yield session
    engine.dispose()


def test_custom_lookup_names_and_types(book, custom_session):
    template = '{#shelf} {#count} {#read} {#date} {#saga} {#saga_index} {#stars} {#missing:0>3s}'
    assert names.render_filename(template, book, custom_session) == 'Favorites 0 No 2020-05-06 Custom Saga 1.5 3.5'
    assert names.render_filename('{#computed}', book, custom_session) == 'Favorites Book, The'
    assert names.render_filename('{#cycle}', book, custom_session) == 'book-42'
    book.id = 43
    assert names.render_filename('{#shelf}{#count}{#read}', book, custom_session) == 'book-43'


@pytest.mark.parametrize('display', [
    '{"composite_template": "{title:uppercase()}"}', 'null', 'not json',
])
def test_unavailable_computed_field_is_empty_and_logged(book, custom_session, caplog, display):
    custom_session.get(db.CustomColumns, 7).display = display
    custom_session.commit()
    assert names.render_filename('{title}-{#computed}', book, custom_session) == 'Book, The-'
    assert 'Could not read custom field #computed' in caplog.text


def test_multivalue_custom_field(book, custom_session):
    custom_session.execute(text("INSERT INTO custom_column_1 VALUES (2, 'Other')"))
    custom_session.execute(text('INSERT INTO books_custom_column_1_link VALUES (42, 2, NULL)'))
    assert names.render_filename('{#shelf}', book, custom_session) == 'Favorites, Other'


def test_settings_column_is_added_on_upgrade_and_persists(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE settings (id INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO settings VALUES (1)'))
    with Session(engine) as session:
        config_sql._migrate_table(session, config_sql._Settings)
        row = session.query(config_sql._Settings).one()
        assert row.config_opds_filename_template == ''
        row.config_opds_filename_template = '{title} ({id})'
        session.commit()
    with Session(engine) as session:
        config_sql._migrate_table(session, config_sql._Settings)
        assert session.query(config_sql._Settings).one().config_opds_filename_template == '{title} ({id})'
    engine.dispose()


@pytest.fixture
def download(monkeypatch, book):
    from cps import helper
    monkeypatch.setattr(helper, 'current_user', NS(is_authenticated=False))
    monkeypatch.setattr(helper, 'calibre_db', NS(
        get_filtered_book=lambda *args, **kwargs: book,
        get_book_format=lambda *args: NS(name='library-file'), session=None,
    ))
    monkeypatch.setattr(helper.config, 'config_unicode_filename', False, raising=False)
    monkeypatch.setattr(helper.config, 'config_title_regex', r'^(The|A|An)\s+', raising=False)
    monkeypatch.setattr(helper, 'do_download_file', lambda book, fmt, client, data, headers, **kw: Response(b'book', headers=headers))
    return helper


@pytest.mark.parametrize('fmt', ['epub', 'pdf', 'kepub'])
def test_download_header_uses_template_and_actual_extension(download, fmt):
    response = download.get_download_link(42, fmt, '', filename_template='{series_index:0>3s} - {title}')
    disposition, options = parse_options_header(response.headers['Content-Disposition'])
    assert disposition == 'attachment'
    assert unquote(options['filename']) == '002 - Book, The.' + fmt
    assert response.data == b'book'


@pytest.mark.parametrize('template', [None, '', '{title.__class__}'])
def test_default_and_invalid_stored_template_keep_legacy_name(download, template):
    response = download.get_download_link(42, 'epub', '', filename_template=template)
    assert unquote(parse_options_header(response.headers['Content-Disposition'])[1]['filename']) == 'The Book - Ann Writer.epub'


def test_unicode_header_is_ascii_with_utf8_filename(download, book):
    book.sort = '日本語 Café'
    response = download.get_download_link(42, 'epub', '', filename_template='{title}')
    header = response.headers['Content-Disposition']
    assert header.isascii() and "filename*=UTF-8''" in header
    assert parse_options_header(header)[1]['filename'] == '日本語 Café.epub'


@pytest.mark.parametrize('agent,client', [('KOReader', ''), ('Kobo', 'kobo')])
def test_opds_route_passes_template_without_changing_client(monkeypatch, agent, client):
    from cps import opds
    monkeypatch.setattr(opds.auth, 'current_user', lambda: NS(role_download=lambda: True))
    monkeypatch.setattr(opds, 'abort_unless_opds_book_exposed', lambda book_id: None)
    monkeypatch.setattr(opds.config, 'config_opds_filename_template', '{title}', raising=False)
    get_download = Mock(return_value='download')
    monkeypatch.setattr(opds, 'get_download_link', get_download)
    with Flask(__name__).test_request_context(headers={'User-Agent': agent}):
        assert inspect.unwrap(opds.opds_download_link)('42', 'EPUB') == 'download'
    get_download.assert_called_once_with('42', 'epub', client, allow_public_shelf_books=True, filename_template='{title}')


@pytest.fixture
def admin_config(monkeypatch):
    from cps import admin
    from cps.api import admin as api_admin

    class Config(NS):
        def set_from_dictionary(self, values, key, convert):
            if key not in values:
                return False
            setattr(self, key, convert(values[key]))
            return True

    config = Config(
        config_opds_filename_template='{title}', config_calibre_web_title='Library',
        config_books_per_page=30, config_random_books=6, config_authors_max=3,
        config_theme=1, config_default_locale='en', config_default_language='all',
        config_server_announcement='', save=Mock(),
    )
    monkeypatch.setattr(admin, 'config', config)
    monkeypatch.setattr(api_admin, 'config', config)
    monkeypatch.setattr(api_admin, 'current_user', NS(
        is_authenticated=True, is_anonymous=False, role_admin=lambda: True,
    ))
    monkeypatch.setattr(api_admin, 'locale_options', lambda: [])
    monkeypatch.setattr(api_admin, 'book_language_options', lambda: [])
    monkeypatch.setattr(admin, 'persist_configured_columns', lambda *args: None)
    monkeypatch.setattr(admin, 'load_eligible_columns', lambda: [])
    monkeypatch.setattr(admin, 'check_valid_read_column', lambda value: True)
    monkeypatch.setattr(admin, 'check_valid_restricted_column', lambda value: True)
    monkeypatch.setattr(admin, 'before_request', lambda: None)
    monkeypatch.setattr(admin, 'view_configuration', lambda: 'configuration page')
    monkeypatch.setattr(admin, 'flash', Mock())
    monkeypatch.setattr(admin, '_', lambda message, **kw: message % kw if kw else message)
    return config, admin, api_admin


@pytest.mark.parametrize('value', ['{authors} - {title}', ''])
def test_both_admin_editors_save_and_reset_template(admin_config, value):
    config, classic, api = admin_config
    app = Flask(__name__)
    with app.test_request_context('/admin/viewconfig', method='POST', data={'config_opds_filename_template': value}):
        assert inspect.unwrap(classic.update_view_configuration)() == 'configuration page'
    assert config.config_opds_filename_template == value
    config.save.assert_called_once()
    config.save.reset_mock()
    with app.test_request_context('/api/v1/admin/config', method='POST', json={'config_opds_filename_template': value}):
        response = inspect.unwrap(api.admin_update_config)()
        assert response.json['config_opds_filename_template'] == value
    config.save.assert_called_once()


def test_invalid_template_does_not_partially_change_configuration(admin_config):
    config, classic, api = admin_config
    app = Flask(__name__)
    data = {'config_opds_filename_template': '{title.__class__}', 'config_calibre_web_title': 'Changed'}
    with app.test_request_context('/admin/viewconfig', method='POST', data=data):
        inspect.unwrap(classic.update_view_configuration)()
    assert config.config_opds_filename_template == '{title}'
    assert config.config_calibre_web_title == 'Library'
    classic.flash.assert_called_once()
    with app.test_request_context('/api/v1/admin/config', method='POST', json=data):
        response, status = inspect.unwrap(api.admin_update_config)()
        assert status == 400 and response.json['error']['code'] == 'invalid_request'
    assert config.config_opds_filename_template == '{title}'
    assert config.config_calibre_web_title == 'Library'
    config.save.assert_not_called()


def test_old_forms_do_not_reset_template(admin_config):
    config, classic, api = admin_config
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={}):
        inspect.unwrap(classic.update_view_configuration)()
    with app.test_request_context(method='POST', json={}):
        inspect.unwrap(api.admin_update_config)()
    assert config.config_opds_filename_template == '{title}'


def test_non_admin_cannot_change_template(admin_config, monkeypatch):
    config, classic, api = admin_config
    monkeypatch.setattr(api.current_user, 'role_admin', lambda: False)
    with Flask(__name__).test_request_context(method='POST', json={'config_opds_filename_template': '{id}'}):
        response, status = inspect.unwrap(api.admin_update_config)()
        assert status == 403
    config.save.assert_not_called()


def test_classic_template_parses_custom_field_example():
    # Literal {#...} opens a Jinja comment unless the example is escaped.
    path = Path(__file__).resolve().parents[2] / 'cps/templates/config_view_edit.html'
    source = path.read_text()
    Environment().parse(source)
    assert 'name="config_opds_filename_template"' in source


def test_web_download_ignores_opds_preference(download, monkeypatch):
    monkeypatch.setattr(download.config, 'config_opds_filename_template', '{id}', raising=False)
    response = download.get_download_link(42, 'epub', '')
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'The Book - Ann Writer.epub'


@pytest.mark.parametrize('requested,client,extension', [
    ('epub', '', 'epub'), ('kepub', 'kobo', 'kepub.epub'), ('fallback', '', 'epub'),
])
def test_real_file_response_keeps_custom_header_and_format_fallback(
        monkeypatch, tmp_path, book, requested, client, extension):
    from cps import helper
    from cps.progress_syncing import settings
    from cps.services import kobo_post_download_restore
    from cps.tasks import kepub_backfill
    monkeypatch.setattr(helper, 'current_user', NS(is_authenticated=False))
    monkeypatch.setattr(kobo_post_download_restore, 'record_download', Mock())
    monkeypatch.setattr(settings, 'is_koreader_sync_enabled', lambda: False)
    monkeypatch.setattr(kepub_backfill, 'is_kepub_backfill_pending', lambda: True)
    monkeypatch.setattr(helper, 'config', NS(
        config_unicode_filename=False, config_title_regex='', config_use_google_drive=False,
        config_kepubifypath='kepubify', config_kobo_prefer_kepub=True,
        config_embed_metadata=False, config_binariesdir='', get_book_path=lambda: str(tmp_path),
    ))
    monkeypatch.setattr(helper, 'calibre_db', NS(
        get_filtered_book=lambda *a, **kw: book, session=None,
        get_book_format=lambda _id, fmt: None if requested == 'fallback' and fmt == 'KEPUB' else NS(name='library-file'),
    ))
    book.path = 'book'
    folder = tmp_path / book.path
    folder.mkdir()
    actual_format = 'epub' if requested == 'fallback' else requested
    source = folder / ('library-file.' + actual_format)
    source.write_bytes(b'unchanged book bytes')
    app = Flask(__name__)
    app.add_url_rule('/download', view_func=lambda: helper.get_download_link(
        42, 'kepub' if requested == 'fallback' else requested, client,
        filename_template='{title} ({id})',
    ))
    with app.test_client() as browser:
        response = browser.get('/download')
        assert response.status_code == 200
        assert response.data == b'unchanged book bytes'
        assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'Book, The (42).' + extension
    assert source.read_bytes() == b'unchanged book bytes'
