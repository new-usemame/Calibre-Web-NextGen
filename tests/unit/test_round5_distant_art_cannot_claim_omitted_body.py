"""SOURCE-free: matching art descriptions do not authorize foreign prose ink."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_distant_art_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('role',['figure','table'])
def test_distant_object_description_never_owns_an_omitted_ordinary_body_row(tmp_path,monkeypatch,role):
    p=h.InkPage()
    lines=['An independent source paragraph begins with a clear ordinary opening.',
           'This genuine omitted source sentence has enough words to require recovery.',
           'The authored closing line completes the source paragraph without illustration.']
    for i,text in enumerate(lines):p.page.insert_text((22,60+i*13),text,fontsize=5)
    p.blocks.append(h.model_block(lines[0]+' '+lines[2],'prose','body',[20,50,282,91]))
    # This actual small geometric object is wholly above and horizontally away
    # from the printed paragraph. Its model description coincides with prose,
    # but no object-owned label instance corroborates that description.
    p.page.draw_rect([240,10,280,30],color=(0,0,0),width=1)
    p.page.draw_line((240,20),(280,20),color=(0,0,0),width=1)
    p.page.draw_line((260,10),(260,30),color=(0,0,0),width=1)
    p.blocks.append(h.model_block(lines[1],'unrelated',role,[240,10,280,30]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[' '.join(lines)],'global lexical equality cannot hide a real omitted prose row as foreign object ownership'
