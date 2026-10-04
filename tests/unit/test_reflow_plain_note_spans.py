"""Only issued numeric source substrings may acquire independently checked links."""
import copy
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import _layout_atoms as atoms, _layout_notes as notes
from cps.services.reflow import assemble, enriched_source, extract, layout_ops as ops, layout_requests as requests, build_epub
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_structural_ops import source, X
from tests.unit.test_reflow_layout_ops import answer

pytestmark=pytest.mark.unit
E=notes.EPUB_TYPE

@pytest.fixture
def span_source(source):
    book,doc,tmp=source
    book.notes=[]
    book.elements[1].runs=[['t','A source 😀&.6 and orators".7 remain.']]
    book.elements.extend([assemble.Element(kind='p',pno=0,runs=[['t','6. First complete note.']]),
                          assemble.Element(kind='p',pno=0,runs=[['t','7. Second complete note.']])])
    page=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    raw=extract.read_page(doc,0);prepared=ops.prepare(book,doc,0,page,raw)
    response=answer(prepared);response['groups'][2]['role']='heading1'
    for group in response['groups'][-2:]:group['role']='note'
    candidates=prepared.model_view()['note_candidates']
    spans=[r for r in candidates['references'] if 'atom' in r]
    assert [r['label'] for r in spans]==['6','7']
    response['note_bindings']=[dict(reference=r['id'],note=next(n['id'] for n in candidates['labels'] if n['label']==r['label'])) for r in spans]
    return book,doc,tmp,page,raw,prepared,response,spans


def admit(f):
    book,doc,_,page,raw,prepared,response,_=f
    return prepared.accept(book,doc,response,source_page=page,raw_page=raw)


def test_exact_unicode_substring_recovery_and_semantic_review(span_source):
    book,doc,_,page,raw,prepared,response,spans=span_source
    plan=admit(span_source);compiled=plan.compile(book,doc,source_page=page,raw_page=raw)
    source=json.loads(prepared.contract_json)
    assert spans[0]['start']==3 and spans[0]['end']==4
    root=ops._tree(compiled.body)
    refs=[n for n in root.iter('a') if n.get(E)=='noteref']
    assert [n.text for n in refs]==['6','7'] and all(not len(n) for n in refs)
    assert not list(root.iter('sup'))
    unbound=copy.deepcopy(response);unbound.pop('note_bindings')
    plain=atoms.render(source,unbound)
    restored=notes.verified_source_output(source,response,atoms.validate(source,response),dict(body=compiled.body,furniture=compiled.furniture))
    assert ET.tostring(ops._tree(restored['body']))==ET.tostring(ops._tree(plain['body']))
    review=requests.review(prepared,plan);view=json.loads(review['messages'][1]['content'])
    assert view['note_bindings']==response['note_bindings']
    rejected=dict(snapshot=view['snapshot'],accept=False,continuation_accept=False,problems=['note_or_caption_association'])
    assert requests.validate_review(prepared,plan,rejected).accepted_plan is None
    response['note_bindings'].pop()
    with pytest.raises(ContractError,match='stale'):requests.validate_review(prepared,admit(span_source),rejected)


@pytest.mark.parametrize('damage',['move','duplicate','nested','digit','prefix','suffix','spacing','note_spacing','attrs','root_text','bounds','duplicate_note'])
def test_span_admission_and_output_fail_closed(span_source,monkeypatch,damage):
    book,doc,_,page,raw,prepared,response,spans=span_source
    if damage=='bounds':
        response['note_bindings'][0]['reference']=spans[0]['id']+'-0-4'
        with pytest.raises(ContractError):admit(span_source)
        return
    if damage=='duplicate_note':
        response['note_bindings'][1]['note']=response['note_bindings'][0]['note']
        with pytest.raises(ContractError):admit(span_source)
        return
    plan=admit(span_source);render=ops._render
    def corrupt(*args):
        result=render(*args);root=ops._tree(result['body']);ref=next(a for a in root.iter('a') if a.get(E)=='noteref');parent=next(n for n in root if ref in list(n))
        if damage=='move':
            old=parent.text;parent.text=ref.text+old;ref.text=old[-1:];parent.text=parent.text[:-1]
        elif damage=='duplicate':parent.append(copy.deepcopy(ref))
        elif damage=='nested':
            i=list(parent).index(ref);parent.remove(ref);carrier=ET.Element('span');carrier.append(ref);parent.insert(i,carrier)
        elif damage=='digit':ref.text='8'
        elif damage=='prefix':parent.text='changed '+parent.text
        elif damage=='suffix':ref.tail=(ref.tail or '')+' changed'
        elif damage=='spacing':parent.text=(parent.text or '').replace(' ', '  ')
        elif damage=='note_spacing':
            note=next(n for n in root if n.get(E)=='footnote');note[0].tail=' '+(note[0].tail or '')
        elif damage=='attrs':ref.set('href','#other')
        else:root.text='Unadmitted words'
        result['body']=(root.text or '')+''.join(ET.tostring(n,encoding='unicode') for n in root)
        return result
    monkeypatch.setattr(ops,'_render',corrupt)
    with pytest.raises(ContractError):plan.compile(book,doc,source_page=page,raw_page=raw)


@pytest.mark.parametrize('reference',['<em>3</em>','<span class="reflow-uncertain">3</span>','<a href="#old">3</a>','word3','<sup>3</sup>'])
def test_plain_spans_exclude_nonplain_or_uncertain_material(reference):
    source=atoms.prepare('<p>Body '+reference+' continues.</p><p>3. Full note.</p>','source',0)
    assert not notes.candidates(source)['references']


def test_standalone_and_repeated_digits_have_distinct_source_bound_ids():
    source=atoms.prepare('<p>First 3 then 3 end.</p><p>3. First note.</p><p>3. Second note.</p>','source',0)
    refs=[r for r in notes.candidates(source)['references'] if r['label']=='3' and source['atoms'][int(r['atom'][1:])]['text']=='3']
    assert len(refs)==2 and refs[0]['id']!=refs[1]['id']
    assert all(r['start']==0 and r['end']==1 for r in refs)
    moved=copy.deepcopy(source);moved['snapshot']='another'
    assert not {r['id'] for r in refs}&{r['id'] for r in notes.candidates(moved)['references']}


def test_actual_span_publication_roundtrip_crosses_chapters(span_source):
    book,doc,tmp,page,raw,_,_,_=span_source;plan=admit(span_source)
    out=build_epub.build(book,str(tmp/'span-direct.epub'),doc=doc,source_pages={0:page},layout_plans=[plan],raw_pages={0:raw})
    assert build_epub.validate(out.path)==[]
    with zipfile.ZipFile(out.path) as z:
        roots={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        refs=[(name,n) for name,r in roots.items() for n in r.iter(X+'a') if n.get(E)=='noteref']
        assert len(refs)==2
        for name,ref in refs:
            filename,ident=ref.get('href').split('#');target='OEBPS/'+filename
            assert target!=name
            matches=[n for n in roots[target].iter() if n.get('id')==ident];assert len(matches)==1
            note=matches[0];back=note.find(X+'a');home,backid=back.get('href').split('#')
            assert 'OEBPS/'+home==name and backid==ref.get('id')
            assert len([n for n in roots[name].iter() if n.get('id')==backid])==1



def test_actual_native_source_spans_cross_chapters(tmp_path):
    import pymupdf
    from cps.services.reflow.native_ipc import NativeDocument
    path=tmp_path/'source.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=500,height=700)
        p.insert_text((50,100),'A reference.6 and another.7 remain in source prose.',fontsize=12)
        p.insert_text((50,160),'Source annotations',fontsize=18)
        p.insert_text((50,220),'6. First complete source annotation remains.',fontsize=12)
        p.insert_text((50,260),'7. Second complete source annotation remains.',fontsize=12)
        pdf.save(path)
    with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
        result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
        book=result.book;raw=result.raw_pages[0]
        page=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        prepared=ops.prepare(book,doc,0,page,raw);contract=json.loads(prepared.contract_json)
        candidates=prepared.model_view()['note_candidates'];spans=[r for r in candidates['references'] if 'atom' in r]
        assert [r['label'] for r in spans]==['6','7']
        response=answer(prepared)
        for group in response['groups']:
            first=next(a for a in contract['atoms'] if a['id']==group['ranges'][0][0])
            if first['text'] in ('6.','7.'):group['role']='note'
            elif first['text']=='Source':group['role']='heading1'
        response['note_bindings']=[dict(reference=r['id'],note=next(n['id'] for n in candidates['labels'] if n['label']==r['label'])) for r in spans]
        plan=prepared.accept(book,doc,response,source_page=page,raw_page=raw)
        built=build_epub.build(book,str(tmp_path/'span-native.epub'),doc=doc,source_pages={0:page},layout_plans=[plan],raw_pages={0:raw})
        assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        roots={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        refs=[(name,n) for name,r in roots.items() for n in r.iter(X+'a') if n.get(E)=='noteref']
        assert len(refs)==2
        for home,ref in refs:
            filename,ident=ref.get('href').split('#');destination='OEBPS/'+filename
            assert destination!=home
            targets=[n for n in roots[destination].iter() if n.get('id')==ident];assert len(targets)==1
            back=targets[0].find(X+'a');filename,ident=back.get('href').split('#')
            assert 'OEBPS/'+filename==home and ident==ref.get('id')
            assert len([n for n in roots[home].iter() if n.get('id')==ident])==1


def test_same_atom_spans_and_mixed_sup_cannot_reuse_target():
    source=atoms.prepare('<p>Word.3/.4 <sup class="noteref-unresolved">3</sup> ends.</p><p>3. First note.</p><p>4. Second note.</p>','source',0)
    candidates=notes.candidates(source);spans=[r for r in candidates['references'] if 'atom' in r]
    assert len(spans)==2 and spans[0]['atom']==spans[1]['atom']
    response=dict(snapshot=source['snapshot'],joins=[],groups=[dict(role='paragraph' if i==0 else 'note',ranges=[b['range']]) for i,b in enumerate(source['blocks'])],note_bindings=[dict(reference=r['id'],note=next(n['id'] for n in candidates['labels'] if n['label']==r['label'])) for r in spans])
    rendered=atoms.render(source,response)
    ops._check_output(source,response,atoms.validate(source,response),rendered)
    response['note_bindings'].append(dict(reference=candidates['references'][0]['id'],note=response['note_bindings'][0]['note']))
    with pytest.raises(ContractError,match='duplicate'):atoms.render(source,response)


def test_plain_span_navigation_collision_is_not_admitted(monkeypatch):
    source=atoms.prepare('<p>Reference.3 remains.</p><p>3. Full note.</p><p id="taken">Original opaque.</p>','source',0)
    candidates=notes.candidates(source)
    response=dict(snapshot=source['snapshot'],joins=[],groups=[dict(role=role,ranges=[b['range']]) for role,b in zip(('paragraph','note','source'),source['blocks'])],note_bindings=[dict(reference=candidates['references'][0]['id'],note=candidates['labels'][0]['id'])])
    monkeypatch.setattr(notes,'identities',lambda *args:('taken','another'))
    with pytest.raises(ContractError,match='collision'):atoms.render(source,response)


def test_a_valid_word_join_cannot_consume_a_reference_atom():
    source=atoms.prepare('<p>Body word.3- continues.</p><p>3. Full note.</p>','source',0,wrap_lefts=['a1'],wrap_rights=['a2'])
    candidates=notes.candidates(source)
    response=dict(snapshot=source['snapshot'],joins=[dict(left='a1',right='a2',hyphen='keep')],groups=[dict(role='paragraph' if i==0 else 'note',ranges=[b['range']]) for i,b in enumerate(source['blocks'])])
    atoms.render(source,response)  # the underlying word join itself is admitted
    response['note_bindings']=[dict(reference=candidates['references'][0]['id'],note=candidates['labels'][0]['id'])]
    with pytest.raises(ContractError,match='joined reference'):atoms.render(source,response)


def test_next_page_word_join_cannot_consume_prior_page_reference(tmp_path):
    import pymupdf
    from cps.services.reflow import skeleton
    path=tmp_path/'boundary.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=500,height=700)
        p.insert_text((50,100),'Body word.3-',fontsize=12)
        p.insert_text((50,200),'3. A complete note.',fontsize=12)
        pdf.new_page(width=500,height=700).insert_text((50,100),'continues on this page.',fontsize=12)
        pdf.save(path)
    with pymupdf.open(path) as doc:
        pages={0:[assemble.Element(kind='p',pno=0,runs=[['t','Body word.3-']],bbox=(40,80,300,120)),
                  assemble.Element(kind='p',pno=0,runs=[['t','3. A complete note.']],bbox=(40,180,300,220))],
               1:[assemble.Element(kind='p',pno=1,runs=[['t','continues on this page.']],bbox=(40,80,300,120))]}
        book=assemble.Book(pages=pages,elements=sum(pages.values(),[]),style=skeleton.BookStyle(12))
        sources=[enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in (0,1)]
        raws=[extract.read_page(doc,p) for p in (0,1)]
        left=ops.prepare(book,doc,0,sources[0],raws[0]);right=ops.prepare(book,doc,1,sources[1],raws[1],previous_source=sources[0],previous_raw=raws[0])
        c=left.model_view()['note_candidates'];ref=c['references'][0]
        response=answer(left);response['groups'][1]['role']='note'
        unbound=left.accept(book,doc,response,source_page=sources[0],raw_page=raws[0])
        response['note_bindings']=[dict(reference=ref['id'],note=c['labels'][0]['id'])]
        bound=left.accept(book,doc,response,source_page=sources[0],raw_page=raws[0])
        right_response=answer(right);right_response.update(continuation=True,boundary_join=dict(left=ref['atom'],right=json.loads(right.contract_json)['wrap_rights'][0],hyphen='keep'))
        right_plan=right.accept(book,doc,right_response,source_page=sources[1],raw_page=raws[1],previous_source=sources[0],previous_raw=raws[0])
        kw=dict(left_source=sources[0],right_source=sources[1],left_raw=raws[0],right_raw=raws[1])
        assert ops.compile_boundary(unbound,right_plan,book,doc,**kw)['hyphen']=='keep'
        with pytest.raises(ContractError,match='reference span'):ops.compile_boundary(bound,right_plan,book,doc,**kw)


def test_equal_digit_anchor_cannot_move_to_another_source_occurrence():
    source=atoms.prepare('<p>First 3 then 3 end.</p><p>3. Full note.</p>','source',0)
    candidates=notes.candidates(source)
    response=dict(snapshot=source['snapshot'],joins=[],groups=[dict(role='paragraph' if i==0 else 'note',ranges=[b['range']]) for i,b in enumerate(source['blocks'])],note_bindings=[dict(reference=candidates['references'][0]['id'],note=candidates['labels'][-1]['id'])])
    rendered=atoms.render(source,response);root=ops._tree(rendered['body'])
    before=''.join(root.itertext())
    parent=root[0];ref=next(parent.iter('a'))
    parent.text='First 3 then ';ref.tail=' end.'
    assert ''.join(root.itertext())==before
    rendered['body']=''.join(ET.tostring(n,encoding='unicode') for n in root)
    with pytest.raises(ContractError,match='position'):ops._check_output(source,response,atoms.validate(source,response),rendered)
