"""Small source digits: shallow raised reference versus actual baseline prose."""
import importlib.util
import io
from pathlib import Path
import pytest
import pymupdf
from PIL import Image
from cps.services.reflow import vision_pipeline, vision_pixels
_spec=importlib.util.spec_from_file_location('round4_shallow_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

class ShallowMarkerPage(h.InkPage):
    def __init__(self,raised):
        super().__init__()
        self.raised=raised
        prefix='The clear mineral'
        self.page.insert_text((24,55),prefix,fontsize=9)
        x=24+pymupdf.get_text_length(prefix,fontsize=9)+7
        self.page.insert_text((x,55),'(',fontsize=9)
        start=x+pymupdf.get_text_length('(',fontsize=9)+1
        baseline=53.8 if raised else 55
        for i,digit in enumerate('23'):
            self.page.insert_text((start,baseline),digit,fontsize=6,color=(1,0,0) if i==0 else (0,0,1))
            start+=pymupdf.get_text_length(digit,fontsize=6)
        end=start+1
        self.page.insert_text((end,55),').',fontsize=9)
        self.marker_box=[x-.5,46,end+pymupdf.get_text_length(')',fontsize=9)+.5,58]
        self.page.insert_text((24,84),'Several ordinary words identify the readable source paragraph.',fontsize=9)
        self.text='The clear mineral (23). Several ordinary words identify the readable source paragraph.'
        self.blocks.append(h.model_block(self.text,'body','body',[23,40,282,90]))
    def finish(self):
        image,words,reading=super().finish()
        b=self.marker_box
        words=[w for w in words if not(w['bbox'][0]<b[2] and w['bbox'][2]>b[0] and w['bbox'][1]<b[3] and w['bbox'][3]>b[1])]
        words.append(dict(text='(23).',bbox=list(b),confidence=99))
        words.sort(key=lambda w:(w['bbox'][1],w['bbox'][0]))
        return image,words,reading

@pytest.mark.parametrize('raised',[True,False])
def test_shallow_small_digits_link_only_when_source_ink_is_above_prose_baseline(tmp_path,monkeypatch,raised):
    page=ShallowMarkerPage(raised);notes=h.InkPage()
    notes.text('23 A finite synthetic note explains the source mineral.','note','footnote',y=320,size=7,marker='23',model='A finite synthetic note explains the source mineral.')
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:[])
    actual=vision_pipeline._literal_note_refs;requests=[]
    def read_cached(image):
        picture=Image.open(io.BytesIO(image)).convert('RGB')
        assert picture.width>=(page.marker_box[2]-page.marker_box[0])*12*.75,'the request must retain the complete physical source marker'
        pixels=list(picture.getdata())
        assert any(r>130 and g<100 and b<100 for r,g,b in pixels)
        assert any(b>130 and r<100 and g<100 for r,g,b in pixels)
        requests.append(image)
        return dict(label='23',uncertain=False,cached=True,new_cost_usd=0)
    def refs(tokens,optical,display,box,cache,labels,pno,context):
        context['marker_reader']=read_cached
        return actual(tokens,optical,display,box,cache,labels,pno,context)
    monkeypatch.setattr(vision_pipeline,'_literal_note_refs',refs)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[page,notes])
    if raised:
        refs,notes=h.assert_exact_returns(docs,1)
        assert ''.join(refs[0][1].itertext())=='23'
        assert len(requests)==1,'a shallow but physically raised complete marker reaches its cached source reading'
    else:
        assert requests==[],'small font alone cannot turn ordinary baseline digits into a metered marker read'
        assert h.refs_and_notes(docs)[0]==[]
        assert page.text in h.body_texts(docs)
