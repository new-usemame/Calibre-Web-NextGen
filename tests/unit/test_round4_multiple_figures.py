"""Independent publication control for neighboring captioned illustrations."""
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('round4_art_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_two_captioned_figures_have_separate_complete_prose_free_pixels(tmp_path,monkeypatch):
    page=h.InkPage()
    page.text('Ordinary prose introduces two separate source illustrations.', 'opening')
    expected=[]
    for i,top in enumerate([85,205]):
        box=[35,top,260,top+50];expected.append(box)
        page.page.draw_rect(box,color=(0,0,0),fill=(.85,.85,.85),width=2)
        page.page.draw_line((35,top),(260,top+50),color=(0,0,0),width=2)
        page.blocks.append(h.model_block('A synthetic source illustration',f'art{i}','figure',[115,top+15,170,top+35]))
        page.text(f'Illustration {i+1} depicts a distinct synthetic object.',f'cap{i}','caption',y=top+66,size=7)
    page.text('Ordinary live prose follows both separate illustrations.', 'closing', y=300)
    _,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    figures=[rec for rec in result['images'].values() if rec['kind']=='figure']
    assert len(figures)==2
    for rec,box in zip(figures,expected):
        assert rec['bbox'][0]<=box[0] and rec['bbox'][1]<=box[1]
        assert rec['bbox'][2]>=box[2] and rec['bbox'][3]>=box[3]
    assert figures[0]['bbox'][3]<figures[1]['bbox'][1]
    assert len([node for node in h.body_nodes(docs) if node.tag.endswith('}figure')])==2
    assert len(h.body_texts(docs))==2
