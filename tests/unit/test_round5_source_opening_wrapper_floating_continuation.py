"""SOURCE-free opening wrappers cannot interrupt a proved unfinished paragraph.

Only scanned exact source endpoints and structural attachment can admit the
lexical opening wrapper; published punctuation remains byte-for-byte literal.
"""
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
_s=importlib.util.spec_from_file_location('round5_wrapper_source_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(cross_page,context,barrier='none'):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    left='The quiet observers refer to the following illustration'
    right='(fig. 8), we may compare the enclosed curves with later observations.'
    if barrier=='terminal':left+='.'
    if barrier=='enumerator':right='(a) This is a separate numbered item with its own boundary.'
    p.text(left,'left',x=22,y=55,size=6)
    if barrier=='endpoint':
        p.blocks[-1]['text']=left.replace('illustration','description')
        left=p.blocks[-1]['text'];p.blocks[-1]['low_confidence']=[len(left.split())-1]
    p.figure()
    if context:p.page.draw_line((120,100),(120,45),color=(0,0,0),width=2)
    if barrier=='heading':q.text('An authored section opens here.','heading','heading',x=22,y=210 if not cross_page else 35,size=8)
    x=36 if barrier=='indented' else 22
    role='footnote' if barrier=='note' else 'body'
    q.text(right,'right',role,x=x,y=230 if not cross_page else 75,size=6,marker='7' if barrier=='note' else '')
    later='A separate concluding paragraph preserves its authored boundary.'
    q.text(later,'later',x=22,y=285 if not cross_page else 130,size=6)
    return ([p,q] if cross_page else [p]),left,right,later


@pytest.mark.parametrize('cross_page',[False,True])
@pytest.mark.parametrize('context',[False,True])
def test_exact_scanned_opening_wrapper_continues_before_float_group(tmp_path,monkeypatch,cross_page,context):
    pages,left,right,later=fixture(cross_page,context)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    records={b['id']:b for page in report['pages'].values() for b in page['blocks']}
    assert records['left']['printed_tail']=='illustration' and records['right']['printed_head']=='(fig.'
    assert not records['left']['continues_next'] and not records['right']['continues_previous']
    texts=h.body_texts(docs);expected=left+' '+right
    assert expected in texts,'source-proved unfinished paragraph stays continuous before its parenthetical illustration reference'
    assert texts.count(expected)==1 and later in texts
    # Authored wrapper punctuation remains literal; continuity flags are false.
    assert expected.count('(fig. 8),')==1
    nodes=h.body_nodes(docs);joined=next(i for i,n in enumerate(nodes) if h.plain(n)==expected)
    art=next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert joined<art
    assert [h.plain(n) for n in nodes if n.get('class')=='caption']==['A connected synthetic diagram.']
    if context:
        assert report['degraded_figure_count']==1 and nodes[art].find('.//x:a',h.NS) is not None
    else:assert nodes[art].find('.//x:img',h.NS) is not None


@pytest.mark.parametrize('barrier',['indented','terminal','heading','endpoint','note','enumerator'])
def test_opening_wrapper_cannot_waive_source_or_authored_boundaries(tmp_path,monkeypatch,barrier):
    pages,left,right,later=fixture(True,True,barrier)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    texts=h.body_texts(docs)
    assert left+' '+right not in texts
    assert left in texts and later in texts
    if barrier=='note':
        _,notes=h.refs_and_notes(docs)
        assert len(notes)==1 and right in ' '.join(''.join(notes[0][1].itertext()).split())
        assert right not in texts
    else:assert right in texts
    if barrier=='heading':assert any(h.plain(n)=='An authored section opens here.' for n in h.body_nodes(docs))


def test_native_opening_wrapper_policy_and_navigation_remain_unchanged(tmp_path):
    left='The native authors refer to the following illustration'
    right='(fig. 8), where another native paragraph retains its original reading boundary.'
    assert not assemble.continues(left,right),'source-proved wrapper admission must not change the shared native lexical policy'
    doc=pymupdf.open();pages={}
    try:
        for n,text in enumerate([left,right]):
            page=doc.new_page(width=431.97,height=647.97);page.insert_text((54,110),text,fontsize=10)
            pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=(54,96,405,114))]
        nav=dict(id='pdfgoto_p0000_x47',kind=1,status='resolved',pno=0,source_element=0,source_item=None,source_offset=left.index('authors'),source_extent=7,dest_page=1,dest_element=0,dest_item=None,dest_offset=right.index('another'),dest_note=None,label='authors',dest_point=[54,110])
        book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages,source_navigation=[nav])
        class Reader:
            cache=tmp_path/'unused-cache'
            def read(self,*args):raise AssertionError('Native control must not request model reading')
            def read_marker(self,*args):raise AssertionError('Native control must not request marker reading')
        Reader.cache.mkdir();out=tmp_path/'native-wrapper.epub'
        report=vision_pipeline.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
        assert report['vision_pages']==[] and report['native_pages']==[0,1]
        assert build_epub.validate(str(out))==[]
        with zipfile.ZipFile(out) as z:docs={name:ET.fromstring(z.read(name)) for name in z.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')}
        texts=[' '.join(''.join(n.itertext()).split()) for root in docs.values() for n in root.findall('.//x:p',h.NS)]
        assert not any(left+' '+right in text for text in texts)
        assert any(left in text for text in texts) and any(right in text for text in texts)
        refs=[(name,a) for name,root in docs.items() for a in root.findall('.//x:a',h.NS) if 'pdfgoto' in a.get('href','')]
        assert len(refs)==1 and ''.join(refs[0][1].itertext())=='authors'
        name,ref=refs[0];target=h.destination(name,ref.get('href'))
        assert any(n.get('id')==target[1] for n in docs[target[0]].iter())
        origin=next(n.get('id') for n in docs[name].iter() if 'pdfref_' in n.get('id',''))
        assert any(a.get('href','').endswith('#'+origin) and ''.join(a.itertext())=='Return' for root in docs.values() for a in root.findall('.//x:a',h.NS))
    finally:doc.close()
