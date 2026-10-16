"""Source-free marker recall/crop controls, no model calls."""
import importlib.util
import io
from pathlib import Path
import pytest
import pymupdf
from PIL import Image
from cps.services.reflow import vision_pixels, vision_pipeline
_spec=importlib.util.spec_from_file_location('round4_marker_recall_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

class MarkerPage(h.InkPage):
    def __init__(self,label,compound=False,shifted=False,tip=False,raised=True):
        super().__init__()
        self.label=label;self.shifted=shifted
        prefix='The quartz-basalt' if compound else 'The quiet mineral'
        self.page.insert_text((24,55),prefix,fontsize=9)
        x=24+pymupdf.get_text_length(prefix,fontsize=9)+7
        self.page.insert_text((x,55),'(',fontsize=9)
        start=x+pymupdf.get_text_length('(',fontsize=9)+1
        size=5 if raised else 9;baseline=50 if raised else 55
        self.first_digit=[start,baseline-size, start+pymupdf.get_text_length(label[0],fontsize=size),baseline+.3]
        for i,digit in enumerate(label):
            self.page.insert_text((start,baseline),digit,fontsize=size,color=(1,0,0) if i==0 else (0,0,1))
            start+=pymupdf.get_text_length(digit,fontsize=size)
        end=start+1
        self.page.insert_text((end,55),').',fontsize=9)
        self.marker_box=[x-.5,46,end+pymupdf.get_text_length(')',fontsize=9)+.5,58]
        if tip:
            # A disconnected closing-parenthesis tip, not a baseline digit.
            self.page.draw_rect([end-.55,55.4,end-.2,56.1],color=(0,0,0),fill=(0,0,0),width=0)
        text=prefix+' ('+label+'). Several ordinary words identify the readable source paragraph.'
        self.page.insert_text((24,84),'Several ordinary words identify the readable source paragraph.',fontsize=9)
        self.blocks.append(h.model_block(text,'body','body',[23,40,282,90]))
    def finish(self):
        image,words,reading=super().finish()
        b=self.marker_box
        words=[w for w in words if not (w['bbox'][0]<b[2] and w['bbox'][2]>b[0] and w['bbox'][1]<b[3] and w['bbox'][3]>b[1])]
        if self.shifted:
            for w in words:
                if w['bbox'][1]<60:w['bbox'][1]-=4
        words.append(dict(text='(").',bbox=list(b),confidence=99))
        words.sort(key=lambda w:(w['bbox'][1],w['bbox'][0]))
        return image,words,reading

@pytest.mark.parametrize('label,compound,shifted,tip,initial',[('17',True,False,False,True),('17',False,True,False,True),('18',False,False,True,False),('19',False,False,True,False)])
def test_genuine_marker_request_contains_all_digit_ink_then_links_exact_note(tmp_path,monkeypatch,label,compound,shifted,tip,initial):
    page=MarkerPage(label,compound,shifted,tip);notes=h.InkPage()
    notes.text(label+' A finite synthetic note explains this source mineral.','note','footnote',y=320,size=7,marker=label,model='A finite synthetic note explains this source mineral.')
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:[dict(label='1',bbox=page.first_digit,method='synthetic_partial_character_read')] if initial and display.pno==0 else [])
    actual=vision_pipeline._literal_note_refs;requests=[]
    def read_cached(image):
        picture=Image.open(io.BytesIO(image)).convert('RGB')
        assert picture.width >= (page.marker_box[2]-page.marker_box[0])*12*.75, 'a first-digit ROI cannot contain both full-height parentheses and every digit of the source label'
        pixels=list(picture.getdata())
        assert any(r>130 and g<100 and b<100 for r,g,b in pixels),'the actual source first digit must be in the request JPEG'
        assert any(b>130 and r<100 and g<100 for r,g,b in pixels),'the whole raised label includes the independently blue second digit, not a first-digit fragment'
        requests.append(image)
        return dict(label=label,uncertain=False,cached=True,new_cost_usd=0)
    def refs(tokens,optical,display,box,cache,labels,pno,context):
        context['marker_reader']=read_cached
        return actual(tokens,optical,display,box,cache,labels,pno,context)
    monkeypatch.setattr(vision_pipeline,'_literal_note_refs',refs)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[page,notes])
    refs,notes=h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())==label
    assert len(requests)==1,'one independently proven complete ROI reaches the cached marker response'
    assert label in ''.join(notes[0][1].itertext())

@pytest.mark.parametrize('label',['1','2186'])
def test_ordinary_baseline_digits_with_descending_neighbor_letters_never_become_notes(tmp_path,monkeypatch,label):
    notes=h.InkPage()
    # Several adjacent ordinary baseline words end below the baseline because
    # of descenders; this is not source evidence that the numeric item is raised.
    baseline=55
    # Use a fresh actual source page so transformed optical labels still select
    # exactly their original glyph regions, not a pretend source number.
    page=h.InkPage()
    prefix='A lengthy genealogy'
    page.page.insert_text((24,baseline),prefix,fontsize=9)
    x=24+pymupdf.get_text_length(prefix,fontsize=9)+7
    page.page.insert_text((x,baseline),'('+label+').',fontsize=9)
    page.page.insert_text((24,84),'An ordinary enumeration or year stays in the reading paragraph.',fontsize=9)
    text=prefix+' ('+label+'). An ordinary enumeration or year stays in the reading paragraph.'
    page.blocks.append(h.model_block(text,'body','body',[23,40,282,90]))
    notes.text(label+' A selected independent note is unrelated to the body number.','note','footnote',y=320,size=7,marker=label,model='A selected independent note is unrelated to the body number.')
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:[])
    actual=vision_pipeline._literal_note_refs;requests=[]
    def read_cached(image):
        requests.append(image)
        return dict(label=label,uncertain=False,cached=True,new_cost_usd=0)
    def refs(tokens,optical,display,box,cache,labels,pno,context):
        context['marker_reader']=read_cached
        return actual(tokens,optical,display,box,cache,labels,pno,context)
    monkeypatch.setattr(vision_pipeline,'_literal_note_refs',refs)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[page,notes])
    assert requests==[],'ordinary baseline item/year cannot consume a compact marker request despite a finite same-labelled note'
    assert h.refs_and_notes(docs)[0]==[]
    assert text in h.body_texts(docs)
