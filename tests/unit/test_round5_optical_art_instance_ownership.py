"""SOURCE-free converter: a wrapped BODY rectangle does not own art lettering.

Hints deliberately overlap real BODY ink, so excluding an entire art hint would
also be wrong. Exact source word instances must preserve the genuine paragraph
once while leaving figure labels in their source object/context.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_art_instance_ink',_helper)
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('art_on_left',[True,False])
def test_wrapped_body_never_adopts_source_art_labels_from_union_rectangle(tmp_path,monkeypatch,art_on_left):
    p=h.InkPage()
    lines=['The careful observer compares several useful specimens before describing',
           'their ordinary growth along the coast while clear prose continues',
           'beside the illustrated object and across every following source row.',
           'The complete paragraph then resumes below the picture without adding',
           'any small diagram lettering to its authored reading.']
    positions=[(22,45),(153 if art_on_left else 22,77),(153 if art_on_left else 22,92),(22,158),(22,172)]
    # Small font keeps each synthetic row wholly inside the 300pt source page.
    for text,(x,y) in zip(lines,positions):p.page.insert_text((x,y),text,fontsize=3.6)
    labels=['Figure sixty Alpha shell Small harbour coast',
            'Beta specimen Upper ridge Lower valley Basin east']
    ax=28 if art_on_left else 176
    p.page.draw_rect([ax,60,ax+93,130],width=1,color=(0,0,0))
    for i,text in enumerate(labels):p.page.insert_text((ax+3,78+i*13),text,fontsize=3.8)
    p.blocks.append(h.model_block(' '.join(lines),'prose','body',[20,36,281,176]))
    # The coarse RAW art hint extends into genuine BODY. Literal figure-owned
    # labels are corroborated, but a broad rectangle veto would lose prose.
    p.blocks.append(h.model_block(' '.join(labels),'art','figure',[ax-4,54,ax+112,137]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[' '.join(lines)], 'source art label instances never enter live BODY; true wrapped BODY survives exactly once'


def test_omitted_true_body_row_is_still_recovered_when_no_other_object_owns_it(tmp_path,monkeypatch):
    p=h.InkPage()
    lines=['An independent source paragraph begins with a clear ordinary opening.',
           'This genuine omitted source sentence has enough words to require recovery.',
           'The authored closing line completes the source paragraph without illustration.']
    for i,text in enumerate(lines):p.page.insert_text((22,45+i*13),text,fontsize=5)
    p.blocks.append(h.model_block(lines[0]+' '+lines[2],'prose','body',[20,35,282,76]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[' '.join(lines)], 'genuine unclaimed prose omissions remain recoverable'


def test_figure_owned_lettering_outside_incomplete_hint_never_fills_body_gap(tmp_path,monkeypatch):
    p=h.InkPage()
    lines=['A complete ordinary paragraph introduces the adjacent source illustrations.',
           'Its second printed line remains genuine authored body prose throughout.',
           'The same paragraph resumes below both objects without borrowing labels.',
           'Every later body word must survive this incomplete object hint exactly once.']
    for text,y in zip(lines,[43,56,175,188]):p.page.insert_text((22,y),text,fontsize=5)
    labels=['Panel six Alpha specimen Left shore Ancient valley',
            'Panel seven Beta specimen Upper ridge Eastern basin']
    for text,x in zip(labels,[32,174]):
        p.page.draw_rect([x-3,76,x+95,136],width=1,color=(0,0,0))
        p.page.insert_text((x,101),text,fontsize=3.3)
    p.blocks.append(h.model_block(' '.join(lines),'prose','body',[20,33,282,192]))
    # The source object transcription owns both labels, but the hint describes
    # only its first subfigure. Half the label ink is outside every RAWart hint.
    p.blocks.append(h.model_block(' '.join(labels),'paired','figure',[28,75,133,138]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[' '.join(lines)], 'source-confirmed object labels beyond a coarse hint never become ordinary BODY'
