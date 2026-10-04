"""Checked associations must survive preservation, review and EPUB publication."""
import copy
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import assemble, enriched_source, extract, build_epub
from cps.services.reflow import layout_ops as ops, layout_requests as requests
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_structural_ops import source, X
from tests.unit.test_reflow_layout_ops import answer

pytestmark = pytest.mark.unit
E = '{http://www.idpf.org/2007/ops}type'


@pytest.fixture
def note_source(source):
    book, doc, tmp = source
    book.notes = []
    book.elements[1].runs = [['t', 'A source reference '], ['sup', '6', 0], ['t', ' remains.']]
    book.elements.append(assemble.Element(kind='p', pno=0, runs=[['t', '6. The locked role remains.']]))
    page = enriched_source.prepare_source_page(book, 0, {'layer': 'native'})
    raw = extract.read_page(doc, 0)
    prepared = ops.prepare(book, doc, 0, page, raw)
    contract = json.loads(prepared.contract_json)
    ref = next(a['id'] for a in contract['atoms'] if a['html'] == '<sup class="noteref-unresolved">6</sup>')
    note = next(a['id'] for a in contract['atoms'] if a['text'] == '6.')
    response = answer(prepared)
    response['groups'][-1]['role'] = 'note'
    response['groups'][2]['role'] = 'heading1'
    response['note_bindings'] = [dict(reference=ref, note=note)]
    return book, doc, tmp, page, raw, prepared, response


def admit(fixture, response=None):
    book, doc, _, page, raw, prepared, default = fixture
    return prepared.accept(book, doc, default if response is None else response, source_page=page, raw_page=raw)


def compile_plan(fixture, response=None):
    book, doc, _, page, raw, _, _ = fixture
    return admit(fixture, response).compile(book, doc, source_page=page, raw_page=raw)


def test_checked_links_keep_source_words_sup_and_resources_and_absent_binding_is_unchanged(note_source):
    out = compile_plan(note_source)
    root = ops._tree(out.body)
    ref = next(a for a in root.iter('a') if a.get(E) == 'noteref')
    note = next(a for a in root.iter() if a.get(E) == 'footnote')
    back = note.find('a')
    assert ref.get('href') == '#' + note.get('id')
    assert back.get('href') == '#' + ref.get('id')
    assert back.text == '6.' and len(back) == 0
    assert note.tag == 'div'
    sup = next(n for n in root.iter('sup') if ref in list(n))
    assert sup.attrib == {'class':'noteref-unresolved'} and len(sup) == 1
    assert not sup.text and ref.text == '6' and len(ref) == 0
    from cps.services.reflow import _layout_notes as notes, _layout_atoms as atoms
    source = json.loads(note_source[-2].contract_json)
    recovered = notes.verified_source_output(source,note_source[-1],atoms.validate(source,note_source[-1]),dict(body=out.body,furniture=out.furniture))
    restored = ops._tree(recovered['body']).find('.//sup')
    restored.tail = None
    assert ET.tostring(restored,encoding='unicode') == '<sup class="noteref-unresolved">6</sup>'
    assert 'The locked role remains.' in ''.join(note.itertext())
    assert root.find('.//img').get('src') == 'images/fig_p0000_0.jpg'
    no = copy.deepcopy(note_source[-1]); no.pop('note_bindings')
    plain = compile_plan(note_source, no)
    no['note_bindings'] = []
    assert compile_plan(note_source, no).body == plain.body
    assert out.expected_body == plain.expected_body
    assert '<aside class="source-note-unbound">6. The locked role remains.</aside>' in plain.body


@pytest.mark.parametrize('damage', ['unknown', 'cross_page', 'self', 'non_note', 'not_first', 'hidden', 'null', 'wrong_type', 'non_prose', 'opaque', 'mismatch'])
def test_invalid_associations_never_admit(note_source, damage):
    response = copy.deepcopy(note_source[-1]); binding = response['note_bindings'][0]
    if damage == 'unknown': binding['reference'] = 'missing'
    elif damage == 'cross_page': binding['reference'] = 'p1:a3'
    elif damage == 'self': binding['reference'] = binding['note']
    elif damage == 'non_note': response['groups'][-1]['role'] = 'paragraph'
    elif damage == 'not_first': binding['note'] = 'a' + str(int(binding['note'][1:]) + 1)
    elif damage == 'hidden': binding['href'] = '#invented'
    elif damage == 'null': response['note_bindings'] = None
    elif damage == 'wrong_type': binding['reference'] = []
    elif damage == 'non_prose': response['groups'][1]['role'] = 'heading2'
    elif damage == 'opaque': binding['reference'] = response['groups'][3]['ranges'][0][0]
    elif damage == 'mismatch': binding['reference'] = response['groups'][0]['ranges'][0][0]
    with pytest.raises(ContractError): admit(note_source, response)


@pytest.mark.parametrize('damage', ['href', 'id', 'child', 'wrapper', 'backlink', 'placement', 'note_id', 'extra_anchor', 'root_text', 'root_tail', 'numeral', 'nested_ref', 'extra_sup', 'extra_inner_anchor', 'duplicate_backlink', 'missing_backlink', 'wrong_container', 'source_position', 'legacy_shape', 'missing_sup', 'nested_sup', 'note_text_escape'])
def test_renderer_cannot_forge_navigation_or_its_placement(note_source, monkeypatch, damage):
    response=copy.deepcopy(note_source[-1])
    if damage == 'note_text_escape':response['groups'].insert(0,response['groups'].pop())
    plan = admit(note_source,response); render = ops._render
    def corrupt(*args):
        out = render(*args); root = ops._tree(out['body'])
        ref = next(a for a in root.iter('a') if a.get(E) == 'noteref')
        note = next(a for a in root.iter() if a.get(E) == 'footnote')
        sup = next(n for n in root.iter('sup') if ref in list(n))
        if damage == 'root_text': root.text = 'Invented leading words'
        elif damage == 'root_tail': root[-1].tail = 'Invented trailing words'
        elif damage == 'href': ref.set('href', '#missing')
        elif damage == 'id': ref.set('id', 'forged')
        elif damage == 'child': sup.set('class', 'changed')
        elif damage == 'wrapper': ref.tag = 'span'
        elif damage == 'backlink': note.find('a').set('href', '#missing')
        elif damage == 'note_id': note.set('id', 'forged')
        elif damage == 'extra_anchor': ET.SubElement(note, 'a', {'href': '#bogus'})
        elif damage == 'placement':
            parent = next(n for n in root if sup in list(n)); parent.remove(sup); note.append(sup)
        elif damage == 'numeral': ref.text = '7'
        elif damage == 'nested_ref':
            sup.remove(ref); ET.SubElement(sup,'span').append(ref)
        elif damage == 'extra_sup':
            parent = next(n for n in root if sup in list(n)); parent.append(ET.fromstring('<sup class="noteref-unresolved">6</sup>'))
        elif damage == 'extra_inner_anchor': ET.SubElement(sup,'a',{'href':'#forged'}).text='6'
        elif damage == 'duplicate_backlink': note.append(copy.deepcopy(note.find('a')))
        elif damage == 'missing_backlink':
            back=note.find('a');note.text=(back.text or '')+(back.tail or '');note.remove(back)
        elif damage == 'wrong_container': note.tag = 'aside'
        elif damage == 'source_position':
            parent=next(n for n in root if sup in list(n));parent.text,parent[0].tail=parent[0].tail,parent.text
        elif damage == 'note_text_escape':
            following=root[list(root).index(note)+1];back=note.find('a')
            following.text=(back.tail or '')+' '+(following.text or '');back.tail=None
        elif damage == 'missing_sup':
            parent=next(n for n in root if sup in list(n));index=list(parent).index(sup)
            ref.tail=sup.tail;parent.remove(sup);parent.insert(index,ref)
        elif damage == 'nested_sup':
            parent=next(n for n in root if sup in list(n));index=list(parent).index(sup)
            parent.remove(sup);wrapper=ET.Element('span');wrapper.append(sup);parent.insert(index,wrapper)
        elif damage == 'legacy_shape':
            parent=next(n for n in root if sup in list(n));index=list(parent).index(sup)
            sup.remove(ref);sup.text=ref.text;ref.text=None;ref.tail=sup.tail;sup.tail=None
            parent.remove(sup);ref.append(sup);parent.insert(index,ref)
        out['body'] = (root.text or '') + ''.join(ET.tostring(n, encoding='unicode') for n in root)
        return out
    monkeypatch.setattr(ops, '_render', corrupt)
    book, doc, _, page, raw, _, _ = note_source
    with pytest.raises(ContractError): plan.compile(book, doc, source_page=page, raw_page=raw)


def test_model_and_independent_review_receive_exact_binding_and_reject_stale_review(note_source):
    prepared = note_source[-2]; plan = admit(note_source)
    proposal = requests.proposal(prepared)
    view = json.loads(proposal['messages'][1]['content'])
    assert view['note_candidates']['references'] == [dict(id=note_source[-1]['note_bindings'][0]['reference'], label='6')]
    assert 'note_bindings' in proposal['response_schema']['properties']
    review = requests.review(prepared, plan)
    view = json.loads(review['messages'][1]['content'])
    assert view['note_bindings'] == note_source[-1]['note_bindings']
    response = dict(snapshot=view['snapshot'], accept=False, continuation_accept=False, problems=['note_or_caption_association'])
    assert requests.validate_review(prepared, plan, response).accepted_plan is None
    no = copy.deepcopy(note_source[-1]); no.pop('note_bindings'); plain = admit(note_source, no)
    with pytest.raises(ContractError, match='stale'): requests.validate_review(prepared, plain, response)


def test_actual_publication_binds_both_directions_across_chapters(note_source):
    book, doc, tmp, page, raw, _, _ = note_source
    out = build_epub.build(book, str(tmp/'checked-notes.epub'), doc=doc, source_pages={0:page}, layout_plans=[admit(note_source)], raw_pages={0:raw})
    assert build_epub.validate(out.path) == []
    with zipfile.ZipFile(out.path) as archive:
        roots = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')}
        refs = [(name, a) for name, root in roots.items() for a in root.iter(X+'a') if a.get(E) == 'noteref']
        assert len(refs) == 1
        name, ref = refs[0]
        target_file, ident = ref.get('href').split('#')
        target_name = 'OEBPS/' + target_file if target_file else name
        assert target_name != name, 'fixture must cross an actual chapter boundary'
        note = next(n for n in roots[target_name].iter() if n.get('id') == ident)
        back = note.find(X+'a')
        back_file, back_id = back.get('href').split('#')
        assert 'OEBPS/' + back_file == name and back_id == ref.get('id')
        assert back.text == '6.'
        assert note.tag == X+'div'
        sup=next(n for n in roots[name].iter(X+'sup') if ref in list(n))
        assert sup.attrib == {'class':'noteref-unresolved'}
        assert ref.text == '6' and not len(ref)
        assert archive.getinfo('OEBPS/images/fig_p0000_0.jpg').file_size > 0


@pytest.mark.parametrize('reference,label', [
    ('<sup class="noteref-unresolved"><span class="reflow-uncertain">6</span></sup>', '6.'),
    ('<a href="#existing"><sup class="noteref-unresolved">6</sup></a>', '6.'),
    ('<em><sup class="noteref-unresolved">6</sup></em>', '6.'),
    ('x<sup class="noteref-unresolved">6</sup>', '6.'),
    ('<sup class="noteref-unresolved">6</sup>', '<span class="reflow-uncertain">6.</span>'),
    ('<sup class="noteref-unresolved">6</sup>', '7.'),
])
def test_unsupported_marker_or_label_shapes_cannot_gain_link_authority(reference, label):
    # Private grammar seam only: production authority is exercised separately via
    # the SourcePage factory above. No manufactured PreparedLayout is admitted.
    from cps.services.reflow import _layout_atoms as atoms
    source = atoms.prepare('<p>Source ' + reference + ' remains.</p><p>' + label + ' Note words.</p>', 'grammar', 0)
    ref = next(a['id'] for a in source['atoms'] if 'sup' in a['html'])
    note = source['blocks'][1]['range'][0]
    response = dict(snapshot=source['snapshot'], joins=[], groups=[
        dict(role='paragraph', ranges=[source['blocks'][0]['range']]),
        dict(role='note', ranges=[source['blocks'][1]['range']])], note_bindings=[dict(reference=ref, note=note)])
    with pytest.raises(ContractError): atoms.validate(source, response)


def test_source_changes_invalidate_checked_binding_before_publication(note_source):
    book, doc, _, page, raw, prepared, response = note_source
    plan = admit(note_source)
    book.elements[-1].runs[0][1] = '7. The locked role remains.'
    fresh = enriched_source.prepare_source_page(book, 0, {'layer':'native'})
    with pytest.raises(ContractError): plan.compile(book, doc, source_page=fresh, raw_page=raw)
    current = ops.prepare(book, doc, 0, fresh, raw)
    with pytest.raises(ContractError): current.accept(book, doc, response, source_page=fresh, raw_page=raw)
    response = copy.deepcopy(response); response['snapshot'] = current.snapshot_id
    with pytest.raises(ContractError, match='labels differ'): current.accept(book, doc, response, source_page=fresh, raw_page=raw)


def test_id_collision_is_rejected_even_if_generator_returns_source_owned_id(monkeypatch):
    from cps.services.reflow import _layout_atoms as atoms, _layout_notes as notes
    source = atoms.prepare('<p>Source <sup class="noteref-unresolved">6</sup> remains.</p>'
                           '<p>6. Note words.</p><p id="owned">Original opaque.</p>', 'grammar', 0)
    response = dict(snapshot=source['snapshot'], joins=[], groups=[
        dict(role=role, ranges=[block['range']]) for role, block in
        zip(('paragraph', 'note', 'source'), source['blocks'])],
        note_bindings=[dict(reference='a1', note='a3')])
    monkeypatch.setattr(notes, 'identities', lambda *args: ('owned', 'fresh-note'))
    with pytest.raises(ContractError, match='collision'): atoms.validate(source, response)


def test_no_eligible_marker_keeps_proposal_wire_and_schema_without_note_extension(source):
    from tests.unit.test_reflow_layout_ops import prepared
    value = prepared(source)[-1]
    wire = requests.proposal(value)
    view = json.loads(wire['messages'][1]['content'])
    assert 'note_candidates' not in view
    assert 'note_bindings' not in wire['response_schema']['properties']


@pytest.mark.parametrize('duplicate', ['reference', 'note'])
def test_distinct_counterparts_cannot_reuse_either_side_of_a_binding(duplicate):
    from cps.services.reflow import _layout_atoms as atoms
    source = atoms.prepare('<p>First <sup class="noteref-unresolved">6</sup> second '
                           '<sup class="noteref-unresolved">6</sup> ends.</p>'
                           '<p>6. First note.</p><p>6. Second note.</p>', 'grammar', 0)
    refs = [a['id'] for a in source['atoms'] if '<sup' in a['html']]
    notes = [b['range'][0] for b in source['blocks'][1:]]
    response = dict(snapshot=source['snapshot'], joins=[], groups=[
        dict(role=role, ranges=[b['range']]) for role, b in
        zip(('paragraph', 'note', 'note'), source['blocks'])],
        note_bindings=[dict(reference=ref, note=note) for ref, note in zip(refs, notes)])
    atoms.validate(source, response)  # Matching labels alone do not imply duplicates.
    response['note_bindings'][1][duplicate] = response['note_bindings'][0][duplicate]
    with pytest.raises(ContractError, match='duplicate'): atoms.validate(source, response)


def test_bound_div_remains_outside_main_flow_when_note_group_precedes_prose(note_source):
    response=copy.deepcopy(note_source[-1])
    response['groups'].insert(0,response['groups'].pop())
    output=compile_plan(note_source,response)
    pages=build_epub._page_blocks({0:output.page_html})
    assert len(pages[0]['asides'])==1
    assert pages[0]['asides'][0].startswith('<div ')
    assert not any('class="footnote"' in block for block in pages[0]['body'])
    # Unrelated native divs never gain checked-note semantics by class alone.
    assert not build_epub._is_aside('<div class="footnote">Native source block.</div>')


def test_compiled_bound_note_is_not_the_main_prose_page_turn_endpoint(note_source):
    output=compile_plan(note_source)
    pages=build_epub._page_blocks({0:output.page_html,1:'<p>Continuation body.</p>'})
    original_note=pages[0]['asides'][0]
    # This seam exercises publication's execution of a checked boundary decision;
    # separate paired-plan tests verify admission of the decision itself.
    assert build_epub._join_page_turns(pages,layout_pages={0,1},
        layout_boundaries={1:{'hyphen':None}})==1
    assert pages[0]['asides']==[original_note]
    assert 'Continuation body.' in pages[0]['body'][-1]
    assert 'class="footnote"' not in pages[0]['body'][-1]
