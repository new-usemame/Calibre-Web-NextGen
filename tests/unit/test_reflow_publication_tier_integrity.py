"""Synthetic readers must not bypass the tier selected for their own word."""
import copy
import pymupdf
import pytest
from lxml import etree
from cps.services.reflow import vision_pipeline as vp,pixel_adjudication as pa,native_text
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit

class Package:
    def __init__(self):self.images={}
    def image(self,name,data):self.images[name]=data


@pytest.mark.parametrize('printed,chosen,status,method,tier,expected',[
    ('1st','Ist','RESOLVED','two_local_pixel_readings','flagged','1st'),
    ('zarnel','zernel','RESOLVED','two_own_word_language_readings','flagged','zernel'),
    ('garden','1\x98','UNRESOLVED','pixel_read_inconclusive','print',None),
])
def test_compiler_emits_the_selected_tier_instead_of_a_bare_dagger(tmp_path,monkeypatch,printed,chosen,status,method,tier,expected):
    reading=f'The blue {printed} stays beside the quiet path in summer.'
    with pymupdf.open()as drawing,pymupdf.open()as scan:
        p=drawing.new_page(width=420,height=170);p.insert_text((20,60),reading,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in p.get_text('words')]
        page=scan.new_page(width=420,height=170);page.insert_image(page.rect,pixmap=p.get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**kw:pa.CropWitness(words))
        original=pa.adjudicate
        def reader(*a,**kw):
            result=original(*a,**kw);i=result['tokens'].index(printed)
            result['tokens'][i]=chosen;result['marked'].add(i)
            decision=dict(source=[printed],model=[printed],chosen=[chosen],status=status,method=method,
                source_range=[i,i+1],model_range=[i,i+1])
            result.setdefault('word_notes',{})[i]=decision
            result.setdefault('adjudication',[]).append(decision)
            return result
        monkeypatch.setattr(pa,'adjudicate',reader)
        package=Package();assets={}
        compiled,receipt=vp.compile_page(answer(block(reading)),words,SourceDisplay(scan,0),0,
            package,assets,pixel_words=words,regions={'b1':[[15,40,410,75]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    root=etree.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(compiled['body']+compiled['asides'])+'</div>')
    flags=root.xpath('.//span[@class="reflow-plausible"]')
    if tier=='flagged':
        assert len(flags)==1 and ''.join(flags[0].itertext())==expected
        assert any(v.get('kind')in ('inline_print_preview','flagged_unit_preview')for v in assets.values())
    else:
        assert not flags
        assert '\x98'not in ''.join(root.xpath('.//p')[0].itertext())
        assert receipt['blocks'][0]['source_image_words']==[2]
        assert root.xpath('.//img')


def test_native_ordinal_keeps_its_typed_reading_with_print_instead_of_a_wrong_resolution(tmp_path,monkeypatch):
    with pymupdf.open()as doc:
        page=doc.new_page(width=350,height=170)
        page.insert_text((20,60),'The 1st sample stays readable.',fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in page.get_text('words')]
        monkeypatch.setattr(native_text,'drawn_word_authority',lambda *a:False)
        monkeypatch.setattr(native_text,'drawn_word_authorities',lambda p,values:[False]*len(values))
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        monkeypatch.setattr(pa,'refine_fallback',lambda *a,**k:None)
        decision=dict(source=['1st'],model=['1st'],chosen=['Ist'],status='RESOLVED',method='two_local_pixel_readings')
        monkeypatch.setattr(pa,'audit_agreement',lambda *a,**k:{1:copy.deepcopy(decision)})
        output=dict(pno=0,body=['<p>The 1st sample stays readable.</p>'],asides=[])
        records=[dict(id='native-0',role='body',source_regions=[[15,40,340,80]])]
        package=Package();assets={}
        vp._audit_native_pixels(doc,output,records,tmp_path,package,assets)
    root=etree.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(output['body'])+'</div>')
    flags=root.xpath('.//span[@class="reflow-plausible"]')
    assert len(flags)==1 and ''.join(flags[0].itertext())=='1st'
    assert 'Ist'not in ''.join(root.itertext())
    assert records[0]['flagged_readings']['1']['reading']=='1st'
