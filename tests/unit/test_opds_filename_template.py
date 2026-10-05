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
from cps.admin import view_configuration as classic_view_configuration
from cps.services import opds_filename as names


pytestmark = pytest.mark.unit


@pytest.fixture
def book():
    return NS(
        id=42, title='The Book', sort='Book, The', author_sort='Writer, Ann',
        authors=[NS(id=2,name='Ann Writer',sort='Writer, Ann'), NS(id=1,name='Ben Reader',sort='Reader, Ben')], isbn='9781234567890',
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
    '{title:.999s}', '{title:uppercase()}', 'x' * 1025, None, 12, ['{title}'], '\ud800', '{title}\udfff',
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
    assert names.render_filename('{#computed}', book, custom_session) == 'Favorites The Book'
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
    orderer = object.__new__(db.CalibreDB)
    orderer.ensure_session = lambda: None
    monkeypatch.setattr(helper, 'calibre_db', NS(
        get_filtered_book=lambda *args, **kwargs: book,
        get_book_format=lambda *args: NS(name='library-file'), session=None,
        order_authors=orderer.order_authors,
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
        config_theme=1, config_default_locale='en', config_default_language='all', config_default_role=0,
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
    monkeypatch.setattr(admin, 'view_configuration', lambda **kwargs: 'configuration page')
    monkeypatch.setattr(admin, 'flash', Mock())
    monkeypatch.setattr(admin, '_', lambda message, **kw: message % kw if kw else message)
    monkeypatch.setattr(names, '_', lambda message, **kw: message % kw if kw else message)
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
    classic.flash.assert_not_called()
    with app.test_request_context('/api/v1/admin/config', method='POST', json=data):
        response, status = inspect.unwrap(api.admin_update_config)()
        assert status == 400 and response.json['error']['code'] == 'invalid_opds_filename_template'
    assert config.config_opds_filename_template == '{title}'
    assert config.config_calibre_web_title == 'Library'
    config.save.assert_not_called()


def test_unencodable_json_template_is_rejected_before_config_mutation(admin_config):
    """JSON can carry a lone surrogate that SQLite cannot store; reject before writes."""
    config, classic, api = admin_config
    app = Flask(__name__)
    with app.test_request_context(method='POST', json={
        'config_opds_filename_template': '\ud800',
        'config_books_per_page': 45, 'config_calibre_web_title': 'Changed',
    }):
        response, status = inspect.unwrap(api.admin_update_config)()
    assert status == 400 and response.json['error']['code'] == 'invalid_opds_filename_template'
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


def test_rendered_classic_field_round_trips_through_the_editor(admin_config):
    from html.parser import HTMLParser
    class Inputs(HTMLParser):
        def __init__(self):super().__init__();self.values={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='input' and 'name' in attrs:self.values[attrs['name']]=attrs.get('value','')
    config,classic,_=admin_config
    config.config_opds_filename_template='{title} "quoted" & {#custom_field}'
    class RenderConfig:
        def __getattr__(self,key):
            if key.startswith(('role_','show_')):return lambda *args:False
            return getattr(config,key,'')
    source=Path(__file__).resolve().parents[2]/'cps/templates/config_view_edit.html'
    env=Environment(autoescape=True,extensions=['jinja2.ext.i18n'])
    env.install_null_translations()
    template=env.from_string(source.read_text())
    ctx=template.new_context({'conf':RenderConfig(),'url_for':lambda *args,**kwargs:'/admin/viewconfig','csrf_token':lambda:'test','sidebar':[],'readColumns':[],'restrictColumns':[],'sortableColumns':[],'translations':[],'languages':[]})
    html=''.join(template.blocks['body'](ctx));inputs=Inputs();inputs.feed(html)
    assert inputs.values['config_opds_filename_template']==config.config_opds_filename_template
    with Flask(__name__).test_request_context('/admin/viewconfig',method='POST',data={'config_opds_filename_template':inputs.values['config_opds_filename_template']}):
        assert inspect.unwrap(classic.update_view_configuration)()=='configuration page'
    assert config.config_opds_filename_template==inputs.values['config_opds_filename_template']


def test_real_web_route_ignores_opds_preference(download, monkeypatch):
    from cps import web
    monkeypatch.setattr(download.config, 'config_opds_filename_template', '{id}', raising=False)
    monkeypatch.setattr(web,'get_download_link',download.get_download_link)
    with Flask(__name__).test_request_context('/download/42/epub'):
        response=inspect.unwrap(web.download_link)(42,'epub','None')
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename']=='The Book - Ann Writer.epub'


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
    orderer = object.__new__(db.CalibreDB)
    orderer.ensure_session = lambda: None
    monkeypatch.setattr(helper, 'calibre_db', NS(
        get_filtered_book=lambda *a, **kw: book, session=None, order_authors=orderer.order_authors,
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


@pytest.mark.parametrize('template', ['{series_index:03}', '{series_index:05s}'])
def test_ambiguous_zero_width_requires_explicit_alignment(template):
    with pytest.raises(ValueError):
        names.validate_template(template)

@pytest.mark.parametrize('has_series,expected', [(True,'Saga, The - 002 - Book, The'),(False,'Book, The')])
def test_conditional_affixes_make_one_template_work_for_series_and_standalone(book,has_series,expected):
    if not has_series:book.series=[]
    assert names.render_filename('{series:|| - }{series_index:0>3s|| - }{title}',book,None)==expected

@pytest.mark.parametrize('template', ['{title:|one}', '{title:|{id}|}', '{title:uppercase()|| - }'])
def test_conditional_affixes_do_not_enable_nested_fields_or_functions(template):
    with pytest.raises(ValueError):names.validate_template(template)

@pytest.mark.parametrize('title', ['Invoice\u202Efdp.', 'one\u2066two\u2069', 'a\u2028b\u2029c'])
def test_filename_omits_invisible_format_and_line_controls(book,title):
    import unicodedata
    book.title=book.sort=title
    result=names.render_filename('{title}',book,None)
    assert not any(unicodedata.category(c) in ('Cc','Cf','Zl','Zp') for c in result)

def test_entirely_invisible_expansion_uses_documented_book_id(book):
    book.title=book.sort='\u200b'
    assert names.render_filename('{title}',book,None)=='book-42'

@pytest.mark.parametrize('torn',[False,True])
def test_actual_download_orders_linked_authors_and_tolerates_torn_rows(download,book,torn):
    book.author_sort='Writer, Ann & Reader, Ben'
    book.authors.reverse()
    if torn:book.authors.insert(0,None)
    response=download.get_download_link(42,'epub','',filename_template='{authors}')
    assert unquote(parse_options_header(response.headers['Content-Disposition'])[1]['filename'])=='Ann Writer & Ben Reader.epub'


def test_deep_composite_does_not_poison_later_shallow_field(book, custom_session):
    import json
    for number in range(10):
        target = f'#level_{number + 1}' if number < 9 else '#shelf'
        custom_session.add(db.CustomColumns(
            id=20 + number, label=f'level_{number}', datatype='composite',
            display=json.dumps({'composite_template': '{' + target + '}'}),
        ))
    custom_session.commit()
    assert names.render_filename('{#level_0}{#shelf}', book, custom_session) == 'Favorites'


def test_invalid_classic_template_remains_available_for_correction(admin_config, monkeypatch):
    config, classic, _ = admin_config
    render = Mock(return_value='invalid form')
    monkeypatch.setattr(classic, 'view_configuration', render)
    with Flask(__name__).test_request_context(method='POST', data={'config_opds_filename_template': '{title:03}'}):
        assert inspect.unwrap(classic.update_view_configuration)() == 'invalid form'
    assert render.call_args.kwargs['opds_filename_template'] == '{title:03}'
    assert render.call_args.kwargs['opds_filename_error']
    assert config.config_opds_filename_template == '{title}'
    config.save.assert_not_called()


def test_middle_composites_are_not_cached_after_a_depth_cutoff(book, custom_session):
    import json
    for number in range(10):
        target = f'#level_{number + 1}' if number < 9 else '#shelf'
        custom_session.add(db.CustomColumns(id=20 + number, label=f'level_{number}', datatype='composite',
            display=json.dumps({'composite_template': '{' + target + '}'})))
    custom_session.commit()
    assert names.render_filename('{#level_0}|{#level_5}', book, custom_session) == '_Favorites'
    assert names.render_filename('{#level_5}|{#level_0}', book, custom_session) == 'Favorites_'


@pytest.mark.parametrize('value', [' ', '  \t  '])
def test_both_admin_editors_treat_whitespace_as_blank(admin_config, value):
    config, classic, api = admin_config
    app = Flask(__name__)
    with app.test_request_context(method='POST', data={'config_opds_filename_template': value}):
        inspect.unwrap(classic.update_view_configuration)()
    assert config.config_opds_filename_template == ''
    with app.test_request_context(method='POST', json={'config_opds_filename_template': value}):
        inspect.unwrap(api.admin_update_config)()
    assert config.config_opds_filename_template == ''


def test_custom_composite_uses_metadata_names_instead_of_top_level_sort_names(book, custom_session):
    custom_session.get(db.CustomColumns, 7).display = '{"composite_template": "{title} - {series}"}'
    custom_session.commit()
    assert names.render_filename('{#computed}', book, custom_session) == 'The Book - The Saga'


def test_affixes_are_omitted_when_formatting_removes_the_value(book):
    assert names.render_filename('{series:.0|| - }{title}', book, None) == 'Book, The'


@pytest.mark.parametrize('name', ['COM¹', 'COM²', 'COM³', 'LPT¹', 'LPT²', 'LPT³', 'CONIN$', 'CONOUT$'])
def test_reserved_device_names_remain_safe_with_unicode_preserved(book, name):
    assert names.render_filename(name, book, None).startswith('_')


def test_author_sort_collisions_do_not_duplicate_authors(download, book):
    book.authors = [NS(id=1, name='Writer A', sort='Smith, J'), NS(id=2, name='Writer B', sort='Smith, J')]
    book.author_sort = 'Smith, J & Smith, J'
    response = download.get_download_link(42, 'epub', '', filename_template='{authors}')
    filename = parse_options_header(response.headers['Content-Disposition'])[1]['filename']
    assert filename.count('Writer A') == filename.count('Writer B') == 1


def test_optional_template_metadata_read_failure_keeps_the_download_available(download):
    from sqlalchemy.exc import OperationalError
    def unavailable(*args):
        raise OperationalError('SELECT', {}, Exception('owned transient read failure'))
    download.calibre_db.order_authors = unavailable
    response = download.get_download_link(42, 'epub', '', filename_template='{authors}')
    assert response.status_code == 200
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'The Book - Ann Writer.epub'


def test_invalid_template_keeps_other_classic_drafts_without_persisting(admin_config, monkeypatch):
    config, classic, _ = admin_config
    render = Mock(return_value='invalid form')
    monkeypatch.setattr(classic, 'view_configuration', render)
    with Flask(__name__).test_request_context(method='POST', data={
        'config_opds_filename_template': '{title:03}', 'config_calibre_web_title': 'Draft site',
        'config_books_per_page': '45', 'config_default_locale': 'fr', 'download_role': 'on',
    }):
        inspect.unwrap(classic.update_view_configuration)()
    draft = render.call_args.kwargs['draft_config']
    assert draft.config_calibre_web_title == 'Draft site'
    assert draft.config_books_per_page == 45
    assert draft.config_default_locale == 'fr'
    assert draft.config_default_role != config.config_default_role
    assert config.config_calibre_web_title == 'Library'
    assert config.config_books_per_page == 30
    config.save.assert_not_called()


def test_classic_draft_does_not_share_the_live_config_dirty_queue(monkeypatch):
    from cps import admin
    config = config_sql.ConfigSQL()
    object.__setattr__(config, 'config_default_role', 0)
    object.__setattr__(config, 'config_calibre_web_title', 'Saved title')
    object.__setattr__(config, 'config_books_per_page', 30)
    config.dirty.clear()
    monkeypatch.setattr(admin, 'config', config)
    with Flask(__name__).test_request_context(method='POST', data={
        'config_calibre_web_title': 'Draft title', 'config_books_per_page': '45',
    }):
        from flask import request
        draft = admin._view_configuration_draft(request.form)
    assert draft.config_calibre_web_title == 'Draft title'
    assert draft.config_books_per_page == 45
    assert config.config_calibre_web_title == 'Saved title'
    assert config.config_books_per_page == 30
    assert config.dirty == []
    assert draft.dirty is not config.dirty


@pytest.mark.parametrize('value', ['\u200b', '\ufeff', None])
def test_api_blank_template_matches_classic_and_legacy_download(admin_config, download, value):
    config, _, api = admin_config
    with Flask(__name__).test_request_context(method='POST', json={'config_opds_filename_template': value}):
        response = inspect.unwrap(api.admin_update_config)()
    assert response.json['config_opds_filename_template'] == ''
    response = download.get_download_link(42, 'epub', '', filename_template=value or '')
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'The Book - Ann Writer.epub'


def test_composite_fanout_has_bounded_work_without_losing_shallow_values(book, custom_session, monkeypatch):
    import json
    for number in range(8):
        target = f'#fan_{number + 1}' if number < 7 else '#shelf'
        custom_session.add(db.CustomColumns(id=40 + number, label=f'fan_{number}', datatype='composite',
            display=json.dumps({'composite_template': ('{' + target + '}') * 4})))
    custom_session.commit()
    calls = 0
    original = names._BookValues._custom_value
    def bounded(self, label):
        nonlocal calls
        calls += 1
        # Fail early on the unbounded renderer rather than timing its hang.
        assert calls <= 256
        return original(self, label)
    monkeypatch.setattr(names._BookValues, '_custom_value', bounded)
    result = names.render_filename('{#fan_0}', book, custom_session)
    assert result.startswith('Favorites')
    assert calls < 30


def test_bad_composite_is_logged_once_per_download(book, custom_session, caplog):
    custom_session.get(db.CustomColumns, 7).display = 'not JSON'
    custom_session.commit()
    assert names.render_filename('{#computed}{#computed}{#computed}', book, custom_session) == 'book-42'
    assert caplog.text.count('Could not read custom field #computed') == 1


def test_multivalue_custom_names_keep_book_link_order(book, custom_session):
    column = custom_session.get(db.CustomColumns, 1)
    column.is_multiple = True
    column.display = '{"is_names": true}'
    custom_session.execute(text("INSERT INTO custom_column_1 VALUES (2, 'Amy A')"))
    custom_session.execute(text('DELETE FROM books_custom_column_1_link'))
    custom_session.execute(text('INSERT INTO books_custom_column_1_link VALUES (42, 2, NULL), (42, 1, NULL)'))
    custom_session.commit()
    assert names.render_filename('{#shelf}', book, custom_session) == 'Amy A & Favorites'


def test_overflowed_custom_date_is_missing_without_breaking_other_fields(book, custom_session):
    custom_session.execute(text("UPDATE custom_column_4 SET value = '9999-12-31T23:00:00-05:00'"))
    assert names.render_filename('{#date}{title}', book, custom_session) == 'Book, The'


def test_transliteration_empty_name_uses_book_id(book):
    book.title = book.sort = '🎉🎉'
    assert names.render_filename('{title}', book, None, unicode_filename=True) == 'book-42'


def test_author_collision_preserves_original_relationship_order(download, book):
    book.authors = [NS(id=1, name='Writer A', sort='Smith, J'), NS(id=2, name='Writer B', sort='Smith, J')]
    book.author_sort = 'Smith, J & Smith, J'
    response = download.get_download_link(42, 'epub', '', filename_template='{authors}')
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'Writer A & Writer B.epub'


@pytest.mark.parametrize('relationship', ['tags', 'languages', 'publishers', 'series', 'ratings'])
def test_torn_optional_relationship_keeps_available_template_fields(book, relationship):
    getattr(book, relationship).insert(0, None)
    assert names.render_filename('{id}-{tags}', book, None) == '42-Fiction, Space'


@pytest.mark.parametrize('title', ['COM0', 'LPT0', 'NUL .x'])
def test_reserved_device_names_with_zero_or_space_before_extension(book, title):
    assert names.render_filename(title, book, None).startswith('_')


def test_other_classic_validation_errors_preserve_template_and_title(admin_config, monkeypatch):
    config, classic, _ = admin_config
    render = Mock(return_value='invalid form')
    monkeypatch.setattr(classic, 'view_configuration', render)
    with Flask(__name__).test_request_context(method='POST', data={
        'config_opds_filename_template': '{id}', 'config_calibre_web_title': 'Draft site',
        'support_settings_present': '1', 'config_support_url': 'javascript:bad',
    }):
        inspect.unwrap(classic.update_view_configuration)()
    draft = render.call_args.kwargs['draft_config']
    assert draft.config_opds_filename_template == '{id}'
    assert draft.config_calibre_web_title == 'Draft site'
    assert config.config_calibre_web_title == 'Library'
    config.save.assert_not_called()


def test_sort_fallback_matches_actual_library_udf(book, monkeypatch):
    config = NS(config_title_regex=r'(The|A)\s+')
    monkeypatch.setattr(db.CalibreDB, 'config', config)
    engine = create_engine('sqlite://')
    # The registered library UDF is the oracle for configured regex behavior.
    from sqlalchemy import event
    event.listen(engine, 'connect', db._register_sqlite_udfs)
    book.sort = None
    book.title = 'My The Book'
    with engine.connect() as connection:
        expected = connection.execute(text('SELECT title_sort(:title)'), {'title': book.title}).scalar()
    assert names.render_filename('{title}', book, None, config.config_title_regex) == expected
    engine.dispose()


def test_global_custom_budget_stops_distinct_lookups_but_keeps_builtin_metadata(book, custom_session, monkeypatch):
    monkeypatch.setattr(names, 'MAX_CUSTOM_LOOKUPS', 1, raising=False)
    assert names.render_filename('{#shelf}{#count}{title}', book, custom_session) == 'FavoritesBook, The'


def test_classic_draft_restriction_controls_follow_selected_draft_column(admin_config, monkeypatch):
    config, classic, _ = admin_config
    config.config_restricted_column = 1
    draft = NS(config_restricted_column=3)
    query = Mock()
    query.filter.return_value = query
    query.all.return_value = []
    monkeypatch.setattr(classic, 'calibre_db', NS(session=NS(query=lambda *args: query), speaking_language=lambda: []))
    monkeypatch.setattr(classic, 'get_available_locale', lambda: [])
    monkeypatch.setattr(classic, 'restricted_column_datatype', lambda value: 'bool' if value == 3 else 'text')
    render = Mock(return_value='draft form')
    monkeypatch.setattr(classic, 'render_title_template', render)
    assert inspect.unwrap(classic_view_configuration)(draft_config=draft) == 'draft form'
    assert render.call_args.kwargs['restriction_is_bool'] is True


@pytest.mark.parametrize('regex', [r'^(A|The){4294967296}', '(' * 5000 + 'A' + ')' * 5000])
def test_invalid_admin_regex_cannot_break_library_sort_trigger(book, monkeypatch, regex):
    from sqlalchemy import event
    monkeypatch.setattr(db.CalibreDB, 'config', NS(config_title_regex=regex))
    engine = create_engine('sqlite://')
    event.listen(engine, 'connect', db._register_sqlite_udfs)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT)'))
        connection.execute(text('CREATE TRIGGER book_sort AFTER INSERT ON books BEGIN UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id; END'))
        connection.execute(text("INSERT INTO books(title) VALUES ('The Book')"))
        assert connection.execute(text('SELECT sort FROM books')).scalar() == 'The Book'
    engine.dispose()
    book.sort = None
    assert names.render_filename('{title}', book, None, regex) == 'The Book'


def test_deeply_nested_display_json_is_missing_without_breaking_download(book, custom_session):
    custom_session.get(db.CustomColumns, 7).display = '[' * 5000 + '0' + ']' * 5000
    custom_session.commit()
    assert names.render_filename('{#computed}{title}', book, custom_session) == 'Book, The'


def test_blank_preference_keeps_empty_first_author_legacy_name(download, book):
    book.authors[0].name = ''
    response = download.get_download_link(42, 'epub', '', filename_template='')
    assert parse_options_header(response.headers['Content-Disposition'])[1]['filename'] == 'The Book -.epub'


@pytest.mark.parametrize('title', ['می\u200cخواهم', 'क्\u200dष', '👩\u200d💻'])
def test_filename_preserves_meaningful_script_and_emoji_joiners(book, title):
    book.title = book.sort = title
    assert names.render_filename('{title}', book, None) == title


@pytest.mark.parametrize('bad', [{'config_theme': 'bogus'}, {'config_random_books': 'bogus'}])
def test_api_invalid_later_field_cannot_change_any_configuration(admin_config, bad):
    config, _, api = admin_config
    with Flask(__name__).test_request_context(method='POST', json={'config_books_per_page': 5, 'config_opds_filename_template': '{id}', **bad}):
        response, status = inspect.unwrap(api.admin_update_config)()
    assert status == 400
    assert config.config_books_per_page == 30
    assert config.config_opds_filename_template == '{title}'
    config.save.assert_not_called()


@pytest.mark.parametrize('value', [float('inf'), 2 ** 100, -(2 ** 100)])
def test_api_numeric_overflow_cannot_leak_changes_into_live_config(admin_config, value):
    config, _, api = admin_config
    with Flask(__name__).test_request_context(method='POST', json={'config_books_per_page': value, 'config_opds_filename_template': '{id}'}):
        response, status = inspect.unwrap(api.admin_update_config)()
    assert status == 400
    assert config.config_books_per_page == 30
    assert config.config_opds_filename_template == '{title}'
    config.save.assert_not_called()
