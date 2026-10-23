"""Synthetic independent raster reads must see complete midpoint ink."""
import io
from types import SimpleNamespace
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import vision_pixels
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('crossing',[False,True])
def test_independent_witness_splits_only_a_positively_blank_gutter(tmp_path,monkeypatch,crossing):
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda name:'synthetic-tesseract')
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=250)
        page.insert_text((178 if crossing else 30,100),'complete',fontsize=16)
        calls=[]
        def recognize(args,**kwargs):
            picture=Image.open(io.BytesIO(kwargs['input']));calls.append(picture.size)
            text='complete'if picture.width>=picture.height*1.2 else 'fragment'
            data=('level\tleft\ttop\twidth\theight\tconf\ttext\n'
                  f'5\t20\t40\t80\t20\t96\t{text}\n').encode()
            return SimpleNamespace(returncode=0,stdout=data,stderr=b'')
        monkeypatch.setattr(vision_pixels.subprocess,'run',recognize)
        display=SourceDisplay(doc,0);pix=display.pixmap(scale=3,max_pixels=8_000_000)
        words=vision_pixels._recognize(display,pix,pix.tobytes('png'),tmp_path/'receipt.json')
    assert len(calls)==(1 if crossing else 2)
    assert [w['text']for w in words]==(['complete']if crossing else ['fragment','fragment'])
