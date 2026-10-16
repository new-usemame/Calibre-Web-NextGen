"""Independent SOURCE-free paragraph boundaries, exercised through final EPUB.

Locked readings and original synthetic ink are separate evidence. The checks
preserve every authored character and source paragraph boundary rather than
trusting model continuation flags or assuming lowercase implies continuation.
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
_spec=importlib.util.spec_from_file_location('round6_original_ink_controls',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def ordinary(cross_page=False, barrier='none', art=False):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    left='The amber observers compare the quiet measurements recorded by'
    right='Aspen while considering the later observations beside the river.'
    if barrier=='lowercase':right='patient workers while considering the later observations beside the river.'
    if barrier=='terminal':left+='.'
    if barrier=='endpoint':source=left.replace('by','near');model=left
    else:source=model=left
    p.text(source,'left',x=22,y=55,size=6,model=model,next=barrier!='none')
    if barrier=='endpoint':p.blocks[-1]['low_confidence']=[len(left.split())-1]
    if art:p.figure()
    if barrier=='heading':q.text('A Separate Authored Section','section','heading',x=22,y=220 if art and not cross_page else 65 if not cross_page else 25,size=8)
    role='footnote' if barrier=='note' else 'body'
    y=235 if art and not cross_page else 90 if not cross_page else 55
    second='Every following source line begins at the ordinary column margin.'
    if barrier=='indent':
        # The source opening is indented while its next row returns to the true
        # column margin. No duplicate erased native text is used as optical ink.
        q.page.insert_text((37,y),right,fontsize=6)
        q.page.insert_text((22,y+11),second,fontsize=6)
        q.blocks.append(h.model_block(right+' '+second,'right',role,[21,y-9,282,y+15],previous=True))
    else:q.text([right,second], 'right',role,x=22,y=y,size=6,previous=barrier!='none',marker='7' if barrier=='note' else '')
    later='A separate concluding paragraph retains its own completed sentence.'
    q.text(later,'later',x=22,y=325 if not cross_page else 170,size=6)
    return ([p,q] if cross_page else [p]),left,right+' Every following source line begins at the ordinary column margin.',later


@pytest.mark.parametrize('cross_page',[False,True])
@pytest.mark.parametrize('art',[False,True])
def test_source_proved_uppercase_continuation_is_one_paragraph(tmp_path,monkeypatch,cross_page,art):
    pages,left,right,later=ordinary(cross_page,art=art)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    records={b['id']:b for page in report['pages'].values() for b in page['blocks']}
    assert records['left']['printed_tail']=='by' and records['right']['printed_head']=='Aspen'
    assert not records['left']['continues_next'] and not records['right']['continues_previous']
    texts=h.body_texts(docs);expected=left+' '+right
    assert texts==[expected,later], 'an unfinished SOURCE paragraph cannot break before its unindented proper name'
    if art:
        nodes=h.body_nodes(docs)
        assert next(i for i,n in enumerate(nodes) if h.plain(n)==expected)<next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
        assert [h.plain(n) for n in nodes if n.get('class')=='caption']==['A connected synthetic diagram.']


@pytest.mark.parametrize('cross_page',[False,True])
def test_source_same_column_fragments_continue_without_reader_flags(tmp_path,monkeypatch,cross_page):
    pages,left,right,later=ordinary(cross_page,'lowercase')
    # Flags are deliberately false: source geometry and endpoints earn the seam.
    for page in pages:
        for b in page.blocks:b['continues_next']=b['continues_previous']=False
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    assert h.body_texts(docs)==[left+' '+right,later]


@pytest.mark.parametrize('barrier',['terminal','indent','heading','endpoint','note'])
@pytest.mark.parametrize('cross_page',[False,True])
def test_flags_and_case_cannot_erase_a_source_paragraph_boundary(tmp_path,monkeypatch,barrier,cross_page):
    pages,left,right,later=ordinary(cross_page,barrier,art=True)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    texts=h.body_texts(docs)
    assert left+' '+right not in texts, 'source punctuation, indent, source disagreement and structural owners are barriers'
    assert left in texts and later in texts
    if barrier=='note':
        _,notes=h.refs_and_notes(docs)
        assert any(right in ' '.join(''.join(n.itertext()).split()) for _,n in notes)
        assert right not in texts
    else:assert right in texts


@pytest.mark.parametrize('with_art',[False,True])
def test_omitted_unindented_source_row_continues_around_large_gap(tmp_path,monkeypatch,with_art):
    p=h.InkPage();left='The quiet observers followed the ordinary path beneath'
    right='the amber canopy while keeping every original word in order.'
    p.text(left,'left',x=22,y=55,size=6)
    if with_art:p.figure()
    # Source ink exists even though the locked reading omits this entire row.
    p.page.insert_text((22,235),right,fontsize=6)
    later='A separate concluding paragraph ends the account.'
    p.text(later,'later',x=22,y=300,size=6)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert any(any(g.get('kind')=='omitted_pixel_line_recovery' for g in b.get('guard',[])) for b in report['pages'][0]['blocks'])
    assert h.body_texts(docs)==[left+' '+right,later], 'source-backed omitted prose must retain its paragraph across a large layout gap'


@pytest.mark.parametrize('case',['wholeword','compound','ambiguous'])
@pytest.mark.parametrize('cross_page',[False,True])
def test_source_vocabulary_resolves_word_division_without_losing_compound(tmp_path,monkeypatch,case,cross_page):
    p=h.InkPage();q=h.InkPage() if cross_page else p
    left='The quiet observers followed careful trav-' if case=='wholeword' else 'The quiet observers followed the bridge-'
    right='elers through the ordinary valley.' if case=='wholeword' else 'side path through the ordinary valley.'
    p.text(left,'left',x=22,y=55,size=6)
    q.text(right,'right',x=22,y=95 if not cross_page else 55,size=6)
    vocab='Other travelers completed a separate account.' if case=='wholeword' else 'Another bridge-side path forms a separate account.'
    q.text(vocab,'vocab',x=22,y=155 if not cross_page else 115,size=6)
    if case=='ambiguous':q.text('The bridgeside spelling occurs in another authored example.','alternate',x=22,y=200,size=6)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p,q] if cross_page else [p])
    texts=h.body_texts(docs);expected=left[:-1]+right if case=='wholeword' else left+right
    assert expected in texts, 'SOURCE whole words can heal discretionary divisions; SOURCE compounds win ambiguous evidence'
    assert texts.count(expected)==1 and vocab in texts
    assert sum(t.count('travelers') for t in texts)==2 if case=='wholeword' else sum(t.count('bridge-side') for t in texts)==2


@pytest.mark.parametrize('indented',[False,True])
def test_native_source_geometry_preserves_real_indent_and_unfinished_name(tmp_path,indented):
    left='The native observers compare the quiet measurements recorded by'
    right='patient workers continue their separate account.' if indented else 'Aspen while considering the later native observations.'
    doc=pymupdf.open();pages={}
    try:
        p=doc.new_page(width=431.97,height=647.97);p.insert_text((54,590),left,fontsize=7)
        q=doc.new_page(width=431.97,height=647.97);q.insert_text((74 if indented else 54,90),right,fontsize=7)
        q.insert_text((54,102),'The following native line establishes the original column margin.',fontsize=7)
        for n,text,bbox in [(0,left,[54,582,400,592]),(1,right,[74 if indented else 54,82,400,104])]:
            pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=bbox)]
        pages[1][0].runs.append(['t',' The following native line establishes the original column margin.'])
        book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
        class Reader:
            cache=tmp_path/'native-cache'
            def read(self,*args):raise AssertionError('Native test cannot read a model')
            def read_marker(self,*args):raise AssertionError('Native test cannot read a marker')
        Reader.cache.mkdir();out=tmp_path/'native.epub'
        report=vision_pipeline.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
        assert report['native_pages']==[0,1] and report['vision_pages']==[]
        assert build_epub.validate(str(out))==[]
        with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        texts=h.body_texts(docs);complete=right+' The following native line establishes the original column margin.'
        assert texts==([left,complete] if indented else [left+' '+complete]), 'native source opening geometry must distinguish true indent from a continued proper name'
    finally:doc.close()


@pytest.mark.parametrize('cross_page',[False,True])
def test_original_ruled_table_and_attached_caption_cannot_split_continued_name(tmp_path,monkeypatch,cross_page):
    pages,left,right,later=ordinary(cross_page,art=True)
    art=next(b for b in pages[0].blocks if b['role']=='figure');art['role']='table'
    # The actual original rectangle supplies horizontal/vertical table rules;
    # a bare model TABLE role alone cannot earn structural float provenance.
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    record=next(b for b in report['pages'][0]['blocks'] if b['id']=='art')
    assert record['role']=='table' and record['source_bound_art']
    assert h.body_texts(docs)==[left+' '+right,later]
    assert [h.plain(n) for n in h.body_nodes(docs) if n.get('class')=='caption']==['A connected synthetic diagram.']


def test_complete_printed_footer_stays_separate_from_cross_page_body_continuation(tmp_path,monkeypatch):
    pages,left,right,later=ordinary(True)
    note='A separate printed explanation ends here.'
    pages[0].text('7 '+note,'note','footnote',x=22,y=360,size=6,model=note,marker='7')
    # A locked structured reference points to the independently printed footer.
    pages[0].blocks[0]['text']+=' <n:note>'
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    assert h.body_texts(docs)==[left+' '+right,later]
    refs,notes=h.assert_exact_returns(docs,1)
    assert len(notes)==1 and note in ' '.join(''.join(notes[0][1].itertext()).split())
    assert all(note not in text for text in h.body_texts(docs))
