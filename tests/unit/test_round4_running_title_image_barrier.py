"""Garbled OCR must not put separately printed marginal titles in artwork."""
import importlib.util
import io
from pathlib import Path
import zipfile
import pytest
from PIL import Image
_spec=importlib.util.spec_from_file_location('running_title_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('title_inside_seed',[False,True])
def test_original_title_ink_barrier_keeps_authored_diagram_lettering(tmp_path,monkeypatch,title_inside_seed):
    page=h.InkPage()
    y=135 if title_inside_seed else 23
    page.text('A QUIET NORTHERN ATLAS','rh','running_head',x=110,y=y,size=7,color=(1,0,0))
    rect=page.figure()
    if title_inside_seed:
        # The filled illustration was drawn after the title; redraw that authored
        # lettering on top so the negative actually contains its asserted ink.
        page.page.insert_text((110,y),'A QUIET NORTHERN ATLAS',fontsize=7,color=(1,0,0))
    page.text('A separate ordinary paragraph follows the complete artwork.','body',y=235)
    original_finish=page.finish
    def garbled_title_optical():
        image,words,answer=original_finish()
        for word in words:
            if word['bbox'][1]<y and word['bbox'][3]>y-7 and word['text'] in {'A','QUIET','NORTHERN','ATLAS'}:
                word['text']='zxq'+word['text'].lower()
        return image,words,answer
    page.finish=garbled_title_optical
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    figures=[(src,entry) for src,entry in result['images'].items() if entry['kind']=='figure']
    assert len(figures)==1
    src,entry=figures[0];box=entry['bbox']
    assert box[0]<=rect[0] and box[1]<=rect[1] and box[2]>=rect[2] and box[3]>=rect[3], 'all authored corner ink is retained'
    with zipfile.ZipFile(epub) as archive:
        picture=Image.open(io.BytesIO(archive.read('OEBPS/'+src))).convert('RGB')
        red=sum(r>140 and g<100 and b<100 for r,g,b in picture.getdata())
    if title_inside_seed:
        assert red>20, 'authored lettering inside the original illustration seed is not erased by a title-role hint'
    else:
        assert box[1]>35 and red==0, 'separate marginal title ink must stay outside the actual packaged illustration'
    assert 'A separate ordinary paragraph follows the complete artwork.' in ' '.join(h.body_texts(docs))
