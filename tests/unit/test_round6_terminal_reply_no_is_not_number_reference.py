"""SOURCE-free completed reply versus source-exact numbered reference."""
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import importlib.util,zipfile
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
hp=Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py'
spec=importlib.util.spec_from_file_location('round6_no_reply_original_ink',hp);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
pytestmark=pytest.mark.unit
@pytest.mark.parametrize('native',[False,True],ids=['scan','native'])
@pytest.mark.parametrize('kind',['pronoun_reply','numeric_reply','numbered_reference','ordinary_art'])
def test_completed_reply_no_and_true_reference_keep_distinct_source_meaning(tmp_path,monkeypatch,native,kind):
    left='The amber observer answered No.'
    first='I then closed the ordinary ledger.' if kind=='pronoun_reply' else '12 observers then returned to the ordinary chamber.'
    if kind=='numbered_reference':
        left='The amber observer inspected numbered item No.'
        first='12 before entering the result in the ordinary ledger.'
    if kind=='ordinary_art':
        left='The amber observer completed the ordinary art.'
        first='I then closed the ordinary ledger.'
    rest='The remaining source row preserves every authored word.'
    right=first+' '+rest
    later='A separate concluding paragraph remains finished.'
    if native:
        doc=pymupdf.open()
        try:
            p=doc.new_page(width=431.97,height=647.97);p.insert_text((72,590),left,fontsize=7)
            q=doc.new_page(width=431.97,height=647.97);q.insert_text((72,90),first,fontsize=7);q.insert_text((72,102),rest,fontsize=7);q.insert_text((72,160),later,fontsize=7)
            pages={0:[assemble.Element('p',runs=[['t',left]],pno=0,bbox=[72,582,405,592])],1:[assemble.Element('p',runs=[['t',right]],pno=1,bbox=[72,82,405,104]),assemble.Element('p',runs=[['t',later]],pno=1,bbox=[72,152,405,162])]}
            book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
            class Reader:
                cache=tmp_path/'cache'
                def read(self,*args):raise AssertionError('native original SOURCE only, no model')
                def read_marker(self,*args):raise AssertionError('no marker read')
            Reader.cache.mkdir();out=tmp_path/'native.epub';report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book));assert report['native_pages']==[0,1]
            assert build_epub.validate(str(out))==[]
            with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
            assert doc[0].get_text('words')[-1][4]==left.split()[-1]
            assert doc[1].get_text('words')[0][4]==first.split()[0]
        finally:doc.close()
    else:
        p=h.InkPage();q=h.InkPage();p.text(left,'left',x=22,y=55,size=6,next=False);q.text([first,rest],'right',x=22,y=55,size=6,previous=False);q.text(later,'later',x=22,y=170,size=6)
        _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p,q]);rows={b['id']:b for page in report['pages'].values() for b in page['blocks']}
        assert rows['left']['printed_tail']==left.split()[-1] and rows['right']['printed_head']==first.split()[0]
        assert rows['right']['first_line_x']==rows['right']['opening_column_left']
    expected=[left+' '+right,later] if kind=='numbered_reference' else [left,right,later]
    assert h.body_texts(docs)==expected, 'A completed reply No. is terminal even when the next ordinary paragraph starts with a pronoun or numeral; a genuine numbered reference remains continuous.'
