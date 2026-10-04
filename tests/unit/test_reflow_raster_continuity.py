"""Source pixels may share a paragraph; neither pixels nor OCR become text edits."""
import copy
import json
import zipfile
from dataclasses import asdict
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import assemble, skeleton, extract, enriched_source, layout_ops as ops, build_epub, layout_requests
from cps.services.reflow import _layout_atoms as atoms
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_layout_ops import answer
pytestmark=pytest.mark.unit

@pytest.fixture
def raster_pair(tmp_path):
    path=tmp_path/'pixels.pdf';pdf=pymupdf.open()
    for text in ['Original raster com-', 'pletion stays literal.']:
        p=pdf.new_page(width=300,height=300);p.insert_text((30,60),text)
    pdf.save(path);pdf.close();doc=pymupdf.open(path)
    raws=[extract.read_page(doc,p) for p in range(2)]
    box=raws[0].text_blocks[0].bbox
    left=assemble.Element(kind='fig',pno=0,bbox=box)
    right=assemble.Element(kind='p',pno=1,runs=[['t','pletion stays literal.']],bbox=raws[1].text_blocks[0].bbox)
    book=assemble.Book(elements=[left,right],pages={0:[left],1:[right]},style=skeleton.BookStyle(body_size=11),
        figures=[dict(pno=0,bbox=box,found='ocr_uncertain_region',full_page=False,needs_ink=False)])
    sources=[enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in range(2)]
    yield book,doc,raws,sources
    doc.close()

def plans(pair,role='paragraph'):
    book,doc,raws,sources=pair;out=[]
    for p in range(2):
        ctx=dict(previous_source=sources[0],previous_raw=raws[0]) if p else {}
        prepared=ops.prepare(book,doc,p,sources[p],raws[p],**ctx);a=answer(prepared)
        main=next(g for g in a['groups'] if g['role']=='paragraph')
        main['role']=role
        a.update(continuation=bool(p),boundary_join=None)
        out.append(prepared.accept(book,doc,a,source_page=sources[p],raw_page=raws[p],**ctx))
    return out

@pytest.mark.parametrize('role',['paragraph','quote'])
def test_factory_pixels_continue_without_disclosure_or_lexical_rewrite(raster_pair,tmp_path,role):
    book,doc,raws,sources=raster_pair
    raw_before=[asdict(r) for r in raws];figures=copy.deepcopy(book.figures)
    pp=plans(raster_pair,role)
    binding,view,_,eligible=layout_requests._review_material(pp[1].prepared,pp[1],pp[0]);assert eligible
    assert view['previous']['last_main_role']==role
    from cps.services.reflow import layout_lexical
    assert not layout_lexical.candidates(pp)
    built=build_epub.build(book,str(tmp_path/'proof.epub'),doc=doc,layout_plans=pp,
        source_pages=dict(enumerate(sources)),raw_pages=dict(enumerate(raws)))
    assert build_epub.validate(built.path)==[] and built.page_joins==1
    with zipfile.ZipFile(built.path) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        tag='{http://www.w3.org/1999/xhtml}'+('p' if role=='paragraph' else 'blockquote')
        groups=[n for r in roots for n in r.iter(tag) if 'pletion stays literal.' in ''.join(n.itertext())]
        assert len(groups)==1 and len(list(groups[0].iter('{http://www.w3.org/1999/xhtml}img')))==1
        assert 'OCR uncertain' not in ''.join(groups[0].itertext())
        assert sum('OCR uncertain' in ''.join(n.itertext()) for r in roots for n in r.iter('{http://www.w3.org/1999/xhtml}p') if 'source-evidence-notice' in n.get('class',''))>=1
        assert any(n.get('id')=='pg_0001' for n in groups[0].iter())
    assert figures==book.figures and raw_before==[asdict(r) for r in raws]

@pytest.mark.parametrize('barrier',['quote','heading2','lineblock'])
def test_raster_boundary_cannot_cross_roles_or_headings(raster_pair,barrier):
    book,doc,raws,sources=raster_pair;left,right=plans(raster_pair)
    a=json.loads(right.answer_json);a['groups'][0]['role']=barrier
    from dataclasses import replace
    right=replace(right,answer_json=ops._json(a))
    assert not layout_requests._review_material(right.prepared,right,left)[3]
    with pytest.raises(ContractError):ops.compile_boundary(left,right,book,doc,left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1])

@pytest.mark.parametrize('damage',['raw','asset','adjacency'])
def test_raster_continuation_rejects_stale_source_or_missing_asset(raster_pair,tmp_path,monkeypatch,damage):
    book,doc,raws,sources=raster_pair;pp=plans(raster_pair)
    if damage=='raw':raws[0].blocks[0].lines[0].spans[0].text+=' changed'
    elif damage=='asset':
        def fail(*args,**kwargs):raise ValueError('missing crop')
        monkeypatch.setattr(extract,'crop_jpeg',fail)
    else:
        with pytest.raises(ContractError,match='nonadjacent'):
            ops.compile_boundary(pp[0],pp[0],book,doc,left_source=sources[0],right_source=sources[0],left_raw=raws[0],right_raw=raws[0])
        return
    with pytest.raises((ContractError,ValueError)):
        build_epub.build(book,str(tmp_path/'bad.epub'),doc=doc,layout_plans=pp,source_pages=dict(enumerate(sources)),raw_pages=dict(enumerate(raws)))


def test_same_page_raster_group_keeps_distinct_paragraph_and_full_atom(raster_pair):
    book,doc,raws,sources=raster_pair
    fragment=sources[0].html+'<p>Continuation.</p><p>A separate paragraph.</p>'
    source=atoms.prepare(fragment,'fixture',0,{0:[30,40,200,60]})
    a=dict(snapshot=source['snapshot'],joins=[],groups=[
        dict(role='paragraph',ranges=[source['blocks'][0]['range'],source['blocks'][2]['range']]),
        dict(role='source',ranges=[source['blocks'][1]['range']]),
        dict(role='paragraph',ranges=[source['blocks'][3]['range']])])
    rendered=ops._render(source,a);ops._check_output(source,a,atoms.validate(source,a),rendered)
    root=ops._tree(rendered['body']);assert root[0].find('.//img') is not None
    assert ''.join(root[0].itertext()).strip()=='Continuation.' and ''.join(root[-1].itertext())=='A separate paragraph.'
    bad=copy.deepcopy(a);bad['joins']=[dict(left=source['atoms'][0]['id'],right=source['blocks'][2]['range'][0],hyphen='drop')]
    with pytest.raises(ContractError):atoms.validate(source,bad)
    bad=copy.deepcopy(a);bad['groups'][0]['ranges'].pop(0)
    with pytest.raises(ContractError):atoms.validate(source,bad)
    corrupted=dict(rendered,body=rendered['body'].replace('fig_p0000_0.jpg','forged.jpg'))
    with pytest.raises(ContractError):ops._check_output(source,a,atoms.validate(source,a),corrupted)

@pytest.mark.parametrize('reason',['source_visual_table','unrecovered_scan_layer','scan_figure_band','native_outline_conflict'])
def test_nontext_or_whole_page_figures_remain_opaque(raster_pair,reason):
    book,doc,raws,_=raster_pair;book.figures[0]['found']=reason
    # Coverage metadata is independently mandatory for actual admitted tables.
    fragment=build_epub.page_fragment(book,0)
    source=atoms.prepare(fragment,'negative',0)
    assert source['blocks'][0]['opaque'] and source['blocks'][0]['kind']=='figure'

def test_text_to_pixel_boundary_keeps_literal_hyphen_without_lexical_join(raster_pair,tmp_path):
    book,doc,raws,sources=raster_pair
    book.pages[0]=[assemble.Element(kind='p',pno=0,runs=[['t','A literal com-']])]
    book.pages[1]=[assemble.Element(kind='fig',pno=1,bbox=raws[1].text_blocks[0].bbox)]
    book.elements=book.pages[0]+book.pages[1]
    book.figures=[dict(pno=1,bbox=raws[1].text_blocks[0].bbox,found='native_spacing_uncertain',full_page=False,needs_ink=False)]
    sources[:]=[enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in range(2)]
    pp=plans(raster_pair)
    built=build_epub.build(book,str(tmp_path/'text-to-pixel.epub'),doc=doc,layout_plans=pp,source_pages=dict(enumerate(sources)),raw_pages=dict(enumerate(raws)))
    assert built.page_joins==1 and not build_epub.validate(built.path)
    with zipfile.ZipFile(built.path) as z:
        html=''.join(z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
        assert 'A literal com-' in html
        assert 'Character spacing is uncertain.' in html


def test_no_continuation_decision_means_two_paragraphs_and_negative_review_stays_refused(raster_pair,tmp_path):
    from dataclasses import replace
    book,doc,raws,sources=raster_pair;left,right=plans(raster_pair)
    binding=layout_requests._review_material(right.prepared,right,left)[0]
    negative=layout_requests.validate_review(right.prepared,right,dict(snapshot=binding,accept=False,continuation_accept=False,problems=['paragraph_merge']),left)
    assert negative.accepted_plan is None
    right=layout_requests.validate_review(right.prepared,right,dict(snapshot=binding,accept=True,continuation_accept=False,problems=[]),left).accepted_plan
    assert ops.compile_boundary(left,right,book,doc,left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1]) is None
    # Pixel context is actually included, not a text-only claim of availability.
    contract=json.loads(right.prepared.contract_json)
    assert [p['page'] for p in contract['binding']['raster_panels']]==[0,1]
    assert any(a.get('raster_region') for a in contract['previous']['atoms'])
    proposal=layout_requests.proposal(right.prepared,left)
    review=layout_requests.review(right.prepared,right,left)
    assert json.loads(proposal['messages'][-1]['content'])['binding']['raster_panels']==json.loads(review['messages'][-1]['content'])['raster_panels']


def test_native_child_publishes_same_source_raster_projection(raster_pair,tmp_path):
    # Real isolated child publication; no parent plan can mint native authority.
    from cps.services.reflow.native_ipc import NativeDocument
    book,doc,raws,sources=raster_pair
    path=tmp_path/'native-projection.epub'
    with NativeDocument(doc.name,scratch_root=tmp_path/'child') as child:
        result=build_epub.build(book,str(path),doc=child,source_pages=dict(enumerate(sources)))
    assert not build_epub.validate(result.path)
    with zipfile.ZipFile(path) as z:
        html=''.join(z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
        assert 'source-raster' in html and 'OCR uncertain' in html

from tests.unit.test_reflow_furniture_figure_coverage import covered

def test_raster_projection_preserves_existing_mandatory_furniture_coverage(covered):
    doc,raw,line,make=covered;book,source=make()
    before=ops.prepare(book,doc,0,source,raw)
    assert before.coverage['production_ready']
    book.figures[0]['found']='native_spacing_uncertain'
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    after=ops.prepare(book,doc,0,source,raw)
    assert after.coverage['production_ready'],after.coverage
    assert json.loads(before.contract_json)['binding']['opaque_furniture_coverage']==json.loads(after.contract_json)['binding']['opaque_furniture_coverage']


def test_pixel_boundary_cannot_authorize_hyphen_rewrite_or_fake_emitted_endpoint():
    with pytest.raises(ContractError):build_epub._merge_layout_paragraphs('<p>A com-</p>','<p>pletion.</p>','',None,pixel_boundary=True)
    pixel='<p><span class="source-raster"><img src="x.jpg"/></span></p>'
    with pytest.raises(ContractError):build_epub._merge_layout_paragraphs('<p>A com-</p>',pixel,'','drop',pixel_boundary=True)
