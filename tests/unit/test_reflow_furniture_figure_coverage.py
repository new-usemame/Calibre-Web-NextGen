"""An existing protected figure can account for furniture, never add it twice."""
import copy
import json
from dataclasses import replace
import pytest
from cps.services.reflow import assemble, enriched_source, extract, layout_ops as ops, skeleton

@pytest.fixture
def covered(tmp_path):
    import pymupdf
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    page.insert_text((60,80),'Echo heading',fontsize=14)
    page.insert_text((60,180),'Body stays separate.',fontsize=12)
    path=tmp_path/'covered.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    raw=extract.read_page(doc,0);lines=[l for b in raw.text_blocks for l in b.lines]
    line=lines[0];x0,y0,x1,y1=line.bbox;mid=(x0+x1)/2
    def make(mode='single'):
        boxes=[(x0-5,y0-5,x1+5,y1+5)]
        if mode in ('union','gap'):
            gap=1 if mode=='gap' else -2
            boxes=[(x0-5,y0-5,mid-gap,y1+5),(mid+gap,y0-5,x1+5,y1+5)]
        elif mode=='partial':boxes=[(x0-5,y0-5,x1-1,y1+5)]
        regions=[skeleton.Region('furniture',[line],bbox=line.bbox)]
        regions += [skeleton.Region('figure',bbox=b,reason='measured_fixture',needs_ink=True) for b in boxes]
        regions += [skeleton.Region('body',[lines[1]],bbox=lines[1].bbox)]
        book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,regions)],skeleton.BookStyle(12),[raw])
        source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        return book,source
    yield doc,raw,line,make
    doc.close()

@pytest.mark.parametrize('mode',['single','union'])
def test_existing_required_image_accounts_for_entire_furniture_without_plaintext_duplication(covered,mode):
    doc,raw,line,make=covered;book,source=make(mode)
    value=ops.prepare(book,doc,0,source,raw)
    assert value.coverage['production_ready'],value.coverage
    c=json.loads(value.contract_json)
    assert not any(a.get('origin',{}).get('kind')=='retained_furniture' for a in c['atoms'])
    assert not any('Echo' in a['text'] for a in c['atoms'])
    # The proof must affect this source identity; covered pixels are protected.
    assert c['binding'].get('opaque_furniture_coverage')
    images=[a for a in c['atoms'] if 'fig_p0000_' in a['html']]
    assert len(images)==(2 if mode=='union' else 1)
    assert all(a['protected'] for a in images)

@pytest.mark.parametrize('mode',['partial','gap'])
def test_partial_or_gapped_pixel_coverage_remains_unsupported(covered,mode):
    doc,raw,line,make=covered;book,source=make(mode)
    value=ops.prepare(book,doc,0,source,raw)
    assert not value.coverage['production_ready']
    assert any('furniture_canonical_overlap' in r for r in value.coverage['unsupported_reasons'])

@pytest.mark.parametrize('damage',['nonmandatory','figure_frame','raw_frame','rotation','text_overlap','note_overlap','uncertain','navigation','missing_doc'])
def test_opaque_coverage_does_not_bypass_other_authority_or_protection(covered,damage):
    doc,raw,line,make=covered;book,source=make('single')
    if damage=='nonmandatory':book.figures[0]['needs_ink']=False
    elif damage=='figure_frame':book.figures[0]['source_geometry']={'space':'reading','orientation':90}
    elif damage=='raw_frame':raw.source_geometry={'space':'reading','orientation':90}
    elif damage=='rotation':doc[0].set_rotation(90)
    elif damage=='text_overlap':
        el=replace(book.pages[0][-1],bbox=line.bbox,line_boxes=[line.bbox]);book.pages[0].append(el);book.elements.append(el)
    elif damage=='note_overlap':
        book.notes.append(assemble.Note(1,'A note.',0,bbox=line.bbox))
    elif damage=='uncertain':line.spans[0].uncertain=True
    elif damage=='navigation':raw.source_links=[{'status':'unmapped','pno':0}]
    if damage not in ('raw_frame','uncertain','navigation','rotation','missing_doc'):
        source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    try:value=ops.prepare(book,None if damage=='missing_doc' else doc,0,source,raw)
    except (ops.ContractError,ValueError,TypeError,AttributeError):return
    assert not value.coverage['production_ready'],damage

@pytest.mark.parametrize('damage',['wrong_asset_index','missing_canonical_image'])
def test_image_reference_and_inventory_identity_are_both_required(covered,damage):
    from cps.services.reflow import source_inventory
    doc,raw,line,make=covered;book,source=make()
    if damage=='wrong_asset_index':
        inv=copy.deepcopy(book.source_inventory[0]);inv['assets'][0]['book_figure_index']=99
        inv['sha256']=source_inventory.digest({k:v for k,v in inv.items() if k!='sha256'})
        book.source_inventory[0]=inv
    else:
        book.pages[0]=[e for e in book.pages[0] if e.kind!='fig']
        book.elements=[e for e in book.elements if e.kind!='fig']
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    value=ops.prepare(book,doc,0,source,raw)
    assert not value.coverage['production_ready']


def test_covered_image_remains_required_and_source_bound_at_compile(covered):
    doc,raw,line,make=covered;book,source=make('union')
    value=ops.prepare(book,doc,0,source,raw);c=json.loads(value.contract_json)
    response=dict(snapshot=value.snapshot_id,joins=[],groups=[
        dict(role='source' if b['opaque'] else 'paragraph',ranges=[b['range']]) for b in c['blocks']])
    plan=value.accept(book,doc,response,source_page=source,raw_page=raw)
    compiled=plan.compile(book,doc,source_page=source,raw_page=raw)
    assert compiled.body.count('fig_p0000_')==2 and 'Echo' not in compiled.body
    broken=copy.deepcopy(response);broken['groups'].pop(0)
    with pytest.raises(ops.ContractError):value.accept(book,doc,broken,source_page=source,raw_page=raw)
    book.figures[0]['bbox'][0]-=1
    with pytest.raises(ops.ContractError):plan.compile(book,doc,source_page=source,raw_page=raw)

@pytest.mark.parametrize('provenance',[
    {'layer':'native','page_rect':[10,0,510,700]},
    {'layer':'native','pno':1},
    {'layer':'ocr'},
])
def test_image_coverage_requires_known_current_native_source_frame(covered,provenance):
    doc,raw,line,make=covered;book,_=make()
    source=enriched_source.prepare_source_page(book,0,provenance)
    value=ops.prepare(book,doc,0,source,raw)
    assert not value.coverage['production_ready']


def test_uncovered_page_snapshot_is_unchanged_by_optional_coverage_probe(covered,monkeypatch):
    from cps.services.reflow import _layout_furniture
    doc,raw,line,make=covered;book,source=make('partial')
    current=ops.prepare(book,doc,0,source,raw)
    monkeypatch.setattr(_layout_furniture,'_opaque_coverage',lambda *a,**kw:None)
    without=ops.prepare(book,doc,0,source,raw)
    assert current.snapshot_id==without.snapshot_id
    assert current.contract_json==without.contract_json


def test_canonical_figure_order_must_match_resource_crop_order(covered):
    doc,raw,line,make=covered;book,_=make('union')
    positions=[i for i,e in enumerate(book.pages[0]) if e.kind=='fig']
    first,second=positions
    book.pages[0][first],book.pages[0][second]=book.pages[0][second],book.pages[0][first]
    book.elements=list(book.pages[0])
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    value=ops.prepare(book,doc,0,source,raw)
    assert not value.coverage['production_ready']
