"""Inline source glyphs remain indivisible without freezing surrounding prose."""
import copy
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import _layout_atoms as atoms
from cps.services.reflow.structural_ops import ContractError

pytestmark = pytest.mark.unit


def proposal(source, role='quote'):
    return dict(snapshot=source['snapshot'], joins=[], groups=[dict(
        role=role, ranges=[[source['atoms'][0]['id'], source['atoms'][-1]['id']]])])


@pytest.mark.parametrize('fragment', [
    '<p>Alpha <a href="original.xhtml#glyph"><img src="glyph.png" alt="Uncertain source" /></a> omega.</p>',
    '<p>pre<a href="original.xhtml#glyph"><img src="glyph.png" /></a>fix</p>',
    '<p>Alpha<strong><a href="original.xhtml#glyph"><img src="glyph.png" /></a></strong>omega</p>',
])
def test_quote_layout_preserves_inline_image_and_link_without_opaque_paragraph(fragment):
    source = atoms.prepare(fragment, {}, 0)
    rendered = atoms.render(source, proposal(source))['body']
    before, after = ET.fromstring(fragment), ET.fromstring(rendered)
    assert after.tag == 'blockquote'
    assert ''.join(before.itertext()).split() == ''.join(after.itertext()).split()
    for tag in ('img', 'a'):
        assert [ET.tostring(n) for n in before.iter(tag)] == [ET.tostring(n) for n in after.iter(tag)]
    assert any(a['protected'] and '<img' in a['html'] for a in source['atoms'])


def test_literal_object_character_is_not_removed_with_virtual_image_slot():
    source = atoms.prepare('<p>Literal \ufffc <img src="glyph.png" /> end.</p>', {}, 0)
    assert ' '.join(a['text'] for a in atoms.model_view(source)['atoms']).count('\ufffc') == 1
    result = atoms.render(source, proposal(source))['body']
    assert ''.join(ET.fromstring(result).itertext()).count('\ufffc') == 1
    assert len(list(ET.fromstring(result).iter('img'))) == 1


def test_nested_protected_images_and_note_markers_reprepare_identically():
    fragment = ('<p>Sect <a href="source"><img src="one.png" /></a> in Latin '
                '<sup class="noteref-unresolved"><span class="reflow-uncertain">2 (?)</span></sup> '
                'and <a href="other"><img src="two.png" /></a> end.</p>')
    snapshots = set()
    for _ in range(32):
        source = atoms.prepare(fragment, {}, 0)
        result = ET.fromstring(atoms.render(source, proposal(source))['body'])
        assert len(list(result.iter('img'))) == 2
        assert len(list(result.iter('sup'))) == 1
        assert ''.join(result.itertext()).split() == ''.join(ET.fromstring(fragment).itertext()).split()
        snapshots.add(source['snapshot'])
    assert len(snapshots) == 1


def test_image_cannot_be_omitted_duplicated_or_reclassified_as_running_furniture():
    source = atoms.prepare('<p>Alpha <img src="glyph.png" /> omega.</p>', {}, 0)
    good = proposal(source)
    atoms.render(source, good)
    image = next(a for a in source['atoms'] if '<img' in a['html'])
    for mutation in ('omit', 'duplicate', 'furniture'):
        bad = copy.deepcopy(good)
        if mutation == 'omit':
            bad['groups'][0]['ranges'] = [[a['id'], a['id']] for a in source['atoms'] if a is not image]
        elif mutation == 'duplicate':
            bad['groups'].append(dict(role='paragraph', ranges=[[image['id'], image['id']]]))
        else:
            bad['groups'][0]['role'] = 'furniture'
        with pytest.raises(ContractError):
            atoms.render(source, bad)


@pytest.mark.parametrize('fragment', [
    '<p><a href="original.xhtml#folio"><img src="folio.png" /></a></p>',
    '<p><img src="folio.png" /></p>',
])
def test_image_only_paragraph_exposes_whole_source_block_and_geometry(fragment):
    # A raster-only block has no editable prose. Keeping its exact DOM opaque
    # lets the model locate it on the page and choose a source disposition.
    source = atoms.prepare(fragment, {}, 0, {0: [20, 700, 45, 720]})
    view = atoms.model_view(source)
    assert len(view['protected_blocks']) == 1
    assert view['protected_blocks'][0]['bbox'] == [20, 700, 45, 720]
    answer = proposal(source, role='source_furniture')
    rendered = atoms.render(source, answer)
    assert not rendered['body'].strip()
    before, after = ET.fromstring(fragment), ET.fromstring(rendered['furniture'])
    assert ET.tostring(before) == ET.tostring(after)
