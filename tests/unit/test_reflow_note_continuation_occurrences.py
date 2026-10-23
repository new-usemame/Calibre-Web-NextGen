"""Synthetic note continuations retain the note's unique printed occurrences."""
from types import SimpleNamespace
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


def test_unclaimed_note_row_prefix_is_not_recovered_into_the_preceding_note():
    first='An earlier note ends.'
    second='The next note starts with these words and continues beside the window.'
    a=[dict(text=t,bbox=[80+i*25,400,102+i*25,408],confidence=99)
       for i,t in enumerate(first.split())]
    b=[dict(text=t,bbox=[80+i*25,430,102+i*25,438],confidence=99)
       for i,t in enumerate(second.split()[:5])]
    b += [dict(text=t,bbox=[45+i*25,442,67+i*25,450],confidence=99)
          for i,t in enumerate(second.split()[5:])]
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,700))
    reading=answer(block(first,ident='n1',role='footnote',marker='1',bbox=(160,570,700,585)),
                   block(second,ident='n2',role='footnote',marker='2',bbox=(160,610,850,645)))
    regions={'n1':[[80,400,190,408]],'n2':[[80,430,220,438],[95,442,300,450]]}
    recovered,ids=vp.cover_missing_lines(reading,a+b,a+b,display,regions,{})
    assert ids==set(), 'The unique note reading already owns this indented printed prefix'
    assert [x['id']for x in recovered['blocks']]==['n1','n2']
    own=vp._owned_block_words(recovered['blocks'][1],recovered,a+b,display,regions,{},ordered=True)
    assert [w['text']for w in own]==second.split()
    assert all(regions['_instances'][vp._source_instance(w)]=='n2'for w in b)
