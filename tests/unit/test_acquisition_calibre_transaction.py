# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise helper policy against SQLite and real files; Calibre API is a test double.

This proves transaction/recovery decisions, not Calibre's binary implementation.
An image-level integration run remains required before feature exposure.
"""
import hashlib
import importlib.util
import json
import sqlite3
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest


@pytest.fixture
def helper(monkeypatch):
    modules = {
        'calibre.db.adding': ['run_import_plugins','run_import_plugins_before_metadata'],
        'calibre.db.legacy': ['LibraryDatabase'],
        'calibre.db.utils': ['find_identical_books'],
        'calibre.ebooks.metadata': ['string_to_authors'],
        'calibre.ebooks.metadata.meta': ['get_metadata'],
        'calibre.ptempfile': ['TemporaryDirectory'],
    }
    for name, attributes in modules.items():
        module = types.ModuleType(name)
        for attribute in attributes: setattr(module,attribute,lambda *args,**kwargs:None)
        monkeypatch.setitem(sys.modules,name,module)
    path = Path(__file__).resolve().parents[2]/'scripts/calibre_ingest_transaction.py'
    spec = importlib.util.spec_from_file_location('_acquisition_calibre_helper_test',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class Cache:
    def __init__(self,path):
        self.connection=sqlite3.connect(path/'metadata.db')
        self.connection.executescript('CREATE TABLE books (id INTEGER PRIMARY KEY); CREATE TABLE identifiers(book INTEGER,type TEXT,val TEXT, UNIQUE(book,type)); CREATE TABLE cwng_acquisition_ingest_result(source_sha256 TEXT PRIMARY KEY,result_json TEXT);')
        self.backend=types.SimpleNamespace(execute=self.connection.execute,conn=self.connection)
        self.write_lock=nullcontext()
        self.paths={}; self.path=path; self.add_calls=0
    def formats(self,book_id): return [extension.upper() for bid,extension in self.paths if bid==book_id]
    def add_format(self,book_id,extension,path,**kwargs):
        Path(self.paths[book_id,extension]).write_bytes(Path(path).read_bytes())
    def data_for_find_identical_books(self): return None
    def format_abspath(self,book_id,extension): return self.paths.get((book_id,extension))
    def field_for(self,field,book_id,default_value=None):
        return dict(self.connection.execute('SELECT type,val FROM identifiers WHERE book=?',(book_id,)))
    def set_field(self,field,values):
        for book_id,identifiers in values.items():
            for key,value in identifiers.items():
                self.connection.execute('INSERT OR REPLACE INTO identifiers VALUES (?,?,?)',(book_id,key,value))
    def dump_metadata(self,**kwargs): pass
    def add_books(self,entries,**kwargs):
        self.add_calls+=1
        book_id=self.connection.execute('INSERT INTO books DEFAULT VALUES').lastrowid
        for extension,source in entries[0][1].items():
            path=self.path/('library-'+str(book_id)+'.'+extension)
            path.write_bytes(Path(source).read_bytes()); self.paths[book_id,extension]=str(path)
        return [book_id],[]


@pytest.mark.parametrize('existing_format',[True,False])
def test_acquisition_never_replaces_existing_edition(helper,monkeypatch,tmp_path,existing_format):
    cache=Cache(tmp_path)
    cache.connection.execute('INSERT INTO books VALUES (17)')
    original=tmp_path/'annotated.epub'; original.write_bytes(b'existing annotated edition')
    if existing_format: cache.paths[17,'epub']=str(original)
    monkeypatch.setattr(helper,'find_identical_books',lambda *args:{17})
    incoming=tmp_path/'incoming.epub'; incoming.write_bytes(b'new candidate edition')
    source=helper.content_digest(incoming)
    with cache.connection:
        result=helper.add_acquisition(cache,object(),'epub',str(incoming),source)
    assert original.read_bytes()==b'existing annotated edition'
    assert cache.add_calls==(0 if existing_format else 1)
    assert result['disposition']==('existing_retained' if existing_format else 'imported')
    expected=original if existing_format else incoming
    assert result['imported_sha256']==helper.content_digest(expected)
    assert result['book_ids']==([17] if existing_format else [18])
    replay=helper.acquisition_result(cache,source)
    incoming.write_bytes(b'different conversion on retry')
    assert replay==dict(result,status='already_imported')
    cache.connection.close()


def test_provenance_rolls_back_with_calibre_book_transaction(helper,monkeypatch,tmp_path):
    cache=Cache(tmp_path); monkeypatch.setattr(helper,'find_identical_books',lambda *args:set())
    incoming=tmp_path/'book.epub'; incoming.write_bytes(b'book bytes'); digest=helper.content_digest(incoming)
    with pytest.raises(RuntimeError):
        with cache.connection:
            helper.add_acquisition(cache,object(),'epub',str(incoming),digest)
            raise RuntimeError('crash before commit')
    assert helper.acquisition_result(cache,digest) is None
    assert cache.connection.execute('SELECT * FROM books').fetchall()==[]
    # Calibre filesystem copy is not ACID; the row/receipt must not claim success.
    assert list(tmp_path.glob('library-*.epub'))
    cache.connection.close()


def test_embedded_or_legacy_identifiers_cannot_manufacture_helper_receipt(helper,tmp_path):
    cache=Cache(tmp_path); digest='a'*64
    fake=dict(source_sha256=digest,imported_sha256='b'*64,book_ids=[17],disposition='imported')
    cache.connection.execute('INSERT INTO books VALUES (17)')
    cache.connection.execute('INSERT INTO identifiers VALUES (?,?,?)',(17,'cwng_acquisition_result_'+digest,json.dumps(fake)))
    assert helper.acquisition_result(cache,digest) is None
    cache.connection.close()


def test_actual_helper_entry_ignores_global_overwrite_and_replays_committed_digest(helper,monkeypatch,tmp_path):
    cache=Cache(tmp_path)
    cache.connection.execute('INSERT INTO books VALUES (17)'); cache.connection.commit()
    original=tmp_path/'existing.epub'; original.write_bytes(b'annotated original')
    cache.paths[17,'epub']=str(original)
    monkeypatch.setattr(helper,'find_identical_books',lambda *args:{17})
    monkeypatch.setattr(helper,'LibraryDatabase',lambda _:types.SimpleNamespace(new_api=cache,close=lambda:None))
    incoming=tmp_path/'candidate.epub'; incoming.write_bytes(b'candidate edition')
    identity=tmp_path/'source.epub'; identity.write_bytes(b'persistent download')
    monkeypatch.setattr(helper,'prepare_book',lambda path,overrides:iter([(object(),'epub',path)]))
    args=types.SimpleNamespace(path=str(incoming),identity_path=str(identity),expected_import_sha256=None,
        expected_source_sha256=None,database_path=None,library_path=str(tmp_path),acquisition=True,
        metadata_json='{}',action='import',automerge='overwrite',fail_before_commit=False)
    result=helper.run(args)
    assert original.read_bytes()==b'annotated original'
    assert result['disposition']=='existing_retained' and result['book_ids']==[17]
    incoming.write_bytes(b'different reconverted package')
    monkeypatch.setattr(helper,'prepare_book',lambda *args:pytest.fail('recovery repeated import plugins'))
    assert helper.run(args)==dict(result,status='already_imported')
    cache.connection.close()


def test_deleted_result_can_be_reacquired_without_fake_book_id(helper,monkeypatch,tmp_path):
    cache=Cache(tmp_path); monkeypatch.setattr(helper,'find_identical_books',lambda *args:set())
    incoming=tmp_path/'book.epub'; incoming.write_bytes(b'book'); digest=helper.content_digest(incoming)
    with cache.connection: helper.add_acquisition(cache,object(),'epub',str(incoming),digest)
    with cache.connection: cache.connection.execute('DELETE FROM books')
    with cache.connection:
        assert helper.acquisition_result(cache,digest) is None
        result=helper.add_acquisition(cache,object(),'epub',str(incoming),digest)
    assert cache.connection.execute('SELECT id FROM books').fetchall()==[(result['book_ids'][0],)]
    cache.connection.close()


@pytest.mark.parametrize('change',['replaced','deleted'])
def test_helper_reinspection_does_not_replay_stale_existing_format_digest(helper,monkeypatch,tmp_path,change):
    cache=Cache(tmp_path); monkeypatch.setattr(helper,'find_identical_books',lambda *args:set())
    incoming=tmp_path/'book.epub'; incoming.write_bytes(b'book'); digest=helper.content_digest(incoming)
    with cache.connection: first=helper.add_acquisition(cache,object(),'epub',str(incoming),digest)
    book_id=first['book_ids'][0]; stored=Path(cache.paths[book_id,'epub'])
    if change=='replaced': stored.write_bytes(b'replacement edition')
    else: stored.unlink()
    with cache.connection: assert helper.acquisition_result(cache,digest) is None
    monkeypatch.setattr(helper,'find_identical_books',lambda *args:{book_id})
    with cache.connection: second=helper.add_acquisition(cache,object(),'epub',str(incoming),digest)
    if change=='replaced':
        assert second['disposition']=='existing_retained'
        assert second['imported_sha256']==helper.content_digest(stored)!=first['imported_sha256']
    else:
        assert second['disposition']=='imported' and second['book_ids']!=first['book_ids']
    cache.connection.close()
