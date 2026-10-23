"""Synthetic unaligned scan fragments cannot become unsupported live prose."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('context_confidence',[60,99])
@pytest.mark.parametrize('token',['respecti','f','ge','CJ','7','12.5'])
@pytest.mark.parametrize('unresolved_source',[True,False])
def test_unresolved_source_fragment_needs_a_complete_independent_occurrence(token,context_confidence,unresolved_source):
    source=[dict(text=t,bbox=[i*30,20,i*30+25,32],confidence=99)
            for i,t in enumerate(['Keep','the',token,'sample','readable.'])]
    witness=pa.CropWitness([dict(w,confidence=context_confidence)for w in source]);witness.word_scope=lambda word:None
    witness.word_readings=lambda word:[[dict(word,confidence=60)]]*2
    result=pa.adjudicate(['Keep','the','sample','readable.'],source,witness,unresolved_source=unresolved_source)
    assert result['tokens']==[w['text']for w in source]
    note=result['word_notes'][2]
    assert note.get('source_image_only'),'An unplaced optical fragment is visible as print, not a confident extra word'
    assert note['source_bbox']==source[2]['bbox']
    assert 0 not in result['marked']and 4 not in result['marked']


@pytest.mark.parametrize('token', ['unit', '7', '12.5'])
def test_complete_independent_source_word_can_survive_reader_omission(token):
    source=[dict(text=t,bbox=[i*30,20,i*30+25,32],confidence=99)
            for i,t in enumerate(['Keep','the',token,'sample','readable.'])]
    witness=pa.CropWitness(source);witness.word_scope=lambda word:list(word['bbox'])
    witness.word_readings=lambda word:[[dict(word,confidence=99)]]*2
    result=pa.adjudicate(['Keep','the','sample','readable.'],source,witness,unresolved_source=True)
    assert result['tokens']==[w['text']for w in source]
    assert not result['word_notes'].get(2,{}).get('source_image_only')


@pytest.mark.parametrize('complete',[True,False])
def test_tiny_complete_function_word_is_not_a_nonword_fragment(complete):
    source=[dict(text=t,bbox=[i*30,20,i*30+25,32],confidence=99)
            for i,t in enumerate(['Keep','words','to','read','nearby.'])]
    witness=pa.CropWitness([dict(w,confidence=88)for w in source])
    witness.word_scope=lambda word:list(word['bbox'])if complete else[word['bbox'][0]-15,*word['bbox'][1:]]
    witness.word_readings=lambda word:[]
    result=pa.adjudicate(['Keep','words','read','nearby.'],source,witness,unresolved_source=True)
    assert result['tokens']==[w['text']for w in source]
    assert bool(result['word_notes'].get(2,{}).get('source_image_only'))is not complete
