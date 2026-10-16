"""Report only current compiled note bindings, without losing legacy notes."""
import copy
import json
import hashlib
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import assemble, build_epub, enriched_source, extract, layout_build
from cps.services.reflow import layout_ops, pipeline, report, native_codec
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_structural_ops import source, X
from tests.unit.test_reflow_layout_note_bindings import note_source

pytestmark = pytest.mark.unit


@pytest.fixture
def mixed_notes(note_source):
    book, doc, tmp, _, raw, _, response = note_source
    page = doc.new_page(width=500, height=700)
    page.insert_text((50,100), '6 Existing opaque source note.', fontsize=12)
    book.pages[1] = []
    book.notes = [assemble.Note(num=6, text='Existing opaque source note.', pno=1)]
    book.stats = dict(book.stats, notes=1)
    sources = {p: enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in (0,1)}
    raws = {0:raw, 1:extract.read_page(doc,1)}
    prepared = layout_ops.prepare(book,doc,0,sources[0],raw)
    response = copy.deepcopy(response); response['snapshot'] = prepared.snapshot_id
    plan = prepared.accept(book,doc,response,source_page=sources[0],raw_page=raw)
    result = pipeline.ReflowResult(book=book,raw_pages=list(raws.values()),
        page_html={p:s.html for p,s in sources.items()},
        outcomes={0:pipeline.PageOutcome(pno=0,source='model',gate='PASS'),1:pipeline.PageOutcome(pno=1)})
    result.source_pages = sources; result.layout_plans = [plan]
    return result, doc, tmp


def publish(result, doc, target, payload):
    return build_epub.build(result.book,str(target),doc=doc,page_html=result.page_html,
        source_pages=result.source_pages,layout_plans=result.layout_plans,
        raw_pages={r.pno:r for r in result.raw_pages},sidecar=report.sidecar(payload),
        report_html=lambda links,losses:report.about_page(payload,links=links,losses=losses),
        identifier='urn:uuid:11111111-1111-4111-8111-111111111111')


def test_publication_counts_distinct_equal_number_notes_in_audit_json_and_human_report(mixed_notes):
    result, doc, tmp = mixed_notes
    payload = report.numbers(result,document=doc)
    assert payload['structure']['footnotes'] == 2
    assert payload['structure']['footnotes_before_layout'] == 1
    assert payload['structure']['footnotes_bound'] == 1
    assert result.book.stats['notes'] == 1, 'Reporting must not accumulate mutations in baseline stats'
    assert report.numbers(result,document=doc) == payload
    built = publish(result,doc,tmp/'note-report.epub',payload)
    assert built.notes == built.sidecar['notes'] == 2
    assert built.sidecar['notes_source'] == built.sidecar['notes_bound'] == 1
    audit = built.sidecar['layout_operations']
    assert audit['note_bindings_applied'] == 1
    assert audit['pages'][0]['note_bindings'] == json.loads(result.layout_plans[0].answer_json)['note_bindings']
    assert audit['pages'][0]['note_bindings_applied'] == 1
    assert audit['pages'][0]['page'] == 0
    assert audit['pages'][0]['snapshot'] == result.layout_plans[0].prepared.snapshot_id
    assert native_codec.loads(native_codec.dumps(built)).sidecar == built.sidecar
    with zipfile.ZipFile(built.path) as archive:
        sidecar = json.loads(archive.read('META-INF/reflow.json'))
        assert sidecar['structure'] == payload['structure']
        about = archive.read('OEBPS/reflow-about.xhtml').decode()
        assert '<td>Footnotes</td><td>2</td>' in about
        assert '<td>Additional footnotes linked by layout review</td><td>1</td>' in about
        assert archive.getinfo('OEBPS/images/fig_p0000_0.jpg').file_size > 0
        notes = [n for name in archive.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')
                 for n in ET.fromstring(archive.read(name)).iter() if n.tag in (X+'aside',X+'div') and n.get('class') == 'footnote']
        assert len(notes) == 2
        assert {n.tag for n in notes} == {X+'aside',X+'div'}
        assert len({n.get('id') for n in notes}) == 2
    assert build_epub.validate(built.path) == []


def test_no_binding_reports_remain_legacy_and_preview_or_proposal_never_counts(mixed_notes):
    result, doc, tmp = mixed_notes
    plan = result.layout_plans.pop()
    result.preview_html = {0:'<aside class="footnote">Untrusted report bait</aside>'}
    result.proposed_layouts = [json.loads(plan.answer_json)]
    baseline = report.numbers(result)
    assert baseline['structure']['footnotes'] == 1
    assert 'footnotes_bound' not in baseline['structure']
    assert report.numbers(result,document=doc) == baseline
    result.layout_plans = None
    assert report.numbers(result) == baseline
    del result.layout_plans
    assert report.numbers(result) == baseline
    # An admitted grouping without bindings retains the exact old audit shape.
    response = json.loads(plan.answer_json); response.pop('note_bindings')
    raw = result.raw_pages[0]
    plain = plan.prepared.accept(result.book,doc,response,source_page=result.source_pages[0],raw_page=raw)
    result.layout_plans = [plain]
    compiled = layout_build.compile_pages(result.book,doc,[plain],result.source_pages,{r.pno:r for r in result.raw_pages},result.page_html)
    audit = layout_build.report(compiled)
    assert set(audit) == {'applied_pages','pages'}
    assert set(audit['pages'][0]) == {'page','snapshot','gates','groups','joins'}
    built = publish(result,doc,tmp/'legacy-report.epub',report.numbers(result))
    assert built.notes == 1 and 'notes_bound' not in built.sidecar


@pytest.mark.parametrize('damage',['missing_document','duplicate','absent_page','stale_raw','stale_source','response_dict','uncompiled_wrapper'])
def test_reporting_cannot_count_unapplied_or_stale_source_authority(mixed_notes,damage):
    result, doc, _ = mixed_notes
    if damage == 'duplicate': result.layout_plans *= 2
    elif damage == 'absent_page': result.page_html.pop(0)
    elif damage == 'stale_raw': result.raw_pages[0].blocks[0].lines[0].spans[0].text += ' changed'
    elif damage == 'stale_source': result.book.elements[0].runs[0][1] += ' changed'
    elif damage == 'response_dict': result.layout_plans = [json.loads(result.layout_plans[0].answer_json)]
    elif damage == 'uncompiled_wrapper':
        with pytest.raises(ContractError): layout_build.report({0:result.layout_plans[0]})
        return
    with pytest.raises(ContractError): report.numbers(result,document=None if damage=='missing_document' else doc)


def test_reporting_only_change_preserves_navigation_chapters_and_source_resources(mixed_notes,monkeypatch):
    result, doc, tmp = mixed_notes
    current = publish(result,doc,tmp/'current-report.epub',report.numbers(result,document=doc))
    # Reconstruct the former reporting boundary only. The exact same accepted
    # source plan and publication renderer produce both packages.
    def legacy_audit(compiled):
        return dict(applied_pages=sorted(compiled),pages=[dict(page=pno,snapshot=c.snapshot_id,
            gates=json.loads(c.word_gates_json),groups=json.loads(c.groups_json),
            joins=json.loads(c.answer_json)['joins']) for pno,c in sorted(compiled.items())])
    monkeypatch.setattr(layout_build,'note_report',lambda *a,**kw:{})
    monkeypatch.setattr(layout_build,'report',legacy_audit)
    legacy = publish(result,doc,tmp/'before-report.epub',report.numbers(result))
    assert legacy.notes == 1 and current.notes == 2
    with zipfile.ZipFile(legacy.path) as before, zipfile.ZipFile(current.path) as after:
        resources = [n for n in before.namelist() if n.endswith(('.xhtml','.ncx','.jpg','.png','.css'))
                     and n != 'OEBPS/reflow-about.xhtml']
        assert resources and set(before.namelist()) == set(after.namelist())
        hashes = {}
        for name in resources:
            assert before.read(name) == after.read(name), name
            hashes[name] = hashlib.sha256(after.read(name)).hexdigest()
        assert before.read('OEBPS/reflow-about.xhtml') != after.read('OEBPS/reflow-about.xhtml')
        (tmp/'resource-proof.json').write_text(json.dumps(dict(status='IDENTICAL_READING_AND_NAVIGATION_RESOURCES',sha256=hashes),indent=1))
    assert build_epub.validate(current.path) == []


def test_native_current_source_report_and_publication_keep_checked_note_accounting(tmp_path):
    import pymupdf
    from cps.services.reflow.native_ipc import NativeDocument
    from tests.unit.test_reflow_layout_ops import answer
    path=tmp_path/'native-note-report.pdf'
    pdf=pymupdf.open();page=pdf.new_page(width=500,height=700)
    page.insert_text((50,100),'A source reference ',fontsize=12)
    marker_x=50+pymupdf.get_text_length('A source reference ',fontsize=12)
    page.insert_text((marker_x,96),'6',fontsize=8)
    page.insert_text((marker_x+8,100),' remains in this paragraph.',fontsize=12)
    page.insert_text((50,200),'6. A complete source note in ordinary body text.',fontsize=12)
    pdf.save(path);pdf.close()
    with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
        result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
        canonical=enriched_source.prepare_source_page(result.book,0,{'layer':'native'})
        raw=result.raw_pages[0]
        prepared=layout_ops.prepare(result.book,doc,0,canonical,raw)
        candidates=prepared.model_view().get('note_candidates')
        assert candidates and len(candidates['references'])==1,canonical.html
        response=answer(prepared)
        label=next(a['id'] for a in candidates['labels'] if a['label']=='6')
        response['groups'][-1]['role']='note'
        response['note_bindings']=[dict(reference=candidates['references'][0]['id'],note=label)]
        plan=prepared.accept(result.book,doc,response,source_page=canonical,raw_page=raw)
        result.source_pages={0:canonical};result.layout_plans=[plan]
        payload=report.numbers(result,document=doc)
        assert payload['structure']['footnotes_bound']==1
        built=build_epub.build(result.book,str(tmp_path/'native-note-report.epub'),doc=doc,
            page_html=result.page_html,source_pages=result.source_pages,layout_plans=[plan],raw_pages={0:raw},
            sidecar=report.sidecar(payload),report_html={'payload':payload,'show_cost':False})
        with zipfile.ZipFile(built.path) as archive:
            roots=[ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
            assert any(n.tag==X+'div' and n.get('class')=='footnote' for root in roots for n in root.iter())
            assert any(n.find(X+'a') is not None for root in roots for n in root.iter(X+'sup'))
        assert built.notes==1 and built.sidecar['notes_bound']==1
        assert built.sidecar['structure']['footnotes_bound']==1
        assert built.sidecar['layout_operations']['note_bindings_applied']==1
        assert build_epub.validate(built.path)==[]
