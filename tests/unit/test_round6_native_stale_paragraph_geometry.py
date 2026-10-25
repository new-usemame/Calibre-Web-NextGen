"""Independent original native text correspondence, exercised through EPUB.

Cached boxes and formatting spaces are not source paragraph geometry. Unique
complete source text can establish its own rows; ambiguity cannot earn a join.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile
import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('native_stale_source_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('case',['stale_header','leading_spaces','real_indent','ambiguous','changed_word'])
def test_native_whole_paragraph_source_correspondence(tmp_path,case):
    left='The native observers record whether the amber vessel is'
    right='approaching the quiet harbor beside the ordinary river.'
    second='Every following source line establishes the same paragraph margin.'
    complete=right+' '+second
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97)
        p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97)
        q.insert_text((140,60),'An unrelated running title',fontsize=7)
        q.insert_text((110,72),'A second header row with foreign words',fontsize=7)
        x=74 if case=='real_indent' else 54
        original=right.replace('approaching','departing') if case=='changed_word' else right
        q.insert_text((x,90),original,fontsize=7)
        q.insert_text((54,102),second,fontsize=7)
        if case=='ambiguous':
            q.insert_text((54,180),right,fontsize=7)
            q.insert_text((54,192),second,fontsize=7)
        stale=case not in ('leading_spaces','real_indent')
        bbox=[110,52,300,74] if stale else [54,82,400,104]
        runs=[['t',' '*23+right],['t',' '+second]]
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[54,582,400,592])],
               1:[assemble.Element('p',runs=runs,pno=1,bbox=bbox)]}
        book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
        class Reader:
            cache=tmp_path/'native-cache'
            def read(self,*args):raise AssertionError('No model reading is admitted')
            def read_marker(self,*args):raise AssertionError('No marker reading is admitted')
        Reader.cache.mkdir();out=tmp_path/'native.epub'
        report=vision_pipeline.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
        assert report['native_pages']==[0,1] and report['vision_pages']==[]
        assert build_epub.validate(str(out))==[]
        with zipfile.ZipFile(out) as z:
            docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
            if case=='changed_word':
                import json
                metadata=json.loads(z.read('META-INF/reflow.json'))
                units=metadata['pages']['1']['degraded_units']
                assert len(units)==1 and units[0]['page']==1
                assert z.read('OEBPS/'+units[0]['source_image']), 'Unbound cached wording must retain accessible original pixels'
                target=units[0]['source_reference']
                destinations=[n for root in docs.values()for n in root.iter()if n.get('id')==target]
                assert len(destinations)==1 and any(n.tag.endswith('}img')and n.get('src')==units[0]['source_image']for n in destinations[0].iter())
                assert any(n.tag.endswith('}a')and(n.get('href')or'').split('#')[-1]==target for root in docs.values()for n in root.iter())
        if case=='changed_word':
            assert 'departing' in doc[1].get_text() and 'approaching' not in doc[1].get_text()
            assert h.body_texts(docs)==[left], 'A stale header rectangle cannot authorize a cached word contradicted by print; only that unbound unit is disclosed'
            return
        expected=[left+' '+complete] if case in ('stale_header','leading_spaces') else [left,complete]
        assert h.body_texts(docs)==expected, 'complete unique native source rows distinguish layout spaces, stale header boxes, true indent and unproved text'
        assert sum(t.count(right) for t in h.body_texts(docs))==1
    finally:doc.close()


def test_native_inset_paragraph_continues_at_its_own_source_margin(tmp_path):
    left='The inset native explanation retains its own paragraph margin and describes a'
    right='quiet harbor.'
    later='A separate ordinary paragraph starts after the authored section.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97)
        p.insert_text((84,578),'The inset native explanation retains its own paragraph margin',fontsize=7)
        p.insert_text((84,590),'and describes a',fontsize=7)
        q=doc.new_page(width=431.97,height=647.97)
        q.insert_text((84,90),right,fontsize=7)
        q.insert_text((54,130),'A New Authored Section',fontsize=9)
        q.insert_text((54,155),later,fontsize=7)
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[84,570,400,592])],
               1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[84,82,145,94]),
                  assemble.Element('h',runs=[['t','A New Authored Section']],level=2,pno=1,bbox=[54,120,300,133]),
                  assemble.Element('p',runs=[['t',later]],pno=1,bbox=[54,147,400,157])]}
        book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
        class Reader:
            cache=tmp_path/'inset-cache'
            def read(self,*args):raise AssertionError('No model reading is admitted')
            def read_marker(self,*args):raise AssertionError('No marker reading is admitted')
        Reader.cache.mkdir();out=tmp_path/'inset.epub'
        report=vision_pipeline.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
        assert report['native_pages']==[0,1] and report['vision_pages']==[]
        assert build_epub.validate(str(out))==[]
        with zipfile.ZipFile(out) as z:
            docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        assert h.body_texts(docs)==[left+' '+right,later], 'an inset paragraph continues at its own preceding SOURCE row margin, while the following heading and ordinary paragraph remain separate'
        assert any('A New Authored Section'==h.plain(n) for root in docs.values() for n in root.iter() if n.tag.endswith('}h2'))
    finally:doc.close()
