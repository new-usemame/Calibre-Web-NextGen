"""Raster-reviewed emphasis wraps immutable plain atom ranges, never new words."""
import copy
import json
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import _layout_atoms as atoms, layout_ops as ops, layout_requests as requests
from cps.services.reflow.structural_ops import ContractError

pytestmark=pytest.mark.unit

def fixture(fragment='<p>😀&amp; Label word then Label word end.</p>'):
    s=atoms.prepare(fragment,'fixture',0)
    a=dict(snapshot=s['snapshot'],groups=[dict(role='source' if b['opaque'] else 'paragraph',ranges=[b['range']]) for b in s['blocks']],joins=[],emphasis=[['a1','a2']])
    return s,a

def checked(s,a):
    groups=atoms.validate(s,a);r=atoms.render(s,a);ops._check_output(s,a,groups,r)
    return r

def test_inline_labels_preserve_exact_source_and_roles():
    s,a=fixture();a['emphasis'].append(['a4','a5'])
    r=checked(s,a);root=ops._tree(r['body'])
    assert [n.tag for n in root]==['p']
    assert [n.text for n in root.iter('strong')]==['Label word','Label word']
    assert ''.join(root[0].itertext())=='😀& Label word then Label word end.'
    from cps.services.reflow import _layout_emphasis as emphasis
    restored=emphasis.verified_source_output(s,a,atoms.validate(s,a),r)
    plain=copy.deepcopy(a);plain.pop('emphasis')
    assert ET.tostring(ops._tree(restored['body']))==ET.tostring(ops._tree(atoms.render(s,plain)['body']))

@pytest.mark.parametrize('damage',['move_equal','duplicate','nested','attributes','text','prefix','suffix','root','tail'])
def test_corrupt_renderer_cannot_supply_emphasis_expectations(damage):
    s,a=fixture();r=checked(s,a);root=ops._tree(r['body']);p=root[0];n=p[0]
    if damage=='move_equal':p.text='😀& Label word then ';n.tail=' end.'
    elif damage=='duplicate':p.append(copy.deepcopy(n))
    elif damage=='nested':n.append(ET.Element('em'))
    elif damage=='attributes':n.set('style','display:none')
    elif damage=='text':n.text='Label  word'
    elif damage=='prefix':p.text+=' '
    elif damage=='suffix':n.tail+=' '
    elif damage=='root':root.text='hidden'
    else:p.tail='hidden'
    r['body']=(root.text or '')+''.join(ET.tostring(n,encoding='unicode') for n in root)
    with pytest.raises(ContractError):ops._check_output(s,a,atoms.validate(s,a),r)

@pytest.mark.parametrize('ranges',[[['a2','a1']],[['a1','a2'],['a2','a3']],[['a1','a2'],['a1','a2']],[[]],[['unknown','a2']]])
def test_invalid_emphasis_ranges_fail(ranges):
    s,a=fixture();a['emphasis']=ranges
    with pytest.raises(ContractError):checked(s,a)

@pytest.mark.parametrize('fragment,role',[
 ('<p>A <em>Label word</em> end.</p>','paragraph'),
 ('<p>A <span class="reflow-uncertain">Label word</span> end.</p>','paragraph'),
 ('<p>A <a href="#old">Label word</a> end.</p>','paragraph'),
 ('<p>A Label word end.</p>','note'),
 ('<p>A Label word end.</p>','furniture'),
 ('<p>A Label word end.</p>','heading2'),
 ('<aside>A Label word end.</aside>','source')])
def test_nonplain_or_nonprose_emphasis_rejected(fragment,role):
    s,a=fixture(fragment);a['groups'][0]['role']=role
    with pytest.raises(ContractError):checked(s,a)

def test_join_and_group_boundaries_fail_closed():
    s,a=fixture('<p>A Label word end.</p>')
    a['groups']=[dict(role='paragraph',ranges=[['a0','a1']]),dict(role='quote',ranges=[['a2','a3']])]
    with pytest.raises(ContractError):checked(s,a)
    s=atoms.prepare('<p>A la- bel end.</p>','source',0,wrap_lefts=['a1'],wrap_rights=['a2'])
    a=dict(snapshot=s['snapshot'],groups=[dict(role='paragraph',ranges=[['a0','a3']])],joins=[dict(left='a1',right='a2',hyphen='drop')],emphasis=[['a1','a2']])
    with pytest.raises(ContractError):checked(s,a)
    a['joins']=[]
    with pytest.raises(ContractError):checked(s,a)  # excludes a future boundary-left join too


def test_unrelated_sup_and_plain_note_references_coexist_but_overlap_fails():
    from cps.services.reflow import _layout_notes as notes
    s=atoms.prepare('<p>A Label word.3 and <sup class="noteref-unresolved">4</sup> remain.</p><p>3. First note.</p><p>4. Second note.</p>','source',0)
    a=dict(snapshot=s['snapshot'],groups=[dict(role='paragraph' if i==0 else 'note',ranges=[b['range']]) for i,b in enumerate(s['blocks'])],joins=[],emphasis=[['a1','a1']])
    c=notes.candidates(s)
    a['note_bindings']=[dict(reference=r['id'],note=next(n['id'] for n in c['labels'] if n['label']==r['label'])) for r in c['references']]
    r=checked(s,a)
    assert len(list(ops._tree(r['body']).iter('strong')))==1
    assert len([n for n in ops._tree(r['body']).iter('a') if n.get(notes.EPUB_TYPE)=='noteref'])==2
    a['emphasis']=[['a1','a2']]
    with pytest.raises(ContractError):checked(s,a)


def test_review_explicitly_binds_emphasis_and_can_reject_semantics():
    s,a=fixture()
    import hashlib
    s['binding']=dict(page=0, pdf_sha256='pdf', source_identity=s['identity'],
                      raster_sha256=hashlib.sha256(b'raster').hexdigest())
    s.pop('snapshot')
    s['snapshot']=atoms.digest(s)
    a['snapshot']=s['snapshot']
    prepared=ops.PreparedLayout(0,'pdf',s['snapshot'],s['identity'],ops._json(s),'{}',b'raster')
    plan=ops.LayoutPlan(prepared,ops._json(a));request=requests.review(prepared,plan)
    view=json.loads(request['messages'][1]['content'])
    assert view['emphasis']==a['emphasis']
    assert view['emphasis_source_atoms']==[dict(id='a1',text='Label'),dict(id='a2',text='word')]
    assert view['proposed_layout'][0]['text']=='😀& Label word then Label word end.'
    rejection=dict(snapshot=view['snapshot'],accept=False,continuation_accept=False,problems=['unsupported_change'])
    assert requests.validate_review(prepared,plan,rejection).accepted_plan is None
    a['emphasis']=[['a4','a5']]
    with pytest.raises(ContractError,match='stale'):requests.validate_review(prepared,ops.LayoutPlan(prepared,ops._json(a)),rejection)
    from cps.services.reflow import layout_domain
    proposed=layout_domain.proposal(prepared)
    import jsonschema
    from tests.unit.test_reflow_boundary_construction import explicit_fixture
    from cps.services.reflow import layout_domain
    emitted=layout_domain.choice_fixture(s,explicit_fixture(prepared,dict(a,continuation=False,boundary_join=None)))
    jsonschema.validate(emitted,proposed['response_schema'])
    emitted['emphasis']=[['invented','a2']]
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(emitted,proposed['response_schema'])


@pytest.mark.parametrize('native',[False,True])
def test_actual_publication_preserves_every_resource_except_checked_emphasis(tmp_path,native):
    import zipfile
    import pymupdf
    from cps.services.reflow import extract, assemble, enriched_source, build_epub
    from cps.services.reflow.native_ipc import NativeDocument
    from tests.unit.test_reflow_layout_ops import answer
    path=tmp_path/'source.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=500,height=700)
        p.insert_text((50,100),'Label words stay inside this ordinary paragraph.',fontsize=12)
        p.insert_text((50,200),'Another paragraph preserves its source wording.',fontsize=12)
        pdf.save(path)
    context=NativeDocument(path,scratch_root=tmp_path/'native') if native else pymupdf.open(path)
    with context as doc:
        if native:
            result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
            book=result.book;raw=result.raw_pages[0]
        else:
            raw=extract.read_page(doc,0)
            from cps.services.reflow import skeleton
            regions=[skeleton.Region('body',list(block.lines),bbox=block.bbox) for block in raw.text_blocks]
            book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,regions)],skeleton.BookStyle(12),[raw])
        page=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        prepared=ops.prepare(book,doc,0,page,raw);s=json.loads(prepared.contract_json);a=answer(prepared)
        ids=[n['id'] for n in s['atoms'] if n['text'] in ('Label','words')];assert len(ids)==2
        paths=[]
        for marked in (False,True):
            if marked:a['emphasis']=[ids]
            plan=prepared.accept(book,doc,a,source_page=page,raw_page=raw)
            out=build_epub.build(book,str(tmp_path/('bound.epub' if marked else 'unbound.epub')),doc=doc,source_pages={0:page},layout_plans=[plan],raw_pages={0:raw},identifier='emphasis-fixture')
            assert build_epub.validate(out.path)==[];paths.append(out.path)
    with zipfile.ZipFile(paths[0]) as before,zipfile.ZipFile(paths[1]) as after:
        assert before.namelist()==after.namelist()
        strong=0
        for name in before.namelist():
            left,right=before.read(name),after.read(name)
            if name.endswith('.xhtml'):
                l,r=ET.fromstring(left),ET.fromstring(right)
                for parent in r.iter():
                    for n in list(parent):
                        if n.tag.endswith('}strong') and n.get('id','').startswith('emphasis-'):
                            strong+=1;assert n.text=='Label words'
                            index=list(parent).index(n);value=(n.text or '')+(n.tail or '')
                            if index:parent[index-1].tail=(parent[index-1].tail or '')+value
                            else:parent.text=(parent.text or '')+value
                            parent.remove(n)
                assert ET.tostring(l)==ET.tostring(r),name
            elif name.endswith('.json') or name.endswith('.opf'):
                continue  # paired artifact probe audits volatile fields separately
            else:assert left==right,name
        assert strong==1

def test_compact_schema_retains_exact_plain_id_eligibility():
    import jsonschema
    s,a=fixture('<p>Plain start <em>styled</em> middle <sup>4</sup> final words.</p><aside>Opaque source.</aside>')
    view=atoms.model_view(s);schema=requests.response_schema(view)
    expected={n['id'] for n in s['atoms'] if n['text'] in ('Plain','start','middle','final','words.')}
    order=[n['id'] for n in s['atoms']]
    expanded={identifier for first,last in view['emphasis_ranges'] for identifier in order[order.index(first):order.index(last)+1]}
    assert expanded==expected
    validator=jsonschema.Draft202012Validator(schema)
    a.update(continuation=False,boundary_join=None)
    for identifier in order+['invented']:
        a['emphasis']=[[identifier,identifier]]
        assert validator.is_valid(a)==(identifier in expected),identifier
    a['emphasis']=[]
    rendered=checked(s,a)
    assert len(list(ops._tree(rendered['body']).iter('em')))==1
