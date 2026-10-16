"""SOURCE-free narrow note gutters do not change authored column order."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('narrow_note_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def make_page(mirror=False,complete=False,prepartitioned=False):
    p=h.InkPage();font=pymupdf.Font('cjk');size=4.1
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    def ink(text,x,y):p.page.insert_text((x,y),text,fontsize=size,fontname='unicode')
    body='An ordinary explanation cites a survey. Another explanation cites an archive.'
    ink('An ordinary explanation cites a survey.*',15,45)
    ink('Another explanation cites an archive.†',15,60)
    p.blocks.append(h.model_block('An ordinary explanation cites a survey.<n:first> Another explanation cites an archive.<n:second>','body','body',[13,36,282,65]))
    lines=['The original survey carefully compares coastal records',
           'and describes several independent observations collected',
           'with separate archival evidence supporting each comparison','within']
    prefix=['precisely measured districts where the original workers',
            'compare specimens and preserve reliable observations',
            'and additional evidence confirms these careful comparisons.']
    citation=['This separate archival citation identifies the author',
              'and the independently printed publication.']
    right=15+max(font.text_length(t,fontsize=size) for t in lines)+8
    x1,x2=(right,15) if mirror else (15,right)
    if complete:lines[-1]+='.'
    for n,t in enumerate(lines):ink(('* ' if n==0 else '')+t,x1,305+12*n)
    for n,t in enumerate(prefix):ink(('† ' if n==0 and complete else '')+t,x2,305+12*n)
    for n,t in enumerate(citation):ink(('† ' if n==0 and not complete else '')+t,x2,358+12*n)
    first=' '.join(lines);head=' '.join(prefix);last=' '.join(citation)
    p.blocks.append(h.model_block(first+(' '+head if prepartitioned else ''),'first','footnote',
        [13,295,282,346] if prepartitioned else [x1-2,295,x1+118,346],marker='*'))
    p.blocks.append(h.model_block(last if prepartitioned else head+' '+last,'second','footnote',
        [x2-2,348,x2+118,376] if prepartitioned else [x2-2,291,x2+118,376],marker='†'))
    finish=p.finish
    def optical():
        image,words,answer=finish()
        symbol=next(w for w in words if w['text']=='†' and w['bbox'][1]>(290 if complete else 348))
        symbol['text']='+';symbol.pop('confidence',None)
        return image,words,answer
    p.finish=optical
    return p,body,first,head,last


def note_text(note):
    text=' '.join(h.plain(p) for p in note.findall('.//x:p',h.NS))
    # The separately displayed physical note label is not a lexical note word.
    return text[2:] if text.startswith(('* ','† ')) else text


@pytest.mark.parametrize('mirror',[False,True])
@pytest.mark.parametrize('prepartitioned',[False,True])
def test_partitioned_note_retains_exact_logical_order_across_narrow_gutter(tmp_path,monkeypatch,mirror,prepartitioned):
    p,body,first,head,last=make_page(mirror=mirror,prepartitioned=prepartitioned)
    original=vision_pipeline.convert;calls=[]
    def convert(doc,path,reader,*args,**kwargs):
        def read_marker(image):
            calls.append(image)
            return dict(label='†',uncertain=False,cached=True,new_cost_usd=0)
        reader.read_marker=read_marker
        return original(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,2)
    targets={(name,n.get('id')):n for name,n in notes}
    selected={''.join(r.itertext()):targets[h.destination(name,r.get('href'))] for name,r in refs}
    assert note_text(selected['*'])==first+' '+head,'physical row interleaving must not replace exact authored column-order note words'
    assert note_text(selected['†'])==last,'the lower physical marker owns only its separate citation'
    assert h.body_texts(docs)==[body]
    if not prepartitioned:assert calls,'misrecognized interior plus requires bounded original-glyph verification'


def test_complete_independent_notes_across_narrow_gutter_keep_their_owners(tmp_path,monkeypatch):
    p,body,first,head,last=make_page(complete=True)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,2)
    targets={(name,n.get('id')):n for name,n in notes}
    selected={''.join(r.itertext()):targets[h.destination(name,r.get('href'))] for name,r in refs}
    assert note_text(selected['*'])==first
    assert note_text(selected['†'])==head+' '+last
    assert h.body_texts(docs)==[body]


def test_same_row_word_gaps_do_not_reorder_a_single_note_into_columns(tmp_path,monkeypatch):
    p=h.InkPage();p.text('An ordinary explanation cites a note.','body',y=45,size=6,
        model='An ordinary explanation cites a note.<n:first>')
    rows=[('The careful survey records','several reliable observations'),
          ('The original author compares','independently measured districts.' )]
    for n,(left,right) in enumerate(rows):
        p.page.insert_text((15,310+12*n),('* ' if n==0 else '')+left,fontsize=4)
        x=15+pymupdf.get_text_length(('* ' if n==0 else '')+left,fontsize=4)+8
        p.page.insert_text((x,310+12*n),right,fontsize=4)
    text=' '.join(' '.join(row) for row in rows)
    p.blocks.append(h.model_block(text,'first','footnote',[13,300,282,326],marker='*'))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);_,notes=h.assert_exact_returns(docs,1)
    assert note_text(notes[0][1])==text,'lexically proved row-order prose remains row-order despite persistent spaces'
