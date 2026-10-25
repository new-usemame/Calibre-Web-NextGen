"""Synthetic divided compounds require evidence of both printed pieces."""
import pytest
from cps.services.reflow import pixel_adjudication as pa

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('prefix',[False,True])
def test_cropped_compound_tail_cannot_certify_a_different_complete_word(prefix):
    source=[dict(text='case',bbox=[20,35,45,45],confidence=99)]
    witness=pa.CropWitness([dict(source[0],text='ease')]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text='ease',confidence=99)]]*2
    witness.neighbor_occurrences=([dict(text='book-',bbox=[140,20,170,30],confidence=99)]if prefix else [])
    result=pa.adjudicate(['book-case'],source,witness)
    assert result['tokens']==['book-case'],'A tail-only view cannot replace the full divided compound'
    note=result['word_notes'][0]
    assert note['status']=='UNRESOLVED'and note['source_image_only']
    assert note['method']=='unproved_complete_divided_word'
    assert note['unplaced_prefix']is not prefix
    if prefix:assert note['source_parts']==[[140,20,170,30],[20,35,45,45]]


def test_complete_literal_word_keeps_its_independent_correcting_evidence():
    source=[dict(text='case',bbox=[20,35,45,45],confidence=99)]
    witness=pa.CropWitness([dict(source[0],text='ease')])
    result=pa.adjudicate(['case'],source,witness)
    assert result['tokens']==['case']and not result['marked']


@pytest.mark.parametrize('mapped_parts',[False,True])
def test_divided_word_print_keeps_both_owned_parts_without_middle_ink(mapped_parts):
    import io
    import pymupdf
    from PIL import Image
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    source=[dict(text='case',bbox=[20,35,45,45],confidence=99)]
    witness=pa.CropWitness([dict(source[0],text='ease')]);witness.model_reading=True
    witness.word_readings=lambda word:[[dict(word,text='ease',confidence=99)]]*2
    witness.neighbor_occurrences=[dict(text='tool-',bbox=[140,20,170,30],confidence=99)]
    note=pa.adjudicate(['tool-case'],source,witness)['word_notes'][0]
    parts=note['source_parts']
    if mapped_parts:parts=[dict(bbox=box)for box in parts]
    class Package:
        def image(self,name,data):self.data=data
    package=Package()
    with pymupdf.open()as doc:
        page=doc.new_page(width=200,height=80)
        for box in ([140,20,170,30],[20,35,45,45],[80,25,100,40]):
            page.draw_rect(box,color=(0,0,0),fill=(0,0,0))
        vp._word_disclosure_image(package,'images/compound.jpg',SourceDisplay(doc,0),
            pymupdf.Rect(18,18,172,47),parts)
    ink=Image.open(io.BytesIO(package.data)).convert('L')
    for point in ((150,25),(30,40)):
        assert ink.getpixel((int((point[0]-18)*3),int((point[1]-18)*3)))<30
    assert ink.getpixel((int((90-18)*3),int((30-18)*3)))>240,'Unowned middle ink must remain masked'


def test_page_publication_retains_complete_divided_word_evidence(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    with pymupdf.open()as doc,pymupdf.open()as scan:
        page=doc.new_page(width=240,height=100)
        page.insert_text((140,30),'tool-',fontsize=10)
        page.insert_text((20,47),'case stays readable.',fontsize=10)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        prefix=words[0];body=words[1:]
        target=scan.new_page(width=240,height=100);target.insert_image(target.rect,pixmap=page.get_pixmap(matrix=pymupdf.Matrix(3,3)))
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(body))
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package();images={}
        compiled,record=vp.compile_page(answer(block('tool-case stays readable.')),
            [],SourceDisplay(scan,0),0,package,images,pixel_words=words,
            regions={'b1':[[10,34,230,65]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
        evidence=[v for v in images.values()if v.get('source_parts')]
        assert evidence,'Publication must keep the divided prefix, not lose the unit on a part-format error'
        assert any(v['bbox'][0]<=min(prefix['bbox'][0],body[0]['bbox'][0]) and
            v['bbox'][1]<=prefix['bbox'][1] and v['bbox'][2]>=prefix['bbox'][2] and
            v['bbox'][3]>=body[0]['bbox'][3]for v in evidence)
        assert any(name in package.images for name,value in images.items()if value in evidence)
        assert record['blocks'][0]['published_tokens']==['tool-case','stays','readable.']
        assert 'source-word-image'in ''.join(compiled['body'])
        assert 'source-disclosure-ref'in ''.join(compiled['body'])
