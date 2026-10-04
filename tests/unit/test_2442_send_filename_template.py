# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture book mail without SMTP; attachment names never select source files."""
import inspect
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_babel import Babel

from cps import helper, db
from cps.tasks import mail
from cps.api import admin as api_admin

pytestmark = pytest.mark.unit


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    book = NS(id=42, title='The Book', sort='Book, The', author_sort='Writer, Ann',
              authors=[NS(id=1, name='Ann Writer', sort='Writer, Ann')],
              series=[NS(name='The Saga', sort='Saga, The')], series_index=2.5,
              tags=[], ratings=[], publishers=[], languages=[], isbn='',
              last_modified=None, timestamp=None, pubdate=None, path='Library Book',
              data=[NS(name='Stored-Calibre', format='EPUB')])
    folder=tmp_path/book.path;folder.mkdir()
    payload=b'actual unchanged library bytes'
    (folder/'Stored-Calibre.epub').write_bytes(payload)
    config=NS(mail_filename_template='{series} #{series_index} - {title}',
              config_unicode_filename=False, config_title_regex='',
              config_use_google_drive=False, config_binariesdir='', config_embed_metadata=False,
              get_book_path=lambda:str(tmp_path),
              get_mail_settings=lambda:{'mail_from':'library@example.com','mail_server_type':0})
    monkeypatch.setattr(helper,'config',config);monkeypatch.setattr(mail,'config',config)
    monkeypatch.setattr(helper,'get_sendable_book',lambda *args:book)
    orderer=object.__new__(db.CalibreDB);orderer.ensure_session=lambda:None
    monkeypatch.setattr(helper,'calibre_db',NS(session=None,order_authors=orderer.order_authors,
                         get_book=lambda n:book,get_book_format=lambda *args:book.data[0]))
    monkeypatch.setattr(helper,'get_email_body_text',lambda:'Book delivery')
    queued=[];monkeypatch.setattr(helper.WorkerThread,'add',lambda user,task:queued.append(task))
    monkeypatch.setattr(mail.TaskEmail,'_personal_cover_copy',lambda *args:None)
    checksums=[];monkeypatch.setattr(mail.TaskEmail,'_register_kosync_checksum',
                         lambda self,path,fmt,name:checksums.append((path,fmt,name)))
    return NS(book=book,config=config,queued=queued,checksums=checksums,payload=payload,folder=folder)


def send(delivery):
    assert helper.send_mail(42,'epub',0,'reader@example.com',str(delivery.folder.parent),1,
                           user=NS(id=1,is_anonymous=False)) is None
    assert len(delivery.queued)==1
    return delivery.queued[0]


def test_template_changes_mime_and_sync_name_without_renaming_source(delivery):
    task=send(delivery);message=task.prepare_message();part=next(message.iter_attachments())
    assert part.get_filename()=='The Saga #2.5 - The Book.epub'
    assert part.get_payload(decode=True)==delivery.payload
    assert delivery.checksums==[(str(delivery.folder/'Stored-Calibre.epub'),'epub',part.get_filename())]
    assert sorted(p.name for p in delivery.folder.iterdir())==['Stored-Calibre.epub']


def test_blank_keeps_existing_attachment_name_and_bytes(delivery):
    delivery.config.mail_filename_template=''
    part=next(send(delivery).prepare_message().iter_attachments())
    assert part.get_filename()=='Stored-Calibre.epub'
    assert part.get_payload(decode=True)==delivery.payload


def test_missing_series_conditional_prefix_and_unicode_header(delivery):
    delivery.book.series=[];delivery.book.title='航海 / Return\r\nInjected: no';delivery.book.sort='Wrong sort'
    delivery.config.mail_filename_template='{series:|(|) }{title}'
    part=next(send(delivery).prepare_message().iter_attachments())
    filename=part.get_filename()
    assert '航海' in filename and 'Wrong sort' not in filename
    assert '/' not in filename and '\r' not in filename and '\n' not in filename
    assert not filename.startswith('(') and filename.endswith('.epub')
    assert part.get_payload(decode=True)==delivery.payload


def test_convert_then_send_preserves_requested_name_with_target_extension(delivery,monkeypatch):
    delivery.book.data[0].format='MOBI';(delivery.folder/'Stored-Calibre.mobi').write_bytes(b'mobi input')
    monkeypatch.setattr(helper,'url_for',lambda *a,**kw:'/book/42')
    captured=[];monkeypatch.setattr(helper,'TaskConvert',lambda *args,**kwargs:captured.append((args,kwargs)) or NS())
    assert helper.send_mail(42,'epub',1,'reader@example.com',str(delivery.folder.parent),1,
                           user=NS(id=1,is_anonymous=False)) is None
    settings=captured[0][0][3]
    assert settings['attachment_name']=='The Saga #2.5 - The Book.epub'
    assert captured[0][0][0]==str(delivery.folder/'Stored-Calibre')


def test_invalid_template_rejected_before_other_mail_settings_change(monkeypatch):
    app=Flask(__name__);Babel(app);config=NS(mail_server='original',mail_filename_template='',save=Mock(),
                       mail_port=25,mail_use_ssl=0,mail_login='',mail_from='',mail_size=0,mail_server_type=0)
    monkeypatch.setattr(api_admin,'config',config);monkeypatch.setattr(api_admin,'_require_admin',lambda:None)
    with app.test_request_context(json={'mail_server':'must not persist','mail_filename_template':'{title.__class__}'}):
        result=inspect.unwrap(api_admin.admin_update_mail)()
        status=result[1] if isinstance(result,tuple) else result.status_code
    assert status==400
    assert config.mail_server=='original' and config.mail_filename_template==''
    config.save.assert_not_called()


def test_real_conversion_task_forwards_name_to_the_actual_mime_task(delivery,monkeypatch):
    from cps.tasks import convert
    from cps.tasks.convert import TaskConvert
    monkeypatch.setattr(convert,'config',delivery.config)
    delivery.book.data[0].format='MOBI'
    (delivery.folder/'Stored-Calibre.mobi').write_bytes(b'mobi input')
    monkeypatch.setattr(helper,'url_for',lambda *a,**kw:'/book/42')
    helper.send_mail(42,'epub',1,'reader@example.com',str(delivery.folder.parent),1,
                     user=NS(id=1,is_anonymous=False))
    conversion=delivery.queued.pop();assert isinstance(conversion,TaskConvert)
    conversion.results['path']=delivery.book.path
    monkeypatch.setattr(conversion,'_convert_ebook_format',lambda:'Stored-Calibre.epub')
    conversion.run(NS(add=lambda user,task:delivery.queued.append(task)))
    part=next(delivery.queued[0].prepare_message().iter_attachments())
    assert part.get_filename()=='The Saga #2.5 - The Book.epub'
    assert part.get_payload(decode=True)==delivery.payload
    assert delivery.checksums[-1][2]==part.get_filename()


def test_invalid_stored_template_falls_back_without_losing_delivery(delivery):
    delivery.config.mail_filename_template='{title.__class__}'
    part=next(send(delivery).prepare_message().iter_attachments())
    assert part.get_filename()=='Stored-Calibre.epub' and part.get_payload(decode=True)==delivery.payload


def test_classic_rejects_invalid_template_before_token_or_smtp_mutation(monkeypatch):
    from cps import admin
    app=Flask(__name__);Babel(app)
    config=NS(mail_gmail_token={'token':'unchanged'},mail_server='original',save=Mock())
    monkeypatch.setattr(admin,'config',config);monkeypatch.setattr(admin,'flash',Mock())
    render=Mock(return_value='invalid draft');monkeypatch.setattr(admin,'edit_mailsettings',render)
    with app.test_request_context(method='POST',data={'mail_filename_template':'{title.__class__}',
                                                     'invalidate':'1','mail_server':'changed'}):
        assert inspect.unwrap(admin.update_mailsettings)()=='invalid draft'
    assert config.mail_gmail_token=={'token':'unchanged'} and config.mail_server=='original'
    config.save.assert_not_called();render.assert_called_once_with(mail_filename_template='{title.__class__}')


def test_existing_settings_migrate_with_blank_legacy_default():
    from cps import config_sql
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE settings (id INTEGER PRIMARY KEY, mail_server TEXT)'))
        connection.execute(text("INSERT INTO settings VALUES (1,'preserved.smtp')"))
    with Session(engine) as session:
        config_sql._migrate_table(session,config_sql._Settings)
        row=session.execute(text('SELECT mail_server,mail_filename_template FROM settings')).one()
        assert row==('preserved.smtp','')


@pytest.mark.parametrize('value', ['{series} #{series_index} - {title}', None])
def test_api_saves_and_resets_template_without_returning_secret(monkeypatch,value):
    app=Flask(__name__);Babel(app)
    config=NS(mail_server='smtp',mail_filename_template='before',save=Mock(),mail_port=25,
              mail_use_ssl=0,mail_login='',mail_from='',mail_size=0,mail_server_type=0,
              mail_password_e='stored-secret')
    monkeypatch.setattr(api_admin,'config',config);monkeypatch.setattr(api_admin,'_require_admin',lambda:None)
    with app.test_request_context(json={'mail_filename_template':value}):
        response=inspect.unwrap(api_admin.admin_update_mail)()
    assert response.status_code==200
    assert response.json['mail_filename_template']==(value or '')
    assert config.mail_password_e=='stored-secret' and 'stored-secret' not in response.get_data(as_text=True)
    config.save.assert_called_once()


def test_personal_copy_bytes_and_final_name_are_the_sync_inputs(delivery,monkeypatch,tmp_path):
    personal=tmp_path/'personal';personal.mkdir();f=personal/'user-copy.epub';f.write_bytes(b'private cover bytes')
    monkeypatch.setattr(mail.TaskEmail,'_personal_cover_copy',lambda *args:(str(personal),'user-copy'))
    part=next(send(delivery).prepare_message().iter_attachments())
    assert part.get_filename()=='The Saga #2.5 - The Book.epub'
    assert part.get_payload(decode=True)==b'private cover bytes'
    assert delivery.checksums==[(str(f),'epub',part.get_filename())]
    assert not f.exists() and (delivery.folder/'Stored-Calibre.epub').read_bytes()==delivery.payload


@pytest.mark.parametrize('bad_key',['mail_port','mail_size_mb'])
def test_valid_template_with_bad_number_leaves_all_mail_settings_unchanged(monkeypatch,bad_key):
    app=Flask(__name__);Babel(app)
    config=NS(mail_server='original',mail_filename_template='before',mail_port=25,
              mail_size=25*1024*1024,mail_password_e='original-secret',save=Mock())
    before=vars(config).copy()
    monkeypatch.setattr(api_admin,'config',config);monkeypatch.setattr(api_admin,'_require_admin',lambda:None)
    with app.test_request_context(json={'mail_filename_template':'{title}','mail_server':'changed',
                                       'mail_password':'must not change',bad_key:'bad'}):
        response,status=inspect.unwrap(api_admin.admin_update_mail)()
    assert status==400 and vars(config)==before
    config.save.assert_not_called()
