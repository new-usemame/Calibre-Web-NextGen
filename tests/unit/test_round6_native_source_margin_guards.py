"""Original native paragraph margins, independent of span formatting spaces."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile
import pymupdf
import pytest
from cps.services.reflow import assemble,build_epub,vision_pipeline
_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('native_source_margin_guard_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit

def publish(tmp_path,doc,pages):
    book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
    class Reader:
        cache=tmp_path/'cache'
        def read(self,*args):raise AssertionError('No model reading')
        def read_marker(self,*args):raise AssertionError('No marker reading')
    Reader.cache.mkdir();out=tmp_path/'native.epub'
    report=vision_pipeline.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
    assert report['native_pages']==[0,1] and report['vision_pages']==[]
    assert build_epub.validate(str(out))==[]
    with zipfile.ZipFile(out) as z:
        docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    return docs

@pytest.mark.parametrize('new_indent',[False,True])
def test_one_row_inset_continuation_uses_preceding_own_margin(tmp_path,new_indent):
    left='The inset observers describe the ordinary native pattern as a'
    right='quiet harbor.'
    second='Every following inset row retains its own native paragraph margin.'
    later='A separate ordinary paragraph starts after the authored section.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97)
        p.insert_text((84,578),'The inset observers describe the ordinary native pattern',fontsize=7)
        p.insert_text((84,590),'as a',fontsize=7)
        q=doc.new_page(width=431.97,height=647.97)
        q.insert_text((94 if new_indent else 84,90),right,fontsize=7)
        if new_indent:q.insert_text((84,102),second,fontsize=7)
        q.insert_text((72,130),'A New Authored Section',fontsize=9)
        q.insert_text((72,155),later,fontsize=7)
        complete=right+' '+second if new_indent else right
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[84,570,400,592])],
               1:[assemble.Element('p',runs=[['t',complete]],pno=1,bbox=[84,82,400 if new_indent else 145,104 if new_indent else 94]),
                  assemble.Element('h',level=2,runs=[['t','A New Authored Section']],pno=1,bbox=[72,120,300,133]),
                  assemble.Element('p',runs=[['t',later]],pno=1,bbox=[72,147,400,157])]}
        docs=publish(tmp_path,doc,pages)
        assert h.body_texts(docs)==([left,complete,later] if new_indent else [left+' '+right,later])
        assert any(h.plain(n)=='A New Authored Section' for root in docs.values() for n in root.iter() if n.tag.endswith('}h2'))
    finally:doc.close()

@pytest.mark.parametrize('format_spaces',[False,True])
def test_native_span_spaces_cannot_cancel_real_source_indent_after_hyphen(tmp_path,format_spaces):
    left='The native draft retains its authored trailing bridge-'
    right='side observers begin a genuinely separate indented paragraph.'
    second='Every following source row returns to the ordinary column margin.'
    doc=pymupdf.open()
    try:
        p=doc.new_page(width=431.97,height=647.97);p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97);q.insert_text((74,90),right,fontsize=7)
        q.insert_text((54,102),second,fontsize=7)
        complete=right+' '+second
        pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[54,582,400,592])],
               1:[assemble.Element('p',runs=[['t',(' '*23 if format_spaces else '')+complete]],pno=1,bbox=[54,82,400,104])]}
        docs=publish(tmp_path,doc,pages)
        assert h.body_texts(docs)==[left,complete], 'formatting spaces do not erase a verified SOURCE first-line indent even after a literal hyphen'
    finally:doc.close()
