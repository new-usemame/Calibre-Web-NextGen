"""Synthetic exact ink shapes may reuse an earned independent lexical proof."""
import pymupdf
import pytest
from cps.services.reflow import reading_tiers as tiers
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('case',['same','different','opaque','accent','weak_proof'])
def test_repeated_ink_needs_a_complete_proof_and_same_own_shape(case):
    with pymupdf.open()as doc:
        p=doc.new_page(width=300,height=150)
        p.insert_text((25,50),'modern',fontsize=14)
        p.insert_text((25,100),'modem'if case=='different'else'modern',fontsize=14)
        words=p.get_text('words')
        notes={0:dict(status='RESOLVED',method='pixel_word_boundary_only'if case=='weak_proof'else'independent_upright_crop',chosen=['modern']),
               1:dict(status='UNRESOLVED',method='unproved_native_character_map'if case=='opaque'else'unproved_diacritic_identity'if case=='accent'else'unproved_shared_stroke_identity',
                      chosen=['modern'],source_image_only=True)}
        result=dict(tokens=['modern','modern'],boxes={i:list(w[:4])for i,w in enumerate(words)},
                    word_notes=notes,marked={0,1},adjudication=[])
        tiers.settle_repeated_word_shapes(result,SourceDisplay(doc,0))
    note=result['word_notes'][1]
    assert note['status']==('RESOLVED'if case=='same'else'UNRESOLVED')
    if case=='same':
        assert not note['source_image_only']
        assert note['shape_proof']['reading']=='modern'
        assert 1 in result['marked'],'A proved model correction keeps its disclosure'
