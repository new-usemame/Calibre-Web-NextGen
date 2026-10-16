"""SOURCE-free: a footer-note hint cannot globally relocate unrelated prose."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_distant_note_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def test_footer_note_cannot_relocate_a_far_omitted_body_row_by_text_alone(tmp_path,monkeypatch):
    p=h.InkPage()
    lines=['An independent source paragraph begins with a clear ordinary opening.',
           'This genuine omitted source sentence has enough words to require recovery.',
           'The authored closing line completes the source paragraph without illustration.']
    for i,text in enumerate(lines):p.page.insert_text((22,60+i*13),text,fontsize=5)
    p.blocks.append(h.model_block(lines[0]+' '+lines[2],'prose','body',[20,50,282,91]))
    # A damaged note reading coincides with distant BODY. Its printed footer,
    # label and distinct explanatory ink supply the actual local note territory.
    p.page.insert_text((22,350),'* A separate authored note has its own distinct explanation.',fontsize=4)
    p.page.draw_line((22,335),(160,335),color=(0,0,0),width=1)
    p.blocks.append(h.model_block(lines[1],'note','footnote',[20,343,200,354],marker='*'))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[' '.join(lines)],'exact foreign prose text does not authorize a footer note to own a distant body row'
