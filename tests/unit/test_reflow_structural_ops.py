# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-preserving operations are legal choices, not leaked semantic answers."""
import base64
import copy
import hashlib
import json
import re
import zipfile
from dataclasses import asdict
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, extract, skeleton, structural_ops as ops

pytestmark = pytest.mark.unit
X = '{http://www.w3.org/1999/xhtml}'


@pytest.fixture
def source(tmp_path):
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    body_width=pymupdf.get_text_length('Attribution: First source sentence. Following ordinary prose.',fontsize=12)
    title_width=pymupdf.get_text_length('Learning the sky',fontsize=20)
    page.insert_text((50+(body_width-title_width)/2,65),'Learning the sky',fontsize=20)
    page.insert_text((50,105),'Attribution: First source sentence. Following ordinary prose.',fontsize=12)
    page.draw_circle((200,300),60)
    page.insert_text((130,385),'Figure 1. Original caption',fontsize=10)
    page.insert_text((50,610),'7 Uncertain original reference.',fontsize=10)
    path=tmp_path/'source.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    elements=[assemble.Element(kind='p',pno=0,runs=[['t','Learning the sky']],bbox=(50,40,250,70)),
        assemble.Element(kind='p',pno=0,runs=[['t','Attribution: “First '],['t','source','italic'],
            ['t',' sentence.”'],['sup','7',0,'uncertain'],['t',' Following ordinary prose.']],bbox=(50,85,440,145)),
        assemble.Element(kind='h',pno=0,level=2,runs=[['t','Established heading']]),
        assemble.Element(kind='p',pno=0,runs=[['t','Immutable table row']],table_row=True),
        assemble.Element(kind='fig',pno=0,bbox=(135,235,265,365)),
        assemble.Element(kind='caption',pno=0,runs=[['t','Figure 1. Original caption','italic']],
                         bbox=(130,370,320,390),caption_uncertain=True)]
    book=assemble.Book(elements=elements,pages={0:elements},
        style=skeleton.BookStyle(body_size=12),
        notes=[assemble.Note(num=7,text='Uncertain original reference.',pno=0,uncertain=True,bbox=(50,590,350,620))],
        figures=[{'pno':0,'bbox':(135,235,265,365),'found':'source','needs_ink':False}])
    title=extract.read_page(doc,0).text_blocks[0].lines[0]
    elements[0].bbox=title.bbox;elements[0].line_boxes=[title.bbox]
    yield book,doc,tmp_path
    doc.close()


def prepared(source, **kw):
    book,doc,_=source
    return ops.prepare(book,doc,0,'revision-1',{'layer':'native','gold':'must not leak'},seed=19,
                       raw_page=extract.read_page(doc,0),**kw)


def choose(p, element, kind, end=None):
    return next(c['candidate_id'] for c in p.candidates() if c['element_id']==element
                and c['kind']==kind and (end is None or c['source_range'][1]==end))


def response(p, ids):
    return {'protocol':ops.PROTOCOL,'snapshot_id':p.snapshot_id,'select':ids}


def texts(path):
    with zipfile.ZipFile(path) as z:
        bodies=[ET.fromstring(z.read(n)).find(X+'body') for n in sorted(z.namelist())
                if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
        return bodies


def test_uniform_choices_contain_real_pixels_and_wrong_but_legal_alternatives(source):
    p=prepared(source);view=p.model_view();c=view['candidates']
    assert len({tuple(sorted(x)) for x in c})==1
    assert not any(word in json.dumps(view) for word in ('abstention_controls','eligible','must not leak'))
    data=base64.b64decode(view['source_image']['data_url'].split(',',1)[1])
    assert data[:2]==b'\xff\xd8' and hashlib.sha256(data).hexdigest()==view['source_image']['sha256']
    assert choose(p,'e0','heading')
    assert not any(c['element_id']=='e1' and c['kind']=='heading' for c in p.candidates())
    end=len('Attribution: “First source sentence.”7')
    assert choose(p,'e1','quote',end)
    assert choose(p,'e1','quote',end+len(' Following ordinary prose.'))
    assert not any(x['element_id'] in ('e2','e3','e4','e5') for x in c), 'no-op or unsupported source structure'
    assert prepared(source).model_view()==view, 'frozen presentation must reproduce'


def test_real_epub_wrappers_keep_words_style_inventory_and_uncertain_evidence(source):
    book,doc,tmp=source;p=prepared(source);before=copy.deepcopy(asdict(book))
    quote_end=len('Attribution: “First source sentence.”7')
    ids=[choose(p,'e0','heading'),choose(p,'e1','quote',quote_end)]
    plan=p.accept(book,doc,response(p,ids))
    baseline=tmp/'baseline.epub';selected=tmp/'selected.epub'
    build_epub.build(book,str(baseline),doc=doc)
    build_epub.build(book,str(selected),doc=doc,operation_plans=[plan])
    assert asdict(book)==before, 'wrappers must not mutate source text, inventory or metadata'
    assert build_epub.validate(str(selected))==[]
    roots=texts(selected);headings=[h for r in roots for h in r.iter(X+'h2')]
    assert any(h.text=='Learning the sky' for h in headings)
    quotes=[q for r in roots for q in r.iter(X+'blockquote')];assert len(quotes)==1
    qt=''.join(quotes[0].itertext());assert 'First source sentence.' in qt and 'Following' not in qt
    assert any(e.text=='source' for e in quotes[0].iter(X+'em'))
    assert '(?)' in qt
    notes=[n for r in roots for n in r.iter(X+'aside')]
    assert any('original-p0000.xhtml#notes'==a.get('href') for n in notes for a in n.iter(X+'a'))
    assert any('original-p0000.xhtml#caption_0'==a.get('href') for r in roots for a in r.iter(X+'a'))
    words=lambda rs:re.findall(r'\w+', ' '.join(''.join(r.itertext()) for r in rs))
    assert words(roots)==words(texts(baseline)), 'meaningful token sequence changed'
    with zipfile.ZipFile(baseline) as a,zipfile.ZipFile(selected) as b:
        assert {n:a.read(n) for n in a.namelist() if n.endswith('.jpg')}=={
            n:b.read(n) for n in b.namelist() if n.endswith('.jpg')}


@pytest.mark.parametrize('attack',['unknown','stale','duplicate','overlap','prose','retarget_note','remove_evidence'])
def test_admission_rejects_invalid_operations_atomically(source,attack):
    book,doc,_=source;p=prepared(source);cid=choose(p,'e0','heading');r=response(p,[cid])
    if attack=='unknown':r['select']=['op-unknown']
    elif attack=='stale':r['snapshot_id']='stale'
    elif attack=='duplicate':r['select']=[cid,cid]
    elif attack=='overlap':r['select'].append(choose(p,'e0','quote'))
    elif attack=='prose':r['html']='<h2>invented words</h2>'
    elif attack=='retarget_note':r['note_number']=188
    else:r['remove_evidence']=True
    before=copy.deepcopy(asdict(book))
    with pytest.raises(ops.ContractError):p.accept(book,doc,r)
    assert asdict(book)==before


@pytest.mark.parametrize('metadata',['note','style','geometry'])
def test_a_cached_plan_cannot_erase_changed_source_metadata_at_build(source,metadata):
    book,doc,tmp=source;p=prepared(source);plan=p.accept(book,doc,response(p,[choose(p,'e0','heading')]))
    if metadata=='note':book.notes[0].uncertain=False
    elif metadata=='style':book.pages[0][1].runs[1][2]='changed-style'
    else:book.figures[0]['bbox']=(1,1,2,2)
    target=tmp/'stale.epub'
    with pytest.raises(ops.ContractError):build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
    assert not target.exists()


def test_bounds_and_abstention_are_coverage_not_semantic_success(source):
    book,doc,tmp=source;p=prepared(source,max_candidates=2,max_context_chars=1000)
    coverage=p.model_view()['coverage']
    assert coverage['legal_choices_sent']<=2
    assert coverage['omitted_element_ids'] and coverage['unsupported_operation_kinds']
    assert 'source_ambiguous' not in coverage
    plan=p.accept(book,doc,response(p,[]));assert plan.compile(book,doc)=={}


def test_ocr_word_uncertainty_without_a_preserving_renderer_is_unsupported(source):
    """The Book cannot bind Recovery-only word annotations; do not erase them."""
    book,doc,_=source
    with pytest.raises(ops.ContractError):
        ops.prepare(book,doc,0,'revision-1',{'layer':'ocr','uncertain_words':1})


def test_builder_refuses_to_overwrite_enriched_current_html(source):
    """A source Book plan does not authorize erasing another current HTML state."""
    book,doc,tmp=source;p=prepared(source)
    plan=p.accept(book,doc,response(p,[choose(p,'e0','heading')]))
    current=build_epub.page_fragment(book,0).replace('Following',
        '<span class="reflow-uncertain">Following (?)</span>')
    target=tmp/'enriched.epub'
    with pytest.raises(ops.ContractError):
        build_epub.build(book,str(target),doc=doc,page_html={0:current},operation_plans=[plan])
    assert not target.exists()


@pytest.mark.parametrize('mode', ['none', 'subset', 'all'])
def test_verifier_approved_subset_is_the_only_rendered_source_and_nav_change(source, mode):
    book, doc, tmp = source
    p = prepared(source)
    ids = [choose(p, 'e0', 'heading'), choose(p, 'e1', 'quote')]
    verification = ops.prepare_verification(book, doc, p.accept(book, doc, response(p, ids)))
    approved = [] if mode == 'none' else ids[:1] if mode == 'subset' else ids
    reply = dict(verification.empty_response(), approve=approved)
    plan = verification.accept(book, doc, reply)
    assert set(plan.selected) == set(approved)
    before = copy.deepcopy(asdict(book))
    target = tmp / ('verified-' + mode + '.epub')
    build_epub.build(book, str(target), doc=doc, operation_plans=[plan])
    assert build_epub.validate(str(target)) == [] and asdict(book) == before
    roots = texts(target)
    assert sum(1 for r in roots for h in r.iter(X+'h2') if h.text == 'Learning the sky') == bool(approved)
    assert sum(1 for r in roots for q in r.iter(X+'blockquote')) == (mode == 'all')
    with zipfile.ZipFile(target) as z:
        nav = ET.fromstring(z.read('OEBPS/nav.xhtml'))
        assert ('Learning the sky' in ''.join(nav.itertext())) == bool(approved)
    assert any('original-p0000.xhtml#notes' == a.get('href') for r in roots for a in r.iter(X+'a'))
    baseline = tmp / 'verification-baseline.epub'
    build_epub.build(book, str(baseline), doc=doc)
    words = lambda rs: re.findall(r'\w+', ' '.join(''.join(r.itertext()) for r in rs))
    assert words(roots) == words(texts(baseline))
    assert any(e.text == 'source' for r in roots for e in r.iter(X+'em'))
    with zipfile.ZipFile(baseline) as a, zipfile.ZipFile(target) as b:
        assert {n:a.read(n) for n in a.namelist() if n.endswith('.jpg')} == {
            n:b.read(n) for n in b.namelist() if n.endswith('.jpg')}


@pytest.mark.parametrize('attack', ['malformed', 'duplicate', 'unknown', 'unproposed',
                                    'snapshot', 'proposal', 'protocol', 'extra', 'source'])
def test_verifier_atomic_rejection_never_falls_back_to_proposal(source, attack):
    book, doc, tmp = source
    p = prepared(source); cid = choose(p, 'e0', 'heading')
    verification = ops.prepare_verification(book, doc, p.accept(book, doc, response(p, [cid])))
    reply = dict(verification.empty_response(), approve=[cid])
    if attack == 'malformed': reply = 'not JSON'
    elif attack == 'duplicate': reply['approve'] *= 2
    elif attack == 'unknown': reply['approve'] = ['op-unknown']
    elif attack == 'unproposed': reply['approve'] = [choose(p, 'e1', 'quote')]
    elif attack == 'snapshot': reply['snapshot_id'] = 'stale'
    elif attack == 'proposal': reply['proposal_id'] = 'stale'
    elif attack == 'protocol': reply['protocol'] = ops.PROTOCOL
    elif attack == 'extra': reply['select'] = [cid]
    else: book.pages[0][0].punctuation_uncertain = True
    with pytest.raises(ops.ContractError): verification.accept(book, doc, reply)
    result = verification.resolve(book, doc, reply)
    assert result.rejected and result.reason and result.plan is None
    target = tmp / 'fallback.epub'
    build_epub.build(book, str(target), doc=doc, operation_plans=result.operation_plans)
    assert not any(h.text == 'Learning the sky' for r in texts(target) for h in r.iter(X+'h2'))


def test_verifier_proposal_binding_rechecks_overlap_and_final_staleness(source):
    book, doc, tmp = source; p = prepared(source)
    ids = [choose(p, 'e0', 'heading'), choose(p, 'e0', 'quote')]
    with pytest.raises(ops.ContractError):
        ops.prepare_verification(book, doc, ops.OperationPlan(p, tuple(ids)))
    v = ops.prepare_verification(book, doc, p.accept(book, doc, response(p, ids[:1])))
    changed = ops.prepare_verification(book, doc, p.accept(book, doc, response(p, ids[1:])))
    assert v.proposal_id != changed.proposal_id
    plan = v.accept(book, doc, dict(v.empty_response(), approve=ids[:1]))
    book.notes[0].uncertain = False
    with pytest.raises(ops.ContractError):
        build_epub.build(book, str(tmp/'stale-verified.epub'), doc=doc, operation_plans=[plan])
    assert not (tmp/'stale-verified.epub').exists()


@pytest.fixture
def enriched(source):
    from cps.services.reflow.enriched_source import prepare_source_page
    book, doc, _ = source
    records = [{'token':'source','score':22,'source_bbox':[1,2,3,4]},
               {'token':'source','score':31,'source_bbox':[5,6,7,8]},
               {'token':'.s','score':7,'source_bbox':[9,10,11,12]}]
    layer = {'layer':'ocr','uncertain_words':3,'orientation':0}
    canonical = prepare_source_page(book,0,layer,records)
    p = ops.prepare(book,doc,0,'enriched-1',layer,source_page=canonical,raw_page=extract.read_page(doc,0))
    return canonical,p,layer,records


def test_enriched_marks_records_survive_actual_approved_and_fallback_builder(source,enriched,monkeypatch):
    book,doc,tmp=source;canonical,p,layer,records=enriched
    report=canonical.report()
    assert report['marked']==1 and report['unplaced_record_indices']==[1,2]
    assert report['uncertain'][0]['score']!=report['uncertain'][1]['score']
    assert json.loads(canonical.records_json)==records
    assert p.model_view()['context']['source_enrichment']['marked']==1
    def forbidden(*a,**kw):raise AssertionError('annotator must not run after canonical preparation')
    from cps.services.reflow import annotate
    monkeypatch.setattr(annotate,'annotate_page',forbidden)
    for selection in ([],[choose(p,'e0','heading')],[choose(p,'e1','quote')]):
        plan=p.accept(book,doc,response(p,selection),source_page=canonical)
        target=tmp/('enriched-'+str(len(selection))+str(bool(selection and selection[0]==choose(p,'e0','heading')))+'.epub')
        build_epub.build(book,str(target),doc=doc,source_pages={0:canonical},operation_plans=[plan])
        assert build_epub.validate(str(target))==[]
        roots=texts(target)
        marks=[e for r in roots for e in r.iter(X+'span') if e.get('class')=='reflow-uncertain' and ''.join(e.itertext())=='source']
        assert len(marks)==1
        assert any(''.join(e.itertext())=='source' for r in roots for e in r.iter(X+'em'))
        assert any(a.get('href')=='original-p0000.xhtml#page' for r in roots for a in r.iter(X+'a'))
        if selection and selection[0]==choose(p,'e0','heading'):
            chapter=next(r for r in roots if any('Learning the sky' in ''.join(h.itertext()) for h in r.iter(X+'h2')))
            assert 'Some note labels or associations' in ''.join(chapter.itertext())
            assert 'OCR readings are uncertain' in ''.join(chapter.itertext())
    fallback=tmp/'enriched-fallback.epub'
    build_epub.build(book,str(fallback),doc=doc,source_pages={0:canonical},page_html={0:'<script>bad</script>'})
    assert any('source'==''.join(e.itertext()) for r in texts(fallback) for e in r.iter(X+'span'))


@pytest.mark.parametrize('change',['score','bbox','html','missing'])
def test_enriched_cache_and_final_builder_require_current_confidence(source,enriched,change):
    from cps.services.reflow.enriched_source import prepare_source_page
    from dataclasses import replace
    book,doc,tmp=source;canonical,p,layer,records=enriched
    cid=choose(p,'e0','heading');reply=response(p,[cid])
    plan=p.accept(book,doc,reply,source_page=canonical)
    if change=='score':records[0]['score']=99
    if change=='bbox':records[0]['source_bbox']=[11,12,13,14]
    current=prepare_source_page(book,0,layer,records)
    if change=='html':current=replace(current,html=current.html.replace('reflow-uncertain','removed'))
    if change=='missing':current=None
    with pytest.raises(ops.ContractError):p.accept(book,doc,reply,source_page=current)
    target=tmp/'stale-enriched.epub'
    with pytest.raises(ops.ContractError):
        build_epub.build(book,str(target),doc=doc,operation_plans=[plan],source_pages={0:current} if current else {})
    assert not target.exists()


def test_canonical_duplicate_occurrence_cross_style_entity_and_atomic_boundary(source):
    from cps.services.reflow.enriched_source import prepare_source_page, wrap
    book,doc,_=source
    e=book.pages[0][0]
    e.runs=[['t','same same & “one. two” '],['t','cross','italic'],['t','style']]
    records=[{'token':'same','score':20}, {'token':'one. two','score':15},
             {'token':'crossstyle','score':9}, {'token':'.s','score':4}]
    layer={'layer':'ocr','uncertain_words':4}
    canonical=prepare_source_page(book,0,layer,records)
    report=canonical.report();assert report['marked']==2 and report['unplaced_record_indices']==[2,3]
    # Mark occurrence, escaping and style are established before wrapping.
    fragment=build_epub.split_blocks(canonical.html)[0]
    assert '<span class="reflow-uncertain" title="uncertain reading">same</span> same &amp;' in fragment
    with pytest.raises(ops.ContractError):wrap(fragment,e,[ops._Spec(0,'quote',0,len('same same & “one.'))])
    p=ops.prepare(book,doc,0,'enriched-1',layer,source_page=canonical,raw_page=extract.read_page(doc,0))
    assert all(c['source_range'][1]!=len('same same & “one.') for c in p.candidates() if c['element_id']=='e0')
    plan=p.accept(book,doc,response(p,[choose(p,'e0','quote',len(e.text))]),source_page=canonical)
    rendered=canonical.render(book,plan.compile(book,doc,source_page=canonical))
    assert fragment in rendered, 'full quote must move the already enriched paragraph unchanged'
    replay=prepare_source_page(book,0,layer,records)
    assert replay.identity==canonical.identity
    assert p.accept(book,doc,response(p,[]),source_page=replay).selected==()


def test_enriched_verifier_and_full_recovery_provenance_round_trip(source):
    from cps.services.reflow.enriched_source import prepare_recovery_page
    from cps.services.reflow.source import Recovery,PageRecovery
    book,doc,_=source
    recovery=Recovery(provenance={0:PageRecovery(pno=0,layer='ocr',uncertain_words=1,
        uncertain=[{'token':'source','score':23,'source_bbox':[1,2,3,4]}],
        page_rect=(0,0,500,700),derotation=(1,0,0,1,0,0),orientation=0)})
    canonical=prepare_recovery_page(book,0,recovery)
    provenance=asdict(recovery.provenance[0])
    assert json.loads(canonical.provenance_json)['derotation']==[1,0,0,1,0,0]
    p=ops.prepare(book,doc,0,'new-source-contract',provenance,source_page=canonical,raw_page=extract.read_page(doc,0))
    cid=choose(p,'e0','heading')
    plan=p.accept(book,doc,response(p,[cid]),source_page=canonical)
    v=ops.prepare_verification(book,doc,plan,source_page=canonical)
    assert v.accept(book,doc,dict(v.empty_response(),approve=[cid]),source_page=canonical).selected==(cid,)
    assert v.resolve(book,doc,dict(v.empty_response(),approve=[cid])).rejected


def test_recovery_cache_observation_does_not_change_semantic_snapshot_or_erase_current_provenance(source):
    from cps.services.reflow.enriched_source import prepare_recovery_page
    from cps.services.reflow.source import Recovery,PageRecovery
    book,doc,_=source
    recovery=Recovery(provenance={0:PageRecovery(pno=0,layer='ocr',uncertain_words=1,
        uncertain=[{'token':'source','score':23,'source_bbox':[1,2,3,4]}],seconds=1.2,reused=False)})
    cold=prepare_recovery_page(book,0,recovery)
    p=ops.prepare(book,doc,0,'stable-contract',asdict(recovery.provenance[0]),source_page=cold,raw_page=extract.read_page(doc,0))
    recovery.provenance[0].seconds=.001;recovery.provenance[0].reused=True
    warm=prepare_recovery_page(book,0,recovery)
    q=ops.prepare(book,doc,0,'stable-contract',asdict(recovery.provenance[0]),source_page=warm,raw_page=extract.read_page(doc,0))
    assert cold.identity==warm.identity and p.model_view()==q.model_view()
    assert json.loads(cold.provenance_json)['reused'] is False
    assert json.loads(warm.provenance_json)['reused'] is True
    assert p.accept(book,doc,response(p,[choose(p,'e0','heading')]),source_page=warm).selected
