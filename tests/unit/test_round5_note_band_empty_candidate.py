"""SOURCE-free note-band proposals preserve empty and ordinary anchor outcomes."""
import pytest
from cps.services.reflow import vision_pipeline

pytestmark=pytest.mark.unit


def words():
    return [dict(text=text,bbox=[10+18*i,300,25+18*i,305])
            for i,text in enumerate(['Careful','observers','record','independent','evidence'])]


def test_empty_note_candidate_has_no_source_anchors():
    optical=words()
    assert vision_pipeline._note_pixel_anchors(optical,list(range(5)),[],[8,295,110,315])==[]


def test_ordinary_note_candidate_without_exact_source_anchors_stays_unbound():
    optical=words()
    candidate=['different','printed','words','cannot','corroborate']
    assert vision_pipeline._note_pixel_anchors(optical,list(range(5)),candidate,[8,295,110,315])==[]


def test_ordinary_exact_note_candidate_retains_all_original_source_instances():
    optical=words()
    candidate=[w['text'].casefold() for w in optical]
    assert vision_pipeline._note_pixel_anchors(optical,list(range(5)),candidate,[8,295,110,315])==[(i,i) for i in range(5)]
