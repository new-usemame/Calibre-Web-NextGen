"""Synthetic crop edits require the affected ink, not remote contextual words."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark = pytest.mark.unit


def scene(extra_box):
    text = 'Please keep these clear words beside the open window today.'
    source = [dict(text=t, confidence=99,
                   bbox=[i*25 + (25 if i>=5 else 0),20,
                         i*25 + (25 if i>=5 else 0)+20,32])
              for i,t in enumerate(text.split())]
    witness = source[:5]+[dict(text='bright',confidence=99,bbox=extra_box)]+source[5:]
    return source,witness


@pytest.mark.parametrize('box', [[400,20,420,32],[125,80,145,92]])
def test_contextual_crop_cannot_borrow_an_inserted_word_from_another_strip(box):
    source,witness=scene(box)
    result=pa.refine_fallback([w['text']for w in source],witness,{'bright'},source=source)
    assert result['tokens']==[w['text']for w in source]
    assert not any(d['status']=='RESOLVED'for d in result['decisions'])


def test_contextual_crop_can_restore_ink_inside_the_owned_same_row_gap():
    source,witness=scene([125,20,145,32])
    result=pa.refine_fallback([w['text']for w in source],witness,{'bright'},source=source)
    assert result['tokens']==[w['text']for w in witness]
    assert result['word_notes'][5]['status']=='RESOLVED'
    assert result['boxes'][5]==[125,20,145,32]
