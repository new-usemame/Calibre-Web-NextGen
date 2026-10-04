"""Inactive layout admission cannot substitute stored HTML for current source."""
import copy
import json
from dataclasses import asdict, replace

import pytest
from cps.services.reflow import enriched_source, extract, layout_ops as ops
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_structural_ops import source

pytestmark=pytest.mark.unit


def prepared(source):
    book,doc,_=source
    page=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    raw=extract.read_page(doc,0)
    value=ops.prepare(book,doc,0,page,raw)
    return book,doc,page,raw,value


def answer(value):
    data=json.loads(value.contract_json)
    return dict(snapshot=value.snapshot_id,joins=[],groups=[
        dict(role='source' if block['opaque'] else 'paragraph',ranges=[block['range']])
        for block in data['blocks']])


def compile_plan(source):
    book,doc,page,raw,value=prepared(source)
    plan=value.accept(book,doc,answer(value),source_page=page,raw_page=raw,prototype=True)
    return book,doc,page,raw,value,plan


def test_protected_page_roundtrip_admits_current_canonical_source_without_full_raw_mapping(source):
    book,doc,page,raw,value,_=compile_plan(source)
    assert value.coverage['production_ready']
    assert 'source_inventory_missing' in value.coverage['coverage_limitations']
    plan=value.accept(book,doc,answer(value),source_page=page,raw_page=raw)
    result=plan.compile(book,doc,source_page=page,raw_page=raw)
    assert 'Uncertain original reference.' in result.body
    assert 'images/fig_p0000_0.jpg' in result.body
    assert 'noteref' in result.body
    assert result.production_ready


def test_equal_field_forged_factory_page_is_not_authority(source):
    book,doc,page,raw,value=prepared(source)
    forged=replace(page)
    assert asdict(forged)==asdict(page)
    with pytest.raises(ContractError,match='factory'):
        ops.prepare(book,doc,0,forged,raw)


@pytest.mark.parametrize('change',['omit','duplicate','unknown'])
def test_atom_occurrences_must_be_covered_exactly_once(source,change):
    book,doc,page,raw,value=prepared(source)
    response=answer(value)
    if change=='omit':response['groups'].pop()
    elif change=='duplicate':response['groups'].append(copy.deepcopy(response['groups'][0]))
    else:response['groups'][0]['ranges']=[['foreign','foreign']]
    with pytest.raises(ContractError):
        value.accept(book,doc,response,source_page=page,raw_page=raw,prototype=True)


def test_final_compile_reprepares_after_source_or_raw_mutation(source):
    book,doc,page,raw,value,plan=compile_plan(source)
    raw.blocks[0].lines[0].spans[0].text+=' changed'
    with pytest.raises(ContractError,match='stale'):
        plan.compile(book,doc,source_page=page,raw_page=raw,prototype=True)
    raw=extract.read_page(doc,0)
    book.pages[0][0].runs[0][1]+=' changed'
    with pytest.raises(ContractError):
        plan.compile(book,doc,source_page=page,raw_page=raw,prototype=True)


@pytest.mark.parametrize('mutation',['word','asset','anchor'])
def test_renderer_corruption_fails_independent_text_and_protected_tree_checks(source,monkeypatch,mutation):
    book,doc,page,raw,value,plan=compile_plan(source)
    render=ops._render
    def corrupt(*args):
        output=render(*args)
        before,after={'word':('Learning the sky','Learning the sea'),
                      'asset':('images/fig_p0000_0.jpg','images/forged.jpg'),
                      'anchor':('id="fn_7"','id="missing"')}[mutation]
        assert before in output['body'], 'fixture must exercise the corruption'
        output['body']=output['body'].replace(before,after)
        return output
    monkeypatch.setattr(ops,'_render',corrupt)
    with pytest.raises(ContractError):
        plan.compile(book,doc,source_page=page,raw_page=raw,prototype=True)


def test_current_capture_binds_raw_and_reports_unresolved_inventory(source):
    from cps.services.reflow import source_inventory, skeleton
    book,doc,page,raw,_=prepared(source)
    # This capture is real source accounting, not a full canonical ownership map.
    skel=skeleton.PageSkeleton(0,raw.width,raw.height,[])
    book.source_inventory[0]=source_inventory.capture(source_inventory.catalog(raw),skel)
    value=ops.prepare(book,doc,0,page,raw)
    assert value.coverage['inventory_present']
    assert 'source_inventory_unresolved' in value.coverage['coverage_limitations']
    assert value.coverage['production_ready']
    plan=value.accept(book,doc,answer(value),source_page=page,raw_page=raw,prototype=True)
    book.source_inventory[0]['lines'][0]['source'].spans[0].text+=' altered'
    with pytest.raises(ContractError,match='inventory'):
        plan.compile(book,doc,source_page=page,raw_page=raw,prototype=True)


@pytest.fixture
def boundary_source(tmp_path):
    import pymupdf
    from cps.services.reflow import assemble, skeleton
    doc=pymupdf.open()
    for text in ('An inter-\nAnother co-', 'operation resumes with ordinary source words.'):
        page=doc.new_page(width=500,height=700)
        page.insert_text((50,100),text,fontsize=12)
    path=tmp_path/'boundary.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    raws=[extract.read_page(doc,p) for p in range(2)]
    pages={}
    for raw in raws:
        lines=[l for b in raw.text_blocks for l in b.lines]
        pages[raw.pno]=[assemble.Element(kind='p',pno=raw.pno,
            runs=[['t',' '.join(l.text for l in lines)]],bbox=(40,70,470,160))]
    book=assemble.Book(pages=pages,elements=sum(pages.values(),[]),style=skeleton.BookStyle(12),
        notes=[assemble.Note(num=1,text='A separate source note.',pno=0)])
    sources=[enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in range(2)]
    yield book,doc,sources,raws
    doc.close()


def test_boundary_requires_actual_admitted_body_endpoints_and_both_plans(boundary_source):
    book,doc,sources,raws=boundary_source
    left=ops.prepare(book,doc,0,sources[0],raws[0])
    right=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
    contract=json.loads(right.contract_json)
    assert len(contract['previous']['wrap_lefts'])==2
    left_plan=left.accept(book,doc,answer(left),source_page=sources[0],raw_page=raws[0],prototype=True)
    response=answer(right)
    response.update(continuation=True,boundary_join=dict(left=contract['previous']['wrap_lefts'][-1],
        right=contract['wrap_rights'][0],hyphen='drop'))
    def admit():
        return right.accept(book,doc,response,source_page=sources[1],raw_page=raws[1],
            previous_source=sources[0],previous_raw=raws[0],prototype=True)
    def compile_pair(right_plan):
        return ops.compile_boundary(left_plan,right_plan,book,doc,left_source=sources[0],
            right_source=sources[1],left_raw=raws[0],right_raw=raws[1],prototype=True)
    decision=compile_pair(admit())
    assert decision['hyphen']=='drop' and decision['production_ready']
    response['boundary_join']['left']=contract['previous']['wrap_lefts'][0]
    with pytest.raises(ContractError,match='endpoints'):compile_pair(admit())
    with pytest.raises(ContractError,match='two admitted'):
        ops.compile_boundary(None,admit(),book,doc,left_source=sources[0],right_source=sources[1],
            left_raw=raws[0],right_raw=raws[1],prototype=True)


def test_note_role_is_unbound_and_lineblock_keeps_admitted_printed_lines(boundary_source):
    book,doc,sources,raws=boundary_source
    value=ops.prepare(book,doc,0,sources[0],raws[0])
    data=json.loads(value.contract_json)
    lines=data['printed_lines']
    assert len(lines)==2
    response=dict(snapshot=value.snapshot_id,joins=[],groups=[
        dict(role='note',ranges=lines[0]['ranges']),dict(role='lineblock',ranges=lines[1]['ranges'])]+[
        dict(role='source',ranges=[b['range']]) for b in data['blocks'] if b['opaque']])
    plan=value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0],prototype=True)
    output=plan.compile(book,doc,source_page=sources[0],raw_page=raws[0],prototype=True)
    assert '<aside class="source-note-unbound">An inter-</aside>' in output.body
    assert '<aside class="source-note-unbound"><a ' not in output.body


@pytest.mark.parametrize('hyphen',[None,'keep','drop'])
def test_printed_line_boundaries_control_linebreaks_and_explicit_join_characters(boundary_source,hyphen):
    book,doc,sources,raws=boundary_source
    value=ops.prepare(book,doc,0,sources[0],raws[0])
    data=json.loads(value.contract_json)
    lines=data['printed_lines']
    response=answer(value)
    response['groups'][0]=dict(role='lineblock',ranges=[r['ranges'][0] for r in lines])
    if hyphen:
        response['joins']=[dict(left=lines[0]['ranges'][0][1],right=lines[1]['ranges'][0][0],hyphen=hyphen)]
    plan=value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0],prototype=True)
    output=plan.compile(book,doc,source_page=sources[0],raw_page=raws[0],prototype=True)
    expected={'keep':'inter-Another','drop':'interAnother',None:'inter-<br />Another'}[hyphen]
    assert expected in output.body
    if hyphen:
        response['joins'][0]['left']=lines[0]['ranges'][0][0]
        with pytest.raises(ContractError):
            value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0],prototype=True)


@pytest.fixture
def furniture_source(tmp_path):
    import pymupdf
    from cps.services.reflow import assemble, skeleton
    doc=pymupdf.open()
    for _ in range(2):
        page=doc.new_page(width=500,height=700)
        page.insert_text((60,80),'Echo heading',fontsize=14,fontname='heit')
        page.insert_text((60,180),'Echo heading',fontsize=12)
    path=tmp_path/'furniture.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    raws=[extract.read_page(doc,p) for p in range(2)]
    skeletons=[]
    for raw in raws:
        lines=[l for b in raw.text_blocks for l in b.lines]
        skeletons.append(skeleton.PageSkeleton(raw.pno,raw.width,raw.height,[
            skeleton.Region('furniture',[lines[0]],bbox=lines[0].bbox),
            skeleton.Region('body',[lines[1]],bbox=lines[1].bbox)]))
    book=assemble.assemble(skeletons,skeleton.BookStyle(12),raws)
    sources=[enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in range(2)]
    yield book,doc,sources,raws
    doc.close()


def test_retained_equal_text_furniture_has_page_specific_source_ownership(furniture_source):
    book,doc,sources,raws=furniture_source
    value=ops.prepare(book,doc,0,sources[0],raws[0])
    assert value.coverage['production_ready'], value.coverage
    contract=json.loads(value.contract_json)
    restored=[a for a in contract['atoms'] if a.get('origin',{}).get('kind')=='retained_furniture']
    assert restored and all(a['origin']['page']==0 and a['origin']['line_id']=='l0' for a in restored)
    response=answer(value)
    response['groups'][-1]['role']='heading2'
    plan=value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0])
    result=plan.compile(book,doc,source_page=sources[0],raw_page=raws[0])
    assert result.production_ready and result.body.count('Echo')==2
    assert '<h2><em>Echo</em> <em>heading</em></h2>' in result.body
    other=ops.prepare(book,doc,1,sources[1],raws[1])
    with pytest.raises(ContractError):
        other.accept(book,doc,response,source_page=sources[1],raw_page=raws[1])
    # Furniture's separate output channel is still checked, never silently dropped.
    response['groups'][-1]['role']='furniture'
    plan=value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0])
    output=plan.compile(book,doc,source_page=sources[0],raw_page=raws[0])
    assert output.furniture and output.expected_furniture=='Echo heading'
    assert 'reflow-retained-furniture' in output.page_html
    assert output.page_html.count('Echo')==2
    for mutation in ('omit','duplicate'):
        broken=copy.deepcopy(response)
        if mutation=='omit':broken['groups'].pop()
        else:broken['groups'].append(copy.deepcopy(broken['groups'][-1]))
        with pytest.raises(ContractError):
            value.accept(book,doc,broken,source_page=sources[0],raw_page=raws[0])


@pytest.mark.parametrize('damage',['unverified','uncertain','glyph','link','image_owner','hidden_native'])
def test_unsafe_furniture_is_not_exposed_and_production_falls_back(furniture_source,damage):
    from cps.services.reflow import source_inventory, skeleton
    book,doc,sources,raws=furniture_source
    raw=raws[0];lines=[l for b in raw.text_blocks for l in b.lines]
    if damage=='unverified':raw.transcript_unverified=True
    elif damage=='uncertain':lines[0].spans[0].uncertain=True
    elif damage=='glyph':lines[0].spans[0].encoding_unresolved=True
    elif damage=='hidden_native':raw.text_layer_invisible=True
    elif damage=='link':raw.source_links=[{'status':'unmapped','pno':0}]
    skel=skeleton.PageSkeleton(0,raw.width,raw.height,[skeleton.Region('furniture',[lines[0]],bbox=lines[0].bbox),
        skeleton.Region('body',[lines[1]],bbox=lines[1].bbox)])
    if damage=='image_owner':
        skel.regions.extend([skeleton.Region('artwork',[lines[0]],bbox=lines[0].bbox),
                             skeleton.Region('figure',bbox=lines[0].bbox)])
    book.source_inventory[0]=source_inventory.capture(source_inventory.catalog(raw),skel)
    value=ops.prepare(book,doc,0,sources[0],raw)
    assert not value.coverage['production_ready']
    assert value.coverage['unsupported_reasons']
    assert not any(a.get('origin',{}).get('kind')=='retained_furniture' for a in json.loads(value.contract_json)['atoms'])
    with pytest.raises(ContractError,match='unsupported'):
        value.accept(book,doc,answer(value),source_page=sources[0],raw_page=raw)


def test_factory_equal_live_pages_both_validate_without_issuing_equal_clones(source):
    import gc,weakref
    book,doc,_=source
    first=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    second=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    assert first==second and first is not second
    first.validate(book);second.validate(book)
    with pytest.raises(ContractError):replace(first).validate(book)
    reference=weakref.ref(first)
    del first
    gc.collect()
    assert reference() is None
    second.validate(book)
    with pytest.raises(ContractError):replace(second).validate(book)


@pytest.mark.parametrize('mutation',['omit','duplicate'])
def test_final_compiler_checks_separate_retained_furniture_output(furniture_source,monkeypatch,mutation):
    book,doc,sources,raws=furniture_source
    value=ops.prepare(book,doc,0,sources[0],raws[0]);response=answer(value)
    response['groups'][-1]['role']='furniture'
    plan=value.accept(book,doc,response,source_page=sources[0],raw_page=raws[0])
    real=ops._render
    def corrupt(*args):
        out=real(*args)
        assert out['furniture']
        out['furniture']='' if mutation=='omit' else out['furniture']*2
        return out
    monkeypatch.setattr(ops,'_render',corrupt)
    with pytest.raises(ContractError):
        plan.compile(book,doc,source_page=sources[0],raw_page=raws[0])


def test_protected_figure_geometry_is_visible_to_layout_model(source):
    book,doc,page,raw,value=prepared(source)
    figures=[b for b in value.model_view()['protected_blocks'] if b['kind']=='figure']
    assert len(figures)==1
    assert figures[0]['bbox']==[135,235,265,365]
