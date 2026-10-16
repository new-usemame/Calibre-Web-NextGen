"""Synthetic text written for SOURCE title-dot continuity checks."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile
import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, vision_pipeline as vp

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round6_title_dot_original_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('native',[False,True],ids=['scan','native'])
@pytest.mark.parametrize('boundary',['title','indent','finished'])
def test_source_title_period_does_not_finish_unfinished_sentence(tmp_path,monkeypatch,native,boundary):
    left='The amber measurements were checked according to Dr.'
    if boundary=='finished':left='The amber measurements were checked and the account was finished.'
    first='Drawer, the sample was stored by the wooden cupboards.'
    second='Every following source row stays at its ordinary paragraph margin.'
    right=first+' '+second
    later='A separate concluding paragraph preserves its own finished sentence.'
    x=72;head_x=88 if boundary=='indent' else x
    if native:
        doc=pymupdf.open()
        try:
            p=doc.new_page(width=431.97,height=647.97);p.insert_text((x,590),left,fontsize=7)
            q=doc.new_page(width=431.97,height=647.97);q.insert_text((head_x,90),first,fontsize=7);q.insert_text((x,102),second,fontsize=7);q.insert_text((x,160),later,fontsize=7)
            book=assemble.Book(elements=[],pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[x,582,405,592])],1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[x,82,405,104]),assemble.Element('p',runs=[['t',later]],pno=1,bbox=[x,152,405,162])]})
            book.elements=[e for rows in book.pages.values() for e in rows]
            class Reader:
                cache=tmp_path/'cache'
                def read(self,*a):raise AssertionError('native SOURCE only; no model')
                def read_marker(self,*a):raise AssertionError('no marker call')
            Reader.cache.mkdir();out=tmp_path/'native.epub';report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book));assert report['native_pages']==[0,1]
            assert build_epub.validate(str(out))==[]
            with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
            # Native source geometry, never a cached model/OCR vocabulary.
            assert doc[0].get_text('words')[-1][4]==left.split()[-1]
            assert doc[1].get_text('words')[0][4]=='Drawer,'
        finally:doc.close()
    else:
        p=h.InkPage();q=h.InkPage();p.text(left,'left',x=22,y=55,size=6,next=True)
        if boundary=='indent':
            q.page.insert_text((38,55),first,fontsize=6);q.page.insert_text((22,66),second,fontsize=6)
            q.blocks.append(h.model_block(right,'right','body',[21,46,282,70],previous=True))
        else:q.text([first,second],'right',x=22,y=55,size=6,previous=True)
        q.text(later,'later',x=22,y=170,size=6)
        _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p,q]);records={b['id']:b for page in report['pages'].values() for b in page['blocks']}
        assert records['left']['printed_tail']==left.split()[-1]
        assert records['right']['printed_head']=='Drawer,'
    expected=[left+' '+right,later] if boundary=='title' else [left,right,later]
    assert h.body_texts(docs)==expected, 'a physically unindented title continuation stays one paragraph; true indentation and finished sentences remain separate'

@pytest.mark.parametrize('native',[False,True],ids=['scan','native'])
def test_numbered_figure_reference_period_is_nonterminal_with_source_proof(tmp_path,monkeypatch,native):
    left='The amber observers compare Fig.'
    right='12 before drawing the conclusion from the ordinary measurements.'
    later='A separate concluding paragraph remains complete.'
    if native:
        doc=pymupdf.open()
        try:
            p=doc.new_page(width=431.97,height=647.97);p.insert_text((72,590),left,fontsize=7)
            q=doc.new_page(width=431.97,height=647.97);q.insert_text((72,90),right,fontsize=7);q.insert_text((72,160),later,fontsize=7)
            pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[72,582,405,592])],1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[72,82,405,94]),assemble.Element('p',runs=[['t',later]],pno=1,bbox=[72,152,405,164])]}
            book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
            class Reader:
                cache=tmp_path/'cache'
                def read(self,*a):raise AssertionError('no model')
                def read_marker(self,*a):raise AssertionError('no marker call')
            Reader.cache.mkdir();out=tmp_path/'native.epub';report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book));assert report['native_pages']==[0,1] and build_epub.validate(str(out))==[]
            with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        finally:doc.close()
    else:
        p=h.InkPage();q=h.InkPage();p.text(left,'left',x=22,y=55,size=6,next=True);q.text(right,'right',x=22,y=55,size=6,previous=True);q.text(later,'later',x=22,y=170,size=6)
        _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p,q]);bs={b['id']:b for page in report['pages'].values() for b in page['blocks']};assert bs['left']['printed_tail']=='Fig.' and bs['right']['printed_head']=='12'
    assert h.body_texts(docs)==[left+' '+right,later], 'a numbered source reference does not finish its ordinary sentence at its abbreviation period'

@pytest.mark.parametrize('missing',['wrong_endpoint','unmapped_geometry','native_not_page_edge'])
def test_title_abbreviation_cannot_waive_physical_source_proof(missing):
    left=dict(role='body',continues_next=True,printed_tail='Dr.',printed_tail_box=[72,580,85,590])
    right=dict(role='body',continues_previous=True,printed_head='Drawer,',printed_head_box=[72,90,95,100],first_line_x=72,opening_column_left=72,source_regions=[[72,90,405,112]])
    if missing=='wrong_endpoint':right['printed_head']='Crate,'
    if missing=='unmapped_geometry':right['first_line_x']=None;right.pop('printed_head_box');left.pop('printed_tail_box')
    if missing=='native_not_page_edge':left.update(native_source=True,native_page_foot=False);right.update(native_source=True,native_page_top=True)
    decision=vp._continuation_decision(left,right,[left,right],'The amber result was checked according to Dr.','Drawer, the tray was blue.')
    assert decision['status']!='joined', 'title punctuation cannot excuse wrong endpoints, missing source ink or a native non-page-boundary'
