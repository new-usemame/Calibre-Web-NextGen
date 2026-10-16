"""SOURCE-free interior note markers separate two physical note owners.

A model-labelled second note wrongly includes an unlabelled preceding-column
continuation. Preserve all original words, source confidence and exact links;
only an independently physical marker boundary may partition that text.
"""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline,vision_pixels

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_interior_note_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(mirror=False,plus=False,boundary=True,complete_separate=False,prefix_body=False,actual_plus=False):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    def ink(text,x,y,size=3.1):
        assert x+font.text_length(text,fontsize=size)<h.W
        p.page.insert_text((x,y),text,fontsize=size,fontname='unicode')
    body='One independently printed explanation cites a survey. A second explanation cites an archive.'
    ink('One independently printed explanation cites a survey.*',22,45,size=4.2)
    ink('A second explanation cites an archive.†',22,60,size=4.2)
    model='One independently printed explanation cites a survey.<n:first> A second explanation cites an archive.<n:second>'
    p.blocks.append(h.model_block(model,'body','body',[20,35,282,66]))
    x1,x2=(164,22) if mirror else (22,164)
    leftlines=['The original survey carefully compares coastal records',
               'and describes several independent observations collected',
               'within']
    prefixlines=['precisely measured districts where the original workers',
                 'compare specimens and preserve reliable observations.']
    citationlines=['This separate archival citation identifies the author',
                   'and the independently printed publication.']
    left=' '.join(leftlines);prefix=' '.join(prefixlines);citation=' '.join(citationlines)
    if complete_separate:
        left+='.'
        leftlines[-1]+='.'
    for j,line in enumerate(leftlines):ink(('* ' if j==0 else '')+line,x1,305+12*j)
    for j,line in enumerate(prefixlines):ink(('† ' if j==0 and complete_separate else '')+line,x2,301+12*j)
    for j,line in enumerate(citationlines):ink((('+' if actual_plus else '†')+' ' if j==0 and boundary and not complete_separate else '')+line,x2,340+12*j)
    if prefix_body:p.blocks.append(h.model_block(prefix,'sourcebody','body',[x2-2,291,x2+118,318]))
    p.blocks.append(h.model_block(left,'first','footnote',[x1-2,295,x1+118,333],marker='*'))
    note=h.model_block(prefix+' '+citation,'second','footnote',[x2-2,291,x2+118,359],marker='†')
    note['low_confidence']=[0,len(prefix.split())+2]
    p.blocks.append(note)
    finish=p.finish
    def finish_with_boundary_evidence():
        image,words,answer=finish()
        if plus:
            symbol=next(w for w in words if w['text']=='†' and w['bbox'][1]>330)
            symbol['text']='+';symbol.pop('confidence',None)
        return image,words,answer
    p.finish=finish_with_boundary_evidence
    return p,body,left,prefix,citation


def verified_marker_reader(monkeypatch,label='†'):
    calls=[];original=vision_pipeline.convert
    def convert(doc,path,reader,*args,**kwargs):
        def read_marker(image):
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(image)) as picture:
                assert max(picture.size)<=600,'verification is a compact original glyph, never a paid page'
            calls.append(image)
            return {'label':label,'uncertain':False,'cached':True,'new_cost_usd':0}
        reader.read_marker=read_marker
        return original(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    return calls


def note_text(note):
    return ' '.join(h.plain(p) for p in note.findall('.//x:p',h.NS))


@pytest.mark.parametrize('mirror',[False,True])
@pytest.mark.parametrize('plus',[False,True])
def test_source_interior_marker_keeps_unlabelled_prefix_with_previous_note(tmp_path,monkeypatch,mirror,plus):
    p,body,left,prefix,citation=fixture(mirror=mirror,plus=plus)
    calls=verified_marker_reader(monkeypatch)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    refs,notes=h.assert_exact_returns(docs,2)
    bytarget={(name,n.get('id')):n for name,n in notes}
    selected={''.join(ref.itertext()):bytarget[h.destination(name,ref.get('href'))] for name,ref in refs}
    star=note_text(selected['*']);dagger=note_text(selected['†'])
    assert left+' '+prefix in star,'the unlabelled physical column prefix belongs to the unfinished preceding note'
    assert prefix not in dagger and citation in dagger,'a lower physical dagger owns only its separate citation'
    assert sum(prefix in note_text(n) for _,n in notes)==1 and sum(citation in note_text(n) for _,n in notes)==1
    assert h.body_texts(docs)==[body],'no note continuation or label is ordinary BODY'
    # Original confidence annotations follow their exact source words when the
    # wrongly merged note is partitioned; they are not cleared for convenience.
    assert selected['*'].find('.//x:a[@class="source-disclosure-ref"]',h.NS) is not None
    assert selected['†'].find('.//x:a[@class="source-disclosure-ref"]',h.NS) is not None
    if plus:assert calls,'a misrecognized plus cannot become dagger ownership without cached bounded original-glyph verification'


def test_no_physical_interior_marker_cannot_authorize_note_partition(tmp_path,monkeypatch):
    p,body,left,prefix,citation=fixture(boundary=False)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,2)
    bytarget={(name,n.get('id')):n for name,n in notes}
    selected={''.join(ref.itertext()):bytarget[h.destination(name,ref.get('href'))] for name,ref in refs}
    assert prefix not in note_text(selected['*']),'missing physical marker evidence cannot move model words to another owner'
    assert prefix+' '+citation in note_text(selected['†'])
    assert h.body_texts(docs)==[body]


def test_legitimate_complete_separate_note_is_not_column_continuation(tmp_path,monkeypatch):
    p,body,left,prefix,citation=fixture(complete_separate=True)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,2)
    bytarget={(name,n.get('id')):n for name,n in notes}
    selected={''.join(ref.itertext()):bytarget[h.destination(name,ref.get('href'))] for name,ref in refs}
    assert left in note_text(selected['*']) and prefix not in note_text(selected['*'])
    assert prefix+' '+citation in note_text(selected['†'])
    assert h.body_texts(docs)==[body]


def test_coherent_body_prefix_cannot_be_moved_into_an_unfinished_note(tmp_path,monkeypatch):
    p,body,left,prefix,citation=fixture(prefix_body=True)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,2)
    bytarget={(name,n.get('id')):n for name,n in notes}
    star=next(bytarget[h.destination(name,ref.get('href'))] for name,ref in refs if ''.join(ref.itertext())=='*')
    assert prefix not in note_text(star),'tight coherent BODY instances cannot be reassigned through the model note hint'
    assert h.body_texts(docs)==[body,prefix],'each genuine BODY paragraph remains live once'


def test_real_plus_does_not_authorize_dagger_partition(tmp_path,monkeypatch):
    p,body,left,prefix,citation=fixture(actual_plus=True)
    verified_marker_reader(monkeypatch,label='+')
    try:
        _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    except ValueError as error:
        assert 'marker' in str(error).lower() or 'label' in str(error).lower(),'only an explicit selected-symbol conflict is an allowed strict failure'
        return
    refs,notes=h.assert_exact_returns(docs,2);bytarget={(name,n.get('id')):n for name,n in notes}
    star=next(bytarget[h.destination(name,ref.get('href'))] for name,ref in refs if ''.join(ref.itertext())=='*')
    assert prefix not in note_text(star),'ordinary plus ink is not a globally substituted source dagger boundary'
    assert h.body_texts(docs)==[body]
