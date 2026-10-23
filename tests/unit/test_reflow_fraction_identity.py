"""Synthetic printed fractions cannot be replaced by ordinary OCR punctuation."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('fraction,guess',[('7¼','7%'),('11½','11'),('3¾','3%')])
def test_repeated_alphabet_ocr_does_not_certify_removing_a_printed_fraction(fraction,guess):
    source=[dict(text=guess,bbox=[20,20,45,32],confidence=99)]
    witness=pa.CropWitness(source)
    result=pa.adjudicate([fraction],source,witness)
    note=result['word_notes'][0]
    assert note['status']=='UNRESOLVED'and note.get('source_image_only')
    assert note['source_bbox']==source[0]['bbox']
    assert note['method']=='unproved_mathematical_symbol_identity'


def test_matching_fraction_readings_remain_live():
    source=[dict(text='7¼',bbox=[20,20,45,32],confidence=99)]
    result=pa.adjudicate(['7¼'],source,source)
    assert result['tokens']==['7¼']and not result['marked']
