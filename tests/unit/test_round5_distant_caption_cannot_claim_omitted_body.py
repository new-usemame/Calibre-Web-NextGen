"""SOURCE-free: an uncorroborated caption cannot suppress distant body ink."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_distant_caption_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def test_unmatched_remote_caption_does_not_hide_a_true_omitted_body_row(tmp_path,monkeypatch):
    p=h.InkPage()
    lines=['An independent source paragraph begins with a clear ordinary opening.',
           'This genuine omitted source sentence has enough words to require recovery.',
           'The authored closing line completes the source paragraph without illustration.']
    for i,text in enumerate(lines):p.page.insert_text((22,100+i*13),text,fontsize=5)
    p.blocks.append(h.model_block(lines[0]+' '+lines[2],'prose','body',[20,90,282,131]))
    p.page.draw_rect([240,10,280,30],color=(0,0,0),width=1)
    p.blocks.append(h.model_block('A geometric original source instrument','art','figure',[240,10,280,30]))
    p.page.insert_text((170,42),'A geometric source instrument.',fontsize=4)
    p.blocks.append(h.model_block(lines[1],'cap','caption',[168,36,280,45]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    texts=h.body_texts(docs)
    assert sum(' '.join(lines)==text for text in texts)==1,'a global caption reading match cannot make a genuine omitted body row disappear'
