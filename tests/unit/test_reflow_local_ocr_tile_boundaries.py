"""Synthetic wide-page ink may cross the page midpoint without a gutter."""
import io
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import local_reading,ocr
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit


def test_wide_page_recognition_does_not_cut_a_printed_word_in_half(tmp_path,monkeypatch):
    monkeypatch.setattr(ocr,'_engine',lambda language:('synthetic-engine','test-version','test-data'))
    with pymupdf.open()as doc:
        page=doc.new_page(width=400,height=250)
        page.insert_text((178,100),'complete',fontsize=16)
        def invoke(args,directory,**kwargs):
            picture=Image.open(io.BytesIO((directory/'page.png').read_bytes()))
            # An independent recognizer cannot read letters outside its input.
            text='complete'if picture.width>=picture.height*1.2 else 'com'if picture.getbbox()else 'plete'
            (directory/'result.tsv').write_text(
                'level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n'
                f'5\t1\t1\t1\t20\t40\t80\t20\t96\t{text}\n')
            return 0,'',''
        monkeypatch.setattr(ocr,'_invoke',invoke)
        words,receipt=local_reading.recognize(SourceDisplay(doc,0),tmp_path)
    assert [w['text']for w in words]==['complete']
    assert all(w['paragraph'][0]==0 for w in words)
