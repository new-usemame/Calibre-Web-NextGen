"""Synthetic note-label identity is independent of an OCR digit's spelling."""
from types import SimpleNamespace
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


def test_bound_label_is_excluded_from_a_differently_decoded_native_inventory():
    label=dict(text='1',bbox=[20,20,24,26])
    prose=dict(text='Please',bbox=[28,22,52,30])
    proof=dict(observed_text='2',source_bbox=[20.2,20.3,24.1,26.1])
    assert vp._note_lexical_evidence([label,prose],proof)==[prose]


def test_a_neighbor_notes_raised_label_cannot_enter_the_prior_notes_rounding_halo():
    words=[dict(text='Please',bbox=[30,20,60,28]),dict(text='3',bbox=[20,29,24,35]),
           dict(text='Continue',bbox=[30,32,70,40])]
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,100,100))
    a=block('Please',ident='first',role='footnote',marker='2',bbox=(200,200,700,290))
    b=block('Continue',ident='second',role='footnote',marker='3',bbox=(200,290,750,410))
    regions={'first':[[20,20,70,29]],'second':[[20,29,75,41]]}
    own=vp._owned_block_words(a,answer(a,b),words,display,regions,{},ordered=True)
    assert own==words[:1]
