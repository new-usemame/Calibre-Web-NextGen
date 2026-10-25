"""Original caption note ink requires proof even when both OCR paths omit it."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline,vision_pixels
from cps.services.reflow.source_display import SourceDisplay

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('unrecognized_caption_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def make_page(glyph='†',owner=True):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    p.text('Careful observers compare independently measured specimens.','body',y=45,size=6)
    p.figure('diagram',caption=False)
    text='A connected diagram preserves the original attribution (R. Vale.)'
    printed=text[:-1]+glyph+')' if glyph else text
    p.page.insert_text((22,205),printed,fontsize=5,fontname='unicode')
    p.expected_glyph_bbox=next((c['bbox'] for b in p.page.get_text('rawdict')['blocks']
        for line in b.get('lines',[]) for span in line['spans'] for c in span['chars']
        if c['c']==glyph and glyph and c['bbox'][1]<220),None)
    p.blocks.append(h.model_block(text,'cap','caption',[20,194,282,210]))
    note='This independently printed citation identifies the archival author.'
    if owner:
        p.page.insert_text((22,330),'† '+note,fontsize=4,fontname='unicode')
        p.blocks.append(h.model_block(note,'note','footnote',[20,319,282,336],marker='†'))
    finish=p.finish
    def optical():
        image,words,answer=finish()
        for word in words:
            if word['bbox'][1]<220:word['text']=word['text'].replace('†','')
        return image,words,answer
    p.finish=optical
    return p,text,note


def convert_case(tmp_path,monkeypatch,p,label='†'):
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda *args,**kwargs:[])
    original=vision_pipeline.convert;calls=[];roi_boxes=[]
    original_roi=SourceDisplay.roi_jpeg
    def roi(display,bbox,*args,**kwargs):
        roi_boxes.append(list(bbox));return original_roi(display,bbox,*args,**kwargs)
    monkeypatch.setattr(SourceDisplay,'roi_jpeg',roi)
    def convert(doc,path,reader,*args,**kwargs):
        def read_marker(image):
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(image)) as im:assert max(im.size)<=600
            if p.expected_glyph_bbox is not None:
                assert roi_boxes,'verification must use bounded original-source glyph territory'
                box=roi_boxes[-1];g=p.expected_glyph_bbox
                assert box[0]-.5<=(g[0]+g[2])/2<=box[2]+.5 and box[1]-1<=(g[1]+g[3])/2<=box[3]+1,'a period or closing bracket cannot borrow a dagger verification'
            calls.append(image)
            return dict(label=label,uncertain=False,cached=True,new_cost_usd=0)
        reader.read_marker=read_marker
        return original(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    result=h.convert_ink(tmp_path,monkeypatch,[p])
    return result,calls


def test_caption_original_dagger_with_no_character_candidate_has_exact_note_return(tmp_path,monkeypatch):
    p,text,note=make_page();(_,_,docs),calls=convert_case(tmp_path,monkeypatch,p)
    captions=[n for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS)]
    assert [h.plain(n) for n in captions]==[text],'only a note action is restored; original lexical caption text is unchanged'
    refs,notes=h.assert_exact_returns(docs,1);name,ref=refs[0]
    assert ''.join(ref.itertext())=='†' and any(ref in list(n.iter()) for n in captions)
    target=h.destination(name,ref.get('href'))
    assert note in ''.join(next(n for nn,n in notes if (nn,n.get('id'))==target).itertext())
    assert calls,'an OCR-omitted glyph cannot be guessed from the existence of a local note owner'
    assert h.body_texts(docs)==['Careful observers compare independently measured specimens.']


@pytest.mark.parametrize('glyph,owner,label',[('',True,'†'),('+',True,'+'),('†',False,'†')])
def test_closing_punctuation_plus_or_missing_local_owner_cannot_invent_caption_note(tmp_path,monkeypatch,glyph,owner,label):
    p,text,_=make_page(glyph=glyph,owner=owner)
    (_,_,docs),calls=convert_case(tmp_path,monkeypatch,p,label=label)
    assert h.refs_and_notes(docs)[0]==[],'local note count or arbitrary terminal ink is not proof of a caption dagger'
    captions=[n for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS)]
    printed=text[:-1]+'+)' if glyph=='+' else text
    assert [h.plain(n) for n in captions]==[printed]
    assert h.body_texts(docs)==['Careful observers compare independently measured specimens.']
