"""Synthetic punctuation belongs to one occurrence across whitespace changes."""
import pytest
from cps.services.reflow.reading_tiers import conserve_punctuation_boundaries

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('glyph',[',','.',';',':'])
@pytest.mark.parametrize('case',['glued','two_printed','decimal','remote'])
def test_correction_cannot_repeat_the_delimiter_already_moved_to_previous_token(glyph,case):
    source=[dict(text='garden'+(glyph if case=='two_printed'else''),bbox=[10,20,40,32]),
            dict(text='.'+('25'if case=='decimal'else'which'),bbox=[44,20,70,32])]
    if case=='remote':source[1]['bbox']=[44,80,70,92]
    model=['garden'+glyph,'25'if case=='decimal'else'which']
    token=glyph+model[1]
    result=dict(tokens=[model[0],token],marked={1},word_notes={1:dict(status='RESOLVED',
        source_range=[1,2],model_range=[1,2])},guard=[])
    conserve_punctuation_boundaries(result,source,model)
    assert result['tokens']==[model[0],model[1]if case=='glued'else token]
    assert (1 in result['marked']) is (case!='glued')
    assert bool(result['guard']) is (case=='glued')


def test_page_compilation_consumes_the_boundary_delimiter_once(tmp_path,monkeypatch):
    """The publication path must invoke conservation, not just expose a helper."""
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    printed='The garden, which grows green.'
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        p=drawn.new_page(width=350,height=140);p.insert_text((20,60),printed,fontsize=12)
        ink=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in p.get_text('words')]
        source=[dict(w)for w in ink];source[1]['text']='garden';source[2]['text']='.which'
        page=scan.new_page(width=350,height=140);page.insert_image(page.rect,pixmap=p.get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(ink))
        original=pa.adjudicate
        def corrected(*a,**kw):
            result=original(*a,**kw)
            result['tokens'][2]=',which';result['marked'].add(2)
            result.setdefault('word_notes',{})[2]=dict(source=['.which'],model=['which'],
                chosen=[',which'],status='RESOLVED',method='independent_upright_crop',
                source_range=[2,3],model_range=[2,3])
            return result
        monkeypatch.setattr(pa,'adjudicate',corrected)
        class Package:
            def image(self,*a):pass
        page,receipt=vp.compile_page(answer(block(printed)),source,SourceDisplay(scan,0),0,
            Package(),{},pixel_words=ink,regions={'b1':[[15,40,330,75]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    assert receipt['blocks'][0]['published_tokens']==printed.split()
    assert any(x['kind']=='source_punctuation_boundary_conserved'
               for x in receipt['blocks'][0]['guard'])
    assert ', ,which'not in ''.join(page['body'])
