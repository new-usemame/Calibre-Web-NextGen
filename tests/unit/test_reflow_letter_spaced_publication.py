"""Synthetic spaced capitals form one source-backed reading, never letter links."""
import pytest
from cps.services.reflow import reading_tiers as tiers

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('word',['THE','of'])
@pytest.mark.parametrize('evidence',['own','foreign','weak','separate','opaque'])
def test_marked_letter_atoms_need_a_complete_own_word_boundary(word,evidence):
    boxes={i:[10+i*8,20,16+i*8,30]for i in range(len(word))}
    if evidence=='separate':boxes[1]=[50,20,56,30]
    result=dict(tokens=list(word),boxes=boxes,marked=set(range(len(word))),guard=[],
        word_notes={i:dict(source=[c],model=[c],chosen=[c],status='UNRESOLVED',
            method='unproved_native_character_map'if evidence=='opaque'else'unproved_source_transcription_change',
            source_image_only=True,source_bbox=boxes[i])for i,c in enumerate(word)})
    witness=[dict(text=word,bbox=[10,20,16+(len(word)-1)*8,30],confidence=60 if evidence=='weak'else 99)]
    if evidence=='foreign':witness[0]['bbox']=[100,20,140,30]
    tiers.join_letter_spaced_readings(result,witness)
    if evidence in ('own','opaque'):
        assert result['tokens']==[word]
        tiers.prepare(result)
        assert (0 in result['flagged_readings']) is (evidence=='own')
        assert result['word_notes'][0]['source_image_only'] is (evidence=='opaque')
    else:assert result['tokens']==list(word)
